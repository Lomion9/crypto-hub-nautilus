import os
import sqlite3

import requests

# ==========================================
# TELEGRAM BİLDİRİMİ
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def _telegram_from_settings():
    try:
        from db import DB_FILE

        conn = sqlite3.connect(DB_FILE)
        try:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                ("telegram_bot_token", "telegram_chat_id"),
            ).fetchall()
        finally:
            conn.close()
        data = {key: (value or "").strip() for key, value in rows}
        return data.get("telegram_bot_token", ""), data.get("telegram_chat_id", "")
    except Exception:
        return "", ""


def _telegram_credentials():
    token, chat_id = _telegram_from_settings()
    if not token:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "") or TELEGRAM_BOT_TOKEN
    if not chat_id:
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "") or TELEGRAM_CHAT_ID
    return token, chat_id

_LAST_TELEGRAM_DURUMLAR = {}  # {tf: son gönderilen genel_durum}

def should_send_telegram(tf_sonuclari):
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
    token, chat_id = _telegram_credentials()
    if not token or not chat_id:
        print("  ⚠️ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID tanımlı değil, mesaj gönderilmedi.")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=10)
        if not resp.ok:
            print(f"  ❌ Telegram gönderim hatası: {resp.text}")
    except Exception as e:
        print(f"  ❌ Telegram gönderim hatası: {e}")

def build_telegram_report(failed_borsalar, total_oi, global_funding, price, cvd_spot, cvd_perp, fund_status, tf_sonuclari, kapanan_islemler, buyuk_likidasyonlar=None, premium_pct=None, arb_risk_durumu=None):
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