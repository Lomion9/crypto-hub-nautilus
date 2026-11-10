"""Binance USDT-M verisi. Bu modül veritabanına yazmaz."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from desktop.readout import Read, build_reads

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
HEADERS = {"User-Agent": "CryptoHub-Desktop"}
TIMEOUT = 12

CHART_INTERVALS = ("15m", "1h", "4h", "1d", "1w")
CHART_BARS = 500
CHART_PAGE = 100
WINDOWS: tuple[tuple[str, int], ...] = (
    ("15dk", 1),
    ("1sa", 4),
    ("2sa", 8),
    ("4sa", 16),
    ("8sa", 32),
    ("24sa", 96),
)
BAR_MS = 15 * 60 * 1000
HISTORY_BARS = 200
FUNDING_HOURS = 72
HOUR_MS = 60 * 60 * 1000


class MarketError(RuntimeError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    base: str
    price: float
    change_pct: float
    quote_volume: float


@dataclass(frozen=True)
class Bar:
    time_ms: int
    open: float
    high: float
    low: float
    close: float
    cvd_delta: float
    volume: float = 0.0


@dataclass(frozen=True)
class Point:
    time_ms: int
    value: float


@dataclass(frozen=True)
class Window:
    label: str
    price_pct: float | None
    oi_pct: float | None
    funding_delta: float | None
    cvd_perp: float | None
    cvd_spot: float | None
    closed_ms: int | None = None
    closed_high: float | None = None
    closed_low: float | None = None
    anchor: float | None = None


@dataclass(frozen=True)
class Snapshot:
    symbol: str
    base: str
    interval: str
    price: float | None
    oi_base: float | None
    oi_usd: float | None
    funding_percent: float | None
    next_funding_ms: int | None
    cvd_spot_day: float | None
    cvd_perp_day: float | None
    candles: list[Bar]
    oi_points: list[Point]
    windows: list[Window]
    funding_status: str
    reads: list[Read]
    funding_hourly: list[Point]
    warning: str | None
    session_change: float | None = None
    touches: dict = field(default_factory=dict)
    oi_venues_up: int = 0
    oi_venues_known: int = 0
    funding_venues_up: int = 0
    funding_venues_known: int = 0
    cvd_perp_up: int = 0
    cvd_perp_known: int = 0
    cvd_spot_up: int = 0
    cvd_spot_known: int = 0


def base_of(symbol: str) -> str:
    symbol = symbol.upper()
    if symbol.endswith("USDT"):
        return symbol[:-4]
    return symbol


def _get(url: str, params: dict | None = None):
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise MarketError("Binance'e ulaşılamadı") from exc
    if response.status_code >= 400:
        raise MarketError(f"Binance {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise MarketError("Binance cevabı okunamadı") from exc
    if isinstance(data, dict) and data.get("code") not in (None, 0):
        raise MarketError(str(data.get("msg") or "Binance hatası"))
    return data


_symbol_cache: tuple[float, dict[str, str]] | None = None


def perpetual_symbols() -> dict[str, str]:
    global _symbol_cache
    now = time.time()
    if _symbol_cache is not None and now - _symbol_cache[0] < 1800:
        return _symbol_cache[1]
    payload = _get(f"{FAPI}/fapi/v1/exchangeInfo")
    found: dict[str, str] = {}
    for row in payload.get("symbols", []):
        if row.get("contractType") != "PERPETUAL":
            continue
        if row.get("quoteAsset") != "USDT" or row.get("status") != "TRADING":
            continue
        symbol = str(row.get("symbol") or "")
        base = str(row.get("baseAsset") or base_of(symbol))
        if symbol:
            found[symbol] = base
    if not found:
        raise MarketError("USDT perpetual listesi boş")
    _symbol_cache = (now, found)
    return found


def load_quotes() -> list[Quote]:
    symbols = perpetual_symbols()
    tickers = _get(f"{FAPI}/fapi/v1/ticker/24hr")
    if not isinstance(tickers, list):
        raise MarketError("Fiyat listesi alınamadı")
    quotes: list[Quote] = []
    for row in tickers:
        symbol = str(row.get("symbol") or "")
        base = symbols.get(symbol)
        if base is None:
            continue
        try:
            price = float(row["lastPrice"])
            change = float(row["priceChangePercent"])
            volume = float(row.get("quoteVolume") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0:
            continue
        quotes.append(Quote(symbol, base, price, change, volume))
    quotes.sort(key=lambda item: item.quote_volume, reverse=True)
    if not quotes:
        raise MarketError("Coin listesi alınamadı")
    return quotes


def _bars(raw) -> list[Bar]:
    if not isinstance(raw, list):
        raise MarketError("Mum verisi alınamadı")
    bars: list[Bar] = []
    for row in raw:
        try:
            volume = float(row[5])
            taker_buy = float(row[9])
            bars.append(
                Bar(
                    time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    cvd_delta=taker_buy - (volume - taker_buy),
                    volume=volume,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.time_ms)
    return bars


def fetch_klines(symbol: str, interval: str, limit: int, futures: bool) -> list[Bar]:
    host = f"{FAPI}/fapi/v1/klines" if futures else f"{SPOT}/api/v3/klines"
    raw = _get(host, {"symbol": symbol, "interval": interval, "limit": limit})
    return _bars(raw)


def since_step(start_ms: int, now_ms: int | None = None) -> int:
    """Kısa geçmiş 15 dakika, on günden uzun geçmiş 1 saat."""
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if now - int(start_ms) <= 10 * 24 * 60 * 60 * 1000:
        return 15 * 60 * 1000
    return 60 * 60 * 1000


def bars_since(bars: list[Bar], start_ms: int, step: int) -> list[Bar]:
    """Sinyal saatinden sonra kapanan mumlar. Sinyalin içine düştüğü mum da kalır."""
    opened = int(start_ms)
    return [bar for bar in bars if bar.time_ms + step > opened]


def fetch_klines_since(symbol: str, start_ms: int) -> list[Bar]:
    """Sinyalin açılışından bugüne vadeli mum. Kısa aralık 15 dakika, uzun aralık 1 saat."""
    now = int(time.time() * 1000)
    start_ms = int(start_ms)
    if start_ms <= 0 or start_ms >= now:
        return []
    step = since_step(start_ms, now)
    interval = "15m" if step == 15 * 60 * 1000 else "1h"
    bars: list[Bar] = []
    cursor = max(0, start_ms - step)
    for _page in range(8):
        if cursor >= now:
            break
        raw = _get(
            f"{FAPI}/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "startTime": cursor, "limit": 1500},
        )
        fetched = _bars(raw)
        if not fetched:
            break
        bars.extend(bars_since(fetched, start_ms, step))
        nxt = fetched[-1].time_ms + step
        if nxt <= cursor or len(fetched) < 1500:
            break
        cursor = nxt
    return bars


def fetch_older_klines(symbol: str, interval: str, before_ms: int, limit: int = CHART_PAGE) -> list[Bar]:
    raw = _get(
        f"{FAPI}/fapi/v1/klines",
        {"symbol": symbol, "interval": interval, "limit": limit, "endTime": max(0, before_ms - 1)},
    )
    return [bar for bar in _bars(raw) if bar.time_ms < before_ms]


def _try_klines(symbol: str, interval: str, limit: int, futures: bool) -> list[Bar]:
    try:
        return fetch_klines(symbol, interval, limit, futures)
    except MarketError:
        return []


def _oi_points(symbol: str) -> list[Point]:
    from desktop.oi_feed import load_oi

    return list(load_oi(symbol).series.get("15m") or [])


def bucket_funding(points: list[Point], hours: int) -> list[Point]:
    """Saatlik fonlamayı 8 saatlik ortalamalara toplar. Saatler UTC 00/08/16 sınırına oturur."""
    if hours <= 1 or not points:
        return list(points)
    span = hours * HOUR_MS
    groups: dict[int, list[float]] = {}
    order: list[int] = []
    for point in points:
        key = int(point.time_ms // span * span)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(point.value)
    return [Point(key, sum(groups[key]) / len(groups[key])) for key in order]


def hourly_funding(
    settled: list[tuple[int, float]],
    live: float | None,
    now_ms: int,
) -> list[Point]:
    """Son 72 saatin her saati için geçerli fonlama oranı, yüzde cinsinden.

    Geçmiş saatler son kesilen oranı taşır. İçinde bulunulan saat canlı orandır,
    böylece çizgi kesimler arasında da güncellenir.
    """
    end = (now_ms // HOUR_MS) * HOUR_MS
    start = end - (FUNDING_HOURS - 1) * HOUR_MS
    points: list[Point] = []
    for index in range(FUNDING_HOURS):
        moment = start + index * HOUR_MS
        cutoff = moment + HOUR_MS - 1
        chosen = None
        for stamp, value in settled:
            if stamp <= cutoff:
                chosen = value
            else:
                break
        if index == FUNDING_HOURS - 1 and live is not None:
            chosen = live
        if chosen is None:
            continue
        points.append(Point(moment, chosen))
    return points


def _funding_points(symbol: str) -> list[tuple[int, float]]:
    start = int(time.time() * 1000) - (FUNDING_HOURS + 24) * HOUR_MS
    try:
        raw = _get(
            f"{FAPI}/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": start, "limit": 100},
        )
    except MarketError:
        return []
    if not isinstance(raw, list):
        return []
    points: list[tuple[int, float]] = []
    for row in raw:
        try:
            points.append((int(row["fundingTime"]), float(row["fundingRate"]) * 100))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])
    return points


def _live(symbol: str) -> tuple[float | None, float | None, float | None, int | None]:
    price = oi_base = funding = None
    next_ms = None
    try:
        premium = _get(f"{FAPI}/fapi/v1/premiumIndex", {"symbol": symbol})
        price = float(premium["markPrice"])
        funding = float(premium["lastFundingRate"]) * 100
        next_ms = int(premium["nextFundingTime"])
    except (MarketError, KeyError, TypeError, ValueError):
        pass
    try:
        oi = _get(f"{FAPI}/fapi/v1/openInterest", {"symbol": symbol})
        oi_base = float(oi["openInterest"])
    except (MarketError, KeyError, TypeError, ValueError):
        pass
    return price, oi_base, funding, next_ms


def _closed_bars(bars: list[Bar], now_ms: int, bar_ms: int) -> list[Bar]:
    """Açık son mumu analiz serisinden çıkarır. Grafik serisi buna girmez."""
    if bars and bars[-1].time_ms + bar_ms > now_ms:
        return bars[:-1]
    return bars


def fetch_hourly_oi(symbol: str) -> list[Point]:
    """Son ~30 günün saatlik open interest'i. Uç en fazla 500 kayıt verir."""
    end = int(time.time() * 1000)
    points: list[Point] = []
    seen: set[int] = set()
    for _ in range(4):
        try:
            raw = _get(
                f"{FAPI}/futures/data/openInterestHist",
                {"symbol": symbol, "period": "1h", "limit": 500, "endTime": end},
            )
        except MarketError:
            break
        if not isinstance(raw, list) or not raw:
            break
        batch: list[Point] = []
        for row in raw:
            try:
                stamp = int(row["timestamp"])
                value = float(row["sumOpenInterest"])
            except (KeyError, TypeError, ValueError):
                continue
            if stamp in seen or value <= 0:
                continue
            seen.add(stamp)
            batch.append(Point(stamp, value))
        if not batch:
            break
        points.extend(batch)
        earliest = min(point.time_ms for point in batch)
        if len(raw) < 500:
            break
        end = earliest - 1
    points.sort(key=lambda point: point.time_ms)
    return points


