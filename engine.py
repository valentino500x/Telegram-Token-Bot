"""Motore di trading automatico per i perpetual di Aster DEX.

Ciclo: scarica le candele -> valuta la strategia -> gestisce la posizione.
Ogni trade viene aperto con stop-loss e take-profit basati sull'ATR e la
dimensione e' calcolata in base alla percentuale di rischio configurata.

In modalita' DRY_RUN gli ordini sono solo simulati (paper trading) e lo
stato della posizione fittizia viene gestito internamente.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass

import strategy
from aster_client import AsterClient, AsterAPIError
from config import Settings

log = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    direction: str  # LONG / SHORT
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: float


class TradingEngine:
    def __init__(self, cfg: Settings, client: AsterClient, notify):
        """notify: coroutine function(str) usata per i messaggi Telegram."""
        self.cfg = cfg
        self.client = client
        self.notify = notify
        self.running = False
        self._task: asyncio.Task | None = None
        self._last_candle_acted = 0
        self._filters: dict | None = None
        self._halted_reason: str | None = None
        self.last_signal: strategy.Signal | None = None
        self.paper: PaperPosition | None = None
        self.paper_realized_pnl = 0.0
        self._last_error_msg: str | None = None
        self._last_error_time = 0.0

    # ------------------------------------------------------------- lifecycle

    async def start(self):
        if self.running:
            return
        if not self.cfg.dry_run:
            await asyncio.to_thread(self._setup_symbol)
        self._halted_reason = None
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None

    def _setup_symbol(self):
        self.client.set_margin_type(self.cfg.symbol, self.cfg.margin_type)
        self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)

    async def _loop(self):
        await self.notify(
            f"🤖 Trading avviato su {self.cfg.symbol} ({self.cfg.interval}), "
            f"leva {self.cfg.leverage}x, rischio {self.cfg.risk_per_trade_pct}%/trade"
            + (" — MODALITA' SIMULAZIONE (dry-run)" if self.cfg.dry_run else " — ORDINI REALI")
        )
        while self.running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                return
            except AsterAPIError as e:
                log.warning("Errore API Aster: %s", e)
                msg = f"⚠️ Errore API Aster: {e.msg}"
                if e.code:
                    msg += f" (codice {e.code})"
                await self._notify_error(msg)
            except Exception as e:
                log.exception("Errore nel ciclo di trading")
                await self._notify_error(f"⚠️ Errore nel ciclo di trading: {e}")
            await asyncio.sleep(self.cfg.loop_seconds)

    async def _notify_error(self, msg: str):
        """Notifica un errore su Telegram, ma non ripete lo stesso per 10 minuti."""
        now = time.time()
        if msg != self._last_error_msg or now - self._last_error_time > 600:
            await self.notify(msg)
        self._last_error_msg = msg
        self._last_error_time = now

    # ------------------------------------------------------------------ tick

    async def tick(self):
        raw = await asyncio.to_thread(
            self.client.klines, self.cfg.symbol, self.cfg.interval,
            max(self.cfg.ema_slow * 4, 120))
        candles = strategy.parse_klines(raw)
        sig = strategy.evaluate(
            candles, self.cfg.ema_fast, self.cfg.ema_slow,
            self.cfg.rsi_period, self.cfg.rsi_long_max, self.cfg.rsi_short_min,
            self.cfg.atr_period)
        if sig is None:
            return
        self.last_signal = sig

        if self.cfg.dry_run:
            await self._check_paper_exits(sig.price)

        if self._halted_reason:
            return
        if not await self._daily_loss_ok():
            return

        if sig.direction is None or sig.candle_time <= self._last_candle_acted:
            return

        position = await self.current_position()
        pos_dir = position["direction"] if position else None
        if pos_dir == sig.direction:
            return  # gia' nella direzione giusta

        self._last_candle_acted = sig.candle_time
        if pos_dir is not None:
            await self.close_position(f"segnale opposto ({sig.direction})")
        await self._open_position(sig)

    # -------------------------------------------------------------- position

    async def current_position(self) -> dict | None:
        """Ritorna {'direction','qty','entry','pnl'} oppure None."""
        if self.cfg.dry_run:
            if not self.paper:
                return None
            price = self.last_signal.price if self.last_signal else self.paper.entry
            sign = 1 if self.paper.direction == "LONG" else -1
            return {"direction": self.paper.direction, "qty": self.paper.qty,
                    "entry": self.paper.entry,
                    "pnl": (price - self.paper.entry) * sign * self.paper.qty}
        rows = await asyncio.to_thread(self.client.position_risk, self.cfg.symbol)
        for row in rows if isinstance(rows, list) else [rows]:
            amt = float(row.get("positionAmt", 0) or 0)
            if amt != 0:
                return {"direction": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry": float(row.get("entryPrice", 0) or 0),
                        "pnl": float(row.get("unRealizedProfit", 0) or 0)}
        return None

    async def available_balance(self) -> float:
        if self.cfg.dry_run:
            return 1000.0 + self.paper_realized_pnl  # capitale fittizio
        rows = await asyncio.to_thread(self.client.balance)
        for row in rows if isinstance(rows, list) else [rows]:
            if row.get("asset") == "USDT":
                return float(row.get("availableBalance", 0) or 0)
        return 0.0

    async def _open_position(self, sig: strategy.Signal):
        if sig.atr <= 0:
            return
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
            self.paper = PaperPosition(sig.direction, qty, sig.price, sl, tp, time.time())
            await self.notify(
                f"📄 [SIMULAZIONE] Aperto {sig.direction} {qty:.6g} {self.cfg.symbol} "
                f"@ ~{sig.price:.6g}\n🛑 SL {sl:.6g} | 🎯 TP {tp:.6g} | RSI {sig.rsi:.1f}")
            return

        filters = await asyncio.to_thread(self._symbol_filters)
        qty_str = self._round_step(qty, filters["stepSize"])
        if float(qty_str) < filters["minQty"] or float(qty_str) * sig.price < filters["minNotional"]:
            await self.notify(
                f"⚠️ Trade saltato: quantita' {qty_str} sotto i minimi del mercato "
                f"(minQty {filters['minQty']}, minNotional {filters['minNotional']} USDT)")
            return
        sl_str = self._round_step(sl, filters["tickSize"])
        tp_str = self._round_step(tp, filters["tickSize"])

        order = await asyncio.to_thread(
            self.client.market_order, self.cfg.symbol, side, qty_str)
        try:
            await asyncio.to_thread(
                self.client.stop_market_close, self.cfg.symbol, exit_side, sl_str)
            await asyncio.to_thread(
                self.client.take_profit_market_close, self.cfg.symbol, exit_side, tp_str)
        except AsterAPIError as e:
            # Senza stop-loss non teniamo la posizione aperta
            await self.notify(f"🚨 Impossibile piazzare SL/TP ({e.msg}): chiudo subito la posizione")
            await self.close_position("protezione fallita")
            return

        await self.notify(
            f"✅ Aperto {sig.direction} {qty_str} {self.cfg.symbol} @ ~{sig.price:.6g} "
            f"(ordine {order.get('orderId')})\n🛑 SL {sl_str} | 🎯 TP {tp_str} | RSI {sig.rsi:.1f}")

    async def close_position(self, reason: str = "richiesta manuale") -> bool:
        position = await self.current_position()
        if self.cfg.dry_run:
            if not self.paper:
                return False
            pnl = position["pnl"] if position else 0.0
            self.paper_realized_pnl += pnl
            await self.notify(
                f"📄 [SIMULAZIONE] Chiuso {self.paper.direction} {self.paper.qty:.6g} "
                f"{self.cfg.symbol} ({reason}) — PnL {pnl:+.2f} USDT")
            self.paper = None
            return True
        if not position:
            return False
        await asyncio.to_thread(self.client.cancel_all_orders, self.cfg.symbol)
        side = "SELL" if position["direction"] == "LONG" else "BUY"
        filters = await asyncio.to_thread(self._symbol_filters)
        qty_str = self._round_step(position["qty"], filters["stepSize"])
        await asyncio.to_thread(
            self.client.market_order, self.cfg.symbol, side, qty_str, True)
        await self.notify(
            f"🔒 Chiuso {position['direction']} {qty_str} {self.cfg.symbol} ({reason}) — "
            f"PnL non realizzato al momento: {position['pnl']:+.2f} USDT")
        return True

    async def _check_paper_exits(self, price: float):
        """In simulazione applica SL/TP confrontando il prezzo corrente."""
        if not self.paper:
            return
        p = self.paper
        hit = None
        if p.direction == "LONG":
            if price <= p.stop_loss:
                hit = "stop-loss"
            elif price >= p.take_profit:
                hit = "take-profit"
        else:
            if price >= p.stop_loss:
                hit = "stop-loss"
            elif price <= p.take_profit:
                hit = "take-profit"
        if hit:
            await self.close_position(hit)

    # ------------------------------------------------------------------ risk

    async def _daily_loss_ok(self) -> bool:
        """Ferma il trading se la perdita realizzata oggi supera la soglia."""
        try:
            if self.cfg.dry_run:
                pnl_today = self.paper_realized_pnl
                balance = 1000.0
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
                await self.close_position("limite perdita giornaliera")
                await self.notify(f"🛑 Trading sospeso: {self._halted_reason}. "
                                  f"Usa /run per riattivarlo.")
                self.running = False
                return False
            return True
        except AsterAPIError:
            return True  # non bloccare il trading per un errore di lettura

    # ----------------------------------------------------------- symbol info

    def _symbol_filters(self) -> dict:
        if self._filters is None:
            info = self.client.exchange_info()
            sym = next((s for s in info.get("symbols", [])
                        if s.get("symbol") == self.cfg.symbol), None)
            if not sym:
                raise AsterAPIError(400, -1121, f"simbolo {self.cfg.symbol} non trovato")
            filters = {f["filterType"]: f for f in sym.get("filters", [])}
            self._filters = {
                "stepSize": float(filters.get("LOT_SIZE", {}).get("stepSize", 0.001)),
                "minQty": float(filters.get("LOT_SIZE", {}).get("minQty", 0)),
                "tickSize": float(filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)),
                "minNotional": float(filters.get("MIN_NOTIONAL", {}).get("notional", 5)),
            }
        return self._filters

    def reset_symbol_cache(self):
        self._filters = None
        self._last_candle_acted = 0

    @staticmethod
    def _round_step(value: float, step: float) -> str:
        """Arrotonda per difetto al multiplo di step, come stringa decimale."""
        if step <= 0:
            return f"{value:.8f}".rstrip("0").rstrip(".")
        steps = math.floor(value / step + 1e-9)
        decimals = max(0, -int(math.floor(math.log10(step) + 1e-9)))
        return f"{steps * step:.{decimals}f}"
