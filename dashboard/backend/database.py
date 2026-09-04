"""SQLite access for the dashboard.

Bot tables (veri, durum_*, sinyal_*, ...) are read-only.
The dashboard-owned `settings` table is the only table this module writes.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DB_PATH = ROOT_DIR / "oi_funding_history.db"

SETTING_TELEGRAM_TOKEN = "telegram_bot_token"
SETTING_TELEGRAM_CHAT_ID = "telegram_chat_id"


def get_connection(*, readonly: bool = True) -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")
    if readonly:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=10)
    else:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


TIMEFRAMES = ("15dk", "1sa", "2sa", "4sa", "8sa", "24sa")


def fetch_latest_veri() -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM veri ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def parse_tr_datetime(tarih: str | None, saat: str | None) -> datetime | None:
    if not tarih:
        return None
    saat_val = (saat or "00:00").strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(f"{tarih} {saat_val}", fmt)
        except ValueError:
            continue
    return None


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _in_date_range(tarih: str | None, saat: str | None, start: date | None, end: date | None) -> bool:
    dt = parse_tr_datetime(tarih, saat)
    if dt is None:
        return False
    day = dt.date()
    if start and day < start:
        return False
    if end and day > end:
        return False
    return True


def fetch_veri_rows() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, tarih, saat, oi_btc, funding_pct, price, "
            "price_open, price_high, price_low FROM veri ORDER BY id ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def fetch_history_series(start: date | None, end: date | None) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT tarih, saat, oi_btc, funding_pct, cvd_spot_btc, cvd_perp_btc, price "
            "FROM veri ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    series = []
    for row in rows:
        item = dict(row)
        if not _in_date_range(item.get("tarih"), item.get("saat"), start, end):
            continue
        dt = parse_tr_datetime(item["tarih"], item["saat"])
        if dt is None:
            continue
        series.append(
            {
                "time": int(dt.timestamp()),
                "tarih": item["tarih"],
                "saat": item["saat"],
                "oi_btc": item["oi_btc"],
                "funding_pct": item["funding_pct"],
                "cvd_spot_btc": item["cvd_spot_btc"],
                "cvd_perp_btc": item["cvd_perp_btc"],
                "price": item["price"],
            }
        )
    return series


def fetch_closed_signals(start: date | None, end: date | None) -> list[dict]:
    conn = get_connection()
    try:
        rows: list[dict] = []
        for tf in TIMEFRAMES:
            fetched = conn.execute(f"SELECT * FROM sinyal_{tf}").fetchall()
            for row in fetched:
                item = dict(row)
                if not _in_date_range(item.get("kapanis_tarih"), item.get("kapanis_saat"), start, end):
                    continue
                item["tf"] = tf
                rows.append(item)
        return rows
    finally:
        conn.close()


def fetch_realized_liquidations(start: date | None, end: date | None, limit: int = 8000) -> list[dict]:
    conn = get_connection()
    try:
        fetched = conn.execute(
            "SELECT * FROM gercek_likidasyon ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

    rows = []
    for row in fetched:
        item = dict(row)
        if not _in_date_range(item.get("tarih"), item.get("saat"), start, end):
            continue
        dt = parse_tr_datetime(item.get("tarih"), item.get("saat"))
        item["time"] = int(dt.timestamp()) if dt else None
        rows.append(item)
    rows.reverse()
    return rows


def fetch_timeframe_states() -> list[dict]:
    conn = get_connection()
    try:
        states = []
        for tf in TIMEFRAMES:
            durum = conn.execute(
                f"SELECT * FROM durum_{tf} ORDER BY id DESC LIMIT 1"
            ).fetchone()
            aktif = conn.execute(
                f"SELECT * FROM aktif_islem_{tf} WHERE id = 1"
            ).fetchone()
            item: dict = {"tf": tf, "durum": dict(durum) if durum else None}
            item["aktif"] = dict(aktif) if aktif else None
            states.append(item)
        return states
    finally:
        conn.close()


def ensure_settings_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def get_settings() -> dict[str, str]:
    conn = get_connection(readonly=False)
    try:
        ensure_settings_table(conn)
        conn.commit()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: (row["value"] or "") for row in rows}
    finally:
        conn.close()


def upsert_settings(values: dict[str, str]) -> dict[str, str]:
    conn = get_connection(readonly=False)
    try:
        ensure_settings_table(conn)
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        conn.commit()
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: (row["value"] or "") for row in rows}
    finally:
        conn.close()


def telegram_configured(settings: dict[str, str] | None = None) -> bool:
    data = settings if settings is not None else get_settings()
    token = (data.get(SETTING_TELEGRAM_TOKEN) or "").strip()
    chat_id = (data.get(SETTING_TELEGRAM_CHAT_ID) or "").strip()
    return bool(token and chat_id)
