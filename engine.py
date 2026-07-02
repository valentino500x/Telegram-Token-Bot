"""Motore di trading automatico multi-simbolo per i perpetual di Aster DEX.

Ciclo: per ogni simbolo configurato scarica le candele -> valuta la strategia
-> gestisce la posizione di quel simbolo. Ogni trade viene aperto con
stop-loss e take-profit basati sull'ATR; la dimensione e' calcolata in base
alla percentuale di rischio configurata e c'e' un limite di posizioni
aperte contemporaneamente.

In modalita' DRY_RUN gli ordini sono solo simulati (paper trading).
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field

import strategy
from aster_client import AsterClient, AsterAPIError
from config import Settings

log = logging.getLogger(__name__)

PAPER_START_BALANCE = 1000.0


@dataclass
class PaperPosition:
    direction: str  # LONG / SHORT
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: float
    entry_fee: float = 0.0


@dataclass
class SymbolState:
    last_candle_acted: int = 0
    last_signal: strategy.Signal | None = None
    paper: PaperPosition | None = None
    filters: dict | None = field(default=None)
    disabled_until: float = 0.0  # mercato sospeso fino a questo timestamp


class TradingEngine:
    def __init__(self, cfg: Settings, client: AsterClient, notify):
        """notify: coroutine function(str) usata per i messaggi Telegram."""
        self.cfg = cfg
        self.client = client
        self.notify = notify
        self.running = False
        self._task: asyncio.Task | None = None
        self._halted_reason: str | None = None
        self.states: dict[str, SymbolState] = {}
        self.paper_realized_pnl = 0.0  # PnL netto (commissioni e slippage inclusi)
        self.paper_fees_total = 0.0
        self.paper_trades_closed = 0
        self._last_error_msg: str | None = None
        self._last_error_time = 0.0

    def _state(self, symbol: str) -> SymbolState:
        if symbol not in self.states:
            self.states[symbol] = SymbolState()
        return self.states[symbol]

    # ------------------------------------------------------------- lifecycle

    async def start(self):
        if self.running:
            return
        await self.ensure_symbols()
        if not self.cfg.dry_run:
            await asyncio.to_thread(self._setup_symbols)
        self._halted_reason = None
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def ensure_symbols(self) -> list:
        """Se SYMBOLS=AUTO, sceglie i perpetual USDT piu' scambiati nelle 24h."""
        if not self.cfg.symbols or not self.cfg.symbols[0].upper().startswith("AUTO"):
            return self.cfg.symbols
        first = self.cfg.symbols[0].upper()
        count = self.cfg.auto_symbols_count
        if ":" in first:
            try:
                count = max(1, int(first.split(":", 1)[1]))
            except ValueError:
                pass
        symbols = await asyncio.to_thread(self._discover_symbols, count)
        self.cfg.symbols = symbols
        await self.notify(
            f"🔎 Selezionati automaticamente i {len(symbols)} perpetual "
            f"piu' scambiati (24h):\n{', '.join(symbols)}")
        return symbols

    def _discover_symbols(self, count: int) -> list:
        info = self.client.exchange_info()
        tradable = set()
        for s in info.get("symbols", []):
            if (s.get("status") == "TRADING"
                    and s.get("quoteAsset") == "USDT"
                    and s.get("contractType", "PERPETUAL") == "PERPETUAL"):
                tradable.add(s["symbol"])
        tickers = self.client.ticker_24hr()
        volumes = {}
        for t in tickers if isinstance(tickers, list) else [tickers]:
            sym = t.get("symbol")
            if sym in tradable:
                volumes[sym] = float(t.get("quoteVolume", 0) or 0)
        top = sorted(volumes, key=volumes.get, reverse=True)[:count]
        if not top:
            raise AsterAPIError(0, None, "nessun mercato trovato per la selezione automatica")
        return top

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def _setup_symbols(self):
        # Il motore ragiona in One-way Mode: una sola posizione per mercato
        try:
            self.client.set_position_mode(dual=False)
        except AsterAPIError as e:
            raise AsterAPIError(
                e.status, e.code,
                "impossibile impostare la One-way Mode sul conto "
                f"({e.msg}) — se hai posizioni Hedge aperte su Aster, "
                "chiudile dal sito e rilancia /run") from e
        for symbol in self.cfg.symbols:
            self.client.set_margin_type(symbol, self.cfg.margin_type)
            self.client.set_leverage(symbol, self.cfg.leverage)

    async def _loop(self):
        await self.notify(
            f"🤖 Trading avviato su {', '.join(self.cfg.symbols)} ({self.cfg.interval}), "
            f"leva {self.cfg.leverage}x, rischio {self.cfg.risk_per_trade_pct}%/trade, "
            f"max {self.cfg.max_open_positions} posizioni"
            + (" — MODALITA' SIMULAZIONE (dry-run)" if self.cfg.dry_run else " — ORDINI REALI")
        )
        while self.running:
            for symbol in list(self.cfg.symbols):
                if not self.running:
                    return
                try:
                    await self.tick_symbol(symbol)
                except asyncio.CancelledError:
                    return
                except AsterAPIError as e:
                    log.warning("Errore API Aster su %s: %s", symbol, e)
                    msg = f"⚠️ {symbol}: errore API Aster: {e.msg}"
                    if e.code:
                        msg += f" (codice {e.code})"
                    await self._notify_error(msg)
                except Exception as e:
                    log.exception("Errore nel ciclo di trading (%s)", symbol)
                    await self._notify_error(f"⚠️ {symbol}: errore nel ciclo di trading: {e}")
            await asyncio.sleep(self.cfg.loop_seconds)

    async def _notify_error(self, msg: str):
        """Notifica un errore su Telegram, ma non ripete lo stesso per 10 minuti."""
        now = time.time()
        if msg != self._last_error_msg or now - self._last_error_time > 600:
            await self.notify(msg)
        self._last_error_msg = msg
        self._last_error_time = now

    # ------------------------------------------------------------------ tick

    async def tick_symbol(self, symbol: str):
        state = self._state(symbol)
        if state.disabled_until and time.time() < state.disabled_until:
            return
        raw = await asyncio.to_thread(
            self.client.klines, symbol, self.cfg.interval,
            max(self.cfg.ema_slow * 4, 120))
        candles = strategy.parse_klines(raw)
        sig = strategy.evaluate(candles, self.cfg)
        if sig is None:
            return
        state.last_signal = sig

        if self.cfg.dry_run:
            await self._check_paper_exits(symbol, sig.price)

        if self._halted_reason:
            return
        if not await self._daily_loss_ok():
            return

        if sig.direction is None or sig.candle_time <= state.last_candle_acted:
            return

        position = await self.current_position(symbol)
        pos_dir = position["direction"] if position else None
        if pos_dir == sig.direction:
            return  # gia' nella direzione giusta

        state.last_candle_acted = sig.candle_time
        if pos_dir is not None:
            await self.close_position(symbol, f"segnale opposto ({sig.direction})")
        else:
            open_count = len(await self.all_positions())
            if open_count >= self.cfg.max_open_positions:
                log.info("%s: segnale %s ignorato, gia' %d posizioni aperte",
                         symbol, sig.direction, open_count)
                return
        await self._open_position(symbol, sig)

    # -------------------------------------------------------------- position

    async def current_position(self, symbol: str) -> dict | None:
        """Ritorna {'symbol','direction','qty','entry','pnl'} oppure None."""
        state = self._state(symbol)
        if self.cfg.dry_run:
            if not state.paper:
                return None
            p = state.paper
            price = state.last_signal.price if state.last_signal else p.entry
            sign = 1 if p.direction == "LONG" else -1
            pnl = (price - p.entry) * sign * p.qty - p.entry_fee
            return {"symbol": symbol, "direction": p.direction, "qty": p.qty,
                    "entry": p.entry, "pnl": pnl}
        rows = await asyncio.to_thread(self.client.position_risk, symbol)
        for row in rows if isinstance(rows, list) else [rows]:
            amt = float(row.get("positionAmt", 0) or 0)
            if amt != 0:
                return {"symbol": symbol,
                        "direction": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry": float(row.get("entryPrice", 0) or 0),
                        "pnl": float(row.get("unRealizedProfit", 0) or 0)}
        return None

    async def all_positions(self) -> list[dict]:
        if self.cfg.dry_run:
            out = []
            for symbol in self.cfg.symbols:
                pos = await self.current_position(symbol)
                if pos:
                    out.append(pos)
            return out
        rows = await asyncio.to_thread(self.client.position_risk)
        out = []
        for row in rows if isinstance(rows, list) else [rows]:
            amt = float(row.get("positionAmt", 0) or 0)
            if amt != 0:
                out.append({"symbol": row.get("symbol"),
                            "direction": "LONG" if amt > 0 else "SHORT",
                            "qty": abs(amt),
                            "entry": float(row.get("entryPrice", 0) or 0),
                            "pnl": float(row.get("unRealizedProfit", 0) or 0)})
        return out

    async def available_balance(self) -> float:
        if self.cfg.dry_run:
            return PAPER_START_BALANCE + self.paper_realized_pnl  # capitale fittizio
        rows = await asyncio.to_thread(self.client.balance)
        for row in rows if isinstance(rows, list) else [rows]:
            if row.get("asset") == "USDT":
                return float(row.get("availableBalance", 0) or 0)
        return 0.0

    async def _open_position(self, symbol: str, sig: strategy.Signal):
        if sig.atr <= 0:
            return
        state = self._state(symbol)
        balance = await self.available_balance()
        risk_amount = balance * self.cfg.risk_per_trade_pct / 100.0
        stop_distance = sig.atr * self.cfg.stop_loss_atr
        qty = risk_amount / stop_distance

        max_notional = min(self.cfg.max_position_usdt, balance * self.cfg.leverage * 0.95)
        qty = min(qty, max_notional / sig.price)

        side = "BUY" if sig.direction == "LONG" else "SELL"
        exit_side = "SELL" if sig.direction == "LONG" else "BUY"
        sl = sig.price - stop_distance if sig.direction == "LONG" else sig.price + stop_distance
        tp_distance = sig.atr * self.cfg.take_profit_atr
        tp = sig.price + tp_distance if sig.direction == "LONG" else sig.price - tp_distance

        if self.cfg.dry_run:
            if qty * sig.price < 5:
                return
            # entrata peggiorata dallo slippage + commissione taker di apertura
            slip = self.cfg.slippage_pct / 100.0
            entry = sig.price * (1 + slip) if sig.direction == "LONG" else sig.price * (1 - slip)
            entry_fee = qty * entry * self.cfg.fee_pct / 100.0
            state.paper = PaperPosition(sig.direction, qty, entry, sl, tp,
                                        time.time(), entry_fee)
            await self.notify(
                f"📄 [SIMULAZIONE] Aperto {sig.direction} {qty:.6g} {symbol} "
                f"@ ~{entry:.6g} (fee {entry_fee:.4f} USDT)\n"
                f"🛑 SL {sl:.6g} | 🎯 TP {tp:.6g} | RSI {sig.rsi:.1f}")
            return

        filters = await asyncio.to_thread(self._symbol_filters, symbol)
        qty_str = self._round_step(qty, filters["stepSize"])
        if float(qty_str) < filters["minQty"] or float(qty_str) * sig.price < filters["minNotional"]:
            await self.notify(
                f"⚠️ {symbol}: trade saltato, quantita' {qty_str} sotto i minimi del mercato "
                f"(minQty {filters['minQty']}, minNotional {filters['minNotional']} USDT)")
            return
        sl_str = self._round_step(sl, filters["tickSize"])
        tp_str = self._round_step(tp, filters["tickSize"])

        try:
            order = await asyncio.to_thread(
                self.client.market_order, symbol, side, qty_str)
        except AsterAPIError as e:
            # Errori strutturali del mercato (es. tetto nozionale raggiunto,
            # simbolo in reduce-only): inutile riprovare a ogni giro
            if e.code in (-5018, -1121, -4131):
                state.disabled_until = time.time() + 4 * 3600
                await self.notify(f"⏸ {symbol}: mercato sospeso per 4 ore — "
                                  f"{e.msg} (codice {e.code})")
                return
            raise
        try:
            await asyncio.to_thread(
                self.client.stop_market_close, symbol, exit_side, sl_str)
            await asyncio.to_thread(
                self.client.take_profit_market_close, symbol, exit_side, tp_str)
        except AsterAPIError as e:
            # Senza stop-loss non teniamo la posizione aperta
            await self.notify(f"🚨 {symbol}: impossibile piazzare SL/TP ({e.msg}): "
                              f"chiudo subito la posizione")
            await self.close_position(symbol, "protezione fallita")
            return

        await self.notify(
            f"✅ Aperto {sig.direction} {qty_str} {symbol} @ ~{sig.price:.6g} "
            f"(ordine {order.get('orderId')})\n🛑 SL {sl_str} | 🎯 TP {tp_str} | RSI {sig.rsi:.1f}")

    async def close_position(self, symbol: str, reason: str = "richiesta manuale",
                             exit_price: float | None = None) -> bool:
        state = self._state(symbol)
        position = await self.current_position(symbol)
        if self.cfg.dry_run:
            if not state.paper:
                return False
            p = state.paper
            if exit_price is None:
                exit_price = state.last_signal.price if state.last_signal else p.entry
            # uscita peggiorata dallo slippage + commissione taker di chiusura
            slip = self.cfg.slippage_pct / 100.0
            exit_fill = exit_price * (1 - slip) if p.direction == "LONG" else exit_price * (1 + slip)
            sign = 1 if p.direction == "LONG" else -1
            gross = (exit_fill - p.entry) * sign * p.qty
            exit_fee = p.qty * exit_fill * self.cfg.fee_pct / 100.0
            fees = p.entry_fee + exit_fee
            net = gross - fees
            self.paper_realized_pnl += net
            self.paper_fees_total += fees
            self.paper_trades_closed += 1
            await self.notify(
                f"📄 [SIMULAZIONE] Chiuso {p.direction} {p.qty:.6g} {symbol} ({reason})\n"
                f"PnL netto {net:+.2f} USDT (lordo {gross:+.2f}, commissioni {fees:.4f})")
            state.paper = None
            return True
        if not position:
            return False
        await asyncio.to_thread(self.client.cancel_all_orders, symbol)
        side = "SELL" if position["direction"] == "LONG" else "BUY"
        filters = await asyncio.to_thread(self._symbol_filters, symbol)
        qty_str = self._round_step(position["qty"], filters["stepSize"])
        await asyncio.to_thread(
            self.client.market_order, symbol, side, qty_str, True)
        await self.notify(
            f"🔒 Chiuso {position['direction']} {qty_str} {symbol} ({reason}) — "
            f"PnL non realizzato al momento: {position['pnl']:+.2f} USDT")
        return True

    async def close_all_positions(self, reason: str = "richiesta manuale") -> int:
        closed = 0
        for pos in await self.all_positions():
            if await self.close_position(pos["symbol"], reason):
                closed += 1
        return closed

    async def _check_paper_exits(self, symbol: str, price: float):
        """In simulazione applica SL/TP confrontando il prezzo corrente."""
        state = self._state(symbol)
        if not state.paper:
            return
        p = state.paper
        hit, fill = None, None
        if p.direction == "LONG":
            if price <= p.stop_loss:
                hit, fill = "stop-loss", p.stop_loss
            elif price >= p.take_profit:
                hit, fill = "take-profit", p.take_profit
        else:
            if price >= p.stop_loss:
                hit, fill = "stop-loss", p.stop_loss
            elif price <= p.take_profit:
                hit, fill = "take-profit", p.take_profit
        if hit:
            await self.close_position(symbol, hit, exit_price=fill)

    # ------------------------------------------------------------------ risk

    async def _daily_loss_ok(self) -> bool:
        """Ferma il trading se la perdita realizzata oggi supera la soglia."""
        try:
            if self.cfg.dry_run:
                pnl_today = self.paper_realized_pnl
                balance = PAPER_START_BALANCE
            else:
                midnight = int(time.time() // 86400 * 86400 * 1000)
                rows = await asyncio.to_thread(self.client.income, None, midnight)
                relevant = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
                pnl_today = sum(float(r.get("income", 0) or 0) for r in rows
                                if r.get("incomeType") in relevant)
                balance = await self.available_balance()
            if balance > 0 and pnl_today < 0 and abs(pnl_today) / balance * 100 >= self.cfg.max_daily_loss_pct:
                self._halted_reason = (
                    f"perdita giornaliera {pnl_today:+.2f} USDT oltre il limite "
                    f"del {self.cfg.max_daily_loss_pct}%")
                await self.close_all_positions("limite perdita giornaliera")
                await self.notify(f"🛑 Trading sospeso: {self._halted_reason}. "
                                  f"Usa /run per riattivarlo.")
                self.running = False
                return False
            return True
        except AsterAPIError:
            return True  # non bloccare il trading per un errore di lettura

    # ----------------------------------------------------------- symbol info

    def _symbol_filters(self, symbol: str) -> dict:
        state = self._state(symbol)
        if state.filters is None:
            info = self.client.exchange_info()
            sym = next((s for s in info.get("symbols", [])
                        if s.get("symbol") == symbol), None)
            if not sym:
                raise AsterAPIError(400, -1121, f"simbolo {symbol} non trovato")
            filters = {f["filterType"]: f for f in sym.get("filters", [])}
            state.filters = {
                "stepSize": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "minQty": float(filters.get("LOT_SIZE", {}).get("minQty", 0)),
                "tickSize": float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
                "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 5)),
            }
        return state.filters

    def reset_symbol_cache(self):
        self.states = {}

    @staticmethod
    def _round_step(value: float, step: float) -> str:
        """Arrotonda per difetto al multiplo di step, come stringa decimale."""
        if step <= 0:
            return f"{value:.8f}".rstrip("0").rstrip(".")
        steps = math.floor(value / step + 1e-9)
        decimals = max(0, -int(math.floor(math.log10(step) + 1e-9)))
        return f"{steps * step:.{decimals}f}"
