"""Bollinger Bands, ported from the TradingView Pine v6 script.

ta.stdev uses the population (biased) estimator. EMA seeds with the first
source value. SMMA/RMA seeds with the first full SMA. WMA gives the newest
bar the highest weight. VWMA is sma(source * volume) / sma(volume).
"""

from __future__ import annotations

import math

MA_TYPES = ("SMA", "EMA", "SMMA (RMA)", "WMA", "VWMA")
SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")


def source_value(bar, name: str) -> float:
    if name == "open":
        return bar.open
    if name == "high":
        return bar.high
    if name == "low":
        return bar.low
    if name == "hl2":
        return (bar.high + bar.low) / 2
    if name == "hlc3":
        return (bar.high + bar.low + bar.close) / 3
    if name == "ohlc4":
        return (bar.open + bar.high + bar.low + bar.close) / 4
    return bar.close


def _stdev(values: list[float]) -> float:
    count = len(values)
    if count == 0:
        return 0.0
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    return math.sqrt(variance)


def _basis(src: list[float], volume: list[float], length: int, ma_type: str) -> list[float | None]:
    count = len(src)
    basis: list[float | None] = [None] * count
    if length < 1 or count == 0:
        return basis
    if ma_type == "EMA":
        alpha = 2 / (length + 1)
        previous = src[0]
        basis[0] = previous
        for index in range(1, count):
            previous = alpha * src[index] + (1 - alpha) * previous
            basis[index] = previous
        return basis
    if ma_type == "SMMA (RMA)":
        alpha = 1 / length
        previous = None
        for index in range(count):
            if index < length - 1:
                continue
            if previous is None:
                previous = sum(src[index - length + 1 : index + 1]) / length
            else:
                previous = alpha * src[index] + (1 - alpha) * previous
            basis[index] = previous
        return basis
    if ma_type == "WMA":
        weight = length * (length + 1) / 2
        for index in range(length - 1, count):
            window = src[index - length + 1 : index + 1]
            total = sum(value * (step + 1) for step, value in enumerate(window))
            basis[index] = total / weight
        return basis
    if ma_type == "VWMA":
        for index in range(length - 1, count):
            start = index - length + 1
            traded = sum(volume[start : index + 1])
            if traded <= 0:
                continue
            weighted = sum(src[cursor] * volume[cursor] for cursor in range(start, index + 1))
            basis[index] = weighted / traded
        return basis
    for index in range(length - 1, count):
        basis[index] = sum(src[index - length + 1 : index + 1]) / length
    return basis


def bollinger(
    bars,
    length: int = 20,
    ma_type: str = "SMA",
    source: str = "close",
    mult: float = 2.0,
    offset: int = 0,
    bar_ms: int = 0,
) -> list[tuple[float, float | None, float | None, float | None]]:
    """Return (time, basis, upper, lower). Upper and lower stay empty until a full window exists."""
    if length < 1 or not bars:
        return []
    if ma_type not in MA_TYPES:
        ma_type = "SMA"
    if source not in SOURCES:
        source = "close"
    src = [source_value(bar, source) for bar in bars]
    volume = [float(getattr(bar, "volume", 0.0) or 0.0) for bar in bars]
    basis = _basis(src, volume, length, ma_type)
    shift = offset * bar_ms
    points: list[tuple[float, float | None, float | None, float | None]] = []
    for index, bar in enumerate(bars):
        base = basis[index]
        if base is None:
            continue
        upper = None
        lower = None
        if index >= length - 1:
            deviation = mult * _stdev(src[index - length + 1 : index + 1])
            upper = base + deviation
            lower = base - deviation
        points.append((float(bar.time_ms + shift), base, upper, lower))
    return points


def bollinger_last(
    bars,
    points: list[tuple[float, float | None, float | None, float | None]],
    length: int = 20,
    ma_type: str = "SMA",
    source: str = "close",
    mult: float = 2.0,
    offset: int = 0,
    bar_ms: int = 0,
) -> list[tuple[float, float | None, float | None, float | None]]:
    """Yalnız son mum değiştiyse son noktayı yeniden hesaplar; sonuç bollinger() ile aynıdır."""
    if ma_type not in MA_TYPES:
        ma_type = "SMA"
    if source not in SOURCES:
        source = "close"
    shift = offset * bar_ms
    count = len(bars)
    full = lambda: bollinger(bars, length, ma_type, source, mult, offset, bar_ms)
    if length < 1 or count < length + 1 or len(points) < 2:
        return full()
    if points[-1][0] != float(bars[-1].time_ms + shift) or points[-2][0] != float(bars[-2].time_ms + shift):
        return full()
    window = [source_value(bar, source) for bar in bars[-length:]]
    if ma_type in ("EMA", "SMMA (RMA)"):
        previous = points[-2][1]
        if previous is None:
            return full()
        alpha = 2 / (length + 1) if ma_type == "EMA" else 1 / length
        base = alpha * window[-1] + (1 - alpha) * previous
    elif ma_type == "WMA":
        weight = length * (length + 1) / 2
        base = sum(value * (step + 1) for step, value in enumerate(window)) / weight
    elif ma_type == "VWMA":
        volume = [float(getattr(bar, "volume", 0.0) or 0.0) for bar in bars[-length:]]
        traded = sum(volume)
        if traded <= 0:
            return full()
        base = sum(value * amount for value, amount in zip(window, volume)) / traded
    else:
        base = sum(window) / length
    deviation = mult * _stdev(window)
    return points[:-1] + [(float(bars[-1].time_ms + shift), base, base + deviation, base - deviation)]
