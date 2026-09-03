import numpy as np
from datetime import datetime

from config import CONFIG

# ==========================================
# SİNYAL MANTIĞI (eşik, yön, genel durum, kaldıraç/absorption yorumlama)
# ==========================================

def funding_status(current_funding):
    current_funding = float(current_funding)
    esik = CONFIG['funding_thresholds']['extreme_pct']
    if current_funding > esik:
        return "Aşırı Pozitif"
    elif current_funding > 0.0000:
        return "Pozitif"
    elif current_funding < -esik:
        return "Aşırı Negatif"
    elif current_funding < 0.0000:
        return "Negatif"
    return "Nötr"

def arb_risk_durumu(premium_pct):
    """Binance perp mark price'ının spot endeksten sapma yüzdesine (premium_pct,
    bkz. borsa.get_binance_premium_index) göre bir arb-riski etiketi döndürür.
    BİLİNÇLİ TASARIM: bu, genel_durum()'un döndürdüğü stringe KARIŞTIRILMIYOR --
    o string _islem_yonu, should_send_telegram (dedup), DB karşılaştırmaları
    gibi birçok yerde BİREBİR eşleşmeyle kullanılıyor; içine yeni bir etiket
    eklemek (ör. sonuna ' ⚠️ Arb Riski' eklemek) o eşleşmeleri sessizce kırar.
    Bunun yerine ayrı, paralel bir bilgi olarak (log_snapshot'ın döndürdüğü
    sonuc sözlüğünde ayrı bir alan olarak) taşınıyor.
    premium_pct None ise (bu tur çekilemediyse) "Veri Yok" döner."""
    if premium_pct is None:
        return "Veri Yok"
    esik = CONFIG.get('premium_thresholds', {}).get('extreme_pct', 0.10)
    if premium_pct > esik:
        return "Yüksek (Perp Primli)"
    elif premium_pct < -esik:
        return "Yüksek (Perp İskontolu)"
    return "Normal"

def _periyot_durumu(df_veri, mevcut_deger, periods, esik_pct, kolon):
    """ROLLING-MIN / ROLLING-MAX + EŞİK: son N periyotluk pencerenin dip/tepesine
    olan mesafelerden büyük olanı esik_pct'yi geçmiyorsa Nötr döner (akümülasyon/
    dağıtım tespiti Nötr'e ihtiyaç duyar); geçiyorsa hangi mesafe büyükse o yön
    (Artıyor/Düşüyor) döner. ŞU AN SADECE OI İÇİN KULLANILIYOR (compute_gecici_oi_esigi
    ile birlikte) -- fiyat için artık _periyot_durumu_fiyat (kırılım bazlı) kullanılıyor."""
    if len(df_veri) < periods:
        return "Veri Bekleniyor"
    pencere = df_veri[kolon].iloc[-periods:]
    if pencere.isna().any() or (pencere <= 0).any() or not mevcut_deger:
        return "Veri Bekleniyor"

    pencere_min = pencere.min()
    pencere_max = pencere.max()
    artis_pct = (mevcut_deger - pencere_min) / pencere_min * 100
    dusus_pct = (pencere_max - mevcut_deger) / pencere_max * 100

    if artis_pct <= esik_pct and dusus_pct <= esik_pct:
        return "Nötr"
    return "Artıyor" if artis_pct >= dusus_pct else "Düşüyor"

