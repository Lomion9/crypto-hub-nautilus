"""as_of=None, parametresiz build_map ile aynı kovayı üretir."""

from desktop.liquidity import LEVERAGE, build_map
from desktop.market import HOUR_MS, Bar, Point

MARGIN = 0.004
LEVERAGE_BEFORE = dict(LEVERAGE)


def _series(count: int) -> tuple[list[Bar], list[Point]]:
    bars: list[Bar] = []
    points: list[Point] = []
    for index in range(count):
        stamp = index * HOUR_MS
        price = 60_000 + index * 10
        bars.append(Bar(stamp, price, price + 30, price - 30, price + 5, 0.0, 100.0))
        points.append(Point(stamp, 1_000 + index * 25))
    return bars, points


def _finger(book) -> tuple:
    return (
        tuple(book.times),
        tuple((round(cluster.price, 6), round(cluster.usd, 6)) for cluster in book.long_peaks),
        tuple((round(cluster.price, 6), round(cluster.usd, 6)) for cluster in book.short_peaks),
        tuple(round(sum(column.values()), 6) for column in book.columns),
    )


def main() -> None:
    bars, points = _series(48)
    plain = build_map("BTCUSDT", bars, points, MARGIN, 100)
    named = build_map("BTCUSDT", bars, points, MARGIN, 100, as_of=None)
    assert _finger(plain) == _finger(named), "as_of=None parametresiz çağrıdan ayrıldı"
    last = build_map("BTCUSDT", bars, points, MARGIN, 100, as_of=bars[-1].time_ms)
    assert _finger(last) == _finger(plain), "son mumun zamanı tam seriden ayrıldı"
    cut = bars[23].time_ms
    early = build_map("BTCUSDT", bars, points, MARGIN, 100, as_of=cut)
    assert early.times
    assert early.times[-1] <= cut
    assert all(stamp <= cut for stamp in early.times)
    assert len(early.times) < len(plain.times)
    same = next(index for index, stamp in enumerate(plain.times) if stamp == early.times[-1])
    assert _finger(early)[-1][-1] == _finger(plain)[-1][same]
    later = {bars[40].time_ms: (1.0, 99_000.0)}
    swept = build_map("BTCUSDT", bars, points, MARGIN, 100, sweep=later, as_of=cut)
    assert _finger(swept) == _finger(early), "gelecek fitil kesilen haritaya girdi"
    assert LEVERAGE == LEVERAGE_BEFORE
    print("as_of testi geçti")
    print("tepe long", plain.long_peaks[0].price if plain.long_peaks else None)
    print("kova", len(plain.times), "kesik", len(early.times))


if __name__ == "__main__":
    main()
