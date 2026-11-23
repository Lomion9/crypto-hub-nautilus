"""Yakın stop emri açmaz. Delinen stop sinyali kapatır. Başarısız kapanış sonraki açılışı da keser."""

from desktop.app import STOP_MIN_RATIO, _advance_signal, _stop_cok_yakin, _stop_reached
from desktop.okx_trade import acilis_engelli


def test_mesafe():
    assert STOP_MIN_RATIO == 0.005
    assert _stop_cok_yakin(100, 99.6) is True
    assert _stop_cok_yakin(100, 100.4) is True
    assert _stop_cok_yakin(100, 99.5) is False
    assert _stop_cok_yakin(100, 99.0) is False
    assert _stop_cok_yakin(100, None) is False
    assert _stop_cok_yakin(0, 99) is False


def test_stop_sinyali_kapatir():
    assert _stop_reached("down", 62109, 61355.4, 63700, 62000) is True
    assert _stop_reached("up", 90, 100, 110, 89) is True
    assert _stop_reached("down", 62109, 61355.4, 62000, 61000) is False
    assert _stop_reached("up", 110, 100, 120, 95) is False
    episode = {
        "verdict": "Long Trap",
        "side": "down",
        "tp": 60725.0,
        "sl": 62109.0,
        "entry": 61355.4,
        "candle": 1000,
        "target": 60525.0,
    }
    book, events, notes = _advance_signal(
        {"BTCUSDT": {"1sa": episode}},
        "BTCUSDT",
        {"1sa": (1000, 63000.0, 62000.0)},
        {"1sa": "Long Trap"},
        {"1sa": 60525.0},
        63600.0,
        {"1sa": (63800.0, 62000.0)},
    )
    assert events[0].kind == "stop"
    assert events[0].verdict == "Long Trap"
    assert book["BTCUSDT"]["1sa"]["verdict"] == ""
    assert notes[0][1] == "miss"
    assert notes[0][2] < 0


def test_kapanis_engeli():
    assert acilis_engelli(True, "4sa", set()) is True
    assert acilis_engelli(False, "4sa", {"1sa"}) is False
    assert acilis_engelli(False, "1sa", {"1sa"}) is True
    assert acilis_engelli(False, "4sa", set()) is False


if __name__ == "__main__":
    test_mesafe()
    test_stop_sinyali_kapatir()
    test_kapanis_engeli()
    print("stop mesafe geçti")