def _periyot_durumu_fiyat(df_veri, mevcut_ohlc, periods):
    """FİYAT İÇİN MUM-MUM (candle-vs-candle, higher-high/higher-low) KIRILIM
    MANTIĞI -- eski 'tek nokta vs önceki pencere' testinin yerine geçti.
    'Mevcut mum' (şu an kapanan `periods` adet 15dk barı: df_veri'nin son
    periods-1 satırı + mevcut_ohlc'nin kendisi) ile 'önceki mum' (ondan
    önceki periods adet 15dk barı, tamamen df_veri'den) karşılaştırılır:

      mevcut_low > onceki_low  VE  mevcut_high >= onceki_high  -> Artıyor
        (dip de yükseldi, tepe de en az korundu -- net yükseliş yapısı)
      mevcut_high < onceki_high  VE  mevcut_low <= onceki_low  -> Düşüyor
        (tepe de düştü, dip de en fazla korundu -- net düşüş yapısı)
      aksi halde (DARALMA: mevcut_low > onceki_low AMA mevcut_high < onceki_high;
                  ya da GENİŞLEME: mevcut_low < onceki_low VE mevcut_high > onceki_high)
        -> Nötr (çelişkili/kararsız yapı, tek taraflı kırılım artık yeterli sayılmıyor)

    SABİT MUM (non-overlapping): bu fonksiyon sadece sinir_saatleri sınırlarında
    çağrılır, iki pencere üst üste binmez -- rolling değil.

    Toplam 2*periods-1 satır geçmiş (df_veri) gerektirir: önceki mum için
    periods adet, mevcut mumun geçmiş kısmı için periods-1 adet (+1 mevcut_ohlc)."""
    gerekli = 2 * periods - 1
    if len(df_veri) < gerekli:
        return "Veri Bekleniyor"
    if mevcut_ohlc.get('high') is None or mevcut_ohlc.get('low') is None:
        return "Veri Bekleniyor"

    if periods > 1:
        mevcut_gecmis = df_veri.iloc[-(periods - 1):]
        onceki_pencere = df_veri.iloc[-gerekli:-(periods - 1)]
    else:
        mevcut_gecmis = df_veri.iloc[0:0]  # boş -- mevcut mum sadece mevcut_ohlc'nin kendisi
        onceki_pencere = df_veri.iloc[-periods:]

    if mevcut_gecmis['price_high'].isna().any() or mevcut_gecmis['price_low'].isna().any() \
       or onceki_pencere['price_high'].isna().any() or onceki_pencere['price_low'].isna().any():
        return "Veri Bekleniyor"

    mevcut_high = max(mevcut_gecmis['price_high'].max(), mevcut_ohlc['high']) if periods > 1 else mevcut_ohlc['high']
    mevcut_low = min(mevcut_gecmis['price_low'].min(), mevcut_ohlc['low']) if periods > 1 else mevcut_ohlc['low']
    onceki_high = onceki_pencere['price_high'].max()
    onceki_low = onceki_pencere['price_low'].min()

    if mevcut_low > onceki_low and mevcut_high >= onceki_high:
        return "Artıyor"
    elif mevcut_high < onceki_high and mevcut_low <= onceki_low:
        return "Düşüyor"
    return "Nötr"

def _periyot_durumu_oi(df_veri, mevcut_deger, periods):
    """OI İÇİN AYNI SAF KIRILIM MANTIĞI -- _periyot_durumu_fiyat ile birebir
    simetrik. OI'de gerçek intra-periyot high/low yok (her 15dk'lık satır
    borsalardan çekilen TEK bir REST anlık görüntüsü) -- bu yüzden 'önceki
    mum'un high/low'u, son `periods` adet 15dk'lık OI NOKTA ÖRNEĞİNİN
    kendisinin min/max'ı olarak kuruluyor (yani N tane nokta, bir mumun
    içindeki N tane tick gibi ele alınıyor). Mevcut OI bu aralığın üstüne
    çıkarsa Artıyor, altına inerse Düşüyor, arada kalırsa Nötr. SADECE
    15dk'DAN BÜYÜK timeframe'ler için kullanılır -- 15dk'da periods=1
    olduğundan 'önceki mum' tek bir noktaya iner (high=low=o nokta), bu da
    testi anlamsızlaştırır (her fark Artıyor/Düşüyor sayılır, Nötr hiç
    çıkmaz); 15dk hâlâ compute_gecici_oi_esigi + _periyot_durumu (%-eşikli)
    kullanmaya devam ediyor."""
    if len(df_veri) < periods:
        return "Veri Bekleniyor"
    pencere = df_veri['oi_btc'].iloc[-periods:]
    if pencere.isna().any() or (pencere <= 0).any() or not mevcut_deger:
        return "Veri Bekleniyor"

    onceki_high = pencere.max()
    onceki_low = pencere.min()

    if mevcut_deger > onceki_high:
        return "Artıyor"
    elif mevcut_deger < onceki_low:
        return "Düşüyor"
    return "Nötr"

