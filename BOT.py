"""Bot Telegram per il trading automatico sui perpetual di Aster DEX.

Comandi principali:
  /run      avvia il trading automatico
  /stop     ferma il trading automatico
  /status   stato del bot e ultimo segnale
  /position posizione aperta
  /close    chiude la posizione e cancella gli ordini
  /balance  saldo del conto futures
  /price    prezzo corrente del simbolo
  /pnl      PnL realizzato di oggi
  /symbol   cambia simbolo (es. /symbol ETHUSDT)
  /leverage cambia leva (es. /leverage 5)
  /risk     cambia rischio per trade in % (es. /risk 0.5)

Avvio:  python BOT.py   (configurazione via variabili d'ambiente, vedi .env.example)
"""

import asyncio
import functools
import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

from aster_client import AsterClient, AsterAPIError
from config import Settings
from engine import TradingEngine
from server import start_health_server

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

cfg = Settings()
client = AsterClient(cfg.aster_base_url, cfg.aster_user,
                     cfg.aster_signer, cfg.aster_private_key)
engine: TradingEngine | None = None  # creato in main() quando esiste l'event loop


def admin_only(handler):
    """Consente i comandi solo all'utente TELEGRAM_ADMIN_ID."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if cfg.admin_id and (not user or user.id != cfg.admin_id):
            await update.message.reply_text("⛔ Non sei autorizzato a usare questo bot.")
            return
        try:
            await handler(update, context)
        except AsterAPIError as e:
            await update.message.reply_text(f"⚠️ Errore API Aster: {e.msg} (codice {e.code})")
        except Exception as e:  # noqa: BLE001 - il bot non deve mai morire per un comando
            log.exception("errore nel comando")
            await update.message.reply_text(f"⚠️ Errore: {e}")
    return wrapper


@admin_only
async def cmd_start(update: Update, _):
    mode = "🧪 SIMULAZIONE (dry-run)" if cfg.dry_run else "💸 ORDINI REALI"
    await update.message.reply_text(
        "👋 *Aster DEX Perpetual Trading Bot*\n\n"
        f"Modalita': {mode}\n"
        f"Mercati: `{', '.join(cfg.symbols)}`\n"
        f"Timeframe: `{cfg.interval}` | Leva: `{cfg.leverage}x` | "
        f"Max posizioni: `{cfg.max_open_positions}`\n"
        f"Strategia: `{cfg.strategy}` | SL {cfg.stop_loss_atr}×ATR, TP {cfg.take_profit_atr}×ATR\n"
        f"Commissioni simulate: {cfg.fee_pct}%/lato + slippage {cfg.slippage_pct}%\n\n"
        "Usa /run per avviare il trading automatico e /help per tutti i comandi.",
        parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_help(update: Update, _):
    await update.message.reply_text(
        "/run — avvia il trading automatico\n"
        "/stop — ferma il trading automatico\n"
        "/status — stato e ultimo segnale\n"
        "/position — posizioni aperte\n"
        "/close — chiude TUTTE le posizioni (o /close ETHUSDT per una sola)\n"
        "/balance — saldo futures\n"
        "/price — prezzi correnti\n"
        "/pnl — PnL realizzato oggi\n"
        "/symbols BTCUSDT,ETHUSDT — imposta i mercati seguiti\n"
        "/leverage N — cambia leva\n"
        "/risk PCT — rischio per trade in %")


@admin_only
async def cmd_run(update: Update, _):
    if engine.running:
        await update.message.reply_text("Il trading è già attivo. Usa /status.")
        return
    await engine.start()
    await update.message.reply_text("▶️ Trading automatico avviato.")


@admin_only
async def cmd_stop(update: Update, _):
    if not engine.running:
        await update.message.reply_text("Il trading è già fermo.")
        return
    await engine.stop()
    await update.message.reply_text(
        "⏸ Trading automatico fermato. La posizione aperta (se presente) "
        "resta attiva con i suoi SL/TP: usa /close per chiuderla.")


@admin_only
async def cmd_status(update: Update, _):
    lines = [
        f"🤖 Trading: {'🟢 ATTIVO' if engine.running else '🔴 FERMO'}",
        f"Modalita': {'🧪 simulazione' if cfg.dry_run else '💸 ordini reali'} | Strategia: {cfg.strategy}",
        f"TF: {cfg.interval} | Leva: {cfg.leverage}x | Rischio: {cfg.risk_per_trade_pct}%/trade",
        f"Max posizioni: {cfg.max_open_positions} | Max nozionale: {cfg.max_position_usdt} USDT",
        "",
    ]
    for symbol in cfg.symbols:
        sig = engine.states[symbol].last_signal if symbol in engine.states else None
        if sig:
            trend = "📈" if sig.ema_fast > sig.ema_slow else "📉"
            lines.append(f"{trend} {symbol}: {sig.price:.6g} | RSI {sig.rsi:.1f}")
        else:
            lines.append(f"⏳ {symbol}: in attesa dei primi dati")
    positions = await engine.all_positions()
    if positions:
        lines.append("")
        for pos in positions:
            lines.append(f"📌 {pos['symbol']}: {pos['direction']} {pos['qty']:.6g} "
                         f"@ {pos['entry']:.6g} (PnL {pos['pnl']:+.2f} USDT)")
    else:
        lines.append("\n📌 Nessuna posizione aperta")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_balance(update: Update, _):
    if cfg.dry_run:
        bal = await engine.available_balance()
        await update.message.reply_text(
            f"🧪 Saldo simulato: {bal:.2f} USDT\n"
            f"PnL netto totale: {engine.paper_realized_pnl:+.2f} USDT "
            f"su {engine.paper_trades_closed} trade chiusi\n"
            f"di cui commissioni+slippage pagati: {engine.paper_fees_total:.2f} USDT")
        return
    rows = await asyncio.to_thread(client.balance)
    lines = ["💰 Saldo futures:"]
    for row in rows if isinstance(rows, list) else [rows]:
        total = float(row.get("balance", 0) or 0)
        if total:
            lines.append(f"• {row.get('asset')}: {total:.4f} "
                         f"(disponibile {float(row.get('availableBalance', 0) or 0):.4f})")
    await update.message.reply_text("\n".join(lines) if len(lines) > 1 else "Saldo vuoto.")


@admin_only
async def cmd_position(update: Update, _):
    positions = await engine.all_positions()
    if not positions:
        await update.message.reply_text("Nessuna posizione aperta.")
        return
    lines = []
    for pos in positions:
        lines.append(f"📌 {pos['symbol']}: {pos['direction']} {pos['qty']:.6g}\n"
                     f"   Entrata: {pos['entry']:.6g} | PnL: {pos['pnl']:+.2f} USDT")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        symbol = context.args[0].upper()
        closed = await engine.close_position(symbol, "comando /close")
        if not closed:
            await update.message.reply_text(f"Nessuna posizione aperta su {symbol}.")
        return
    closed = await engine.close_all_positions("comando /close")
    if not closed:
        await update.message.reply_text("Nessuna posizione da chiudere.")


@admin_only
async def cmd_price(update: Update, _):
    lines = []
    for symbol in cfg.symbols:
        price = await asyncio.to_thread(client.ticker_price, symbol)
        lines.append(f"💵 {symbol}: {price:.6g} USDT")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_pnl(update: Update, _):
    if cfg.dry_run:
        await update.message.reply_text(
            f"🧪 PnL netto simulato: {engine.paper_realized_pnl:+.2f} USDT "
            f"({engine.paper_trades_closed} trade chiusi)\n"
            f"Commissioni simulate pagate: {engine.paper_fees_total:.2f} USDT")
        return
    midnight = int(time.time() // 86400 * 86400 * 1000)
    rows = await asyncio.to_thread(client.income, None, midnight)
    relevant = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
    total = sum(float(r.get("income", 0) or 0) for r in rows
                if r.get("incomeType") in relevant)
    await update.message.reply_text(f"📈 PnL realizzato oggi (UTC): {total:+.2f} USDT")


@admin_only
async def cmd_symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"Mercati attuali: {', '.join(cfg.symbols)}\n"
            "Uso: /symbols BTCUSDT,ETHUSDT,SOLUSDT")
        return
    if engine.running or await engine.all_positions():
        await update.message.reply_text(
            "Ferma il trading (/stop) e chiudi le posizioni (/close) prima di cambiare i mercati.")
        return
    raw = " ".join(context.args).replace(" ", ",")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    if not symbols:
        await update.message.reply_text("Nessun simbolo valido.")
        return
    cfg.symbols = symbols
    engine.reset_symbol_cache()
    await update.message.reply_text(f"✅ Mercati impostati: {', '.join(cfg.symbols)}")


@admin_only
async def cmd_leverage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"Leva attuale: {cfg.leverage}x\nUso: /leverage 5")
        return
    lev = int(context.args[0])
    if not 1 <= lev <= 50:
        await update.message.reply_text("La leva deve essere tra 1 e 50.")
        return
    cfg.leverage = lev
    if not cfg.dry_run:
        for symbol in cfg.symbols:
            await asyncio.to_thread(client.set_leverage, symbol, lev)
    await update.message.reply_text(f"✅ Leva impostata a {lev}x")


@admin_only
async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            f"Rischio attuale: {cfg.risk_per_trade_pct}% per trade\nUso: /risk 0.5")
        return
    pct = float(context.args[0].replace(",", "."))
    if not 0.1 <= pct <= 10:
        await update.message.reply_text("Il rischio deve essere tra 0.1% e 10%.")
        return
    cfg.risk_per_trade_pct = pct
    await update.message.reply_text(f"✅ Rischio impostato a {pct}% per trade")


def main():
    problems = cfg.validate()
    if problems:
        for p in problems:
            log.error("Configurazione: %s", p)
        raise SystemExit("Configurazione incompleta, vedi .env.example")

    if cfg.health_port:
        start_health_server(cfg.health_port)

    app = Application.builder().token(cfg.telegram_token).build()

    async def notify(text: str):
        if not cfg.admin_id:
            log.info("NOTIFICA: %s", text)
            return
        for attempt in range(3):
            try:
                await app.bot.send_message(chat_id=cfg.admin_id, text=text)
                return
            except TelegramError as e:
                if attempt == 2:
                    log.warning("Notifica Telegram persa dopo 3 tentativi (%s): %.60s",
                                e, text)
                else:
                    await asyncio.sleep(2 * (attempt + 1))
            except Exception:
                log.exception("invio notifica fallito")
                return

    global engine
    engine = TradingEngine(cfg, client, notify)

    for name, handler in [
        ("start", cmd_start), ("help", cmd_help), ("run", cmd_run),
        ("stop", cmd_stop), ("status", cmd_status), ("balance", cmd_balance),
        ("position", cmd_position), ("close", cmd_close), ("price", cmd_price),
        ("pnl", cmd_pnl), ("symbols", cmd_symbols), ("symbol", cmd_symbols),
        ("leverage", cmd_leverage), ("risk", cmd_risk),
    ]:
        app.add_handler(CommandHandler(name, handler))

    mode = "SIMULAZIONE (dry-run)" if cfg.dry_run else "ORDINI REALI"
    log.info("Bot avviato — %s su %s %s", mode, ",".join(cfg.symbols), cfg.interval)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
