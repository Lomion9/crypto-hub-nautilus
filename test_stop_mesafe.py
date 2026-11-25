"""Yakın stop emri açmaz. Delinen stop sinyali kapatır. Başarısız kapanış sonraki açılışı da keser."""

from desktop.app import STOP_MIN_RATIO, _TargetTable, _advance_signal, _stop_cok_yakin, _stop_reached
from desktop.liquidity import LiquidityMap
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


def _kova_tablosu(levels: list[tuple[float, float]]) -> _TargetTable:
    book = LiquidityMap(
        symbol="BTCUSDT",
        step=1.0,
        closes=[60000.0],
        lows=[58000.0],
        highs=[62000.0],
        columns=[{index: usd for index, (_level, usd) in enumerate(levels)}],
        levels=[{index: level for index, (level, _usd) in enumerate(levels)}],
    )
    return _TargetTable(book)


def test_yakin_tp_sonraki_kova():
    """Büyük kovanın TP'si %0.5'ten yakınsa sonraki büyük kova seçilir."""
    price = 60000.0
    near = 59550.0
    far = 59000.0
    table = _kova_tablosu([(near, 100.0), (far, 40.0)])
    assert table.pick("down", price) == near
    assert table.pick("down", price, STOP_MIN_RATIO) == far
    tight = _kova_tablosu([(near, 100.0), (59650.0, 40.0)])
    assert tight.pick("down", price) == near
    assert tight.pick("down", price, STOP_MIN_RATIO) is None
    edge = 59500.0
    exact = _kova_tablosu([(edge, 10.0)])
    assert exact.pick("down", price, STOP_MIN_RATIO) == edge


def _saat_tablosu(columns: list[list[tuple[float, float]]]) -> _TargetTable:
    book = LiquidityMap(
        symbol="BTCUSDT",
        step=1.0,
        closes=[60000.0],
        lows=[54000.0],
        highs=[66000.0],
        columns=[{index: usd for index, (_level, usd) in enumerate(levels)} for levels in columns],
        levels=[{index: level for index, (level, _usd) in enumerate(levels)} for levels in columns],
    )
    return _TargetTable(book)


def test_supurulmus_kova_secilmez():
    """Fitilin sildiği eski sütun stop ve TP olmaz. Son sütundaki kova seçilir."""
    price = 59115.0
    swept_stop = 58947.0
    live_stop = 56000.0
    swept_tp = 65000.0
    live_tp = 62000.0
    table = _saat_tablosu(
        [
            [(swept_stop, 50_000_000.0), (swept_tp, 40_000_000.0)],
            [(live_stop, 1_000_000.0), (live_tp, 2_000_000.0)],
        ]
    )
    assert table.nearest_stop("up", price) == live_stop
    assert table.pick("up", price) == live_tp


def test_kapanis_engeli():
    assert acilis_engelli(True, "4sa", set()) is True
    assert acilis_engelli(False, "4sa", {"1sa"}) is False
    assert acilis_engelli(False, "1sa", {"1sa"}) is True
    assert acilis_engelli(False, "4sa", set()) is False


if __name__ == "__main__":
    test_mesafe()
    test_stop_sinyali_kapatir()
    test_yakin_tp_sonraki_kova()
    test_supurulmus_kova_secilmez()
    test_kapanis_engeli()
    print("stop mesafe geçti")