def compute_gecici_oi_esigi(df_veri):
    """GEÇİCİ OI eşiği yöntemi (ileride değişecek): en yakın hafta sonu
    gününün 15dk'lık OI okumaları arasındaki ardışık yüzde değişimlerin
    MUTLAK toplamı, o günkü veri sayısının 2 katına bölünür. Sonuç, TÜM
    timeframe'ler için TEK/DÜZ bir eşik olarak kullanılır -- periyot
    sayısına göre ölçeklenmiyor, bilinçli bir sadeleştirme. Yeterli hafta
    sonu verisi yoksa None döner."""
    if 'tarih' not in df_veri.columns or len(df_veri) == 0:
        return None

    gunler = sorted(df_veri['tarih'].unique(), key=lambda t: datetime.strptime(t, '%d.%m.%Y'))
    haftasonu_gunler = [g for g in reversed(gunler) if datetime.strptime(g, '%d.%m.%Y').weekday() >= 5]

    for gun in haftasonu_gunler:
        gun_df = df_veri[df_veri['tarih'] == gun].sort_values('timestamp')
        oi_degerleri = gun_df['oi_btc'].dropna()
        oi_degerleri = oi_degerleri[oi_degerleri > 0]
        n = len(oi_degerleri)
        if n < 2:
            continue

        toplam_degisim_pct = 0.0
        onceki = None
        for deger in oi_degerleri:
            if onceki is not None and onceki > 0:
                toplam_degisim_pct += abs((deger - onceki) / onceki * 100)
            onceki = deger

        return toplam_degisim_pct / (2 * n)

    return None

def _rolling_hareket_mesafesi(seri, periods):
    """Bir kolon (Series, kronolojik sıralı, POZİSYONEL/0-tabanlı index) için,
    her geçerli noktada 'son N periyotluk pencerenin dip/tepesine olan en büyük
    mesafe' değerini (pozisyon, mesafe) çiftleri olarak döndürür — _periyot_durumu
    ile BİREBİR aynı ölçütle. compute_adaptive_tf_thresholds (şu an KULLANILMIYOR,
    bkz. aşağıdaki not) tarafından kullanılıyor."""
    sonuc = []
    for i in range(periods, len(seri)):
        pencere = seri.iloc[i - periods:i]
        mevcut = seri.iloc[i]
        if pencere.isna().any() or (pencere <= 0).any() or not mevcut or mevcut <= 0:
            continue
        pmin, pmax = pencere.min(), pencere.max()
        artis = (mevcut - pmin) / pmin * 100
        dusus = (pmax - mevcut) / pmax * 100
        sonuc.append((i, max(artis, dusus)))
    return sonuc

