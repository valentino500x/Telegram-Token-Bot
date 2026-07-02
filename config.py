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
    symbol: str = field(default_factory=lambda: os.getenv("SYMBOL", "BTCUSDT"))
    interval: str = field(default_factory=lambda: os.getenv("INTERVAL", "5m"))
    leverage: int = field(default_factory=lambda: _int("LEVERAGE", 3))
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

    # --- Strategia (EMA crossover + filtro RSI) ---
    ema_fast: int = field(default_factory=lambda: _int("EMA_FAST", 9))
    ema_slow: int = field(default_factory=lambda: _int("EMA_SLOW", 21))
    rsi_period: int = field(default_factory=lambda: _int("RSI_PERIOD", 14))
    rsi_long_max: float = field(default_factory=lambda: _float("RSI_LONG_MAX", 70.0))
    rsi_short_min: float = field(default_factory=lambda: _float("RSI_SHORT_MIN", 30.0))
    atr_period: int = field(default_factory=lambda: _int("ATR_PERIOD", 14))

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
        return problems
