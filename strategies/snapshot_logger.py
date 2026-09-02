from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.model import DataType
from nautilus_trader.model.identifiers import ClientId

from data_types.market_snapshot import MarketSnapshotData
from data_types.cvd_update import CVDUpdate
from data_types.liquidation_event import LiquidationEvent
from main import log_snapshot
from telegram import should_send_telegram, send_telegram_message, build_telegram_report
from likidasyon import tum_haritalari_hesapla, en_buyuk_likidasyonlar, format_usd_kisaltma


class SnapshotLoggerStrategy(Strategy):
    def on_start(self):
        self.subscribe_data(
            DataType(MarketSnapshotData),
            client_id=ClientId("MARKET_SNAPSHOT"),
        )
        # CVDUpdate'in dış bir data client'ı yok -- BinanceFeedLoggerStrategy'nin
        # doğrudan msgbus.publish ile yayınladığı veriyi burada msgbus.subscribe
        # ile dinliyoruz, DataEngine/subscribe_data'yı hiç devreye sokmuyoruz.
        self.msgbus.subscribe(topic="cvd_update", handler=self.on_cvd_update)
        self.subscribe_data(
            DataType(LiquidationEvent),
            client_id=ClientId("LIQUIDATION_WS"),
        )
        self._latest_cvd_perp = None
        self._son_likidasyon_haritasi = None  # tum_haritalari_hesapla() çıktısının önbelleği
        self.log.info("Abonelikler başladı.")

    def on_cvd_update(self, data: CVDUpdate):
        self._latest_cvd_perp = data.cvd

    def on_data(self, data):
        if isinstance(data, LiquidationEvent):
            self.log.info(
                f"[GERÇEK LİKİDASYON] ({data.kontrat_tipi}) {data.yon} likide edildi "
                f"-> ${data.notional_usd:,.0f} @ ${data.fiyat:,.2f}"
            )
            self._likidasyonu_karsilastir_ve_kaydet(data)
            return

        if not isinstance(data, MarketSnapshotData):
            return

        cvd_perp = self._latest_cvd_perp if self._latest_cvd_perp is not None else data.cvd_perp_btc

        ohlc = {
            'open': data.price_open,
            'high': data.price_high,
            'low': data.price_low,
            'close': data.price,
        }

        sonuc = log_snapshot(
            oi=data.oi_btc,
            funding=data.funding_pct,
            price=data.price,
            cvd_spot=data.cvd_spot_btc,
            cvd_perp=cvd_perp,
            ohlc=ohlc,
            oi_linear=data.oi_linear_btc,
            oi_inverse=data.oi_inverse_btc,
            premium_pct=data.premium_pct,
        )

        if sonuc['premium_pct'] is not None:
            self.log.info(f"Premium (Arb): %{sonuc['premium_pct']:+.4f} ({sonuc['arb_risk_durumu']})")

        for tf, sinyal in sonuc['tf_sonuclari'].items():
            self.log.info(
                f"[{tf}] OI: {sinyal['oi_durum']} | Fiyat: {sinyal['fiyat_durum']} | "
                f"CVD: {sinyal['cvd_durum']} | Sinyal: {sinyal['genel_durum']}"
            )

        for tf, kapanan in sonuc['kapanan_islemler'].items():
            if kapanan:
                self.log.info(
                    f"[{tf}] POZİSYON KAPANDI -> {kapanan['sinyal']} ({kapanan['yon']}) "
                    f"kar_yuzde={kapanan['kar_yuzde']:.2f}%"
                )

        buyuk_likidasyonlar = {'long': None, 'short': None}
        try:
            likidasyon_sonucu = tum_haritalari_hesapla()
            self._son_likidasyon_haritasi = likidasyon_sonucu  # kalibrasyon karşılaştırması için önbelleğe al
            buyuk_likidasyonlar = en_buyuk_likidasyonlar(likidasyon_sonucu, guncel_fiyat=data.price)
            for yon, etiket in [('long', 'Long likidasyonu'), ('short', 'Short likidasyonu')]:
                veri = buyuk_likidasyonlar.get(yon)
                if veri:
                    self.log.info(
                        f"📍 {etiket}: ${format_usd_kisaltma(veri['miktar_usd'])} @ ${veri['fiyat']:,.2f}"
                    )
                else:
                    self.log.info(f"📍 {etiket}: veri yok")
        except Exception as e:
            self.log.error(f"Likidasyon haritası hesaplama hatası: {e}")

        if should_send_telegram(sonuc['tf_sonuclari']):
            report_text = build_telegram_report(
                failed_borsalar=[],
                total_oi=data.oi_btc,
                global_funding=data.funding_pct,
                price=data.price,
                cvd_spot=data.cvd_spot_btc,
                cvd_perp=cvd_perp,
                fund_status=sonuc['funding_durum'],
                tf_sonuclari=sonuc['tf_sonuclari'],
                kapanan_islemler=sonuc['kapanan_islemler'],
                buyuk_likidasyonlar=buyuk_likidasyonlar,
                premium_pct=sonuc['premium_pct'],
                arb_risk_durumu=sonuc['arb_risk_durumu'],
            )
            try:
                send_telegram_message(report_text)
                self.log.info("Telegram raporu gönderildi.")
            except Exception as e:
                self.log.error(f"Telegram gönderim hatası: {e}")

    def _likidasyonu_karsilastir_ve_kaydet(self, event: LiquidationEvent):
        """Gerçek bir likidasyon event'i geldiğinde, en son hesaplanmış tahmini
        haritadaki (tüm katman/pencere kombinasyonları içinden) AYNI YÖNDEKİ
        fiyata en yakın kümeyi bulup farkı hesaplar ve gercek_likidasyon
        tablosuna kalıcı olarak kaydeder."""
        from db import DB_FILE, gercek_likidasyon_kaydet
        import sqlite3

        en_yakin_fiyat = None
        en_yakin_katman = None
        en_yakin_pencere = None
        en_yakin_mesafe = None

        if self._son_likidasyon_haritasi is not None:
            katmanlar = self._son_likidasyon_haritasi.get('katmanlar', {})
            for katman_adi, pencereler in katmanlar.items():
                for pencere, kumeler in pencereler.items():
                    for (fiyat, yon), miktar_btc in kumeler.items():
                        if yon != event.yon:
                            continue
                        mesafe = abs(float(fiyat) - event.fiyat)
                        if en_yakin_mesafe is None or mesafe < en_yakin_mesafe:
                            en_yakin_mesafe = mesafe
                            en_yakin_fiyat = float(fiyat)
                            en_yakin_katman = katman_adi
                            en_yakin_pencere = pencere

        if en_yakin_fiyat is not None:
            fark_yuzde = (event.fiyat - en_yakin_fiyat) / en_yakin_fiyat * 100
            self.log.info(
                f"    ↳ En yakın tahmin: ${en_yakin_fiyat:,.2f} ({en_yakin_katman}/{en_yakin_pencere}s)  "
                f"|  Fark: %{fark_yuzde:+.3f}"
            )
        else:
            self.log.info("    ↳ Karşılaştırılacak tahmini küme bulunamadı.")

        conn = sqlite3.connect(DB_FILE)
        gercek_likidasyon_kaydet(
            conn, event.yon, event.kontrat_tipi, event.fiyat, event.notional_usd,
            tahmini_kume_fiyat=en_yakin_fiyat,
            tahmini_katman=en_yakin_katman,
            tahmini_pencere=en_yakin_pencere,
        )
        conn.close()