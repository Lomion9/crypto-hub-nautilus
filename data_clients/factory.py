from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.identifiers import ClientId

from data_clients.market_data_client import MarketSnapshotDataClient


class MarketSnapshotDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(loop, name, config, msgbus, cache, clock):
        return MarketSnapshotDataClient(
            loop=loop,
            client_id=ClientId(name),
            venue=None,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )