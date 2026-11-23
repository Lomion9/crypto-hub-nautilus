"""Mum saati, tuzak emri ve elle pozisyon kuralları."""

from datetime import datetime, timezone

from desktop.app import (
    SIGNAL_BAR_MS,
    _SignalEvent,
    _closed_candle_ready,
    _newly_due,
    _plan_orders,
    _seed_signal_marks,
    _signal_mark,
)


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _event(label: str, verdict: str, kind: str = "yeni") -> _SignalEvent:
    return _SignalEvent(label, kind, verdict, None)


def _episode(side: str, entry: float, sl: float, verdict: str) -> dict:
    return {"side": side, "entry": entry, "sl": sl, "tp": entry - 500 if side == "down" else entry + 500, "verdict": verdict}


def test_saat_izgarasi():
    seeded_at = datetime(2026, 11, 23, 14, 30, tzinfo=timezone.utc)
    seen = _seed_signal_marks(_ms(seeded_at))
    assert _newly_due(_ms(datetime(2026, 11, 23, 14, 50, tzinfo=timezone.utc)), seen) == []
    opened = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
    hour = datetime(2026, 11, 23, 15, 0, 30, tzinfo=timezone.utc)
    assert "1sa" in _newly_due(_ms(hour), seen)
    assert _closed_candle_ready(_ms(opened), _ms(hour), SIGNAL_BAR_MS["1sa"], "1sa")
    assert not _closed_candle_ready(_ms(opened) - 3_600_000, _ms(hour), SIGNAL_BAR_MS["1sa"], "1sa")
    four = datetime(2026, 11, 23, 0, 0, 20, tzinfo=timezone.utc)
    before_four = datetime(2026, 11, 22, 23, 50, tzinfo=timezone.utc)
    seen_four = _seed_signal_marks(_ms(before_four))
    assert "4sa" in _newly_due(_ms(four), seen_four)
    assert "24sa" in _newly_due(_ms(four), seen_four)
    sunday = datetime(2026, 11, 22, 0, 5, tzinfo=timezone.utc)
    assert sunday.weekday() == 6
    assert _signal_mark(_ms(sunday), "1hafta") != _signal_mark(_ms(datetime(2026, 11, 21, 23, 50, tzinfo=timezone.utc)), "1hafta")
    thursday = datetime(2026, 11, 19, 0, 5, tzinfo=timezone.utc)
    assert thursday.weekday() == 3
    assert _signal_mark(_ms(thursday), "1hafta") == _signal_mark(_ms(thursday) - 120_000, "1hafta")
    previous_sunday = datetime(2026, 11, 15, 0, 0, tzinfo=timezone.utc)
    assert _closed_candle_ready(_ms(previous_sunday), _ms(sunday), SIGNAL_BAR_MS["1hafta"], "1hafta")


def test_elle_pozisyon():
    coin = {"1sa": {"side": "down", "sz": "1", "manual": True}}
    episodes = {"4sa": _episode("down", 100, 110, "Dağıtım")}
    actions, notes = _plan_orders([_event("4sa", "Dağıtım")], coin, episodes, {})
    assert actions == []
    assert notes == ["emir gitmedi: elle pozisyon duruyor"]


def test_ayni_tuzak_susar():
    coin = {"1sa": {"side": "down", "sz": "1", "family": "Long Trap", "limits": ["a", "b"]}}
    episodes = {"4sa": _episode("down", 100, 110, "Long Trap")}
    actions, notes = _plan_orders([_event("4sa", "Long Trap")], coin, episodes, {"4sa": (90.0, 95.0)})
    assert actions == []
    assert notes == []


def test_tuzak_uc_parca():
    coin = {"1sa": {"side": "up", "sz": "1", "family": "Short Trap"}}
    episodes = {"4sa": _episode("down", 100, 110, "Long Trap")}
    actions, notes = _plan_orders([_event("4sa", "Long Trap")], coin, episodes, {"4sa": (90.0, 95.0)})
    assert notes == []
    assert [item["op"] for item in actions] == ["close", "scale"]
    assert actions[1]["budget"] == "third"
    assert actions[1]["limits"] == [90.0, 95.0]
    assert actions[1]["family"] == "Long Trap"


def test_yakin_stop_kapatmaz():
    coin = {"1sa": {"side": "up", "sz": "1"}}
    episodes = {"4sa": _episode("down", 100, 100.4, "Long Trap")}
    actions, notes = _plan_orders([_event("4sa", "Long Trap")], coin, episodes, {"4sa": (90.0, 95.0)})
    assert actions == []
    assert notes == ["emir gitmedi: stop fiyatın %0.5'inden yakın"]


def test_ayni_yon_yalniz_guard():
    coin = {"1sa": {"side": "down", "sz": "1"}}
    episodes = {"4sa": _episode("down", 100, 110, "Dağıtım")}
    actions, notes = _plan_orders([_event("4sa", "Dağıtım")], coin, episodes, {})
    assert notes == []
    assert [item["op"] for item in actions] == ["guard"]
    assert actions[0]["label"] == "1sa"


def test_ters_yon_kapatip_acar():
    coin = {"1sa": {"side": "down", "sz": "1", "limits": ["a"]}}
    episodes = {"4sa": _episode("up", 100, 90, "Sağlıklı Long")}
    actions, notes = _plan_orders([_event("4sa", "Sağlıklı Long")], coin, episodes, {})
    assert notes == []
    assert [item["op"] for item in actions] == ["close", "open"]
    assert actions[1]["budget"] == "risk"


def test_tuzak_sonrasi_kalan_kasa():
    coin = {"1sa": {"side": "down", "sz": "1", "family": "Long Trap", "limits": ["a", "b"]}}
    episodes = {"4sa": _episode("down", 100, 110, "Dağıtım")}
    actions, notes = _plan_orders([_event("4sa", "Dağıtım")], coin, episodes, {})
    assert notes == []
    assert [item["op"] for item in actions] == ["drop", "open", "guard"]
    assert actions[1]["budget"] == "rest"
    assert actions[2]["tp"] == episodes["4sa"]["tp"]
    assert actions[2]["sl"] == episodes["4sa"]["sl"]


if __name__ == "__main__":
    test_saat_izgarasi()
    test_elle_pozisyon()
    test_ayni_tuzak_susar()
    test_tuzak_uc_parca()
    test_yakin_stop_kapatmaz()
    test_ayni_yon_yalniz_guard()
    test_ters_yon_kapatip_acar()
    test_tuzak_sonrasi_kalan_kasa()
    print("emir planı geçti")
