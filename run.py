from pathlib import Path
from dotenv import load_dotenv

# CWD'den bağımsız: .env dosyasını her zaman run.py ile AYNI klasörden yükler.
# load_dotenv() parametresiz çağrılırsa çalışma dizinine (script'i nereden
# çalıştırdığına) bakar -- IDE/farklı klasörden çalıştırınca sessizce bulamaz.
load_dotenv(Path(__file__).resolve().parent / ".env")

from nautilus_trader.config import (
    TradingNodeConfig,
    LiveDataClientConfig,
    ImportableStrategyConfig,
    InstrumentProviderConfig,
)
from nautilus_trader.live.node import TradingNode

from nautilus_trader.adapters.binance import (
    BINANCE,
    BinanceAccountType,
    BinanceDataClientConfig,
    BinanceLiveDataClientFactory,
)
from nautilus_trader.adapters.binance.common.enums import BinanceEnvironment

from data_clients.factory import MarketSnapshotDataClientFactory
from data_clients.liquidation_factory import LiquidationWsDataClientFactory
# ==========================================
# BINANCE NATIVE DATA CLIENT (mainnet, sadece veri -- exec_client YOK)
# ==========================================
# api_key/api_secret verilmezse SDK otomatik BINANCE_API_KEY / BINANCE_API_SECRET
# env değişkenlerine bakar. Public market data (bar/trade tick/order book) için
# API key şart değil, ama Binance'in rate-limit/instrument-provider akışı için
# önerilir -- ortam değişkeni olarak tanımlamak yeterli, koda hardcode etme.
binance_data_config = BinanceDataClientConfig(
    api_key=None,   # env: BINANCE_API_KEY
    api_secret=None,  # env: BINANCE_API_SECRET
    account_type=BinanceAccountType.USDT_FUTURES,
    environment=BinanceEnvironment.LIVE,  # mainnet -- TESTNET/DEMO da mevcut
    instrument_provider=InstrumentProviderConfig(
        load_ids=frozenset({"BTCUSDT-PERP.BINANCE"}),  # tüm borsayı değil, sadece bunu yükle
    ),
)

config = TradingNodeConfig(
    trader_id="LOMION-001",
    data_clients={
        "MARKET_SNAPSHOT": LiveDataClientConfig(),
        BINANCE: binance_data_config,
        "LIQUIDATION_WS": LiveDataClientConfig(),
    },
    strategies=[
        ImportableStrategyConfig(
            strategy_path="strategies.snapshot_logger:SnapshotLoggerStrategy",
            config_path="nautilus_trader.trading.config:StrategyConfig",
            config={},
        ),
        ImportableStrategyConfig(
            strategy_path="strategies.binance_feed_logger:BinanceFeedLoggerStrategy",
            config_path="strategies.binance_feed_logger:BinanceFeedLoggerConfig",
            config={
                "instrument_id": "BTCUSDT-PERP.BINANCE",
                "bar_spec": "1-MINUTE-LAST-EXTERNAL",
                "book_type": "L2_MBP",
            },
        ),
    ],
)

node = TradingNode(config=config)
node.add_data_client_factory("MARKET_SNAPSHOT", MarketSnapshotDataClientFactory)
node.add_data_client_factory(BINANCE, BinanceLiveDataClientFactory)
node.add_data_client_factory("LIQUIDATION_WS", LiquidationWsDataClientFactory)
node.build()

if __name__ == "__main__":
    try:
        node.run()
    finally:
        node.dispose()