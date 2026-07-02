"""Configurazione del bot: tutte le impostazioni arrivano da variabili
d'ambiente (o da un file .env). Vedi .env.example per la lista completa."""

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "si", "sì")


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw else default


def _symbols() -> list:
    raw = os.getenv("SYMBOLS") or os.getenv("SYMBOL") or "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,ASTERUSDT"
    return [s.strip().upper() for s in raw.replace(" ", ",").split(",") if s.strip()]


@dataclass
class Settings:
    # --- Telegram ---
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    # Solo questo utente puo' controllare il bot (0 = nessun controllo, sconsigliato)
    admin_id: int = field(default_factory=lambda: _int("TELEGRAM_ADMIN_ID", 0))

    # --- Aster DEX (API V3 "Pro API") ---
    # Crea l'API wallet su https://www.asterdex.com/en/api-wallet (tab "Pro API")
    aster_base_url: str = field(default_factory=lambda: os.getenv("ASTER_BASE_URL", "https://fapi3.asterdex.com"))
    aster_user: str = field(default_factory=lambda: os.getenv("ASTER_USER_ADDRESS", ""))
    aster_signer: str = field(default_factory=lambda: os.getenv("ASTER_SIGNER_ADDRESS", ""))
    aster_private_key: str = field(default_factory=lambda: os.getenv("ASTER_PRIVATE_KEY", ""))

    # --- Trading ---
    # Lista dei mercati seguiti (SYMBOLS separati da virgola; SYMBOL resta valido).
    # SYMBOLS=AUTO seleziona da sola i perpetual piu' scambiati nelle 24h.
    symbols: list = field(default_factory=lambda: _symbols())
    # Quanti mercati seguire con SYMBOLS=AUTO (si puo' anche scrivere AUTO:30)
    auto_symbols_count: int = field(default_factory=lambda: _int("AUTO_SYMBOLS_COUNT", 20))
    interval: str = field(default_factory=lambda: os.getenv("INTERVAL", "3m"))
    leverage: int = field(default_factory=lambda: _int("LEVERAGE", 3))
    # Numero massimo di posizioni aperte contemporaneamente
    max_open_positions: int = field(default_factory=lambda: _int("MAX_OPEN_POSITIONS", 3))
    margin_type: str = field(default_factory=lambda: os.getenv("MARGIN_TYPE", "CROSSED"))
    loop_seconds: int = field(default_factory=lambda: _int("LOOP_SECONDS", 30))

    # --- Rischio ---
    # Percentuale del saldo disponibile rischiata per singolo trade
    risk_per_trade_pct: float = field(default_factory=lambda: _float("RISK_PER_TRADE_PCT", 1.0))
    # Valore nozionale massimo della posizione in USDT
    max_position_usdt: float = field(default_factory=lambda: _float("MAX_POSITION_USDT", 100.0))
    # Stop loss / take profit in multipli di ATR
    stop_loss_atr: float = field(default_factory=lambda: _float("STOP_LOSS_ATR", 1.5))
    take_profit_atr: float = field(default_factory=lambda: _float("TAKE_PROFIT_ATR", 3.0))
    # Perdita massima giornaliera (percentuale del saldo): oltre questa soglia il bot si ferma
    max_daily_loss_pct: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_PCT", 5.0))

    # --- Strategia ---
    # ema_cross     = incrocio EMA + filtro RSI (segue il trend, pochi trade)
    # rsi_reversion = RSI ipervenduto/ipercomprato (mean reversion, molti trade)
    # breakout      = rottura del canale di Donchian (segue gli strappi)
    strategy: str = field(default_factory=lambda: os.getenv("STRATEGY", "ema_cross").strip().lower())
    ema_fast: int = field(default_factory=lambda: _int("EMA_FAST", 9))
    ema_slow: int = field(default_factory=lambda: _int("EMA_SLOW", 21))
    rsi_period: int = field(default_factory=lambda: _int("RSI_PERIOD", 14))
    rsi_long_max: float = field(default_factory=lambda: _float("RSI_LONG_MAX", 70.0))
    rsi_short_min: float = field(default_factory=lambda: _float("RSI_SHORT_MIN", 30.0))
    # soglie per la strategia rsi_reversion
    rsi_buy_below: float = field(default_factory=lambda: _float("RSI_BUY_BELOW", 30.0))
    rsi_sell_above: float = field(default_factory=lambda: _float("RSI_SELL_ABOVE", 70.0))
    # periodo del canale per la strategia breakout
    donchian_period: int = field(default_factory=lambda: _int("DONCHIAN_PERIOD", 20))
    atr_period: int = field(default_factory=lambda: _int("ATR_PERIOD", 14))

    # --- Commissioni e slippage (usati nella simulazione) ---
    # Taker fee di Aster: 0.035% per lato (apertura E chiusura)
    fee_pct: float = field(default_factory=lambda: _float("FEE_TAKER_PCT", 0.035))
    # Scivolamento di prezzo stimato per gli ordini a mercato, per lato
    slippage_pct: float = field(default_factory=lambda: _float("SLIPPAGE_PCT", 0.01))

    # --- Sicurezza ---
    # In modalita' DRY_RUN il bot simula i trade senza inviare ordini reali (default: attiva)
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))

    # --- Server health-check opzionale (per hosting tipo Render/Replit) ---
    health_port: int = field(default_factory=lambda: _int("PORT", 0))

    def validate(self) -> list[str]:
        """Ritorna la lista dei problemi di configurazione (vuota = tutto ok)."""
        problems = []
        if not self.telegram_token:
            problems.append("TELEGRAM_BOT_TOKEN mancante")
        if not self.dry_run:
            if not self.aster_signer or not self.aster_private_key:
                problems.append("ASTER_SIGNER_ADDRESS / ASTER_PRIVATE_KEY mancanti (necessari con DRY_RUN=false)")
        if self.ema_fast >= self.ema_slow:
            problems.append("EMA_FAST deve essere minore di EMA_SLOW")
        if self.strategy not in ("ema_cross", "rsi_reversion", "breakout"):
            problems.append(f"STRATEGY '{self.strategy}' non valida "
                            "(usa ema_cross, rsi_reversion o breakout)")
        return problems
