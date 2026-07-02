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
        f"Simbolo: `{cfg.symbol}` | Timeframe: `{cfg.interval}` | Leva: `{cfg.leverage}x`\n"
        f"Strategia: EMA {cfg.ema_fast}/{cfg.ema_slow} + RSI {cfg.rsi_period}, "
        f"SL {cfg.stop_loss_atr}×ATR, TP {cfg.take_profit_atr}×ATR\n\n"
        "Usa /run per avviare il trading automatico e /help per tutti i comandi.",
        parse_mode=ParseMode.MARKDOWN)


@admin_only
async def cmd_help(update: Update, _):
    await update.message.reply_text(
        "/run — avvia il trading automatico\n"
        "/stop — ferma il trading automatico\n"
        "/status — stato e ultimo segnale\n"
        "/position — posizione aperta\n"
        "/close — chiude posizione e ordini\n"
        "/balance — saldo futures\n"
        "/price — prezzo corrente\n"
        "/pnl — PnL realizzato oggi\n"
        "/symbol SIMBOLO — cambia simbolo\n"
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
    sig = engine.last_signal
    lines = [
        f"🤖 Trading: {'🟢 ATTIVO' if engine.running else '🔴 FERMO'}",
        f"Modalita': {'🧪 simulazione' if cfg.dry_run else '💸 ordini reali'}",
        f"Simbolo: {cfg.symbol} | TF: {cfg.interval} | Leva: {cfg.leverage}x",
        f"Rischio: {cfg.risk_per_trade_pct}%/trade | Max posizione: {cfg.max_position_usdt} USDT",
    ]
    if sig:
        lines.append(
            f"\nUltimo check — prezzo {sig.price:.6g}, EMA{cfg.ema_fast} {sig.ema_fast:.6g}, "
            f"EMA{cfg.ema_slow} {sig.ema_slow:.6g}, RSI {sig.rsi:.1f}, ATR {sig.atr:.6g}")
    pos = await engine.current_position()
    if pos:
        lines.append(f"\n📌 Posizione: {pos['direction']} {pos['qty']:.6g} "
                     f"@ {pos['entry']:.6g} (PnL {pos['pnl']:+.2f} USDT)")
    else:
        lines.append("\n📌 Nessuna posizione aperta")
    await update.message.reply_text("\n".join(lines))


@admin_only
async def cmd_balance(update: Update, _):
    if cfg.dry_run:
        bal = await engine.available_balance()
        await update.message.reply_text(
            f"🧪 Saldo simulato: {bal:.2f} USDT "
            f"(PnL simulato totale: {engine.paper_realized_pnl:+.2f} USDT)")
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
    pos = await engine.current_position()
    if not pos:
        await update.message.reply_text("Nessuna posizione aperta.")
        return
    await update.message.reply_text(
        f"📌 {pos['direction']} {pos['qty']:.6g} {cfg.symbol}\n"
        f"Entrata: {pos['entry']:.6g}\nPnL: {pos['pnl']:+.2f} USDT")


@admin_only
async def cmd_close(update: Update, _):
    closed = await engine.close_position("comando /close")
    if not closed:
        await update.message.reply_text("Nessuna posizione da chiudere.")


@admin_only
async def cmd_price(update: Update, _):
    price = await asyncio.to_thread(client.ticker_price, cfg.symbol)
    await update.message.reply_text(f"💵 {cfg.symbol}: {price:.6g} USDT")


@admin_only
async def cmd_pnl(update: Update, _):
    if cfg.dry_run:
        await update.message.reply_text(
            f"🧪 PnL simulato totale: {engine.paper_realized_pnl:+.2f} USDT")
        return
    midnight = int(time.time() // 86400 * 86400 * 1000)
    rows = await asyncio.to_thread(client.income, None, midnight)
    relevant = {"REALIZED_PNL", "COMMISSION", "FUNDING_FEE"}
    total = sum(float(r.get("income", 0) or 0) for r in rows
                if r.get("incomeType") in relevant)
    await update.message.reply_text(f"📈 PnL realizzato oggi (UTC): {total:+.2f} USDT")


@admin_only
async def cmd_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(f"Simbolo attuale: {cfg.symbol}\nUso: /symbol ETHUSDT")
        return
    if engine.running or await engine.current_position():
        await update.message.reply_text(
            "Ferma il trading (/stop) e chiudi la posizione (/close) prima di cambiare simbolo.")
        return
    cfg.symbol = context.args[0].upper()
    engine.reset_symbol_cache()
    await update.message.reply_text(f"✅ Simbolo impostato su {cfg.symbol}")


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
        import asyncio
        await asyncio.to_thread(client.set_leverage, cfg.symbol, lev)
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
        if cfg.admin_id:
            try:
                await app.bot.send_message(chat_id=cfg.admin_id, text=text)
            except Exception:
                log.exception("invio notifica fallito")
        else:
            log.info("NOTIFICA: %s", text)

    global engine
    engine = TradingEngine(cfg, client, notify)

    for name, handler in [
        ("start", cmd_start), ("help", cmd_help), ("run", cmd_run),
        ("stop", cmd_stop), ("status", cmd_status), ("balance", cmd_balance),
        ("position", cmd_position), ("close", cmd_close), ("price", cmd_price),
        ("pnl", cmd_pnl), ("symbol", cmd_symbol), ("leverage", cmd_leverage),
        ("risk", cmd_risk),
    ]:
        app.add_handler(CommandHandler(name, handler))

    mode = "SIMULAZIONE (dry-run)" if cfg.dry_run else "ORDINI REALI"
    log.info("Bot avviato — %s su %s %s", mode, cfg.symbol, cfg.interval)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
