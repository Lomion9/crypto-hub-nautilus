from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class MarketSnapshotData(Data):
    """
    crypto-hub'daki 'veri' tablosunun tek satırına karşılık gelen agregatif
    snapshot: OI, funding, fiyat OHLC ve spot/perp CVD, tek polling döngüsünde
    toplanmış haliyle.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        oi_btc: float,
        oi_usd: float,
        oi_linear_btc: float,
        oi_inverse_btc: float,
        funding_pct: float,
        price: float,
        price_open: float,
        price_high: float,
        price_low: float,
        cvd_spot_btc: float,
        cvd_perp_btc: float,
        premium_pct: float | None,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.oi_btc = oi_btc
        self.oi_usd = oi_usd
        self.oi_linear_btc = oi_linear_btc
        self.oi_inverse_btc = oi_inverse_btc
        self.funding_pct = funding_pct
        self.price = price
        self.price_open = price_open
        self.price_high = price_high
        self.price_low = price_low
        self.cvd_spot_btc = cvd_spot_btc
        self.cvd_perp_btc = cvd_perp_btc
        self.premium_pct = premium_pct
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
            f"MarketSnapshotData(instrument_id={self.instrument_id}, "
            f"price={self.price}, oi_btc={self.oi_btc}, "
            f"funding_pct={self.funding_pct}, ts_event={self.ts_event})"
        )