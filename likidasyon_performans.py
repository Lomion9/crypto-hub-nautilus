import sqlite3
from db import DB_FILE

conn = sqlite3.connect(DB_FILE)
cur = conn.cursor()
cur.execute("SELECT yon, kontrat_tipi, fark_yuzde FROM gercek_likidasyon WHERE fark_yuzde IS NOT NULL")
rows = cur.fetchall()
conn.close()

if not rows:
    print("Henüz karşılaştırılabilir veri yok.")
else:
    print(f"Toplam {len(rows)} gerçek likidasyon event'i, tahminle kıyaslandı.\n")
    for yon in ("long", "short"):
        yon_farklari = [abs(f) for y, k, f in rows if y == yon]
        if yon_farklari:
            ortalama = sum(yon_farklari) / len(yon_farklari)
            print(f"{yon.upper()}: {len(yon_farklari)} event, ortalama mutlak sapma %{ortalama:.3f}")
        else:
            print(f"{yon.upper()}: henüz event yok")

    print()
    for kontrat_tipi in ("linear", "inverse"):
        kt_farklari = [abs(f) for y, k, f in rows if k == kontrat_tipi]
        if kt_farklari:
            ortalama = sum(kt_farklari) / len(kt_farklari)
            print(f"{kontrat_tipi.upper()}: {len(kt_farklari)} event, ortalama mutlak sapma %{ortalama:.3f}")