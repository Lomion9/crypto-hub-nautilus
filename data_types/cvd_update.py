from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId


class CVDUpdate(Data):
    """
    BinanceFeedLoggerStrategy'nin trade tick akışından anlık hesapladığı
    kümülatif hacim deltası (CVD). SnapshotLoggerStrategy bunu tüketip
    REST tabanlı get_binance_cvd() çağrısının yerine kullanır.
    """

    def __init__(self, instrument_id: InstrumentId, cvd: float, ts_event: int, ts_init: int) -> None:
        self.instrument_id = instrument_id
        self.cvd = cvd
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init