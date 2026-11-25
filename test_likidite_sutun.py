"""Yalnız son sütunu tutan harita, tam haritanın son saatiyle ve tepeleriyle aynıdır."""

from desktop.liquidity import VenueBook, build_map, clip_map, merge_venues
from desktop.market import HOUR_MS, Bar, Point

MARGIN = 0.004


def _venue(count: int, base: float, name: str, inverse: bool = False, lag: int = 0) -> VenueBook:
    bars: list[Bar] = []
    points: list[Point] = []
    for index in range(count - lag):
        stamp = index * HOUR_MS
        price = base + (index % 17) * 40 - (index % 5) * 25
        bars.append(Bar(stamp, price, price + 90, price - 120, price + 15, (index % 7 - 3) * 2.0, 100.0))
        points.append(Point(stamp, 1_000 + index * 20 - (index % 9) * 35))
    return VenueBook(bars, points, MARGIN, 100, name, inverse)


def _same_tail(full, last) -> None:
    assert full.times == last.times
    assert full.lows == last.lows and full.highs == last.highs
    assert len(last.columns) == 1 and len(last.levels) == 1
    assert full.columns[-1] == last.columns[-1]
    assert full.levels[-1] == last.levels[-1]
    assert full.long_peaks == last.long_peaks
    assert full.short_peaks == last.short_peaks


def test_build_map_son_sutun() -> None:
    venue = _venue(120, 60_000, "binance-usdt")
    full = build_map("BTCUSDT", venue.bars, venue.oi, MARGIN, 100)
    last = build_map("BTCUSDT", venue.bars, venue.oi, MARGIN, 100, keep_columns=False)
    _same_tail(full, last)


def test_merge_venues_son_sutun() -> None:
    venues = [
        _venue(200, 60_000, "binance-usdt"),
        _venue(200, 60_030, "bybit-usdt"),
        _venue(200, 59_980, "okx-usd", inverse=True),
        _venue(200, 3_000, "kucuk-adim"),
        _venue(200, 60_010, "geride", lag=3),
    ]
    full = merge_venues("BTCUSDT", venues)
    last = merge_venues("BTCUSDT", venues, keep_columns=False)
    assert len(full.columns) == len(full.times)
    _same_tail(full, last)
    for hours in (24, 72):
        _same_tail(clip_map(full, hours), clip_map(full, hours, keep_columns=False))


def main() -> None:
    test_build_map_son_sutun()
    test_merge_venues_son_sutun()
    print("son sütun testi geçti")


if __name__ == "__main__":
    main()
