"""Açık mum aralığında Kaufman verimi ve Bollinger bant sırası.

Pandas kullanılmaz. Eşikler son 180 değerin kendi dilimidir.
"""

from __future__ import annotations

N = 20
WINDOW = 180
NEED = N + WINDOW


def read(closes: list[float]) -> dict | None:
    """Son mumun rejimi. 200 kapanış yoksa None."""
    if len(closes) < NEED:
        return None
    series = [float(price) for price in closes]
    if any(price <= 0 for price in series):
        return None
    er_values = _efficiency(series)
    bw_values = _bandwidth(series)
    end = len(series) - 1
    start = end - WINDOW + 1
    ers = er_values[start : end + 1]
    if any(value is None for value in ers):
        return None
    er = ers[-1]
    if er is None:
        return None
    high = _quantile(ers, 0.70)
    low = _quantile(ers, 0.30)
    if er > high:
        name = "trend"
    elif er < low:
        name = "yatay"
    else:
        name = "gecis"
    rank = _rank(bw_values[start : end + 1])
    return {
        "er": er,
        "bw_rank": rank,
        "name": name,
        "squeeze": rank is not None and rank < 0.10,
    }


def _efficiency(closes: list[float]) -> list[float | None]:
    values: list[float | None] = [None] * len(closes)
    for index in range(N, len(closes)):
        net = abs(closes[index] - closes[index - N])
        path = 0.0
        for step in range(index - N + 1, index + 1):
            path += abs(closes[step] - closes[step - 1])
        values[index] = None if path == 0 else net / path
    return values


def _bandwidth(closes: list[float]) -> list[float | None]:
    values: list[float | None] = [None] * len(closes)
    for index in range(N - 1, len(closes)):
        sample = closes[index - N + 1 : index + 1]
        mean = sum(sample) / N
        if mean == 0:
            continue
        var = sum((price - mean) ** 2 for price in sample) / (N - 1)
        values[index] = (4 * var ** 0.5) / mean
    return values


def _rank(window: list[float | None]) -> float | None:
    """Pencerenin son değeri, öncekilerin kaçını geçti."""
    if not window or any(value is None for value in window):
        return None
    last = window[-1]
    earlier = window[:-1]
    if last is None or not earlier:
        return None
    return sum(1 for value in earlier if value is not None and value < last) / len(earlier)


def _quantile(values: list[float | None], portion: float) -> float:
    """Pandas kayan dilimi. Ara değer iki komşunun arasından okunur."""
    ordered = sorted(float(value) for value in values if value is not None)
    if len(ordered) == 1:
        return ordered[0]
    pos = portion * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    weight = pos - low
    return ordered[low] * (1 - weight) + ordered[high] * weight
