"""Trend Long saat kaydı. Eski tablolara yazılmaz. Getiri sinyal anında hesaplanmaz."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from desktop.readout import TREND_LONG, TREND_SHORT, trend_long_bandi, trend_short_bandi

HOUR_MS = 3_600_000
SEMBOL = "BTCUSDT"
HORIZONS = (("getiri_1s", 1), ("getiri_4s", 4), ("getiri_24s", 24))

_COLUMNS = """
    zaman INTEGER PRIMARY KEY,
    olay_id INTEGER NOT NULL,
    fiyat REAL,
    funding_ham REAL,
    funding_bandi TEXT,
    parametre_versiyonu INTEGER NOT NULL,
    oi_status TEXT,
    price_status TEXT,
    cvd_spot REAL,
    cvd_perp REAL,
    spot_onde INTEGER,
    spot_cvd_var INTEGER NOT NULL,
    getiri_1s REAL,
    getiri_4s REAL,
    getiri_24s REAL,
    mae_24s REAL
"""

TABLES = {"trend_long_kayit": "up", "trend_short_kayit": "down"}

_CREATE = """
CREATE TABLE IF NOT EXISTS trend_long_kayit (
    zaman INTEGER PRIMARY KEY,
    olay_id INTEGER NOT NULL,
    fiyat REAL,
    funding_ham REAL,
    funding_bandi TEXT,
    parametre_versiyonu INTEGER NOT NULL,
    oi_status TEXT,
    price_status TEXT,
    cvd_spot REAL,
    cvd_perp REAL,
    spot_onde INTEGER,
    spot_cvd_var INTEGER NOT NULL,
    getiri_1s REAL,
    getiri_4s REAL,
    getiri_24s REAL,
    mae_24s REAL
);
CREATE TABLE IF NOT EXISTS trend_short_kayit (
""" + _COLUMNS + """
);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE)


def kayit_yolu() -> Path:
    return Path.home() / "AppData" / "Roaming" / "CryptoHub" / "liq_tape.sqlite"


def _satir_govdesi(
    zaman: int | None,
    fiyat: float | None,
    funding: float | None,
    oi_status: str,
    price_status: str,
    cvd_spot: float | None,
    cvd_perp: float | None,
    spot_pozitif: bool,
) -> dict | None:
    if not isinstance(zaman, int) or zaman <= 0:
        return None
    if not isinstance(fiyat, (int, float)) or not isinstance(cvd_spot, (int, float)):
        return None
    if not isinstance(cvd_perp, (int, float)):
        return None
    if spot_pozitif and not cvd_spot > 0:
        return None
    if not spot_pozitif and not cvd_spot < 0:
        return None
    if funding is None or not math.isfinite(funding) or float(fiyat) <= 0:
        return None
    onde = float(cvd_spot) >= abs(float(cvd_perp)) if spot_pozitif else abs(float(cvd_spot)) >= abs(float(cvd_perp))
    return {
        "zaman": int(zaman),
        "fiyat": float(fiyat),
        "funding_ham": float(funding),
        "oi_status": oi_status,
        "price_status": price_status,
        "cvd_spot": float(cvd_spot),
        "cvd_perp": float(cvd_perp),
        "spot_onde": 1 if onde else 0,
        "spot_cvd_var": 1,
    }


def trend_satiri(
    symbol: str,
    result: str,
    zaman: int | None,
    fiyat: float | None,
    funding: float | None,
    oi_status: str,
    price_status: str,
    cvd_spot: float | None,
    cvd_perp: float | None,
) -> dict | None:
    """1 saatlik Trend Long satırı. Eksik spot CVD veya başka coin yazılmaz."""
    if symbol != SEMBOL or result != "Trend Long":
        return None
    row = _satir_govdesi(zaman, fiyat, funding, oi_status, price_status, cvd_spot, cvd_perp, True)
    if row is None or funding is None:
        return None
    row["funding_bandi"] = trend_long_bandi(float(funding))
    row["parametre_versiyonu"] = int(TREND_LONG["parametre_versiyonu"])
    row["tablo"] = "trend_long_kayit"
    return row


def trend_short_satiri(
    symbol: str,
    result: str,
    zaman: int | None,
    fiyat: float | None,
    funding: float | None,
    oi_status: str,
    price_status: str,
    cvd_spot: float | None,
    cvd_perp: float | None,
) -> dict | None:
    """1 saatlik Trend Short satırı. Eksik spot CVD veya başka coin yazılmaz."""
    if symbol != SEMBOL or result != "Trend Short":
        return None
    row = _satir_govdesi(zaman, fiyat, funding, oi_status, price_status, cvd_spot, cvd_perp, False)
    if row is None or funding is None:
        return None
    row["funding_bandi"] = trend_short_bandi(float(funding))
    row["parametre_versiyonu"] = int(TREND_SHORT["parametre_versiyonu"])
    row["tablo"] = "trend_short_kayit"
    return row


