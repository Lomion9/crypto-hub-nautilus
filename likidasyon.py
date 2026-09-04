# ==========================================
# LİKİDASYON HARİTASI
# ==========================================

from config import CONFIG
import pandas as pd

# ==========================================
# 1a) KALDIRAÇ DAĞILIMI (funding'e göre dinamik)
# ==========================================
def kaldirac_dagilimi(funding_pct):
    lc = CONFIG['likidasyon']
    taban = {int(k): v for k, v in lc['kaldirac_taban_agirlik'].items()}
    extreme_esik = CONFIG['funding_thresholds']['extreme_pct']
    tavan_kati = lc['siddet_tavan_kati']
    max_kaydirma = lc['max_kaydirma_orani']

    if extreme_esik <= 0:
        return taban

    siddet = min(abs(funding_pct) / extreme_esik, tavan_kati) / tavan_kati  # 0..1
    kaydirma_orani = siddet * max_kaydirma

    dusuk_kaldiraclar = [5, 10]
    yuksek_kaldiraclar = [50, 100]

    toplam_dusuk = sum(taban[k] for k in dusuk_kaldiraclar)
    alinan_pay = toplam_dusuk * kaydirma_orani

    yeni = dict(taban)
    for k in dusuk_kaldiraclar:
        pay_orani = taban[k] / toplam_dusuk if toplam_dusuk > 0 else 0
        yeni[k] = taban[k] - alinan_pay * pay_orani

    toplam_yuksek = sum(taban[k] for k in yuksek_kaldiraclar)
    for k in yuksek_kaldiraclar:
        pay_orani = taban[k] / toplam_yuksek if toplam_yuksek > 0 else 0
        yeni[k] = taban[k] + alinan_pay * pay_orani

    return yeni

# ==========================================
# 1b) TEK BİR OI DELTA'SINI FİYAT KÜMELERİNE ÇEVİRME
# ==========================================
def cvd_agirlikli_long_payi(cvd_perp_delta, oi_delta_abs):
    if oi_delta_abs <= 0:
        return 0.5
    oran = cvd_perp_delta / (abs(cvd_perp_delta) + oi_delta_abs)  # -1..1 arası
    long_payi = 0.5 + oran / 2
    return max(0.0, min(1.0, long_payi))

def delta_kumeleri_hesapla(delta_oi, acilis_fiyati, cvd_perp_delta, funding_pct):
    if delta_oi <= 0:
        return {}

    long_payi = cvd_agirlikli_long_payi(cvd_perp_delta, delta_oi)
    long_miktar = delta_oi * long_payi
    short_miktar = delta_oi * (1 - long_payi)

    dagilim = kaldirac_dagilimi(funding_pct)
    bakim_marji = {int(k): v for k, v in CONFIG['likidasyon']['bakim_marji'].items()}

    kumeler = {}
    for kaldirac, agirlik in dagilim.items():
        bm = bakim_marji[kaldirac]
        bm_guvenli = min(bm, (1 / kaldirac) * 0.9)

        if long_miktar > 0:
            miktar = long_miktar * agirlik
            likit_fiyat = acilis_fiyati * (1 - 1 / kaldirac + bm_guvenli)
            anahtar = (likit_fiyat, 'long')
            kumeler[anahtar] = kumeler.get(anahtar, 0.0) + miktar

        if short_miktar > 0:
            miktar = short_miktar * agirlik
            likit_fiyat = acilis_fiyati * (1 + 1 / kaldirac - bm_guvenli)
            anahtar = (likit_fiyat, 'short')
            kumeler[anahtar] = kumeler.get(anahtar, 0.0) + miktar

    return kumeler

# ==========================================
# 1d) OHLC (HIGH/LOW) İLE TEMİZLEME
# ==========================================
def _kumeleri_temizle(kumeler, price_low, price_high):
    silinecekler = [anahtar for anahtar in kumeler if price_low <= anahtar[0] <= price_high]
    for anahtar in silinecekler:
        del kumeler[anahtar]

# ==========================================
# 1e) OI AZALIŞINI ORANTILI YANSITMA
# ==========================================
def _kumeleri_oransal_kucult(kumeler, kucultme_orani):
    oran = min(max(kucultme_orani, 0.0), 1.0)
    if oran <= 0:
        return
    kalan_carpan = 1.0 - oran
    for anahtar in kumeler:
        kumeler[anahtar] *= kalan_carpan

# ==========================================
# 1c) PENCERE BOYUNCA İLERLEYİP KÜMELERİ BİRİKTİR
# ==========================================
from datetime import timedelta

