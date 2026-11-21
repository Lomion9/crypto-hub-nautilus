"""BTC arşivinde örtüşmeyen pencereler. Canlı haritaya ve LEVERAGE tablosuna yazmaz."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from desktop.liquidity import build_map
from desktop.market import HOUR_MS, Bar, Point

MARGIN = 0.004
MAX_LEVERAGE = 100
BOOTSTRAP = 1000
NEAR_ATR = 0.5
LARGE_SHARE = 0.30
PRESETS = {
    "mevcut": {5: 3, 10: 3, 20: 1, 25: 2, 50: 2, 100: 1},
    "esit": {5: 1, 10: 1, 20: 1, 25: 1, 50: 1, 100: 1},
    "dusuk": {5: 4, 10: 4, 20: 1, 25: 1, 50: 1, 100: 1},
    "yuksek": {5: 1, 10: 1, 20: 1, 25: 2, 50: 4, 100: 4},
}


def _tape():
    path = Path.home() / "AppData" / "Roaming" / "CryptoHub" / "liq_tape.sqlite"
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    bars = []
    for row in con.execute(
        "SELECT saat_ms, o, h, l, c, hacim, taker_alis FROM mum_saat WHERE sembol='BTCUSDT' ORDER BY saat_ms"
    ):
        stamp, opened, high, low, close, volume, taker = row
        if min(opened, high, low, close) <= 0:
            continue
        bars.append(Bar(int(stamp), opened, high, low, close, 2 * taker - volume, volume))
    points = [
        Point(int(stamp), float(value))
        for stamp, value in con.execute(
            "SELECT saat_ms, oi FROM oi_saat WHERE sembol='BTCUSDT' AND oi > 0 ORDER BY saat_ms"
        )
    ]
    prints = [
        (int(stamp), str(side), float(price))
        for stamp, side, price in con.execute(
            "SELECT t_ms, yon, fiyat FROM liq WHERE sembol='BTCUSDT' AND fiyat > 0 ORDER BY t_ms"
        )
    ]
    con.close()
    return bars, points, prints


def _atr(bars: list[Bar]) -> float | None:
    if len(bars) < 2:
        return None
    ranges: list[float] = []
    previous = bars[0].close
    for bar in bars[1:]:
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
        previous = bar.close
    sample = ranges[-24:]
    if not sample:
        return None
    return sum(sample) / len(sample)


def _peak(prices: list[float], step: float) -> float | None:
    if not prices or step <= 0:
        return None
    bins: dict[int, int] = {}
    for price in prices:
        index = int(price / step)
        bins[index] = bins.get(index, 0) + 1
    best = max(bins, key=bins.get)
    return (best + 0.5) * step


def _nearest(price: float, clusters) -> float | None:
    if not clusters:
        return None
    return min(abs(cluster.price - price) for cluster in clusters)


def _entered(price: float, bars: list[Bar]) -> bool:
    return any(bar.low <= price <= bar.high for bar in bars)


def _near(price: float, prints: list[float], radius: float) -> int:
    return sum(1 for print_price in prints if abs(print_price - price) <= radius)


def _side_metrics(clusters, prints: list[float], future: list[Bar], step: float, atr: float, price: float) -> dict:
    found: dict[str, float] = {}
    peak = _peak(prints, step)
    if peak is not None and clusters:
        distance = _nearest(peak, clusters)
        if distance is not None and price > 0:
            found["konum_yuzde"] = distance / price * 100
            if atr > 0:
                found["konum_atr"] = distance / atr
    total = sum(cluster.usd for cluster in clusters)
    if total <= 0 or not clusters:
        return found
    radius = max(step, NEAR_ATR * atr) if atr > 0 else step
    largest = max(cluster.usd for cluster in clusters)
    entered = [cluster for cluster in clusters if _entered(cluster.price, future)]
    if entered and prints:
        ratios = []
        for cluster in entered:
            predicted = cluster.usd / total
            if predicted <= 0:
                continue
            realized = _near(cluster.price, prints, radius) / len(prints)
            ratios.append(realized / predicted)
        if ratios:
            found["isabet"] = sum(ratios) / len(ratios)
    large = [cluster for cluster in clusters if cluster.usd >= LARGE_SHARE * largest]
    if large:
        empty = sum(
            1
            for cluster in large
            if _entered(cluster.price, future) and _near(cluster.price, prints, radius) == 0
        )
        found["bos"] = empty / len(large)
    return found


def _random_clusters(count: int, low: float, high: float, seed: int):
    from desktop.liquidity import Cluster

    rng = random.Random(seed)
    span = high - low
    if count < 1 or span <= 0:
        return []
    return [Cluster(rng.uniform(low, high), 1.0) for _ in range(count)]


def _windows(prints, horizon: int) -> list[int]:
    if not prints:
        return []
    first = prints[0][0]
    last = prints[-1][0]
    start = first // HOUR_MS * HOUR_MS
    stops = []
    while start + horizon * HOUR_MS <= last:
        stops.append(start)
        start += horizon * HOUR_MS
    return stops


def _history(bars, points, stamp: int):
    past_bars = [bar for bar in bars if bar.time_ms <= stamp]
    past_points = [point for point in points if point.time_ms <= stamp]
    return past_bars, past_points


def _run(bars, points, prints, horizon: int) -> dict:
    stops = _windows(prints, horizon)
    names = list(PRESETS) + ["rastgele"]
    bag: dict[str, dict[str, list[float]]] = {
        name: {"konum_yuzde": [], "konum_atr": [], "isabet": [], "bos": []} for name in names
    }
    sides = ("long", "short")
    store = {side: {name: {key: [] for key in bag["mevcut"]} for name in names} for side in sides}
    for stamp in stops:
        past_bars, past_points = _history(bars, points, stamp)
        if len(past_bars) < 48 or len(past_points) < 24:
            continue
        future_end = stamp + horizon * HOUR_MS
        future = [bar for bar in bars if stamp < bar.time_ms <= future_end]
        atr = _atr(past_bars)
        if atr is None or atr <= 0 or not future:
            continue
        price = past_bars[-1].close
        low = min(bar.low for bar in past_bars[-48:])
        high = max(bar.high for bar in past_bars[-48:])
        forward = {
            "long": [print_price for print_stamp, side, print_price in prints if stamp < print_stamp <= future_end and side == "long"],
            "short": [print_price for print_stamp, side, print_price in prints if stamp < print_stamp <= future_end and side == "short"],
        }
        maps = {
            name: build_map(
                "BTCUSDT",
                past_bars,
                past_points,
                MARGIN,
                MAX_LEVERAGE,
                as_of=stamp,
                weights=weights,
            )
            for name, weights in PRESETS.items()
        }
        for side in sides:
            for name, book in maps.items():
                clusters = book.long_peaks if side == "long" else book.short_peaks
                metrics = _side_metrics(clusters, forward[side], future, book.step, atr, price)
                for key, value in metrics.items():
                    store[side][name][key].append(value)
            count = len(maps["mevcut"].long_peaks if side == "long" else maps["mevcut"].short_peaks)
            random_book = _random_clusters(count, low, high, stamp + (1 if side == "short" else 0))
            metrics = _side_metrics(random_book, forward[side], future, maps["mevcut"].step, atr, price)
            for key, value in metrics.items():
                store[side]["rastgele"][key].append(value)
    return store


def _interval(values: list[float]) -> tuple[int, float, float, float] | None:
    count = len(values)
    if count == 0:
        return None
    rng = random.Random(7)
    means = []
    for _ in range(BOOTSTRAP):
        draw = [values[rng.randrange(count)] for _ in range(count)]
        means.append(sum(draw) / count)
    means.sort()
    low = means[int(0.025 * (BOOTSTRAP - 1))]
    high = means[int(0.975 * (BOOTSTRAP - 1))]
    return count, sum(values) / count, low, high


def _print_table(horizon: int, store: dict) -> None:
    print(f"\nufuk {horizon} saat, adım {horizon} saat")
    print(f"marjin {MARGIN}  tavan {MAX_LEVERAGE}  yakınlık max(adım, {NEAR_ATR} ATR)")
    counts = [len(store[side]["mevcut"]["konum_yuzde"]) for side in ("long", "short")]
    small = max(counts) < 30
    if small:
        print(f"örnek sayısı yetersiz (long {counts[0]}, short {counts[1]}). Sonuçtan hüküm çıkarılmaz.")
    for side in ("long", "short"):
        print(f"  {side}")
        print(f"  {'preset':<10} {'metrik':<12} {'n':>4} {'ortalama':>10} {'alt':>10} {'üst':>10}")
        for name in list(PRESETS) + ["rastgele"]:
            for key in ("konum_yuzde", "konum_atr", "isabet", "bos"):
                row = _interval(store[side][name][key])
                if row is None:
                    print(f"  {name:<10} {key:<12} {'0':>4} {'—':>10} {'—':>10} {'—':>10}")
                    continue
                count, mean, low, high = row
                print(f"  {name:<10} {key:<12} {count:4d} {mean:10.3f} {low:10.3f} {high:10.3f}")
    if small:
        return
    print("  preset farkı, mevcut tabanın aralığına göre")
    for side in ("long", "short"):
        base = _interval(store[side]["mevcut"]["konum_yuzde"])
        if base is None:
            continue
        _, _, low, high = base
        for name in ("esit", "dusuk", "yuksek"):
            other = _interval(store[side][name]["konum_yuzde"])
            if other is None:
                continue
            inside = low <= other[1] <= high
            print(f"  {side} {name} konum ortalaması aralığın {'içinde' if inside else 'dışında'}")


def main() -> None:
    bars, points, prints = _tape()
    if not prints or not bars:
        print("arşiv boş")
        return
    span = (prints[-1][0] - prints[0][0]) / HOUR_MS
    print(f"iz {len(prints)}  mum {len(bars)}  oi {len(points)}  iz aralığı {span:.1f} saat")
    for horizon in (12, 24):
        _print_table(horizon, _run(bars, points, prints, horizon))


if __name__ == "__main__":
    main()
