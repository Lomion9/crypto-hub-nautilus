from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class LiquidationEvent(Data):
    """
    Binance'in !forceOrder@arr websocket stream'inden gelen tek bir gerçek
    zorunlu likidasyon event'i. 'yon', LİKİDE EDİLEN pozisyonun yönü (side
    SELL -> long likide edildi, side BUY -> short likide edildi) -- Binance'in
    emrinin kendi yönü değil. 'kontrat_tipi' linear (USDT-margined) ile
    inverse (USD/coin-margined) verisini birbirinden ayırmak için.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        yon: str,            # 'long' | 'short' -- likide edilen pozisyonun yönü
        fiyat: float,          # ortalama likidasyon fiyatı (ap)
        miktar_btc: float,
        notional_usd: float,
        kontrat_tipi: str,     # 'linear' | 'inverse'
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.yon = yon
        self.fiyat = fiyat
        self.miktar_btc = miktar_btc
        self.notional_usd = notional_usd
        self.kontrat_tipi = kontrat_tipi
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init

    def __repr__(self) -> str:
        return (
            f"LiquidationEvent({self.kontrat_tipi} {self.yon} likide, "
            f"${self.notional_usd:,.0f} @ ${self.fiyat:,.2f})"
        )