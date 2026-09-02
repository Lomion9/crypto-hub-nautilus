import asyncio
from datetime import datetime, timedelta, timezone

from nautilus_trader.live.data_client import LiveDataClient
from nautilus_trader.data.messages import SubscribeData, UnsubscribeData

from data_clients.collector import collect_market_snapshot

from nautilus_trader.model import DataType

from nautilus_trader.model.data import CustomData


class MarketSnapshotDataClient(LiveDataClient):
    """
    borsa.py'nin çoklu borsa OI/funding/CVD toplama mantığını periyodik olarak
    çalıştırıp MarketSnapshotData nesnelerini DataEngine'e ileten data client.
    """

    INTERVAL_MINUTES = 15

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
        self._poll_task: asyncio.Task | None = None

    async def _connect(self) -> None:
        self._log.info("MarketSnapshotDataClient bağlandı.")

    async def _disconnect(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
        self._log.info("MarketSnapshotDataClient bağlantısı kesildi.")

    async def _subscribe(self, command: SubscribeData) -> None:
        if not self._is_subscribed:
            self._is_subscribed = True
            self._poll_task = self.create_task(self._poll_loop())

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        self._is_subscribed = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        while True:
            await self._sleep_until_next_boundary()
            try:
                snapshot = await self._collect_async()
                if snapshot is not None:
                    data_type = DataType(type(snapshot))
                    self._handle_data(CustomData(data_type, snapshot))
                else:
                    self._log.warning("Bu tur atlandı: bir borsadan veri alınamadı.")
            except Exception as e:
                self._log.error(f"Snapshot toplama hatası: {e}")

    async def _collect_async(self):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, collect_market_snapshot)

    async def _sleep_until_next_boundary(self) -> None:
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elapsed_min = (now - day_start).total_seconds() / 60
        next_min = (int(elapsed_min // self.INTERVAL_MINUTES) + 1) * self.INTERVAL_MINUTES
        next_boundary = day_start + timedelta(minutes=next_min)
        wait_seconds = (next_boundary - now).total_seconds()
        await asyncio.sleep(max(wait_seconds, 0))