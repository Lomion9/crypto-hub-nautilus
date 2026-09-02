from datetime import datetime, timedelta, timezone

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import AggressorSide, BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy

from borsa import get_binance_cvd
from data_types.cvd_update import CVDUpdate


class BinanceFeedLoggerConfig(StrategyConfig, frozen=True):
    """
    instrument_id: ör. "BTCUSDT-PERP.BINANCE"
    bar_spec: BarType.from_str'a verilecek son parça, ör. "1-MINUTE-LAST-EXTERNAL"
    book_type: L2_MBP (Binance depth-diff akışı için doğru seçenek; L3_MBO
    Binance'te desteklenmiyor)
    """
    instrument_id: str
    bar_spec: str = "1-MINUTE-LAST-EXTERNAL"
    book_type: str = "L2_MBP"


class BinanceFeedLoggerStrategy(Strategy):
    """
    Binance native adapter'dan bar/trade tick/L2 order book akışına abone olur.
    Trade tick akışından anlık CVD hesaplar (aggressor_side'a göre kümülatif
    hacim deltası) ve her 100 tick'te bir CVDUpdate'i msgbus üzerinden doğrudan
    yayınlar (self.msgbus.publish) -- SnapshotLoggerStrategy bunu msgbus.subscribe
    ile dinler. subscribe_data/publish_data yerine msgbus kullanıyoruz çünkü
    CVDUpdate'in dış bir data client'ı yok, sadece iki strategy arası özel veri
    paylaşımı -- DataEngine'in client yönlendirmesi (subscribe_data) burada
    devreye girmemeli, girerse ilgili venue client'ının bu tipi desteklemediği
    hatasını verir.

    CVD'nin referans noktası borsa.py'nin get_binance_cvd()'siyle aynı olmak
    zorunda (UTC gece yarısından itibaren kümülatif) -- yoksa df_gecmis'teki
    geçmiş periyot karşılaştırmaları bozulur. Bu yüzden on_start'ta REST'ten
    bir kerelik gerçek başlangıç değeri çekilir, ondan sonra tick'lerle canlı
    güncellenir, ve her UTC gece yarısında sıfırlanır.
    """

    def __init__(self, config: BinanceFeedLoggerConfig) -> None:
        super().__init__(config)
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(f"{config.instrument_id}-{config.bar_spec}")
        self.book_type = BookType[config.book_type]
        self._trade_tick_sayac = 0
        self._cvd = 0.0

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)
        self.subscribe_trade_ticks(instrument_id=self.instrument_id)
        self.subscribe_order_book_deltas(
            instrument_id=self.instrument_id,
            book_type=self.book_type,
        )

        self._cvd = get_binance_cvd('futures', 'BTCUSDT', interval='1h')
        self.log.info(f"CVD başlangıç değeri (REST'ten, gün başından beri): {self._cvd:.2f}")
        self._sonraki_utc_gece_yarisi_alarm()

        self.log.info(
            f"BinanceFeedLoggerStrategy başladı -- {self.instrument_id} için "
            f"bar/trade-tick/order-book aboneliği açıldı."
        )

    def _sonraki_utc_gece_yarisi_alarm(self) -> None:
        now = datetime.now(timezone.utc)
        yarin_gece_yarisi = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.clock.set_time_alert(
            name="cvd_gunluk_sifirlama",
            alert_time=yarin_gece_yarisi,
            callback=self._cvd_gunluk_sifirla,
        )

    def _cvd_gunluk_sifirla(self, event) -> None:
        self._cvd = 0.0
        self.log.info("UTC gece yarısı: CVD sıfırlandı (yeni gün).")
        self._sonraki_utc_gece_yarisi_alarm()

    def on_bar(self, bar: Bar) -> None:
        pass

    def on_trade_tick(self, tick: TradeTick) -> None:
        self._trade_tick_sayac += 1

        size = float(tick.size)
        if tick.aggressor_side == AggressorSide.BUYER:
            self._cvd += size
        elif tick.aggressor_side == AggressorSide.SELLER:
            self._cvd -= size

        if self._trade_tick_sayac % 100 == 0:
            now_ns = self.clock.timestamp_ns()
            self.msgbus.publish(
                topic="cvd_update",
                msg=CVDUpdate(
                    instrument_id=self.instrument_id,
                    cvd=self._cvd,
                    ts_event=now_ns,
                    ts_init=now_ns,
                ),
            )

    def on_order_book_deltas(self, deltas: OrderBookDeltas) -> None:
        pass

    def on_stop(self) -> None:
        self.unsubscribe_bars(self.bar_type)
        self.unsubscribe_trade_ticks(instrument_id=self.instrument_id)
        self.unsubscribe_order_book_deltas(instrument_id=self.instrument_id)