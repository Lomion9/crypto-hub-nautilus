"""USDT.D, USDC.D, DXY, VIX, TOTAL, TOTAL3, BTC.D ve SP500.

Coin verisi Binance'te kalır. Bu modül yalnızca liste ve fiyat grafiği içindir.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from desktop.format import format_price, format_usd
from desktop.market import HOUR_MS, Bar, Point

HEAD = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
TIMEOUT = 12
QUOTE_TTL = 180
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
CMC = "https://api.coinmarketcap.com/data-api/v3/global-metrics/quotes/historical"
GECKO = "https://api.coingecko.com/api/v3/coins/"
DAY_MS = 24 * HOUR_MS

YAHOO_TICKER = {"DXY": "DX-Y.NYB", "VIX": "^VIX", "SP500": "^GSPC"}
YAHOO_SPAN = {
    "15m": ("15m", "60d"),
    "1h": ("1h", "1y"),
    "4h": ("1h", "1y"),
    "1d": ("1d", "5y"),
    "1w": ("1wk", "10y"),
}
INTERVAL_NAME = {"15m": "15dk", "1h": "1sa", "4h": "4sa", "1d": "1g", "1w": "1hft"}


class IndexDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class IndexSpec:
    symbol: str
    unit: str
    style: str


INDEXES: tuple[IndexSpec, ...] = (
    IndexSpec("USDT.D", "pct", "candle"),
    IndexSpec("USDC.D", "pct", "candle"),
    IndexSpec("USDT.D+USDC.D", "pct", "candle"),
    IndexSpec("DXY", "price", "candle"),
    IndexSpec("VIX", "price", "candle"),
    IndexSpec("TOTAL", "usd", "candle"),
    IndexSpec("TOTAL3", "usd", "candle"),
    IndexSpec("BTC.D", "pct", "candle"),
    IndexSpec("SP500", "price", "candle"),
)
_BY_SYMBOL = {item.symbol: item for item in INDEXES}

QUARTER_MS = 15 * 60 * 1000
DAY_SEC = 24 * 60 * 60
# cmc aralığı, gecko gün sayısı, kova, geriye bakış (saniye)
CRYPTO_SOURCE = {
    "15m": ("15m", "1", QUARTER_MS, 5 * DAY_SEC),
    "1h": ("1h", "90", HOUR_MS, 90 * DAY_SEC),
    "4h": ("1h", "90", HOUR_MS, 90 * DAY_SEC),
    "1d": ("1d", "365", DAY_MS, 400 * DAY_SEC),
    "1w": ("1d", "365", DAY_MS, 400 * DAY_SEC),
}

_lock = threading.Lock()
_quote_at = 0.0
_quote_cache: list[object] = []
_crypto_cache: dict[tuple, tuple[float, dict]] = {}
_yahoo_cache: dict[tuple[str, str], tuple[float, list[Bar]]] = {}


def is_index(symbol: str) -> bool:
    return symbol in _BY_SYMBOL


def index_spec(symbol: str) -> IndexSpec | None:
    return _BY_SYMBOL.get(symbol)


def index_label(symbol: str) -> str:
    return symbol if symbol in _BY_SYMBOL else symbol


def format_index_price(symbol: str, value: float | None) -> str:
    spec = _BY_SYMBOL.get(symbol)
    if value is None or spec is None:
        return "—"
    if spec.unit == "usd":
        return format_usd(value)
    if spec.unit == "pct":
        return f"{value:.2f}"
    return format_price(value)


def release_memory() -> None:
    global _quote_at, _quote_cache
    with _lock:
        _quote_at = 0.0
        _quote_cache = []
        _crypto_cache.clear()
        _yahoo_cache.clear()


def index_quotes_fresh() -> bool:
    return bool(_quote_cache) and time.time() - _quote_at < QUOTE_TTL


def load_index_quotes():
    """Liste satırları. Üç dakika dolmadan ağı tekrar çağırmaz."""
    from desktop.market import Quote

    global _quote_at, _quote_cache
    if index_quotes_fresh():
        return list(_quote_cache)
    crypto = _ensure_crypto_quotes()
    yahoo = _yahoo_quotes()
    rows = []
    values = {symbol: _change_24h(points) for symbol, points in crypto["lines"].items()}
    values.update(
        {
            "DXY": yahoo.get("DXY", (0.0, 0.0)),
            "VIX": yahoo.get("VIX", (0.0, 0.0)),
            "SP500": yahoo.get("SP500", (0.0, 0.0)),
        }
    )
    for spec in INDEXES:
        price, change = values.get(spec.symbol, (0.0, 0.0))
        rows.append(Quote(spec.symbol, spec.symbol, price, change, 0.0))
    with _lock:
        _quote_cache = rows
        _quote_at = time.time()
    return list(rows)


@dataclass
class IndexChart:
    symbol: str
    label: str
    style: str
    unit: str
    caption: str
    candles: list[Bar]
    lines: list[tuple[str, list[Point]]]
    price: float
    change_pct: float


def load_index_chart(symbol: str, interval: str) -> IndexChart:
    spec = _BY_SYMBOL.get(symbol)
    if spec is None:
        raise IndexDataError("Endeks yok")
    if symbol in YAHOO_TICKER:
        bars = _yahoo_bars(symbol, interval)
        if len(bars) < 2:
            raise IndexDataError(f"{symbol} mumları alınamadı")
        price, change = _bar_change(bars)
        name = INTERVAL_NAME.get(interval, interval)
        return IndexChart(symbol, symbol, "candle", spec.unit, f"{symbol}  ·  {name}", bars, [], price, change)
    chosen = interval if interval in CRYPTO_SOURCE else "1d"
    bundle = _ensure_crypto(chosen)
    raw = list(bundle["lines"].get(symbol) or [])
    price, change = _change_24h(raw)
    points = _scale_points(symbol, raw)
    if chosen == "4h":
        bars = _bucket_points(points, 4 * HOUR_MS)
    elif chosen == "1w":
        bars = _bucket_points(points, DAY_MS, week=True)
    else:
        bars = _points_to_bars(points)
    if len(bars) < 2:
        raise IndexDataError(f"{symbol} mumları alınamadı")
    name = INTERVAL_NAME.get(chosen, chosen)
    if spec.unit == "usd":
        caption = f"{symbol}  ·  trilyon $  ·  {name}"
    else:
        caption = f"{symbol}  ·  {name}"
    return IndexChart(symbol, symbol, "candle", spec.unit, caption, bars, [], price, change)


def total3_value(total: float, btc_pct: float, eth_pct: float) -> float:
    return total * max(0.0, 1.0 - btc_pct / 100.0 - eth_pct / 100.0)


def _change_24h(points: list[Point]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    last = points[-1]
    target = last.time_ms - DAY_MS
    previous: Point | None = None
    for point in points:
        if point.time_ms <= target:
            previous = point
        else:
            break
    if previous is None or previous.value == 0:
        return last.value, 0.0
    return last.value, (last.value - previous.value) / abs(previous.value) * 100.0


def _bar_change(bars: list[Bar]) -> tuple[float, float]:
    if not bars:
        return 0.0, 0.0
    last = bars[-1].close
    if len(bars) < 2 or bars[-2].close == 0:
        return last, 0.0
    prev = bars[-2].close
    return last, (last - prev) / abs(prev) * 100.0


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _get(url: str, params: dict | None = None, timeout: int = TIMEOUT) -> dict:
    try:
        response = requests.get(url, params=params, headers=HEAD, timeout=timeout)
    except requests.RequestException as exc:
        raise IndexDataError("Veri alınamadı") from exc
    if response.status_code >= 400:
        raise IndexDataError(f"Veri {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise IndexDataError("Veri okunamadı") from exc
    if not isinstance(payload, dict):
        raise IndexDataError("Veri boş")
    return payload


def _ensure_crypto_quotes() -> dict:
    return _cached_bundle("1h", "2", HOUR_MS, 2 * DAY_SEC)


def _ensure_crypto(interval: str) -> dict:
    cmc_interval, gecko_days, bucket_ms, lookback = CRYPTO_SOURCE[interval]
    return _cached_bundle(cmc_interval, gecko_days, bucket_ms, lookback)


def _cached_bundle(cmc_interval: str, gecko_days: str, bucket_ms: int, lookback: int) -> dict:
    key = (cmc_interval, gecko_days, lookback)
    now = time.time()
    with _lock:
        cached = _crypto_cache.get(key)
    if cached is not None and now - cached[0] < QUOTE_TTL:
        return cached[1]
    bundle = _fetch_bundle(cmc_interval, gecko_days, bucket_ms, lookback)
    with _lock:
        _crypto_cache[key] = (now, bundle)
    return bundle


def _fetch_bundle(cmc_interval: str, gecko_days: str, bucket_ms: int, lookback: int) -> dict:
    end = int(time.time())
    start = end - lookback
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows_job = pool.submit(_cmc_rows, start, end, cmc_interval)
        usdt_job = pool.submit(_safe_caps, "tether", gecko_days, bucket_ms)
        usdc_job = pool.submit(_safe_caps, "usd-coin", gecko_days, bucket_ms)
        rows = rows_job.result()
        usdt_caps = usdt_job.result()
        usdc_caps = usdc_job.result()
    usdt = _dominance(rows, usdt_caps, bucket_ms)
    usdc = _dominance(rows, usdc_caps, bucket_ms)
    return {
        "lines": {
            "USDT.D": usdt,
            "USDC.D": usdc,
            "USDT.D+USDC.D": _sum_points(usdt, usdc),
            "BTC.D": [Point(row["ms"], row["btc"]) for row in rows],
            "TOTAL": [Point(row["ms"], row["total"]) for row in rows],
            "TOTAL3": [Point(row["ms"], total3_value(row["total"], row["btc"], row["eth"])) for row in rows],
        }
    }


def _cmc_rows(start: int, end: int, interval: str) -> list[dict]:
    payload = _get(
        CMC,
        {"convert": "USD", "interval": interval, "timeStart": start, "timeEnd": end},
        timeout=25,
    )
    quotes = (payload.get("data") or {}).get("quotes") or []
    rows: list[dict] = []
    for item in quotes:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote") or []
        if not quote or not isinstance(quote[0], dict):
            continue
        try:
            total = float(quote[0]["totalMarketCap"])
            btc = float(item["btcDominance"])
            eth = float(item["ethDominance"])
            stamp = str(item["timestamp"])
            ms = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        if total <= 0:
            continue
        rows.append({"ms": ms, "day": _day(ms), "total": total, "btc": btc, "eth": eth})
    rows.sort(key=lambda row: row["ms"])
    if len(rows) < 2:
        raise IndexDataError("Toplam piyasa alınamadı")
    return rows


def _safe_caps(coin: str, days: str, bucket_ms: int) -> dict[int, float]:
    try:
        return _gecko_caps(coin, days, bucket_ms)
    except IndexDataError:
        return {}


def _gecko_caps(coin: str, days: str, bucket_ms: int) -> dict[int, float]:
    payload = _get(
        f"{GECKO}{coin}/market_chart",
        {"vs_currency": "usd", "days": days},
        timeout=25,
    )
    found: dict[int, float] = {}
    for row in payload.get("market_caps") or []:
        try:
            ms = int(row[0])
            cap = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if cap <= 0:
            continue
        found[ms // bucket_ms * bucket_ms] = cap
    if len(found) < 2:
        raise IndexDataError(f"{coin} piyasa değeri alınamadı")
    return found


def _dominance(rows: list[dict], caps: dict[int, float], bucket_ms: int) -> list[Point]:
    points: list[Point] = []
    for row in rows:
        cap = caps.get(row["ms"] // bucket_ms * bucket_ms)
        if cap is None or row["total"] <= 0:
            continue
        points.append(Point(row["ms"], 100.0 * cap / row["total"]))
    return points


def _sum_points(left: list[Point], right: list[Point]) -> list[Point]:
    other = {point.time_ms: point.value for point in right}
    summed: list[Point] = []
    for point in left:
        extra = other.get(point.time_ms)
        if extra is None:
            continue
        summed.append(Point(point.time_ms, point.value + extra))
    return summed


def _scale_points(symbol: str, points: list[Point]) -> list[Point]:
    if symbol not in ("TOTAL", "TOTAL3"):
        return points
    return [Point(point.time_ms, point.value / 1e12) for point in points]


def _week_start(ms: int) -> int:
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
    monday = (moment - timedelta(days=moment.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(monday.timestamp() * 1000)


def _bucket_points(points: list[Point], span_ms: int, week: bool = False) -> list[Bar]:
    groups: dict[int, list[float]] = {}
    for point in points:
        key = _week_start(point.time_ms) if week else point.time_ms // span_ms * span_ms
        groups.setdefault(key, []).append(point.value)
    bars: list[Bar] = []
    for key in sorted(groups):
        values = groups[key]
        bars.append(Bar(key, values[0], max(values), min(values), values[-1], 0.0))
    return bars


def _points_to_bars(points: list[Point]) -> list[Bar]:
    bars: list[Bar] = []
    previous: float | None = None
    for point in points:
        open_ = point.value if previous is None else previous
        close = point.value
        bars.append(Bar(point.time_ms, open_, max(open_, close), min(open_, close), close, 0.0))
        previous = close
    return bars[1:] if len(bars) > 1 else bars


def _yahoo_quotes() -> dict[str, tuple[float, float]]:
    found: dict[str, tuple[float, float]] = {}

    def one(symbol: str) -> tuple[str, tuple[float, float]]:
        try:
            bars = _yahoo_bars(symbol, "1d", span=("1d", "5d"), use_cache=False)
            return symbol, _bar_change(bars)
        except IndexDataError:
            return symbol, (0.0, 0.0)

    with ThreadPoolExecutor(max_workers=3) as pool:
        for symbol, change in pool.map(one, YAHOO_TICKER):
            found[symbol] = change
    return found


def _yahoo_bars(symbol: str, interval: str, span: tuple[str, str] | None = None, use_cache: bool = True) -> list[Bar]:
    ticker = YAHOO_TICKER[symbol]
    chosen = span or YAHOO_SPAN.get(interval) or YAHOO_SPAN["1d"]
    key = (symbol, chosen[0], chosen[1])
    now = time.time()
    if use_cache:
        with _lock:
            cached = _yahoo_cache.get(key)
        if cached is not None and now - cached[0] < QUOTE_TTL:
            bars = cached[1]
            return _bucket_4h(bars) if interval == "4h" else bars
    payload = _yahoo_payload(ticker, chosen[0], chosen[1])
    bars = _bars_from_yahoo(payload)
    if not bars and chosen[0] == "1h":
        payload = _yahoo_payload(ticker, "60m", chosen[1])
        bars = _bars_from_yahoo(payload)
    with _lock:
        _yahoo_cache[key] = (now, bars)
    if interval == "4h":
        return _bucket_4h(bars)
    return bars


def _yahoo_payload(ticker: str, interval: str, span: str) -> dict:
    return _get(
        YAHOO + requests.utils.quote(ticker, safe="-."),
        {"interval": interval, "range": span},
    )


def _bars_from_yahoo(payload: dict) -> list[Bar]:
    result = (payload.get("chart") or {}).get("result") or []
    if not result:
        return []
    block = result[0]
    stamps = block.get("timestamp") or []
    quote = ((block.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    bars: list[Bar] = []
    for index, stamp in enumerate(stamps):
        try:
            open_, high, low, close = opens[index], highs[index], lows[index], closes[index]
            if None in (open_, high, low, close):
                continue
            bars.append(Bar(int(stamp) * 1000, float(open_), float(high), float(low), float(close), 0.0))
        except (TypeError, ValueError, IndexError):
            continue
    return bars


def _bucket_4h(bars: list[Bar]) -> list[Bar]:
    span = 4 * HOUR_MS
    groups: dict[int, list[Bar]] = {}
    for bar in bars:
        groups.setdefault(bar.time_ms // span * span, []).append(bar)
    packed: list[Bar] = []
    for key in sorted(groups):
        chunk = groups[key]
        packed.append(
            Bar(
                key,
                chunk[0].open,
                max(item.high for item in chunk),
                min(item.low for item in chunk),
                chunk[-1].close,
                0.0,
                sum(item.volume for item in chunk),
            )
        )
    return packed