def fetch_oi_page(symbol: str, start_ms: int, limit: int = 500) -> list[tuple[int, float, float]]:
    """Saatlik açık pozisyonun bir sayfası. Damga ham bırakılır; kaydırma banda aittir."""
    raw = _get(
        f"{FAPI}/futures/data/openInterestHist",
        {"symbol": symbol, "period": "1h", "limit": limit, "startTime": int(start_ms)},
    )
    if not isinstance(raw, list):
        return []
    rows: list[tuple[int, float, float]] = []
    for row in raw:
        try:
            rows.append(
                (
                    int(row["timestamp"]),
                    float(row["sumOpenInterest"]),
                    float(row["sumOpenInterestValue"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda item: item[0])
    return rows


def fetch_leverage_tier(symbol: str) -> tuple[float, int]:
    """İlk kademenin bakım marjı ve en yüksek kaldıraç. Yoksa %0.4 ve 100x."""
    try:
        raw = _get(f"{FAPI}/fapi/v1/leverageBracket", {"symbol": symbol})
    except MarketError:
        return 0.004, 100
    row = raw[0] if isinstance(raw, list) and raw else raw
    brackets = row.get("brackets") if isinstance(row, dict) else None
    if not brackets:
        return 0.004, 100
    first = brackets[0]
    try:
        margin = float(first["maintMarginRatio"])
        leverage = int(first["initialLeverage"])
    except (KeyError, TypeError, ValueError):
        return 0.004, 100
    if margin <= 0 or margin >= 1:
        margin = 0.004
    if leverage < 1:
        leverage = 100
    return margin, leverage


def _at_or_before(points: list[Point], target: int) -> float | None:
    chosen = None
    for point in points:
        if point.time_ms <= target:
            chosen = point.value
        else:
            break
    return chosen


def _pair_at_or_before(points: list[tuple[int, float]], target: int) -> float | None:
    chosen = None
    for time_ms, value in points:
        if time_ms <= target:
            chosen = value
        else:
            break
    return chosen


def _pct(now: float | None, past: float | None) -> float | None:
    if now is None or past is None or past == 0:
        return None
    return (now - past) / past * 100


def _session_start_ms(now: datetime | None = None) -> int:
    """Gün içi oturum 03:00 Türkiye, yani 00:00 UTC."""
    moment = datetime.now(timezone.utc) if now is None else now.astimezone(timezone.utc)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def _session_cvd(bars: list[Bar], now: datetime | None = None) -> float | None:
    if not bars:
        return None
    start = _session_start_ms(now)
    if bars[0].time_ms > start:
        return None
    return sum(bar.cvd_delta for bar in bars if bar.time_ms >= start)


def _sum_cvd(bars: list[Bar], target: int, last_ms: int) -> float | None:
    if not bars or bars[0].time_ms > target:
        return None
    return sum(bar.cvd_delta for bar in bars if target < bar.time_ms <= last_ms)


# Her analiz aralığı kendi mumundadır. 8sa OI, 4sa ucundan hizalanır.
ANALYSIS: tuple[tuple[str, str, int], ...] = (
    ("15dk", "15m", BAR_MS),
    ("1sa", "1h", HOUR_MS),
    ("2sa", "2h", 2 * HOUR_MS),
    ("4sa", "4h", 4 * HOUR_MS),
    ("8sa", "8h", 8 * HOUR_MS),
    ("24sa", "1d", 24 * HOUR_MS),
    ("1hafta", "1w", 7 * 24 * HOUR_MS),
)
_OI_PERIOD = {"1h": "1h", "2h": "2h", "4h": "4h", "8h": "4h", "1d": "1d", "1w": "1d"}
_FRESH: dict[tuple, tuple[int, object]] = {}


def _fresh(key: tuple, bar_ms: int, load):
    """Aynı mum kapanmadan seri yeniden istenmez. Boş cevap saklanmaz."""
    now = int(time.time() * 1000)
    found = _FRESH.get(key)
    if found is not None and int(found[0]) // bar_ms == now // bar_ms:
        return found[1]
    payload = load()
    if payload:
        _FRESH[key] = (now, payload)
        return payload
    if found is not None:
        return found[1]
    return payload


def _oi_hist(symbol: str, period: str, limit: int = 40) -> list[Point]:
    try:
        raw = _get(
            f"{FAPI}/futures/data/openInterestHist",
            {"symbol": symbol, "period": period, "limit": limit},
        )
    except MarketError:
        return []
    if not isinstance(raw, list):
        return []
    points: list[Point] = []
    for row in raw:
        try:
            value = float(row["sumOpenInterest"])
        except (KeyError, TypeError, ValueError):
            continue
        if value <= 0:
            continue
        points.append(Point(int(row["timestamp"]), value))
    points.sort(key=lambda point: point.time_ms)
    return points


_INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": HOUR_MS,
    "2h": 2 * HOUR_MS,
    "4h": 4 * HOUR_MS,
    "8h": 8 * HOUR_MS,
    "1d": 24 * HOUR_MS,
    "1w": 7 * 24 * HOUR_MS,
}
_KLINE_CACHE: dict[tuple, list[Bar]] = {}
_KLINE_LOCK = threading.Lock()


def merge_kline_tail(cached: list[Bar], fresh: list[Bar], step: int, limit: int) -> list[Bar] | None:
    """Eski mumlar durur. Yeni mumlar uca eklenir. Aralık kopuksa None."""
    if not cached or not fresh or step <= 0:
        return None
    if fresh[0].time_ms > cached[-1].time_ms + step:
        return None
    merged = {bar.time_ms: bar for bar in cached}
    for bar in fresh:
        merged[bar.time_ms] = bar
    ordered = [merged[stamp] for stamp in sorted(merged)]
    if limit > 0 and len(ordered) > limit:
        ordered = ordered[-limit:]
    return ordered


def cached_klines(symbol: str, interval: str, futures: bool, limit: int, required: bool = False) -> list[Bar]:
    """İlk turda tam seri, sonrasında yalnız son birkaç mum. Kısa istek uzun belleği silmez."""
    key = (symbol.upper(), interval, bool(futures))
    step = _INTERVAL_MS.get(interval, BAR_MS)
    with _KLINE_LOCK:
        cached = list(_KLINE_CACHE.get(key, []))

        def store(bars: list[Bar], keep: int) -> list[Bar]:
            if bars:
                _KLINE_CACHE[key] = bars[-keep:]
            stored = _KLINE_CACHE.get(key, [])
            return list(stored[-limit:])

        def full() -> list[Bar]:
            want = max(limit, len(cached))
            bars = fetch_klines(symbol, interval, want, futures) if required else _try_klines(symbol, interval, want, futures)
            return store(bars, want)

        if len(cached) < limit:
            return full()
        fresh = _try_klines(symbol, interval, 5, futures)
        if not fresh:
            return list(cached[-limit:])
        merged = merge_kline_tail(cached, fresh, step, max(limit, len(cached)))
        if merged is None:
            return full()
        return store(merged, max(limit, len(merged)))


def _fresh_klines(symbol: str, interval: str, futures: bool, bar_ms: int) -> list[Bar]:
    return _fresh(
        (symbol, "kline", interval, futures),
        bar_ms,
        lambda: cached_klines(symbol, interval, futures, 40),
    )


def _fresh_oi(symbol: str, interval: str) -> list[Point]:
    from desktop.oi_feed import load_oi

    period = _OI_PERIOD[interval]
    return list(load_oi(symbol).series.get(period) or [])


def _aligned_oi(points: list[Point], bars: list[Bar], bar_ms: int) -> list[Point]:
    series: list[Point] = []
    for bar in bars:
        value = _at_or_before(points, bar.time_ms + bar_ms - 1)
        if value is None:
            continue
        series.append(Point(bar.time_ms, value))
    return series


def _session_price_pct(perp: list[Bar]) -> float | None:
    """Başlık yüzdesi: 03:00 açılışından son muma. Analiz satırından ayrıdır."""
    if not perp:
        return None
    target = _session_start_ms()
    past = None
    for bar in perp:
        if bar.time_ms < target:
            past = bar.close
        elif bar.time_ms == target:
            past = bar.open
            break
        else:
            break
    return _pct(perp[-1].close, past)


def _one_window(
    label: str,
    perp: list[Bar],
    spot: list[Bar],
    oi_points: list[Point],
    bar_ms: int,
    now_ms: int,
    funding_points: list[tuple[int, float]],
    live_funding: float | None,
    keep_open: bool = False,
    cvd_perp: float | None = None,
    cvd_spot: float | None = None,
    from_feed: bool = False,
) -> tuple[Window, tuple]:
    closed = list(perp) if keep_open else _closed_bars(perp, now_ms, bar_ms)
    closed_spot = list(spot) if keep_open else _closed_bars(spot, now_ms, bar_ms)
    last = closed[-1] if closed else None
    previous = closed[-2] if len(closed) >= 2 else None
    spot_bar = None
    if last is not None:
        for bar in closed_spot:
            if bar.time_ms == last.time_ms:
                spot_bar = bar
                break
    aligned = _aligned_oi(oi_points, closed, bar_ms)
    now_oi = aligned[-1].value if aligned else None
    past_oi = aligned[-2].value if len(aligned) >= 2 else None
    past_funding = None
    if last is not None and funding_points:
        past_funding = _pair_at_or_before(funding_points, last.time_ms)
    funding_delta = None
    if live_funding is not None and past_funding is not None:
        funding_delta = live_funding - past_funding
    if not from_feed:
        cvd_perp = None if last is None else last.cvd_delta
        cvd_spot = None if spot_bar is None else spot_bar.cvd_delta
    window = Window(
        label=label,
        price_pct=_pct(None if last is None else last.close, None if previous is None else previous.close),
        oi_pct=_pct(now_oi, past_oi),
        funding_delta=funding_delta,
        cvd_perp=cvd_perp,
        cvd_spot=cvd_spot,
        closed_ms=None if last is None else last.time_ms,
        closed_high=None if last is None else last.high,
        closed_low=None if last is None else last.low,
        anchor=None if previous is None or not keep_open else previous.close,
    )
    return window, (closed, aligned, cvd_spot, cvd_perp)


def load_snapshot(
    symbol: str,
    chart_interval: str = "15m",
    with_chart: bool = True,
    enabled: set[str] | None = None,
) -> Snapshot:
    symbol = symbol.upper().strip()
    if chart_interval not in CHART_INTERVALS:
        raise MarketError(f"Bilinmeyen grafik aralığı: {chart_interval}")

    warning = None
    wanted = [row for row in ANALYSIS if row[0] != "15dk" and (enabled is None or row[0] in enabled)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        if with_chart and chart_interval == "15m":
            fut_chart = pool.submit(cached_klines, symbol, "15m", True, CHART_BARS, True)
            fut_perp = None
        else:
            fut_perp = pool.submit(cached_klines, symbol, "15m", True, HISTORY_BARS, True)
            fut_chart = pool.submit(cached_klines, symbol, chart_interval, True, CHART_BARS) if with_chart else None
        fut_spot = pool.submit(cached_klines, symbol, "15m", False, HISTORY_BARS)
        from desktop.oi_feed import load_oi
        from desktop.cvd_feed import load_cvd

        fut_oi = pool.submit(load_oi, symbol)
        fut_cvd = pool.submit(load_cvd, symbol)
        fut_live = pool.submit(_live, symbol)
        extra = {
            label: (
                pool.submit(_fresh_klines, symbol, interval, True, bar_ms),
                pool.submit(_fresh_klines, symbol, interval, False, bar_ms),
            )
            for label, interval, bar_ms in wanted
        }
        try:
            if fut_perp is None and fut_chart is not None:
                chart_bars = list(fut_chart.result())[-CHART_BARS:]
                perp = chart_bars[-HISTORY_BARS:]
            else:
                perp = list(fut_perp.result()) if fut_perp is not None else []
                chart_bars = list(fut_chart.result())[-CHART_BARS:] if fut_chart is not None else []
        except MarketError as exc:
            raise MarketError(str(exc)) from exc
        spot = fut_spot.result()
        oi_pack = fut_oi.result()
        oi_points = list(oi_pack.series.get("15m") or [])
        from desktop.funding_feed import load_funding

        fund_pack = load_funding(symbol)
        funding_points = fund_pack.hourly
        funding = fund_pack.live
        next_ms = fund_pack.next_ms
        mark, _binance_oi, _binance_funding, _binance_next = fut_live.result()
        try:
            cvd_pack = fut_cvd.result()
        except Exception:
            cvd_pack = None
        fetched = {
            label: (perp_f.result(), spot_f.result())
            for label, (perp_f, spot_f) in extra.items()
        }

    if not perp:
        raise MarketError(f"{symbol} mum verisi boş")
    if with_chart and not chart_bars:
        chart_bars = perp
        warning = f"{chart_interval} grafiği alınamadı, 15dk gösteriliyor"

    price = chart_bars[-1].close if chart_bars else mark
    if price is None:
        price = perp[-1].close
    oi_base = oi_pack.live
    if oi_base is None and oi_points:
        oi_base = oi_points[-1].value
    oi_usd = oi_base * price if oi_base is not None and price is not None else None
    now_ms = int(time.time() * 1000)
    windows: list[Window] = []
    bundles: dict[str, tuple] = {}
    for label, _interval, bar_ms in ANALYSIS:
        if enabled is not None and label not in enabled:
            continue
        if label == "15dk":
            series_perp, series_spot, series_oi = list(perp), list(spot), list(oi_points)
            if series_perp and series_perp[-1].time_ms + bar_ms > now_ms and mark is not None:
                last = series_perp[-1]
                series_perp[-1] = Bar(
                    last.time_ms,
                    last.open,
                    max(last.high, mark),
                    min(last.low, mark),
                    mark,
                    last.cvd_delta,
                    last.volume,
                )
            if series_perp and oi_base is not None:
                stamp = series_perp[-1].time_ms
                series_oi = [point for point in series_oi if point.time_ms < stamp]
                series_oi.append(Point(stamp, oi_base))
        else:
            series_perp, series_spot = fetched[label]
            period = _OI_PERIOD[_interval]
            series_oi = list(oi_pack.series.get(period) or [])
        closed_probe = series_perp if label == "15dk" else _closed_bars(series_perp, now_ms, bar_ms)
        last_bar = closed_probe[-1] if closed_probe else None
        if last_bar is None or cvd_pack is None:
            feed_perp = feed_spot = None
        else:
            feed_perp = cvd_pack.bar("perp", last_bar.time_ms, last_bar.time_ms + bar_ms, now_ms)
            feed_spot = cvd_pack.bar("spot", last_bar.time_ms, last_bar.time_ms + bar_ms, now_ms)
        window, bundle = _one_window(
            label,
            series_perp,
            series_spot,
            series_oi,
            bar_ms,
            now_ms,
            funding_points,
            funding,
            keep_open=label == "15dk",
            cvd_perp=feed_perp,
            cvd_spot=feed_spot,
            from_feed=True,
        )
        windows.append(window)
        bundles[label] = bundle
    funding_status, reads = build_reads(bundles, funding, enabled)

    return Snapshot(
        symbol=symbol,
        base=base_of(symbol),
        interval=chart_interval if chart_bars is not perp or chart_interval == "15m" else "15m",
        price=price,
        oi_base=oi_base,
        oi_usd=oi_usd,
        funding_percent=funding,
        next_funding_ms=next_ms,
        cvd_spot_day=None if cvd_pack is None else cvd_pack.spot_day,
        cvd_perp_day=None if cvd_pack is None else cvd_pack.perp_day,
        candles=chart_bars,
        oi_points=oi_points,
        windows=windows,
        funding_status=funding_status,
        reads=reads,
        funding_hourly=hourly_funding(funding_points, funding, now_ms),
        warning=warning,
        session_change=_session_price_pct(perp),
        oi_venues_up=oi_pack.up,
        oi_venues_known=oi_pack.known,
        funding_venues_up=fund_pack.up,
        funding_venues_known=fund_pack.known,
        cvd_perp_up=0 if cvd_pack is None else cvd_pack.perp_up,
        cvd_perp_known=0 if cvd_pack is None else cvd_pack.perp_known,
        cvd_spot_up=0 if cvd_pack is None else cvd_pack.spot_up,
        cvd_spot_known=0 if cvd_pack is None else cvd_pack.spot_known,
    )
