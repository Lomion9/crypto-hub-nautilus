import asyncio
import json

from nautilus_trader.live.data_client import LiveDataClient
from nautilus_trader.data.messages import SubscribeData, UnsubscribeData
from nautilus_trader.model import DataType
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.identifiers import InstrumentId

from data_types.liquidation_event import LiquidationEvent

BINANCE_FORCEORDER_WS_LINEAR = "wss://fstream.binance.com/ws/!forceOrder@arr"
BINANCE_FORCEORDER_WS_INVERSE = "wss://dstream.binance.com/ws/!forceOrder@arr"


class LiquidationWsDataClient(LiveDataClient):
    """
    Binance'in !forceOrder@arr stream'lerine (hem linear/USDT hem inverse/USD)
    ham websocket bağlantısıyla bağlanır -- Nautilus'un Binance adapter'ı bu
    stream'i native desteklemiyor. Gelen her zorunlu likidasyonu
    LiquidationEvent olarak DataEngine'e basar.
    """

    def __init__(self, loop, client_id, msgbus, cache, clock, venue=None, config=None):
        super().__init__(
            loop=loop,
            client_id=client_id,
            venue=venue,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )
        self._is_subscribed = False
        self._ws_task_linear: asyncio.Task | None = None
        self._ws_task_inverse: asyncio.Task | None = None
        self.instrument_id = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")

    async def _connect(self) -> None:
        self._log.info("LiquidationWsDataClient bağlandı.")

    async def _disconnect(self) -> None:
        for t in (self._ws_task_linear, self._ws_task_inverse):
            if t is not None:
                t.cancel()
        self._log.info("LiquidationWsDataClient bağlantısı kesildi.")

    async def _subscribe(self, command: SubscribeData) -> None:
        if not self._is_subscribed:
            self._is_subscribed = True
            self._ws_task_linear = self.create_task(
                self._ws_loop(BINANCE_FORCEORDER_WS_LINEAR, "linear")
            )
            self._ws_task_inverse = self.create_task(
                self._ws_loop(BINANCE_FORCEORDER_WS_INVERSE, "inverse")
            )

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        self._is_subscribed = False
        for t in (self._ws_task_linear, self._ws_task_inverse):
            if t is not None:
                t.cancel()
        self._ws_task_linear = None
        self._ws_task_inverse = None

    async def _ws_loop(self, url: str, kontrat_tipi: str) -> None:
        import websockets

        while True:
            try:
                async with websockets.connect(url) as ws:
                    self._log.info(f"Binance forceOrder stream'ine bağlanıldı ({kontrat_tipi}).")
                    async for raw in ws:
                        try:
                            self._mesaji_isle(raw, kontrat_tipi)
                        except Exception as e:
                            self._log.error(f"Likidasyon mesajı işleme hatası ({kontrat_tipi}): {e}")
            except Exception as e:
                self._log.error(f"Websocket bağlantı hatası ({kontrat_tipi}), 5sn sonra tekrar denenecek: {e}")
                await asyncio.sleep(5)

    def _mesaji_isle(self, raw: str, kontrat_tipi: str) -> None:
        msg = json.loads(raw)
        o = msg.get("o", {})
        sembol = o.get("s", "")

        # linear: BTCUSDT | inverse: BTCUSD_PERP (Binance coin-margined perpetual)
        if kontrat_tipi == "linear" and sembol != "BTCUSDT":
            return
        if kontrat_tipi == "inverse" and sembol != "BTCUSD_PERP":
            return

        # side SELL -> likide edilen pozisyon LONG'du (zorla satıldı).
        # side BUY  -> likide edilen pozisyon SHORT'tu (zorla alındı).
        yon = "long" if o.get("S") == "SELL" else "short"
        fiyat = float(o.get("ap", o.get("p", 0)))
        miktar = float(o.get("z", o.get("q", 0)))
        if fiyat <= 0 or miktar <= 0:
            return

        # DİKKAT: inverse (coin-margined) kontratlarda miktar genelde BTC değil,
        # sabit-USD-değerli KONTRAT ADEDİ olabilir -- ilk gerçek mesajları
        # loglayıp doğrulamak gerekiyor, aşağıdaki notional hesap bunu varsayımla yapıyor.
        notional_usd = fiyat * miktar if kontrat_tipi == "linear" else miktar * 100  # varsayım: 1 kontrat=$100

        now_ns = self._clock.timestamp_ns()
        event = LiquidationEvent(
            instrument_id=self.instrument_id,
            yon=yon,
            fiyat=fiyat,
            miktar_btc=miktar if kontrat_tipi == "linear" else notional_usd / fiyat,
            notional_usd=notional_usd,
            kontrat_tipi=kontrat_tipi,
            ts_event=now_ns,
            ts_init=now_ns,
        )
        self._handle_data(CustomData(DataType(LiquidationEvent), event))