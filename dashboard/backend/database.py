"""SQLite access for the dashboard.

Bot tables (veri, durum_*, sinyal_*, ...) are read-only.
The dashboard-owned `settings` table is the only table this module writes.
"""

from __future__ import annotations

import sqlite3
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
