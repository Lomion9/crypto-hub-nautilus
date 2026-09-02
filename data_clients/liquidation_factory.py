from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.identifiers import ClientId

from data_clients.liquidation_ws_client import LiquidationWsDataClient


class LiquidationWsDataClientFactory(LiveDataClientFactory):
    @staticmethod
    def create(loop, name, config, msgbus, cache, clock):
        return LiquidationWsDataClient(
            loop=loop,
            client_id=ClientId(name),
            venue=None,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            config=config,
        )