def son_tf_genel_durumlar(conn, kaynak_tf, n):
    """Şu ana kadar (bu turda kaynak_tf için yeni bir kayıt yazıldıysa o da dahil —
    aynı bağlantıda commit beklemeden görünür) yazılmış son n adet
    durum_{kaynak_tf}.genel_durum değerini, en yeniden en eskiye döndürür."""
    rows = conn.execute(
        f"SELECT genel_durum FROM durum_{kaynak_tf} ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return [r[0] for r in rows]

def cvd_durumu(cvd_spot, cvd_perp):
    spot_yon = "Long" if cvd_spot > 0 else ("Short" if cvd_spot < 0 else "Nötr")
    perp_yon = "Long" if cvd_perp > 0 else ("Short" if cvd_perp < 0 else "Nötr")

    if spot_yon == "Nötr" or perp_yon == "Nötr":
        etiket = "Zayıf Sinyal"
    elif spot_yon == perp_yon:
        etiket = "Uyumlu"
    else:
        etiket = "Diverjans"

    return f"Spot {spot_yon} / Perp {perp_yon} ({etiket})"

def genel_durum(fund_status, oi_status, price_status, cvd_spot, cvd_perp):
    fund_positive = (fund_status == "Aşırı Pozitif")
    fund_negative = (fund_status == "Aşırı Negatif")

    # Long taraf: funding Aşırı Pozitif -> longlar baskın/kaldıraçlı
    long_trap = (fund_positive and oi_status == "Artıyor" and price_status == "Düşüyor")
    long_squeeze = (fund_positive and oi_status == "Düşüyor" and price_status == "Düşüyor")

    # Short taraf: funding Aşırı Negatif -> shortlar baskın/kaldıraçlı (long tarafın aynası)
    short_trap = (fund_negative and oi_status == "Artıyor" and price_status == "Artıyor")
    short_squeeze = (fund_negative and oi_status == "Düşüyor" and price_status == "Artıyor")

    if short_squeeze:
        return "Sağlıklı Long" if cvd_spot > 0 else "Short Squeeze"

    if long_squeeze:
        absorption_riski = cvd_spot > 0 and cvd_spot > abs(cvd_perp)
        if absorption_riski:
            return "İşlem Açma"
        return "Sağlıklı Short" if cvd_spot < 0 else "Long Squeeze"

    if long_trap:
        absorption_riski = cvd_spot > 0 and cvd_spot > abs(cvd_perp)
        if absorption_riski:
            return "İşlem Açma"
        return "Long Trap"

    if short_trap:
        dagitim_riski = cvd_spot < 0 and abs(cvd_spot) > abs(cvd_perp)
        if dagitim_riski:
            return "İşlem Açma"
        return "Short Trap"

    if price_status == "Nötr" and oi_status == "Artıyor":
        if cvd_spot > 0 and cvd_spot >= abs(cvd_perp):
            return "Akümülasyon"
        if cvd_spot < 0 and abs(cvd_spot) >= abs(cvd_perp):
            return "Dağıtım"

    return "İşlem Açma"

def _islem_yonu(genel_durum_deger):
    long_sinyaller = {"Sağlıklı Long", "Short Squeeze", "Short Trap", "Akümülasyon"}
    short_sinyaller = {"Sağlıklı Short", "Long Squeeze", "Long Trap", "Dağıtım"}
    if genel_durum_deger in long_sinyaller:
        return 'long'
    if genel_durum_deger in short_sinyaller:
        return 'short'
    return None

TRAP_KATEGORILERI = {"Long Trap": "long", "Short Trap": "short"}

def sinyal_performans_guncelle(conn, tf, genel_durum_deger, price, tarih_str, saat_str, kapanis_esigi=3, tp=None):
    """tp: yeni AÇILACAK bir pozisyon için hedef_belirle(gerçek_yön)'den gelen
    TP fiyatı -- Trap kategorileri de dahil TÜM kategoriler için doğrudan
    _islem_yonu'na göre hedef belirlenir, ayrı bir bekleme/tersine-hedef fazı
    yok; tespit edilir edilmez pozisyon hemen açılır."""

    def aktif_durumu_kaydet(aktif, sayac):
        conn.execute(f"DELETE FROM aktif_islem_{tf} WHERE id=1")
        if aktif is not None:
            conn.execute(
                f"INSERT INTO aktif_islem_{tf} (id, genel_durum, giris_fiyat, giris_tarih, giris_saat, farkli_sayac, hedef_tp) VALUES (1,?,?,?,?,?,?)",
                (aktif['genel_durum'], aktif['giris_fiyat'], aktif['giris_tarih'], aktif['giris_saat'], sayac, aktif.get('hedef_tp'))
            )

    def yeni_baslat(gd, giris_fiyati=None):
        return {'genel_durum': gd, 'giris_fiyat': giris_fiyati if giris_fiyati is not None else price,
                'giris_tarih': tarih_str, 'giris_saat': saat_str, 'hedef_tp': tp}

    def kapanisi_kaydet(aktif, cikis_fiyat, kapanis_tipi):
        giris_fiyat = aktif['giris_fiyat']
        yon = _islem_yonu(aktif['genel_durum'])
        ham_degisim = (cikis_fiyat - giris_fiyat) / giris_fiyat * 100
        kar_yuzde = -ham_degisim if yon == 'short' else ham_degisim

        kapanan = {
            'kapanis_tarih': tarih_str, 'kapanis_saat': saat_str,
            'sinyal': aktif['genel_durum'], 'yon': yon or 'belirsiz',
            'giris_tarih': aktif['giris_tarih'], 'giris_saat': aktif['giris_saat'],
            'giris_fiyat': giris_fiyat, 'cikis_fiyat': cikis_fiyat, 'kar_yuzde': kar_yuzde,
            'kapanis_tipi': kapanis_tipi,
        }
        conn.execute(
            f"INSERT INTO sinyal_{tf} (kapanis_tarih, kapanis_saat, sinyal, yon, giris_tarih, giris_saat, giris_fiyat, cikis_fiyat, kar_yuzde, kapanis_tipi) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (kapanan['kapanis_tarih'], kapanan['kapanis_saat'], kapanan['sinyal'], kapanan['yon'],
             kapanan['giris_tarih'], kapanan['giris_saat'], kapanan['giris_fiyat'], kapanan['cikis_fiyat'],
             kapanan['kar_yuzde'], kapanan['kapanis_tipi'])
        )
        return kapanan

    def yeni_sinyali_islem_baslat_veya_bekle(gd):
        if not gd.startswith("İşlem Açma"):
            aktif_durumu_kaydet(yeni_baslat(gd), 0)

    row = conn.execute(
        f"SELECT genel_durum, giris_fiyat, giris_tarih, giris_saat, farkli_sayac, hedef_tp FROM aktif_islem_{tf} WHERE id=1"
    ).fetchone()

    if row is None:
        yeni_sinyali_islem_baslat_veya_bekle(genel_durum_deger)
        return None

    aktif = {'genel_durum': row[0], 'giris_fiyat': row[1], 'giris_tarih': row[2], 'giris_saat': row[3], 'hedef_tp': row[5]}
    sayac = row[4]

    aktif_yon = _islem_yonu(aktif['genel_durum'])
    if aktif['hedef_tp'] is not None and aktif_yon is not None:
        hedefe_ulasti = (
            (aktif_yon == 'long' and price >= aktif['hedef_tp']) or
            (aktif_yon == 'short' and price <= aktif['hedef_tp'])
        )
        if hedefe_ulasti:
            kapanan = kapanisi_kaydet(aktif, aktif['hedef_tp'], 'Hedefe Ulaşıldı (TP)')
            yeni_sinyali_islem_baslat_veya_bekle(genel_durum_deger)
            return kapanan

    if genel_durum_deger == aktif['genel_durum']:
        aktif_durumu_kaydet(aktif, 0)
        return None

    sayac += 1
    if sayac < kapanis_esigi:
        aktif_durumu_kaydet(aktif, sayac)
        return None

    kapanan = kapanisi_kaydet(aktif, price, 'Sinyal Değişimi')
    yeni_sinyali_islem_baslat_veya_bekle(genel_durum_deger)
    return kapanan

