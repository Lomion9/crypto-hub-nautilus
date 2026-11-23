"""Trend Long getiri kolonlarını, süre geçip mum kapandıktan sonra doldurur.

Sinyal anında çalışmaz. Eski satırların diğer kolonlarına ve başka tablolara yazmaz.
"""

from __future__ import annotations

import sqlite3
import time

from desktop.trend_kayit import TABLES, doldur, ensure, kayit_yolu


def main() -> None:
    path = kayit_yolu()
    if not path.exists():
        print("kayıt dosyası yok")
        return
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        ensure(conn)
        now = int(time.time() * 1000)
        totals = {name: doldur(conn, now, name) for name in TABLES}
        conn.commit()
    finally:
        conn.close()
    for name, counts in totals.items():
        print(
            f"{name}"
            f" 1s={counts['getiri_1s']}"
            f" 4s={counts['getiri_4s']}"
            f" 24s={counts['getiri_24s']}"
            f" mae={counts['mae_24s']}"
        )


if __name__ == "__main__":
    main()
