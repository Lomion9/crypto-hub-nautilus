"""Trend Long saat kaydı. Canlı veritabanına yazmaz."""

import sqlite3

from desktop.trend_kayit import HOUR_MS, doldur, kaydet, trend_satiri


def _db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE mum_saat (
            sembol TEXT, saat_ms INTEGER, o REAL, h REAL, l REAL, c REAL,
            hacim REAL, taker_alis REAL, PRIMARY KEY(sembol, saat_ms)
        )
        """
    )
    return conn


def _mum(conn, saat, low, close):
    conn.execute(
        "INSERT INTO mum_saat VALUES ('BTCUSDT', ?, 100, 100, ?, ?, 1, 1)",
        (saat, low, close),
    )


def _satir(zaman, funding=0.003, spot=2.0, perp=1.0):
    return trend_satiri(
        "BTCUSDT",
        "Trend Long",
        zaman,
        100.0,
        funding,
        "Artıyor",
        "Artıyor",
        spot,
        perp,
    )


def test_satir_kapisi():
    assert trend_satiri("ETHUSDT", "Trend Long", HOUR_MS, 100, 0.003, "Artıyor", "Artıyor", 2, 1) is None
    assert trend_satiri("BTCUSDT", "İşlem Açma", HOUR_MS, 100, 0.003, "Artıyor", "Artıyor", 2, 1) is None
    assert trend_satiri("BTCUSDT", "Trend Long", HOUR_MS, 100, 0.003, "Artıyor", "Artıyor", None, 1) is None
    assert trend_satiri("BTCUSDT", "Trend Long", HOUR_MS, 100, 0.003, "Artıyor", "Artıyor", 0, 1) is None
    row = _satir(HOUR_MS, spot=2, perp=-3)
    assert row["spot_onde"] == 0
    assert row["spot_cvd_var"] == 1
    assert row["funding_bandi"] == "düşük"
    assert _satir(HOUR_MS, funding=0.007)["funding_bandi"] == "normal"


def test_olay_ve_tek_yazim():
    conn = _db()
    ilk = _satir(10 * HOUR_MS)
    assert kaydet(conn, ilk)
    assert kaydet(conn, dict(ilk, fiyat=999)) is False
    ikinci = _satir(11 * HOUR_MS)
    assert kaydet(conn, ikinci)
    ucuncu = _satir(13 * HOUR_MS)
    assert kaydet(conn, ucuncu)
    rows = conn.execute(
        "SELECT zaman, olay_id, fiyat, getiri_1s, getiri_4s, getiri_24s, mae_24s FROM trend_long_kayit ORDER BY zaman"
    ).fetchall()
    assert rows == [
        (10 * HOUR_MS, 1, 100.0, None, None, None, None),
        (11 * HOUR_MS, 1, 100.0, None, None, None, None),
        (13 * HOUR_MS, 2, 100.0, None, None, None, None),
    ]
    assert conn.execute("SELECT COUNT(*) FROM mum_saat").fetchone()[0] == 0


def test_doldur_sure_dolunca():
    conn = _db()
    zaman = 100 * HOUR_MS
    assert kaydet(conn, _satir(zaman))
    for hour in range(1, 25):
        _mum(conn, zaman + hour * HOUR_MS, 97 if hour == 3 else 99, 100 + hour)
    assert doldur(conn, zaman + 2 * HOUR_MS - 1) == {
        "getiri_1s": 0, "getiri_4s": 0, "getiri_24s": 0, "mae_24s": 0,
    }
    one = doldur(conn, zaman + 2 * HOUR_MS)
    assert one["getiri_1s"] == 1
    assert one["getiri_4s"] == 0
    assert conn.execute("SELECT getiri_1s FROM trend_long_kayit").fetchone()[0] == 1.0
    conn.execute("UPDATE trend_long_kayit SET getiri_1s=123 WHERE zaman=?", (zaman,))
    four = doldur(conn, zaman + 5 * HOUR_MS)
    assert four["getiri_4s"] == 1
    assert four["getiri_1s"] == 0
    assert conn.execute("SELECT getiri_1s, getiri_4s FROM trend_long_kayit").fetchone() == (123, 4.0)
    conn.execute("DELETE FROM mum_saat WHERE saat_ms=?", (zaman + 10 * HOUR_MS,))
    early = doldur(conn, zaman + 25 * HOUR_MS)
    assert early["getiri_24s"] == 1
    assert early["mae_24s"] == 0
    _mum(conn, zaman + 10 * HOUR_MS, 97, 110)
    done = doldur(conn, zaman + 25 * HOUR_MS)
    assert done["mae_24s"] == 1
    assert done["getiri_24s"] == 0
    got = conn.execute(
        "SELECT getiri_24s, mae_24s FROM trend_long_kayit"
    ).fetchone()
    assert got == (24.0, -3.0)
    mum = conn.execute(
        "SELECT l, c FROM mum_saat WHERE saat_ms=?",
        (zaman + HOUR_MS,),
    ).fetchone()
    assert mum == (99, 101)


if __name__ == "__main__":
    test_satir_kapisi()
    test_olay_ve_tek_yazim()
    test_doldur_sure_dolunca()
    print("trend kayit geçti")