def pencere_kumeleri_biriktir(df, saat_penceresi, oi_kolonu='oi_linear_btc', temizleme_aktif=True):
    if df.empty or len(df) < 2:
        return {}

    pencere_sonu = df['timestamp'].max()
    pencere_basi_zamani = pencere_sonu - timedelta(hours=saat_penceresi)
    pencere_ici = df[df['timestamp'] > pencere_basi_zamani]
    if pencere_ici.empty:
        return {}

    ilk_index = pencere_ici.index.min()
    baslangic_index = max(ilk_index - 1, df.index.min())  # bir önceki satır = anchor

    toplam_kumeler = {}
    onceki = df.loc[baslangic_index]
    for i in range(baslangic_index + 1, df.index.max() + 1):
        if i not in df.index:
            continue
        satir = df.loc[i]

        if temizleme_aktif and pd.notna(satir.get('price_low')) and pd.notna(satir.get('price_high')):
            _kumeleri_temizle(toplam_kumeler, satir['price_low'], satir['price_high'])

        oi_simdi = satir[oi_kolonu]
        oi_once = onceki[oi_kolonu]

        if pd.isna(oi_simdi) or pd.isna(oi_once):
            onceki = satir
            continue

        delta_oi = oi_simdi - oi_once

        if delta_oi > 0:
            cvd_perp_delta = satir['cvd_perp_btc'] - onceki['cvd_perp_btc']
            yeni_kumeler = delta_kumeleri_hesapla(
                delta_oi=delta_oi,
                acilis_fiyati=satir['price'],
                cvd_perp_delta=cvd_perp_delta,
                funding_pct=satir['funding_pct'],
            )
            for anahtar, miktar in yeni_kumeler.items():
                toplam_kumeler[anahtar] = toplam_kumeler.get(anahtar, 0.0) + miktar

        elif delta_oi < 0 and onceki[oi_kolonu] > 0:
            _kumeleri_oransal_kucult(toplam_kumeler, abs(delta_oi) / onceki[oi_kolonu])

        onceki = satir

    return toplam_kumeler

# ==========================================
# 1f) HEPSİNİ BİRLEŞTİR — DIŞA AÇILAN ASIL FONKSİYON
# ==========================================
KATMANLAR = {
    'linear': 'oi_linear_btc',
    'inverse': 'oi_inverse_btc',
}
PENCERELER = (12, 24)  # saat

def format_usd_kisaltma(deger):
    deger = abs(deger)
    if deger >= 1_000_000_000:
        return f"{deger/1_000_000_000:.2f}B"
    if deger >= 1_000_000:
        return f"{deger/1_000_000:.1f}M"
    if deger >= 1_000:
        return f"{deger/1_000:.1f}K"
    return f"{deger:,.0f}"

def tum_haritalari_hesapla(db_path=None):
    from db import load_history, DB_FILE
    df = load_history(db_path or DB_FILE)

    katmanlar = {}
    guncel_oi = {}
    for katman_adi, oi_kolonu in KATMANLAR.items():
        katmanlar[katman_adi] = {}
        for saat in PENCERELER:
            katmanlar[katman_adi][saat] = pencere_kumeleri_biriktir(
                df, saat_penceresi=saat, oi_kolonu=oi_kolonu, temizleme_aktif=True
            )
        if not df.empty and oi_kolonu in df.columns:
            son_deger = df[oi_kolonu].iloc[-1]
            guncel_oi[katman_adi] = float(son_deger) if pd.notna(son_deger) else None
        else:
            guncel_oi[katman_adi] = None

    return {'katmanlar': katmanlar, 'guncel_oi': guncel_oi}

