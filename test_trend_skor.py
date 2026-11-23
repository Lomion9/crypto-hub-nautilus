"""Trend Long skoru kendi etiketinde kalır. Eski pencere sayacı karışmaz."""

import time

from desktop.app import (
    DOWN,
    UP,
    AMBER,
    _advance_signal,
    _bump_score,
    _order_signal,
    _score_line,
    _score_note,
    _trade_side,
    _verdict_color,
)


def test_renk_ve_emir():
    assert _trade_side("Trend Long") == "up"
    assert _trade_side("Sağlıklı Long") == "up"
    assert _trade_side("Trend Short") == "down"
    assert _trade_side("Dağıtım") == "down"
    assert _order_signal("Trend Long") == "Trend Long"
    assert _order_signal("Trend Short") == "Trend Short"
    assert _order_signal("Sağlıklı Long") == "Sağlıklı Long"
    assert _verdict_color("Trend Long").name() == UP.name()
    assert _verdict_color("Trend Short").name() == DOWN.name()
    assert _verdict_color("Sağlıklı Long").name() == UP.name()
    assert _verdict_color("Dağıtım").name() == DOWN.name()
    assert _verdict_color("Long Trap").name() == AMBER.name()
    assert _verdict_color("İşlem Açma").name() != UP.name()


def test_skor_etiketi():
    episode = {"verdict": "Trend Long", "side": "up", "entry": 100.0}
    assert _score_note("1sa", "hit", episode, 110.0) == ("Trend Long", "hit", 10.0)
    old = {"verdict": "Sağlıklı Long", "side": "up", "entry": 100.0}
    assert _score_note("1sa", "miss", old, 90.0)[0] == "1sa"
    score = _bump_score(
        {},
        "BTCUSDT",
        [
            ("Trend Long", "miss", 1.5),
            ("1sa", "hit", 2.0),
        ],
    )
    coin = score["BTCUSDT"]
    assert coin["Trend Long"]["miss"] == 1
    assert coin["Trend Long"]["pnl"] == 1.5
    assert coin["1sa"]["hit"] == 1
    assert "miss" not in coin["1sa"]
    assert "Trend Long" in _score_line(coin, "Trend Long") or _score_line(coin, "Trend Long").startswith("TP doğru")


def _step(book, candle, verdict, target, spot):
    return _advance_signal(
        book,
        "BTCUSDT",
        {"1sa": (candle, spot, spot)},
        {"1sa": verdict},
        {"1sa": target},
        spot,
    )


def test_kapanis_ayrilir():
    hour = 3_600_000
    start = int(time.time() * 1000) // hour * hour - 6 * hour
    book, events, notes = _step({}, start, "Trend Long", 400, 100)
    assert notes == []
    assert events[0].verdict == "Trend Long"
    assert book["BTCUSDT"]["1sa"]["side"] == "up"
    book, _events, notes = _step(book, start + hour, "Sağlıklı Long", 400, 105)
    assert notes == [("Trend Long", "miss", 5.0)]
    assert book["BTCUSDT"]["1sa"]["verdict"] == "Sağlıklı Long"
    book, _events, notes = _step(book, start + 2 * hour, "Short Squeeze", 400, 106)
    assert notes == []
    assert book["BTCUSDT"]["1sa"]["verdict"] == "Short Squeeze"
    book, events, notes = _step(book, start + 3 * hour, "Trend Long", 400, 100)
    assert notes[0][0] == "1sa"
    assert notes[0][1] == "miss"
    assert book["BTCUSDT"]["1sa"]["verdict"] == "Trend Long"
    book, events, notes = _step(book, start + 4 * hour, "Trend Long", 400, 250)
    assert notes[0] == ("Trend Long", "hit", 100.0)
    assert events[0].kind == "kapandi"


if __name__ == "__main__":
    test_renk_ve_emir()
    test_skor_etiketi()
    test_kapanis_ayrilir()
    print("trend skor geçti")
