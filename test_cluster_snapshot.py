"""Küme kaydı yalnız ekler. as_of gelecekteki mumu dışarıda bırakır."""

import sqlite3

from desktop.liq_tape import _ensure, snapshot_body, write_snapshot
from desktop.market import HOUR_MS, Bar, Point


def _series(count: int) -> tuple[list[Bar], list[Point]]:
    bars: list[Bar] = []
    points: list[Point] = []
    for index in range(count):
        stamp = index * HOUR_MS
        price = 60_000 + index * 20
        bars.append(Bar(stamp, price, price + 40, price - 40, price + 5, 0.0, 100.0))
        points.append(Point(stamp, 1_000 + index * 40))
    return bars, points


def main() -> None:
    bars, points = _series(48)
    cut = bars[23].time_ms
    early = snapshot_body(bars, points, cut, None)
    full = snapshot_body(bars, points, bars[-1].time_ms, 61_000)
    assert early is not None and full is not None
    assert early["t_ms"] == cut
    assert early["fiyat"] == bars[23].close
    assert full["fiyat"] == 61_000
    assert early["atr"] is not None and early["atr"] > 0
    assert early["agirlik"]["5"] == 3
    assert early["kumeler"] != full["kumeler"]
    assert {item["yon"] for item in early["kumeler"]} <= {"long", "short"}
    conn = sqlite3.connect(":memory:")
    _ensure(conn)
    conn.execute(
        "INSERT INTO liq (t_ms, sembol, yon, fiyat, miktar, dolar) VALUES (1, 'BTCUSDT', 'long', 1, 1, 1)"
    )
    write_snapshot(conn, early, "saat")
    write_snapshot(conn, early, "saat")
    write_snapshot(conn, early, "sinyal")
    rows = conn.execute(
        "SELECT pencere, kaynak FROM cluster_snapshot ORDER BY kaynak, pencere"
    ).fetchall()
    assert rows == [(12, "saat"), (24, "saat"), (12, "sinyal"), (24, "sinyal")]
    assert conn.execute("SELECT COUNT(*) FROM liq").fetchone()[0] == 1
    conn.close()
    print("küme kaydı testi geçti")
    print("kesik", len(early["kumeler"]), "tam", len(full["kumeler"]))


if __name__ == "__main__":
    main()