def _periyot_cvd_degisimi(df_veri, current_cvd_spot, current_cvd_perp, periods, tarih_str):
    bugun_df = df_veri[df_veri['tarih'] == tarih_str]
    if bugun_df.empty:
        return None, None

    if len(df_veri) >= periods and df_veri.iloc[-periods]['tarih'] == tarih_str:
        ref = df_veri.iloc[-periods]
    else:
        ref = bugun_df.iloc[0]

    return current_cvd_spot - ref['cvd_spot_btc'], current_cvd_perp - ref['cvd_perp_btc']

def compute_adaptive_tf_thresholds(df_veri):
    """ARTIK ÇAĞRILMIYOR (main.py bunun yerine compute_gecici_oi_esigi +
    _periyot_durumu_fiyat kullanıyor) -- fonksiyon ileride ihtiyaç olursa
    diye kod tabanında referans olarak bırakıldı, silinmedi."""
    ac = CONFIG.get('adaptive', {})
    if not ac.get('enabled', True):
        return None
    if 'tarih' not in df_veri.columns or len(df_veri) == 0:
        return None

    quiet_days_n = ac.get('quiet_days', 3)
    lookback_days = ac.get('lookback_days', 7)
    p = ac.get('noise_percentile', 90)

    gunler = sorted(df_veri['tarih'].unique(), key=lambda t: datetime.strptime(t, '%d.%m.%Y'))
    if len(gunler) < quiet_days_n:
        return None
    gunler = gunler[-lookback_days:]

    sonuc = {}
    for tf, tf_conf in CONFIG['timeframes'].items():
        periods = tf_conf['periods']

        oi_mesafe_pos = _rolling_hareket_mesafesi(df_veri['oi_btc'], periods)
        price_mesafe_pos = _rolling_hareket_mesafesi(df_veri['price'], periods)

        gun_gurultu = {}
        for gun in gunler:
            pos_set = set(df_veri.index[df_veri['tarih'] == gun])

            oi_mesafeler = [m for pos, m in oi_mesafe_pos if pos in pos_set]
            price_mesafeler = [m for pos, m in price_mesafe_pos if pos in pos_set]
            if len(oi_mesafeler) < 3 or len(price_mesafeler) < 3:
                continue
            gun_gurultu[gun] = {
                'oi': float(np.percentile(oi_mesafeler, p)),
                'price': float(np.percentile(price_mesafeler, p)),
            }

        if len(gun_gurultu) < quiet_days_n:
            sonuc[tf] = None
            continue

        en_sakin_oi = sorted(gun_gurultu.values(), key=lambda v: v['oi'])[:quiet_days_n]
        en_sakin_price = sorted(gun_gurultu.values(), key=lambda v: v['price'])[:quiet_days_n]
        sonuc[tf] = {
            'oi_pct': float(np.mean([v['oi'] for v in en_sakin_oi])),
            'price_pct': float(np.mean([v['price'] for v in en_sakin_price])),
        }
    return sonuc