"""Strategia di trading: incrocio di EMA con filtro RSI e ATR per gli stop.

- Segnale LONG:  la EMA veloce incrocia al rialzo la EMA lenta e l'RSI non e' in ipercomprato.
- Segnale SHORT: la EMA veloce incrocia al ribasso la EMA lenta e l'RSI non e' in ipervenduto.

Gli indicatori sono calcolati in puro Python sulle candele chiuse
(l'ultima candela, ancora in corso, viene scartata).
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


def evaluate(candles: list[Candle], ema_fast_p: int, ema_slow_p: int,
             rsi_p: int, rsi_long_max: float, rsi_short_min: float,
             atr_p: int) -> Signal | None:
    """Valuta le candele chiuse e ritorna lo stato corrente della strategia."""
    closes = [c.close for c in candles]
    if len(closes) < ema_slow_p + 2:
        return None

    fast = ema(closes, ema_fast_p)
    slow = ema(closes, ema_slow_p)
    # allinea le due serie sulla coda
    n = min(len(fast), len(slow))
    if n < 2:
        return None
    fast, slow = fast[-n:], slow[-n:]

    cur_rsi = rsi(closes[-(rsi_p * 5):], rsi_p)
    cur_atr = atr(candles[-(atr_p * 5):], atr_p)

    direction = None
    crossed_up = fast[-2] <= slow[-2] and fast[-1] > slow[-1]
    crossed_down = fast[-2] >= slow[-2] and fast[-1] < slow[-1]
    if crossed_up and cur_rsi < rsi_long_max:
        direction = "LONG"
    elif crossed_down and cur_rsi > rsi_short_min:
        direction = "SHORT"

    return Signal(direction=direction, price=closes[-1], atr=cur_atr,
                  ema_fast=fast[-1], ema_slow=slow[-1], rsi=cur_rsi,
                  candle_time=candles[-1].open_time)
