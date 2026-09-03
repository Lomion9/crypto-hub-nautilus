import os
import requests

# ==========================================
# TELEGRAM BİLDİRİMİ
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_LAST_TELEGRAM_DURUMLAR = {}  # {tf: son gönderilen genel_durum}

def should_send_telegram(tf_sonuclari):
    """tf_sonuclari: {tf: {'genel_durum': ..., 'telegram_uygun': ..., 'hedef_cok_yakin': ..., ...}}.
    Sadece 2sa/1sa/4sa işlenir. Bir timeframe'in genel_durum'u 'İşlem Açma'/'Veri
    Bekleniyor' DIŞINDA bir şey gösterip kendi son gönderilen durumundan
    FARKLI OLDUĞUNDA VE 'telegram_uygun' VE 'hedef_cok_yakin' DEĞİLSE True döner."""
    global _LAST_TELEGRAM_DURUMLAR
    gonder = False
    for tf, sonuc in tf_sonuclari.items():
        if tf not in ("1sa", "2sa", "4sa"):
            continue
        gd = sonuc['genel_durum']
        onceki = _LAST_TELEGRAM_DURUMLAR.get(tf)
        if gd in ("İşlem Açma", "Veri Bekleniyor"):
            _LAST_TELEGRAM_DURUMLAR[tf] = gd
            continue
        if gd != onceki:
            if sonuc.get('telegram_uygun') and not sonuc.get('hedef_cok_yakin'):
                gonder = True
                _LAST_TELEGRAM_DURUMLAR[tf] = gd
    return gonder

def send_telegram_message(text, parse_mode="HTML"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("  ⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil, mesaj gönderilmedi.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=10)
        if not resp.ok:
            print(f"  ❌ Telegram gönderim hatası: {resp.text}")
    except Exception as e:
        print(f"  ❌ Telegram gönderim hatası: {e}")

def build_telegram_report(failed_borsalar, total_oi, global_funding, price, cvd_spot, cvd_perp, fund_status, tf_sonuclari, kapanan_islemler, buyuk_likidasyonlar=None, premium_pct=None, arb_risk_durumu=None):
    """Sadeleştirilmiş rapor: sadece 2sa/1sa/4sa, sadece piyasa durumu (genel_durum),
    yön (long/short) ve TP noktası -- Trap kategorilerinde ise beklenecek tetik
    noktası ve tetiklenince açılacak TP. OI/funding/fiyat/CVD/likidasyon
    haritası/premium artık Telegram'a basılmıyor (konsol çıktısında hâlâ mevcut)."""
    from sinyal import TRAP_KATEGORILERI, _islem_yonu

    lines = []
    if failed_borsalar:
        lines.append(f"⚠️ <b>Bağlantı Sağlanamayan Borsalar:</b> {', '.join(failed_borsalar)}")
        lines.append("")

    for tf, sonuc in tf_sonuclari.items():
        if tf not in ("1sa", "2sa", "4sa"):
            continue
        genel = sonuc['genel_durum']
        if genel in ("İşlem Açma", "Veri Bekleniyor"):
            continue

        lines.append(f"[{tf}] <b>{genel}</b>")

        if genel in TRAP_KATEGORILERI:
            gercek_yon = _islem_yonu(genel)
            bekleme_tetik_fiyat = sonuc.get('bekleme_tetik_fiyat')
            hedef = sonuc.get('hedef')
            if bekleme_tetik_fiyat:
                lines.append(f"    ⏳ Beklenecek tetik: ${bekleme_tetik_fiyat:,.2f}")
            if hedef and gercek_yon:
                lines.append(f"    🎯 Tetiklenince: {gercek_yon.upper()}  |  TP: ${hedef['tp']:,.2f}")
        else:
            yon = _islem_yonu(genel)
            hedef = sonuc.get('hedef')
            if yon and hedef:
                lines.append(f"    📈 Yön: {yon.upper()}  |  TP: ${hedef['tp']:,.2f}")

    if kapanan_islemler:
        lines.append("")
        lines.append("💰 <b>KAPANAN SİNYALLER</b>")
        for tf, k in kapanan_islemler.items():
            if tf not in ("1sa", "2sa", "4sa"):
                continue
            lines.append(f"[{tf}] ({k['kapanis_tipi']}) {k['sinyal']} ({k['yon']}) -> %{k['kar_yuzde']:+.2f}")

    return "\n".join(lines)