def harita_ozeti_yazdir(sonuc, guncel_fiyat=None):
    katmanlar = sonuc['katmanlar']
    guncel_oi = sonuc.get('guncel_oi', {})

    for katman_adi, pencereler in katmanlar.items():
        oi_btc = guncel_oi.get(katman_adi)
        if oi_btc is not None and guncel_fiyat:
            oi_usd = oi_btc * guncel_fiyat
            print(f"\n=== {katman_adi.upper()} — şu anki toplam OI: ${format_usd_kisaltma(oi_usd)} ===")
        elif oi_btc is not None:
            print(f"\n=== {katman_adi.upper()} — şu anki toplam OI: {oi_btc:,.2f} BTC (fiyat alınamadı, $ dönüşümü yapılamadı) ===")
        else:
            print(f"\n=== {katman_adi.upper()} — şu anki toplam OI: veri yok ===")

        for saat, kumeler in pencereler.items():
            toplam_btc = sum(kumeler.values())
            toplam_gosterim = f"${format_usd_kisaltma(toplam_btc * guncel_fiyat)}" if guncel_fiyat else f"{toplam_btc:,.2f} BTC (fiyat yok)"
            print(f"  📍 [{saat}s] {len(kumeler)} küme, toplam {toplam_gosterim}")
            if not kumeler:
                continue
            en_buyukler = sorted(kumeler.items(), key=lambda kv: -kv[1])[:3]
            for (fiyat, yon), miktar in en_buyukler:
                mesafe = f" ({'-' if yon=='long' else '+'}{abs(fiyat-guncel_fiyat)/guncel_fiyat*100:.2f}%)" if guncel_fiyat else ""
                miktar_gosterim = f"${format_usd_kisaltma(miktar * guncel_fiyat)}" if guncel_fiyat else f"{miktar:,.2f} BTC (fiyat yok)"
                print(f"      {yon:<6} ${fiyat:>10,.2f}{mesafe}  {miktar_gosterim}")

def en_buyuk_likidasyonlar(sonuc, guncel_fiyat=None):
    en_buyuk = {'long': None, 'short': None}
    for katman_adi, pencereler in sonuc.get('katmanlar', {}).items():
        for saat, kumeler in pencereler.items():
            for (fiyat, yon), miktar_btc in kumeler.items():
                mevcut = en_buyuk[yon]
                if mevcut is None or miktar_btc > mevcut['miktar_btc']:
                    mevcut = {
                        'katman': katman_adi,
                        'pencere': saat,
                        'fiyat': float(fiyat),
                        'miktar_btc': float(miktar_btc),
                    }
                    if guncel_fiyat:
                        mevcut['miktar_usd'] = mevcut['miktar_btc'] * guncel_fiyat
                    en_buyuk[yon] = mevcut
    return en_buyuk

# ==========================================
# DOĞRUDAN ÇALIŞTIRMA (python likidasyon.py)
# ==========================================

# ==========================================
# SİNYALE HEDEF VERME
# ==========================================
def hedef_belirle(yon, tp_tampon=200, saat_penceresi=12, db_path=None):
    from db import load_history, DB_FILE
    df = load_history(db_path or DB_FILE)

    birlesik = {}
    for oi_kolonu in KATMANLAR.values():
        kumeler = pencere_kumeleri_biriktir(df, saat_penceresi=saat_penceresi, oi_kolonu=oi_kolonu, temizleme_aktif=True)
        for anahtar, miktar in kumeler.items():
            birlesik[anahtar] = birlesik.get(anahtar, 0.0) + miktar

    hedef_yon = 'short' if yon == 'long' else 'long'
    adaylar = {anahtar: miktar for anahtar, miktar in birlesik.items() if anahtar[1] == hedef_yon}

    if not adaylar:
        return None

    (hedef_fiyat, _), hedef_miktar = max(adaylar.items(), key=lambda kv: kv[1])

    tp = hedef_fiyat - tp_tampon if yon == 'long' else hedef_fiyat + tp_tampon

    return {
        'hedef_fiyat': hedef_fiyat,
        'hedef_miktar_btc': hedef_miktar,
        'tp': tp,
        'yon': yon,
    }


if __name__ == "__main__":
    from borsa import get_btc_price

    print("📍 Likidasyon haritaları hesaplanıyor (mevcut oi_funding_history.db'den, linear/inverse × 12s/24s)...\n")
    guncel_fiyat = get_btc_price()
    if guncel_fiyat <= 0:
        print("  ⚠️ Güncel fiyat çekilemedi, $ dönüşümü ve mesafe (%) bilgisi olmadan devam ediliyor.")
        guncel_fiyat = None

    sonuc = tum_haritalari_hesapla()
    harita_ozeti_yazdir(sonuc, guncel_fiyat=guncel_fiyat)

    toplam_kume = sum(len(k) for pencereler in sonuc['katmanlar'].values() for k in pencereler.values())
    if toplam_kume == 0:
        print("\n  ℹ️ Hiçbir haritada küme yok -- muhtemelen DB'de henüz linear/inverse OI "
              "verisi biriken yeterli geçmiş (en az birkaç saat) yok, ya da botu daha "
              "yeni bu sürüme güncellediysen eski satırlarda bu kolonlar boş. Bot birkaç "
              "saat daha çalışsın, sonra tekrar dene.")