def kaydet(conn: sqlite3.Connection, row: dict, tablo: str = "trend_long_kayit") -> bool:
    """Aynı saat ikinci kez yazılmaz. Getiri kolonları boş kalır."""
    if tablo not in TABLES:
        return False
    ensure(conn)
    zaman = int(row["zaman"])
    found = conn.execute(
        f"SELECT 1 FROM {tablo} WHERE zaman=?",
        (zaman,),
    ).fetchone()
    if found is not None:
        return False
    prev = conn.execute(
        f"SELECT olay_id FROM {tablo} WHERE zaman=?",
        (zaman - HOUR_MS,),
    ).fetchone()
    if prev is not None:
        olay = int(prev[0])
    else:
        cap = conn.execute(f"SELECT COALESCE(MAX(olay_id), 0) FROM {tablo}").fetchone()
        olay = int(cap[0]) + 1
    conn.execute(
        f"""
        INSERT INTO {tablo} (
            zaman, olay_id, fiyat, funding_ham, funding_bandi, parametre_versiyonu,
            oi_status, price_status, cvd_spot, cvd_perp, spot_onde, spot_cvd_var,
            getiri_1s, getiri_4s, getiri_24s, mae_24s
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL)
        """,
        (
            zaman,
            olay,
            row["fiyat"],
            row["funding_ham"],
            row["funding_bandi"],
            row["parametre_versiyonu"],
            row["oi_status"],
            row["price_status"],
            row["cvd_spot"],
            row["cvd_perp"],
            row["spot_onde"],
            row["spot_cvd_var"],
        ),
    )
    return True


def _yuzde(future: float, entry: float) -> float:
    return (float(future) - float(entry)) / float(entry) * 100


def _getiri(future: float, entry: float, side: str) -> float:
    move = _yuzde(future, entry)
    return move if side == "up" else -move


def doldur(conn: sqlite3.Connection, now_ms: int, tablo: str = "trend_long_kayit") -> dict[str, int]:
    """Boş getiri kolonlarını, ilgili mum kapandıysa doldurur. Dolu hücreye dokunmaz."""
    if tablo not in TABLES:
        return {"getiri_1s": 0, "getiri_4s": 0, "getiri_24s": 0, "mae_24s": 0}
    side = TABLES[tablo]
    ensure(conn)
    counts = {"getiri_1s": 0, "getiri_4s": 0, "getiri_24s": 0, "mae_24s": 0}
    rows = conn.execute(
        f"""
        SELECT zaman, fiyat, getiri_1s, getiri_4s, getiri_24s, mae_24s
        FROM {tablo}
        WHERE getiri_1s IS NULL OR getiri_4s IS NULL OR getiri_24s IS NULL OR mae_24s IS NULL
        """
    ).fetchall()
    if not rows:
        return counts
    candles = {
        int(stamp): (float(low), float(high), float(close))
        for stamp, low, high, close in conn.execute(
            "SELECT saat_ms, l, h, c FROM mum_saat WHERE sembol=?",
            (SEMBOL,),
        )
        if low is not None and high is not None and close is not None and float(close) > 0
    }
    for zaman, fiyat, g1, g4, g24, mae in rows:
        if not isinstance(fiyat, (int, float)) or float(fiyat) <= 0:
            continue
        sets: list[str] = []
        vals: list[float] = []
        for column, hours in HORIZONS:
            current = {"getiri_1s": g1, "getiri_4s": g4, "getiri_24s": g24}[column]
            if current is not None:
                continue
            stamp = int(zaman) + hours * HOUR_MS
            if now_ms < stamp + HOUR_MS or stamp not in candles:
                continue
            sets.append(f"{column}=?")
            vals.append(_getiri(candles[stamp][2], fiyat, side))
        if mae is None and now_ms >= int(zaman) + 25 * HOUR_MS:
            stamps = [int(zaman) + hour * HOUR_MS for hour in range(1, 25)]
            if all(stamp in candles for stamp in stamps):
                if side == "up":
                    worst = min(candles[stamp][0] for stamp in stamps)
                    adverse = _yuzde(worst, fiyat)
                else:
                    worst = max(candles[stamp][1] for stamp in stamps)
                    adverse = -_yuzde(worst, fiyat)
                sets.append("mae_24s=?")
                vals.append(min(0.0, adverse))
        if not sets:
            continue
        vals.append(int(zaman))
        conn.execute(
            f"UPDATE {tablo} SET {', '.join(sets)} WHERE zaman=?",
            vals,
        )
        for name in counts:
            if any(part.startswith(name) for part in sets):
                counts[name] += 1
    return counts
