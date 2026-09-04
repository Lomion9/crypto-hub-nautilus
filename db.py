import os
import sqlite3
import pandas as pd

from config import CONFIG

# ==========================================
# ZAMAN SERİSİ / VERİTABANI
# ==========================================
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oi_funding_history.db")
HISTORY_FILE = DB_FILE  # geri uyumluluk için aynı isim korunuyor
VERI_COLS = ['tarih', 'saat', 'oi_btc', 'oi_usd', 'funding_pct', 'price',
             'price_open', 'price_high', 'price_low', 'oi_linear_btc', 'oi_inverse_btc',
             'cvd_spot_btc', 'cvd_perp_btc', 'premium_pct']

def _migrate_add_ohlc_columns(conn):
    """Var olan (eski şemalı) bir oi_funding_history.db'de price_open/high/low,
    oi_linear/inverse ve premium_pct kolonları yoksa ekler."""
    mevcut_kolonlar = {row[1] for row in conn.execute("PRAGMA table_info(veri)").fetchall()}
    for kolon in ('price_open', 'price_high', 'price_low', 'oi_linear_btc', 'oi_inverse_btc', 'premium_pct'):
        if kolon not in mevcut_kolonlar:
            conn.execute(f"ALTER TABLE veri ADD COLUMN {kolon} REAL")

def _migrate_add_tp_kolonlari(conn, tf):
    """Var olan (eski şemalı) aktif_islem_{tf} ve sinyal_{tf} tablolarına, TP
    (hedef fiyat) takibi için gereken kolonları ekler."""
    aktif_kolonlar = {row[1] for row in conn.execute(f"PRAGMA table_info(aktif_islem_{tf})").fetchall()}
    if 'hedef_tp' not in aktif_kolonlar:
        conn.execute(f"ALTER TABLE aktif_islem_{tf} ADD COLUMN hedef_tp REAL")

    sinyal_kolonlar = {row[1] for row in conn.execute(f"PRAGMA table_info(sinyal_{tf})").fetchall()}
    if 'kapanis_tipi' not in sinyal_kolonlar:
        conn.execute(f"ALTER TABLE sinyal_{tf} ADD COLUMN kapanis_tipi TEXT")

def _migrate_add_trap_etiketi(conn, tf):
    """Var olan (eski şemalı) durum_{tf} tablosuna trap_etiketi kolonunu ekler --
    Trap koşulları artık pozisyon açtırmıyor (genel_durum hep 'İşlem Açma'
    döner), ama hangi Trap'in (varsa) o an oluştuğu bu ayrı kolonda,
    sadece bilgi amaçlı loglanıyor (bkz. sinyal.genel_durum)."""
    kolonlar = {row[1] for row in conn.execute(f"PRAGMA table_info(durum_{tf})").fetchall()}
    if 'trap_etiketi' not in kolonlar:
        conn.execute(f"ALTER TABLE durum_{tf} ADD COLUMN trap_etiketi TEXT")

def _init_gercek_likidasyon_tablosu(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS gercek_likidasyon (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih TEXT, saat TEXT,
        yon TEXT, kontrat_tipi TEXT,
        gercek_fiyat REAL, notional_usd REAL,
        tahmini_kume_fiyat REAL, tahmini_katman TEXT, tahmini_pencere INTEGER,
        fark_usd REAL, fark_yuzde REAL
    )""")

def gercek_likidasyon_kaydet(conn, yon, kontrat_tipi, gercek_fiyat, notional_usd,
                              tahmini_kume_fiyat=None, tahmini_katman=None, tahmini_pencere=None):
    from datetime import datetime
    now = datetime.now()
    fark_usd = None
    fark_yuzde = None
    if tahmini_kume_fiyat is not None:
        fark_usd = gercek_fiyat - tahmini_kume_fiyat
        fark_yuzde = (fark_usd / tahmini_kume_fiyat) * 100
    conn.execute("""
        INSERT INTO gercek_likidasyon
        (tarih, saat, yon, kontrat_tipi, gercek_fiyat, notional_usd,
         tahmini_kume_fiyat, tahmini_katman, tahmini_pencere, fark_usd, fark_yuzde)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (now.strftime('%d.%m.%Y'), now.strftime('%H:%M:%S'), yon, kontrat_tipi,
          gercek_fiyat, notional_usd, tahmini_kume_fiyat, tahmini_katman, tahmini_pencere,
          fark_usd, fark_yuzde))
    conn.commit()

def _init_db(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS veri (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tarih TEXT, saat TEXT, oi_btc REAL, oi_usd REAL, funding_pct REAL,
        price REAL, cvd_spot_btc REAL, cvd_perp_btc REAL
    )''')
    _migrate_add_ohlc_columns(conn)
    _init_gercek_likidasyon_tablosu(conn)
    for tf in CONFIG['timeframes'].keys():
        conn.execute(f'''CREATE TABLE IF NOT EXISTS durum_{tf} (
            id INTEGER PRIMARY KEY,
            tarih TEXT, saat TEXT, funding_durum TEXT, oi_durum TEXT,
            fiyat_durum TEXT, cvd_durum TEXT, genel_durum TEXT, trap_etiketi TEXT
        )''')
        conn.execute(f'''CREATE TABLE IF NOT EXISTS sinyal_{tf} (
            kapanis_tarih TEXT, kapanis_saat TEXT, sinyal TEXT, yon TEXT,
            giris_tarih TEXT, giris_saat TEXT, giris_fiyat REAL, cikis_fiyat REAL, kar_yuzde REAL
        )''')
        conn.execute(f'''CREATE TABLE IF NOT EXISTS aktif_islem_{tf} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            genel_durum TEXT, giris_fiyat REAL, giris_tarih TEXT, giris_saat TEXT, farkli_sayac INTEGER
        )''')
        conn.execute(f'''CREATE TABLE IF NOT EXISTS aktif_bekleme_{tf} (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            genel_durum TEXT, tetik_fiyat REAL, farkli_sayac INTEGER
        )''')
        _migrate_add_tp_kolonlari(conn, tf)
        _migrate_add_trap_etiketi(conn, tf)
    conn.commit()

def load_history(path=DB_FILE):
    """Sadece 'veri' tablosunu okur."""
    conn = sqlite3.connect(path)
    _init_db(conn)
    veri_df = pd.read_sql("SELECT * FROM veri", conn)
    conn.close()

    if veri_df.empty:
        return pd.DataFrame(columns=VERI_COLS + ['timestamp'])

    veri_df['funding_pct'] = veri_df['funding_pct'].astype(float)
    veri_df['timestamp'] = pd.to_datetime(veri_df['tarih'] + ' ' + veri_df['saat'], format='%d.%m.%Y %H:%M')
    return veri_df.sort_values('timestamp').reset_index(drop=True)