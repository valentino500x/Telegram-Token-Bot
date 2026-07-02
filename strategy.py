"""Strategie di trading (tutte simmetriche: aprono sia LONG che SHORT).

- ema_cross:     incrocio EMA veloce/lenta con filtro RSI. Segue il trend,
                 genera pochi segnali ma di qualita'.
- rsi_reversion: mean reversion sull'RSI. LONG quando l'RSI e' in ipervenduto,
                 SHORT quando e' in ipercomprato. Molti segnali (scalping).
- breakout:      rottura del canale di Donchian. LONG se il prezzo supera il
                 massimo delle ultime N candele, SHORT se buca il minimo.

Gli stop (SL/TP) sono sempre basati sull'ATR. Gli indicatori sono calcolati
in puro Python sulle candele chiuse (l'ultima, ancora in corso, e' scartata).
"""

from dataclasses import dataclass


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    direction: str | None  # "LONG", "SHORT" oppure None
    price: float
    atr: float
    ema_fast: float
    ema_slow: float
    rsi: float
    candle_time: int  # open time della candela che ha generato il segnale


def parse_klines(raw: list) -> list[Candle]:
    """Converte la risposta di GET /fapi/v3/klines, scartando la candela in corso."""
    candles = [
        Candle(open_time=int(k[0]), open=float(k[1]), high=float(k[2]),
               low=float(k[3]), close=float(k[4]), volume=float(k[5]))
        for k in raw
    ]
    return candles[:-1] if len(candles) > 1 else candles


def ema(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain, avg_loss = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles: list[Candle], period: int) -> float:
    if len(candles) <= period:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low,
                       abs(c.high - prev.close),
                       abs(c.low - prev.close)))
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def evaluate(candles: list[Candle], cfg) -> Signal | None:
    """Valuta le candele chiuse secondo la strategia scelta in cfg.strategy."""
    closes = [c.close for c in candles]
    needed = max(cfg.ema_slow + 2, cfg.rsi_period + 2,
                 cfg.donchian_period + 2, cfg.atr_period + 2)
    if len(closes) < needed:
        return None

    fast = ema(closes, cfg.ema_fast)
    slow = ema(closes, cfg.ema_slow)
    n = min(len(fast), len(slow))  # allinea le due serie sulla coda
    if n < 2:
        return None
    fast, slow = fast[-n:], slow[-n:]

    cur_rsi = rsi(closes[-(cfg.rsi_period * 5):], cfg.rsi_period)
    cur_atr = atr(candles[-(cfg.atr_period * 5):], cfg.atr_period)

    direction = None
    if cfg.strategy == "ema_cross":
        crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
        crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
        if crossed_up and cur_rsi < cfg.rsi_long_max:
            direction = "LONG"
        elif crossed_down and cur_rsi > cfg.rsi_short_min:
            direction = "SHORT"
    elif cfg.strategy == "rsi_reversion":
        if cur_rsi <= cfg.rsi_buy_below:
            direction = "LONG"
        elif cur_rsi >= cfg.rsi_sell_above:
            direction = "SHORT"
    elif cfg.strategy == "breakout":
        window = candles[-(cfg.donchian_period + 1):-1]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        if closes[-1] > highest:
            direction = "LONG"
        elif closes[-1] < lowest:
            direction = "SHORT"

    return Signal(direction=direction, price=closes[-1], atr=cur_atr,
                  ema_fast=fast[-1], ema_slow=slow[-1], rsi=cur_rsi,
                  candle_time=candles[-1].open_time)
