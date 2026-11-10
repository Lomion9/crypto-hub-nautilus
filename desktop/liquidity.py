"""Tahmini likidasyon haritası. Borsalar pozisyon fiyatını yayınlamaz; bu bir modeldir.

Her sözleşme kendi saati, coin açık pozisyonu ve kaldıraç basamağıyla kurulur.
USDT, USDC ve USD ayrı kitaptır. Kovalar ortak fiyat adımında dolar olarak toplanır.
Coin bir sözleşmede yoksa o kitap atlanır.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

from desktop.market import (
    HOUR_MS,
    Bar,
    MarketError,
    Point,
    fetch_hourly_oi,
    fetch_klines,
    fetch_leverage_tier,
)

# 3x ve 4x yok. Görünür bant 25x, 50x ve 100x'te daha ağır. Toplam 12.
LEVERAGE = {5: 3, 10: 3, 20: 1, 25: 2, 50: 2, 100: 1}


@dataclass(frozen=True)
class Cluster:
    price: float
    usd: float


@dataclass
class VenueBook:
    """Bir sözleşmenin kendi saati, coin açık pozisyonu ve kaldıraç tavanı."""

    bars: list[Bar]
    oi: list[Point]
    margin: float
    max_leverage: int
    name: str = ""
    inverse: bool = False


@dataclass
class LiquidityMap:
    symbol: str
    step: float
    times: list[int] = field(default_factory=list)
    opens: list[float] = field(default_factory=list)
    closes: list[float] = field(default_factory=list)
    lows: list[float] = field(default_factory=list)
    highs: list[float] = field(default_factory=list)
    columns: list[dict[int, float]] = field(default_factory=list)
    levels: list[dict[int, float]] = field(default_factory=list)
    long_peaks: list[Cluster] = field(default_factory=list)
    short_peaks: list[Cluster] = field(default_factory=list)
    source_bars: list[Bar] = field(default_factory=list)
    source_oi: list[Point] = field(default_factory=list)
    margin: float = 0.004
    max_leverage: int = 100
    venues: list[VenueBook] = field(default_factory=list)

    def has_mass(self) -> bool:
        return any(column for column in self.columns)


def nice_step(price: float) -> float:
    """Fiyatın yaklaşık %0.01'i, 1 / 2 / 2.5 / 5 × 10ⁿ basamağına yuvarlanır."""
    raw = abs(price) * 0.0001
    if raw <= 0:
        return 1e-8
    exponent = math.floor(math.log10(raw))
    base = 10**exponent
    mantissa = raw / base
    for nice in (1, 2, 2.5, 5, 10):
        if mantissa <= nice:
            return nice * base
    return 10 * base


def _weights(max_leverage: int) -> dict[int, float]:
    chosen = {level: weight for level, weight in LEVERAGE.items() if level <= max_leverage}
    if not chosen:
        chosen = {min(LEVERAGE): 1}
    total = sum(chosen.values())
    return {level: weight / total for level, weight in chosen.items()}


def _scale(book: dict[int, float], factor: float) -> None:
    if factor == 1:
        return
    dead: list[int] = []
    for key, coins in book.items():
        coins *= factor
        if coins <= 1e-8:
            dead.append(key)
        else:
            book[key] = coins
    for key in dead:
        del book[key]


def _add(
    book: dict[int, float],
    price: float,
    coins: float,
    step: float,
    mass: dict[int, float] | None = None,
) -> None:
    if coins <= 0 or price <= 0:
        return
    index = int(price / step)
    book[index] = book.get(index, 0.0) + coins
    if mass is not None:
        mass[index] = mass.get(index, 0.0) + price * coins


def _scale_mass(book: dict[int, float], mass: dict[int, float], factor: float) -> None:
    _scale(book, factor)
    for key in list(mass):
        if key not in book:
            del mass[key]
        else:
            mass[key] *= factor


def _erase_mass(
    book: dict[int, float],
    mass: dict[int, float],
    step: float,
    *,
    above: float | None = None,
    below: float | None = None,
) -> None:
    """Fiyatın ulaştığı likidasyonları siler. Kovanın ortası değil, içindeki gerçek ortalama fiyat sınanır."""
    if above is None and below is None:
        return
    mass_get = mass.get
    dead: list[int] = []
    for key, coins in book.items():
        weighted = mass_get(key, 0.0)
        level = weighted / coins if coins > 0 and weighted > 0 else (key + 0.5) * step
        if above is not None and level >= above:
            dead.append(key)
        elif below is not None and level <= below:
            dead.append(key)
    for key in dead:
        del book[key]
        mass.pop(key, None)


def _bucket_price(index: int, coins: dict[int, float], mass: dict[int, float], step: float) -> float:
    amount = coins.get(index, 0.0)
    weighted = mass.get(index, 0.0)
    if amount > 0 and weighted > 0:
        return weighted / amount
    return (index + 0.5) * step


def _snapshot(
    longs: dict[int, float],
    long_mass: dict[int, float],
    shorts: dict[int, float],
    short_mass: dict[int, float],
    step: float,
) -> tuple[dict[int, float], dict[int, float]]:
    """O saatin dolar sütunu ve kova fiyatları, tek geçişte."""
    column: dict[int, float] = {}
    found: dict[int, float] = {}
    short_get = shorts.get
    short_mass_get = short_mass.get
    long_mass_get = long_mass.get
    for index, coins in longs.items():
        if coins > 0:
            column[index] = coins * ((index + 0.5) * step)
        amount = coins + short_get(index, 0.0)
        weighted = long_mass_get(index, 0.0) + short_mass_get(index, 0.0)
        if amount > 0 and weighted > 0:
            found[index] = weighted / amount
    column_get = column.get
    for index, coins in shorts.items():
        if coins > 0:
            column[index] = column_get(index, 0.0) + coins * ((index + 0.5) * step)
        if index in longs:
            continue
        weighted = short_mass_get(index, 0.0)
        if coins > 0 and weighted > 0:
            found[index] = weighted / coins
    return column, found


def _peaks(
    book: dict[int, float],
    step: float,
    price: float,
    side: str,
    mass: dict[int, float] | None = None,
) -> list[Cluster]:
    ranked: list[tuple[float, float]] = []
    for index, coins in book.items():
        if coins <= 0:
            continue
        level = _bucket_price(index, book, mass or {}, step)
        if side == "long" and level >= price:
            continue
        if side == "short" and level <= price:
            continue
        ranked.append((level, coins * level))
    ranked.sort(key=lambda item: item[1], reverse=True)
    chosen: list[tuple[float, float]] = []
    for level, usd in ranked:
        if usd <= 0:
            continue
        if all(abs(level - previous) / price >= 0.01 for previous, _usd in chosen):
            chosen.append((level, usd))
        if len(chosen) == 5:
            break
    chosen.sort(key=lambda item: abs(item[0] - price))
    return [Cluster(level, usd) for level, usd in chosen]


def _liq_price(typical: float, level: int, margin: float, inverse: bool, side: str) -> float:
    """Lineer ve ters sözleşmenin likidasyon fiyatı. Ters kitap lineer formüle girmez."""
    if typical <= 0 or level < 1:
        return 0.0
    if not inverse:
        if side == "long":
            return typical * (1 - 1 / level + margin)
        return typical * (1 + 1 / level - margin)
    if side == "long":
        denom = level + 1 - margin * level
    else:
        denom = level - 1 + margin * level
    if denom <= 0:
        return 0.0
    return typical * level / denom


def build_map(
    symbol: str,
    bars: list[Bar],
    oi_points: list[Point],
    margin: float,
    max_leverage: int,
    conserved: bool = False,
    sweep: dict[int, tuple[float, float]] | None = None,
    inverse: bool = False,
) -> LiquidityMap:
    """Saat saat açık pozisyonu kovalar. OI artışı yeni pozisyon, düşüş oransal kapanıştır.
    sweep: saat → (en düşük, en yüksek); grafikte gösterilen borsanın fitili de silmeye katılır."""
    result = LiquidityMap(symbol=symbol.upper(), step=1.0)
    if not bars:
        return result
    last_close = bars[-1].close
    if last_close <= 0:
        return result
    step = nice_step(last_close)
    weights = _weights(max_leverage)
    oi_at: dict[int, float] = {}
    for point in oi_points:
        if point.value > 0:
            oi_at[point.time_ms // HOUR_MS * HOUR_MS] = point.value
    longs: dict[int, float] = {}
    shorts: dict[int, float] = {}
    long_mass: dict[int, float] = {}
    short_mass: dict[int, float] = {}
    previous: float | None = None
    times: list[int] = []
    opens: list[float] = []
    closes: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    columns: list[dict[int, float]] = []
    levels: list[dict[int, float]] = []
    for bar in bars:
        if bar.close <= 0:
            continue
        low = bar.low
        high = bar.high
        if sweep:
            reach = sweep.get(bar.time_ms // HOUR_MS * HOUR_MS)
            if reach is not None:
                low = min(low, reach[0])
                high = max(high, reach[1])
        _erase_mass(longs, long_mass, step, above=low)
        _erase_mass(shorts, short_mass, step, below=high)
        current = oi_at.get(bar.time_ms // HOUR_MS * HOUR_MS)
        if current is not None and current > 0 and previous is not None:
            delta = current - previous
            if delta < 0 and previous > 0:
                _scale_mass(longs, long_mass, current / previous)
                _scale_mass(shorts, short_mass, current / previous)
            elif delta > 0:
                typical = (bar.high + bar.low + bar.close) / 3
                if bar.volume > 0:
                    share = ((bar.volume + bar.cvd_delta) / 2) / bar.volume
                else:
                    share = 0.5
                share = min(1.0, max(0.0, share))
                if conserved:
                    new_longs = delta * share
                    new_shorts = delta * (1 - share)
                else:
                    new_longs = delta * min(1.0, 2 * share)
                    new_shorts = delta * min(1.0, 2 * (1 - share))
                for level, weight in weights.items():
                    _add(
                        longs,
                        _liq_price(typical, level, margin, inverse, "long"),
                        new_longs * weight,
                        step,
                        long_mass,
                    )
                    _add(
                        shorts,
                        _liq_price(typical, level, margin, inverse, "short"),
                        new_shorts * weight,
                        step,
                        short_mass,
                    )
        if current is not None and current > 0:
            previous = current
        times.append(bar.time_ms)
        opens.append(bar.open)
        closes.append(bar.close)
        lows.append(bar.low)
        highs.append(bar.high)
        column, found = _snapshot(longs, long_mass, shorts, short_mass, step)
        columns.append(column)
        levels.append(found)
    price = closes[-1] if closes else last_close
    result.step = step
    result.times = times
    result.opens = opens
    result.closes = closes
    result.lows = lows
    result.highs = highs
    result.columns = columns
    result.levels = levels
    result.long_peaks = _peaks(longs, step, price, "long", long_mass)
    result.short_peaks = _peaks(shorts, step, price, "short", short_mass)
    return result


def _peaks_usd(
    column: dict[int, float],
    step: float,
    price: float,
    side: str,
    levels: dict[int, float] | None = None,
) -> list[Cluster]:
    ranked: list[tuple[float, float]] = []
    prices = levels or {}
    for index, usd in column.items():
        level = prices.get(index, (index + 0.5) * step)
        if side == "long" and level >= price:
            continue
        if side == "short" and level <= price:
            continue
        if usd <= 0:
            continue
        ranked.append((level, usd))
    ranked.sort(key=lambda item: item[1], reverse=True)
    kept: list[tuple[float, float]] = []
    for level, usd in ranked:
        if all(abs(level - prev) / price >= 0.01 for prev, _usd in kept):
            kept.append((level, usd))
        if len(kept) == 5:
            break
    kept.sort(key=lambda item: abs(item[0] - price))
    return [Cluster(level, usd) for level, usd in kept]


def merge_venues(symbol: str, venues: list[VenueBook], conserved: bool = False) -> LiquidityMap:
    """Her borsanın kendi haritasını kurar, dolar kovalarını ortak adımda toplar."""
    sweep_venue = next((venue for venue in venues if venue.name == "binance-usdt"), None)
    if sweep_venue is None and venues:
        sweep_venue = venues[0]
    sweep: dict[int, tuple[float, float]] = {}
    if sweep_venue is not None:
        for bar in sweep_venue.bars:
            if bar.low > 0 and bar.high > 0:
                sweep[bar.time_ms // HOUR_MS * HOUR_MS] = (bar.low, bar.high)
    built = [
        build_map(
            symbol,
            venue.bars,
            venue.oi,
            venue.margin,
            venue.max_leverage,
            conserved,
            sweep,
            venue.inverse,
        )
        for venue in venues
    ]
    built = [book for book in built if book.times and book.closes]
    result = LiquidityMap(symbol=symbol.upper(), step=1.0, venues=list(venues))
    if not built:
        return result
    anchor = next((book.closes[-1] for book in built if book.closes[-1] > 0), 0.0)
    if anchor <= 0:
        return result
    step = nice_step(anchor)
    slots: dict[int, list] = {}
    for book in built:
        src_step = book.step
        same = src_step == step
        for index, stamp in enumerate(book.times):
            hour = stamp // HOUR_MS * HOUR_MS
            prices = book.levels[index] if index < len(book.levels) else {}
            price_get = prices.get
            column = book.columns[index]
            slot = slots.get(hour)
            if slot is None and same:
                usd_column = {bucket: usd for bucket, usd in column.items() if usd > 0}
                slots[hour] = [
                    book.opens[index],
                    book.closes[index],
                    book.lows[index],
                    book.highs[index],
                    usd_column,
                    {bucket: price_get(bucket, (bucket + 0.5) * src_step) * usd for bucket, usd in usd_column.items()},
                ]
                continue
            if slot is None:
                slot = [book.opens[index], book.closes[index], book.lows[index], book.highs[index], {}, {}]
                slots[hour] = slot
            usd_column = slot[4]
            mass_column = slot[5]
            usd_get = usd_column.get
            mass_get = mass_column.get
            for bucket, usd in column.items():
                if usd <= 0:
                    continue
                raw = price_get(bucket, (bucket + 0.5) * src_step)
                dest = bucket if same else int(((bucket + 0.5) * src_step) / step)
                usd_column[dest] = usd_get(dest, 0.0) + usd
                mass_column[dest] = mass_get(dest, 0.0) + raw * usd
    for hour in sorted(slots):
        opened, closed, low, high, column, mass = slots[hour]
        level_column = {
            bucket: mass[bucket] / column[bucket]
            for bucket in column
            if column[bucket] > 0 and mass.get(bucket, 0) > 0
        }
        result.times.append(hour)
        result.opens.append(opened)
        result.closes.append(closed)
        result.lows.append(low)
        result.highs.append(high)
        result.columns.append(column)
        result.levels.append(level_column)
    result.step = step
    price = result.closes[-1]
    last_levels = result.levels[-1] if result.levels else {}
    result.long_peaks = _peaks_usd(result.columns[-1], step, price, "long", last_levels)
    result.short_peaks = _peaks_usd(result.columns[-1], step, price, "short", last_levels)
    first = sweep_venue if sweep_venue is not None else venues[0]
    result.source_bars = list(first.bars)
    result.source_oi = list(first.oi)
    result.margin = first.margin
    result.max_leverage = first.max_leverage
    return result


def _read_json(url: str):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "CryptoHub"})
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


def _closed_hours(bars: list[Bar]) -> list[Bar]:
    now_ms = int(time.time() * 1000)
    if bars and bars[-1].time_ms + HOUR_MS > now_ms:
        return bars[:-1]
    return bars


def _trim_venue(
    bars: list[Bar],
    oi_points: list[Point],
    margin: float,
    leverage: int,
    name: str = "",
    inverse: bool = False,
) -> VenueBook | None:
    bars = _closed_hours(bars)
    if bars:
        cutoff = bars[-1].time_ms
        kept = [point for point in oi_points if point.time_ms <= cutoff]
        if not kept and oi_points:
            latest = max(oi_points, key=lambda point: point.time_ms)
            kept = [Point(cutoff, latest.value)]
        oi_points = kept
    if not bars or not oi_points:
        return None
    if margin <= 0 or margin >= 1:
        margin = 0.004
    if leverage < 1:
        leverage = 100
    return VenueBook(bars[-800:], oi_points, margin, leverage, name, inverse)


def _coin(symbol: str) -> str:
    symbol = symbol.upper().strip()
    return symbol[:-4] if symbol.endswith("USDT") else symbol


def _binance_venue(symbol: str) -> VenueBook | None:
    try:
        bars = fetch_klines(symbol, "1h", 800, True)
        oi_points = fetch_hourly_oi(symbol)
        margin, leverage = fetch_leverage_tier(symbol)
    except MarketError:
        return None
    return _trim_venue(bars, oi_points, margin, leverage, "binance-usdt")


def _binance_usdc_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    market = f"{coin}USDC"
    try:
        bars = fetch_klines(market, "1h", 800, True)
        oi_points = fetch_hourly_oi(market)
        margin, leverage = fetch_leverage_tier(market)
    except MarketError:
        return None
    return _trim_venue(bars, oi_points, margin, leverage, "binance-usdc")


def _inverse_coin(contracts: float, value: float, mark: float | None, size: float) -> float | None:
    """Coin-M geçmişinde value bazen coin, bazen dolar. Mark ile ayırt edilir."""
    if contracts <= 0 or size <= 0:
        return None
    if mark and mark > 0:
        from_contracts = contracts * size / mark
        if value > 0 and abs(value - from_contracts) / from_contracts < 0.35:
            return value
        return from_contracts
    if value > 0 and value < contracts:
        return value
    return None


def _dapi_bars(symbol: str) -> list[Bar]:
    payload = _read_json(
        "https://dapi.binance.com/dapi/v1/klines"
        f"?symbol={quote(symbol)}&interval=1h&limit=800"
    )
    if not isinstance(payload, list):
        return []
    bars: list[Bar] = []
    for row in payload:
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


def _dapi_oi(coin: str) -> list[Point]:
    mark_row = _read_json(f"https://dapi.binance.com/dapi/v1/premiumIndex?symbol={quote(coin)}USD_PERP")
    row = mark_row[0] if isinstance(mark_row, list) and mark_row else mark_row
    mark = None
    if isinstance(row, dict):
        try:
            mark = float(row["markPrice"])
        except (KeyError, TypeError, ValueError):
            mark = None
    size = 100.0 if coin == "BTC" else 10.0
    end = int(time.time() * 1000)
    points: list[Point] = []
    seen: set[int] = set()
    for _ in range(4):
        payload = _read_json(
            "https://dapi.binance.com/futures/data/openInterestHist"
            f"?pair={quote(coin)}USD&contractType=PERPETUAL&period=1h&limit=500&endTime={end}"
        )
        if not isinstance(payload, list) or not payload:
            break
        batch: list[Point] = []
        for item in payload:
            try:
                stamp = int(item["timestamp"])
                contracts = float(item["sumOpenInterest"])
                value = float(item["sumOpenInterestValue"])
            except (KeyError, TypeError, ValueError):
                continue
            coin_oi = _inverse_coin(contracts, value, mark, size)
            if stamp in seen or coin_oi is None or coin_oi <= 0:
                continue
            seen.add(stamp)
            batch.append(Point(stamp, coin_oi))
        if not batch:
            break
        points.extend(batch)
        if len(payload) < 500:
            break
        end = min(point.time_ms for point in batch) - 1
    points.sort(key=lambda point: point.time_ms)
    return points


def _binance_usd_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    market = f"{coin}USD_PERP"
    bars = _dapi_bars(market)
    points = _dapi_oi(coin)
    margin, leverage = 0.004, 100
    return _trim_venue(bars, points, margin, leverage, "binance-usd", True)


def _bybit_venue(symbol: str) -> VenueBook | None:
    return _bybit_book(symbol, "linear", "bybit-usdt", False)


def _bybit_usdc_venue(symbol: str) -> VenueBook | None:
    return _bybit_book(f"{_coin(symbol)}PERP", "linear", "bybit-usdc", False)


def _bybit_usd_venue(symbol: str) -> VenueBook | None:
    return _bybit_book(f"{_coin(symbol)}USD", "inverse", "bybit-usd", True)


def _bybit_book(market: str, category: str, name: str, inverse: bool) -> VenueBook | None:
    payload = _read_json(
        "https://api.bybit.com/v5/market/kline"
        f"?category={quote(category)}&symbol={quote(market)}&interval=60&limit=1000"
    )
    rows = ((payload or {}).get("result") or {}).get("list") or []
    bars: list[Bar] = []
    for row in rows:
        try:
            volume = float(row[5])
            bars.append(
                Bar(
                    time_ms=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    cvd_delta=0.0,
                    volume=volume,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.time_ms)
    closes = {bar.time_ms // HOUR_MS * HOUR_MS: bar.close for bar in bars if bar.close > 0}
    points: list[Point] = []
    seen: set[int] = set()
    cursor = ""
    for _ in range(6):
        url = (
            "https://api.bybit.com/v5/market/open-interest"
            f"?category={quote(category)}&symbol={quote(market)}&intervalTime=1h&limit=200"
        )
        if cursor:
            url += "&cursor=" + (cursor if "%" in cursor else quote(cursor, safe=""))
        page = _read_json(url)
        result = (page or {}).get("result") or {}
        batch = result.get("list") or []
        if not batch:
            break
        for row in batch:
            try:
                stamp = int(row["timestamp"])
                value = float(row["openInterest"])
            except (KeyError, TypeError, ValueError):
                continue
            if inverse:
                price = closes.get(stamp // HOUR_MS * HOUR_MS)
                if not price or price <= 0:
                    continue
                value = value / price
            if stamp in seen or value <= 0:
                continue
            seen.add(stamp)
            points.append(Point(stamp, value))
        cursor = str(result.get("nextPageCursor") or "")
        if not cursor or len(batch) < 200:
            break
    points.sort(key=lambda point: point.time_ms)
    risk = _read_json(
        "https://api.bybit.com/v5/market/risk-limit"
        f"?category={quote(category)}&symbol={quote(market)}"
    )
    tiers = ((risk or {}).get("result") or {}).get("list") or []
    margin, leverage = 0.004, 100
    chosen = next((row for row in tiers if row.get("isLowestRisk") in (1, "1", True)), None)
    if chosen is None and tiers:
        chosen = tiers[0]
    if isinstance(chosen, dict):
        try:
            margin = float(chosen["maintenanceMargin"])
            leverage = int(float(chosen["maxLeverage"]))
        except (KeyError, TypeError, ValueError):
            margin, leverage = 0.004, 100
    return _trim_venue(bars, points, margin, leverage, name, inverse)


def _okx_inst(symbol: str) -> tuple[str, str]:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return f"{base}-USDT-SWAP", f"{base}-USDT"


def _okx_pages(url: str, pages: int) -> list:
    rows: list = []
    end = ""
    for _ in range(pages):
        page_url = url if not end else f"{url}&end={end}"
        payload = _read_json(page_url)
        if not payload or str(payload.get("code")) not in ("0", "None"):
            if payload and str(payload.get("code")) != "0":
                break
        batch = (payload or {}).get("data") or []
        if not batch:
            break
        rows.extend(batch)
        try:
            end = str(int(batch[-1][0]) - 1)
        except (IndexError, TypeError, ValueError):
            break
        if len(batch) < 100:
            break
    return rows


def _okx_venue(symbol: str) -> VenueBook | None:
    inst, family = _okx_inst(symbol)
    return _okx_book(inst, family, "okx-usdt", False)


def _okx_usd_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    return _okx_book(f"{coin}-USD-SWAP", f"{coin}-USD", "okx-usd", True)


def _okx_book(inst: str, family: str, name: str, inverse: bool) -> VenueBook | None:
    inst_q = quote(inst)
    candle_rows: list = []
    latest = _read_json(f"https://www.okx.com/api/v5/market/candles?instId={inst_q}&bar=1H&limit=300")
    if latest and str(latest.get("code")) == "0":
        candle_rows.extend(latest.get("data") or [])
    after = ""
    if candle_rows:
        try:
            after = str(candle_rows[-1][0])
        except (IndexError, TypeError, ValueError):
            after = ""
    for _ in range(3):
        if not after:
            break
        older = _read_json(
            "https://www.okx.com/api/v5/market/history-candles"
            f"?instId={inst_q}&bar=1H&limit=300&after={after}"
        )
        batch = (older or {}).get("data") or []
        if not older or str(older.get("code")) != "0" or not batch:
            break
        candle_rows.extend(batch)
        try:
            after = str(batch[-1][0])
        except (IndexError, TypeError, ValueError):
            break
        if len(batch) < 100:
            break
    taker_rows = _okx_pages(
        "https://www.okx.com/api/v5/rubik/stat/taker-volume-contract"
        f"?instId={inst_q}&period=1H&unit=1",
        9,
    )
    taker: dict[int, tuple[float, float]] = {}
    for row in taker_rows:
        try:
            hour = int(row[0]) // HOUR_MS * HOUR_MS
            sell = float(row[1])
            buy = float(row[2])
        except (IndexError, TypeError, ValueError):
            continue
        taker[hour] = (buy, sell)
    bars: list[Bar] = []
    seen_bars: set[int] = set()
    for row in candle_rows:
        try:
            stamp = int(row[0])
            volume = float(row[6])
        except (IndexError, TypeError, ValueError):
            continue
        if stamp in seen_bars or volume < 0:
            continue
        seen_bars.add(stamp)
        buy_sell = taker.get(stamp // HOUR_MS * HOUR_MS)
        if buy_sell is not None and buy_sell[0] + buy_sell[1] > 0:
            buy, sell = buy_sell
            cvd = buy - sell
            share_volume = buy + sell
        else:
            cvd = 0.0
            share_volume = volume
        bars.append(
            Bar(
                time_ms=stamp,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                cvd_delta=cvd,
                volume=share_volume,
            )
        )
    bars.sort(key=lambda bar: bar.time_ms)
    oi_rows = _okx_pages(
        "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
        f"?instId={inst_q}&period=1H",
        9,
    )
    points: list[Point] = []
    seen_oi: set[int] = set()
    for row in oi_rows:
        try:
            stamp = int(row[0])
            value = float(row[2])
        except (IndexError, TypeError, ValueError):
            continue
        if stamp in seen_oi or value <= 0:
            continue
        seen_oi.add(stamp)
        points.append(Point(stamp, value))
    points.sort(key=lambda point: point.time_ms)
    tiers = _read_json(
        "https://www.okx.com/api/v5/public/position-tiers"
        f"?instType=SWAP&tdMode=cross&instFamily={quote(family)}"
    )
    margin, leverage = 0.004, 100
    best = None
    for row in (tiers or {}).get("data") or []:
        try:
            lever = int(float(row["maxLever"]))
            mmr = float(row["mmr"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or lever > best[0]:
            best = (lever, mmr)
    if best is not None:
        leverage, margin = best
    if str((tiers or {}).get("code")) == "51001":
        return None
    return _trim_venue(bars, points, margin, leverage, name, inverse)


HL_INFO = "https://api.hyperliquid.xyz/info"


def _post_json(url: str, body: dict):
    try:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "CryptoHub"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError, TimeoutError, ValueError):
        return None


def _hl_oi_path() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, "CryptoHub")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "hl_oi.json")


def _hl_oi_series(coin: str, live: float, now_ms: int) -> list[Point]:
    """Hyperliquid yalnızca anlık açık pozisyon verir. Her saat o değer saklanır."""
    path = _hl_oi_path()
    stored: dict = {}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            stored = raw
    except (OSError, json.JSONDecodeError):
        stored = {}
    points: list[Point] = []
    rows = stored.get(coin)
    if isinstance(rows, list):
        for row in rows:
            try:
                points.append(Point(int(row[0]), float(row[1])))
            except (IndexError, TypeError, ValueError):
                continue
    hour = now_ms // HOUR_MS * HOUR_MS
    if live > 0 and (not points or points[-1].time_ms != hour):
        points.append(Point(hour, live))
    cutoff = hour - 40 * 24 * HOUR_MS
    points = [point for point in points if point.time_ms >= cutoff and point.value > 0]
    points.sort(key=lambda point: point.time_ms)
    stored[coin] = [[point.time_ms, point.value] for point in points]
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(stored, handle)
    except OSError:
        pass
    return points


def _hyperliquid_venue(symbol: str) -> VenueBook | None:
    if not symbol.endswith("USDT"):
        return None
    coin = symbol[:-4]
    now_ms = int(time.time() * 1000)
    start = now_ms - 40 * 24 * HOUR_MS
    bars: list[Bar] = []
    seen: set[int] = set()
    for _ in range(4):
        payload = _post_json(
            HL_INFO,
            {
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": "1h", "startTime": start, "endTime": now_ms},
            },
        )
        if not isinstance(payload, list) or not payload:
            break
        last = start
        for row in payload:
            try:
                stamp = int(row["t"])
                volume = float(row["v"])
                bar = Bar(
                    time_ms=stamp,
                    open=float(row["o"]),
                    high=float(row["h"]),
                    low=float(row["l"]),
                    close=float(row["c"]),
                    cvd_delta=0.0,
                    volume=volume,
                )
            except (KeyError, TypeError, ValueError):
                continue
            last = max(last, stamp)
            if stamp in seen:
                continue
            seen.add(stamp)
            bars.append(bar)
        if len(payload) < 500 or last + HOUR_MS >= now_ms - HOUR_MS:
            break
        start = last + HOUR_MS
    bars.sort(key=lambda bar: bar.time_ms)
    snapshot = _post_json(HL_INFO, {"type": "metaAndAssetCtxs"})
    if not isinstance(snapshot, list) or len(snapshot) < 2:
        return None
    meta, contexts = snapshot[0], snapshot[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(contexts, list):
        return None
    asset = None
    context = None
    for index, row in enumerate(universe):
        if isinstance(row, dict) and row.get("name") == coin and index < len(contexts):
            asset = row
            context = contexts[index]
            break
    if not isinstance(asset, dict) or not isinstance(context, dict):
        return None
    try:
        live_oi = float(context["openInterest"])
    except (KeyError, TypeError, ValueError):
        return None
    leverage = 40
    tiers = []
    table_id = asset.get("marginTableId")
    for row in meta.get("marginTables") or []:
        try:
            if row[0] == table_id:
                tiers = (row[1] or {}).get("marginTiers") or []
                break
        except (IndexError, TypeError, AttributeError):
            continue
    levers: list[int] = []
    for tier in tiers:
        try:
            levers.append(int(float(tier["maxLeverage"])))
        except (KeyError, TypeError, ValueError):
            continue
    if not levers:
        try:
            levers.append(int(float(asset["maxLeverage"])))
        except (KeyError, TypeError, ValueError):
            levers.append(40)
    leverage = max(levers)
    margin = 1.0 / (2 * leverage) if leverage >= 1 else 0.004
    points = _hl_oi_series(coin, live_oi, now_ms)
    return _trim_venue(bars, points, margin, leverage, "hyperliquid-usdc")


def _iso_ms(text: str) -> int | None:
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


_MAP_OI_LOCK = threading.Lock()


def _map_oi_path() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, "CryptoHub")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "map_oi.json")


def _map_oi_series(key: str, live: float, now_ms: int) -> list[Point]:
    """Borsa geçmişi olmayan kitapta her saat bir örnek. Tek örnek kütle yazmaz."""
    path = _map_oi_path()
    with _MAP_OI_LOCK:
        stored: dict = {}
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                stored = raw
        except (OSError, json.JSONDecodeError):
            stored = {}
        points: list[Point] = []
        rows = stored.get(key)
        if isinstance(rows, list):
            for row in rows:
                try:
                    points.append(Point(int(row[0]), float(row[1])))
                except (IndexError, TypeError, ValueError):
                    continue
        hour = now_ms // HOUR_MS * HOUR_MS
        if live > 0 and (not points or points[-1].time_ms != hour):
            points.append(Point(hour, live))
        cutoff = hour - 40 * 24 * HOUR_MS
        points = [point for point in points if point.time_ms >= cutoff and point.value > 0]
        points.sort(key=lambda point: point.time_ms)
        stored[key] = [[point.time_ms, point.value] for point in points]
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(stored, handle)
        except OSError:
            pass
        return points


def _htx_leverage(url: str) -> tuple[float, int]:
    payload = _read_json(url)
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    levers: list[int] = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        for item in row.get("list") or []:
            try:
                levers.append(int(float(item["lever_rate"])))
            except (KeyError, TypeError, ValueError):
                continue
    if not levers:
        return 0.004, 100
    leverage = max(levers)
    margin = 1.0 / (2 * leverage) if leverage >= 1 else 0.004
    return margin, leverage


def _htx_bars(url: str) -> list[Bar]:
    payload = _read_json(url)
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    bars: list[Bar] = []
    for row in rows:
        try:
            bars.append(
                Bar(
                    time_ms=int(row["id"]) * 1000,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    cvd_delta=0.0,
                    volume=float(row["amount"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.time_ms)
    return bars


def _htx_oi(url: str) -> list[Point]:
    payload = _read_json(url)
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return []
    data = payload.get("data")
    ticks = data.get("tick") if isinstance(data, dict) else data
    if not isinstance(ticks, list):
        return []
    points: list[Point] = []
    seen: set[int] = set()
    for row in ticks:
        try:
            stamp = int(row["ts"])
            value = float(row["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        if stamp in seen or value <= 0:
            continue
        seen.add(stamp)
        points.append(Point(stamp, value))
    points.sort(key=lambda point: point.time_ms)
    return points


def _htx_usdt_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    code = quote(f"{coin}-USDT")
    bars = _htx_bars(
        "https://api.hbdm.com/linear-swap-ex/market/history/kline"
        f"?contract_code={code}&period=60min&size=2000"
    )
    points = _htx_oi(
        "https://api.hbdm.com/linear-swap-api/v1/swap_his_open_interest"
        f"?contract_code={code}&period=60min&size=200&amount_type=2"
    )
    margin, leverage = _htx_leverage(
        f"https://api.hbdm.com/linear-swap-api/v1/swap_ladder_margin?contract_code={code}"
    )
    return _trim_venue(bars, points, margin, leverage, "htx-usdt")


def _htx_usd_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    code = quote(f"{coin}-USD")
    bars = _htx_bars(
        "https://api.hbdm.com/swap-ex/market/history/kline"
        f"?contract_code={code}&period=60min&size=2000"
    )
    points = _htx_oi(
        "https://api.hbdm.com/swap-api/v1/swap_his_open_interest"
        f"?contract_code={code}&period=60min&size=200&amount_type=2"
    )
    margin, leverage = _htx_leverage(
        f"https://api.hbdm.com/swap-api/v1/swap_ladder_margin?contract_code={code}"
    )
    return _trim_venue(bars, points, margin, leverage, "htx-usd", True)


def _bitfinex_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    key = f"t{coin}F0:USTF0"
    payload = _read_json(
        f"https://api-pub.bitfinex.com/v2/candles/trade:1h:{quote(key, safe=':')}/hist?limit=1000&sort=-1"
    )
    if isinstance(payload, list) and payload and payload[0] == "error":
        return None
    bars: list[Bar] = []
    if isinstance(payload, list):
        for row in payload:
            try:
                bars.append(
                    Bar(
                        time_ms=int(row[0]),
                        open=float(row[1]),
                        high=float(row[3]),
                        low=float(row[4]),
                        close=float(row[2]),
                        cvd_delta=0.0,
                        volume=float(row[5]),
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
    bars.sort(key=lambda bar: bar.time_ms)
    rows: list = []
    end = None
    now_ms = int(time.time() * 1000)
    for _ in range(12):
        url = f"https://api-pub.bitfinex.com/v2/status/deriv/{quote(key, safe=':')}/hist?limit=5000&sort=-1"
        if end is not None:
            url += f"&end={end}"
        page = _read_json(url)
        if isinstance(page, list) and page and page[0] == "error":
            break
        if not isinstance(page, list) or not page or not isinstance(page[0], list):
            break
        rows.extend(page)
        try:
            oldest = min(int(row[0]) for row in page)
        except (TypeError, ValueError, IndexError):
            break
        if now_ms - oldest >= 800 * HOUR_MS or len(page) < 5000:
            break
        end = oldest - 1
    buckets: dict[int, float] = {}
    for row in rows:
        try:
            stamp = int(row[0]) // HOUR_MS * HOUR_MS
            value = float(row[17])
        except (IndexError, TypeError, ValueError):
            continue
        if value > 0:
            buckets[stamp] = value
    points = [Point(stamp, value) for stamp, value in sorted(buckets.items())]
    return _trim_venue(bars, points, 0.004, 100, "bitfinex-usdt")


def _deribit_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    now_ms = int(time.time() * 1000)
    start = now_ms - 40 * 24 * HOUR_MS
    payload = _read_json(
        "https://www.deribit.com/api/v2/public/get_tradingview_chart_data"
        f"?instrument_name={quote(coin)}-PERPETUAL&start_timestamp={start}&end_timestamp={now_ms}&resolution=60"
    )
    result = (payload or {}).get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return None
    ticks = result.get("ticks") or []
    opens = result.get("open") or []
    highs = result.get("high") or []
    lows = result.get("low") or []
    closes = result.get("close") or []
    volumes = result.get("volume") or []
    bars: list[Bar] = []
    for index, stamp in enumerate(ticks):
        try:
            bars.append(
                Bar(
                    time_ms=int(stamp),
                    open=float(opens[index]),
                    high=float(highs[index]),
                    low=float(lows[index]),
                    close=float(closes[index]),
                    cvd_delta=0.0,
                    volume=float(volumes[index]) if index < len(volumes) else 0.0,
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.time_ms)
    summary = _read_json(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_instrument"
        f"?instrument_name={quote(coin)}-PERPETUAL"
    )
    rows = (summary or {}).get("result") if isinstance(summary, dict) else None
    live = 0.0
    if isinstance(rows, list) and rows:
        try:
            mark = float(rows[0]["mark_price"])
            usd = float(rows[0]["open_interest"])
            if mark > 0 and usd > 0:
                live = usd / mark
        except (KeyError, TypeError, ValueError, IndexError):
            live = 0.0
    instrument = _read_json(
        "https://www.deribit.com/api/v2/public/get_instrument"
        f"?instrument_name={quote(coin)}-PERPETUAL"
    )
    info = (instrument or {}).get("result") if isinstance(instrument, dict) else None
    leverage = 50
    if isinstance(info, dict):
        try:
            leverage = int(float(info["max_leverage"]))
        except (KeyError, TypeError, ValueError):
            leverage = 50
    if leverage < 1:
        leverage = 50
    margin = 1.0 / (2 * leverage)
    points = _map_oi_series(f"deribit-usd:{coin}", live, now_ms)
    return _trim_venue(bars, points, margin, leverage, "deribit-usd", True)


def _kraken_symbol(coin: str) -> str:
    base = "XBT" if coin == "BTC" else coin
    return f"PF_{base}USD"


def _kraken_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    market = _kraken_symbol(coin)
    payload = _read_json(f"https://futures.kraken.com/api/charts/v1/trade/{quote(market)}/1h")
    candles = (payload or {}).get("candles") if isinstance(payload, dict) else None
    if not isinstance(candles, list):
        return None
    bars: list[Bar] = []
    for row in candles:
        try:
            bars.append(
                Bar(
                    time_ms=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    cvd_delta=0.0,
                    volume=float(row["volume"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.time_ms)
    instruments = _read_json("https://futures.kraken.com/derivatives/api/v3/instruments")
    listed = (instruments or {}).get("instruments") if isinstance(instruments, dict) else None
    margin, leverage = 0.004, 100
    if isinstance(listed, list):
        found = next((row for row in listed if isinstance(row, dict) and row.get("symbol") == market), None)
        levels = found.get("marginLevels") if isinstance(found, dict) else None
        if isinstance(levels, list) and levels:
            first = min(levels, key=lambda row: float(row.get("numNonContractUnits") or 0))
            try:
                kept_margin = float(first["maintenanceMargin"])
                initial = float(first["initialMargin"])
            except (KeyError, TypeError, ValueError):
                kept_margin, initial = 0.0, 0.0
            if 0 < kept_margin < 1 and initial > 0:
                margin = kept_margin
                leverage = max(1, int(round(1 / initial)))
    tickers = _read_json("https://futures.kraken.com/derivatives/api/v3/tickers")
    rows = (tickers or {}).get("tickers") if isinstance(tickers, dict) else None
    live = 0.0
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("symbol") == market:
                try:
                    live = float(row["openInterest"])
                except (KeyError, TypeError, ValueError):
                    live = 0.0
                break
    now_ms = int(time.time() * 1000)
    points = _map_oi_series(f"kraken-usd:{coin}", live, now_ms)
    return _trim_venue(bars, points, margin, leverage, "kraken-usd")


def _dydx_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    market = f"{coin}-USD"
    rows: list = []
    end = ""
    for _ in range(10):
        url = (
            "https://indexer.dydx.trade/v4/candles/perpetualMarkets/"
            f"{quote(market)}?resolution=1HOUR&limit=100"
        )
        if end:
            url += "&toISO=" + quote(end)
        payload = _read_json(url)
        batch = (payload or {}).get("candles") if isinstance(payload, dict) else None
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(batch)
        try:
            oldest = min(str(row["startedAt"]) for row in batch)
        except (KeyError, TypeError, ValueError):
            break
        if oldest == end or len(batch) < 100:
            break
        end = oldest
    bars: list[Bar] = []
    points: list[Point] = []
    seen: set[int] = set()
    for row in rows:
        stamp = _iso_ms(str(row.get("startedAt") or ""))
        if stamp is None or stamp in seen:
            continue
        try:
            bar = Bar(
                time_ms=stamp,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                cvd_delta=0.0,
                volume=float(row.get("baseTokenVolume") or 0),
            )
            oi_value = float(row["startingOpenInterest"])
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(stamp)
        bars.append(bar)
        if oi_value > 0:
            points.append(Point(stamp, oi_value))
    bars.sort(key=lambda bar: bar.time_ms)
    points.sort(key=lambda point: point.time_ms)
    markets = _read_json("https://indexer.dydx.trade/v4/perpetualMarkets")
    book = ((markets or {}).get("markets") or {}).get(market) if isinstance(markets, dict) else None
    margin, leverage = 0.004, 100
    if isinstance(book, dict) and book.get("maintenanceMarginFraction") not in (None, ""):
        try:
            kept_margin = float(book["maintenanceMarginFraction"])
            initial = float(book["initialMarginFraction"])
        except (KeyError, TypeError, ValueError):
            kept_margin, initial = 0.0, 0.0
        if 0 < kept_margin < 1:
            margin = kept_margin
            leverage = max(1, int(round(1 / initial))) if initial > 0 else 100
    return _trim_venue(bars, points, margin, leverage, "dydx-usd")


def _woo_venue(symbol: str) -> VenueBook | None:
    coin = _coin(symbol)
    market = f"PERP_{coin}_USDT"
    payload = _read_json(f"https://api.woox.io/v1/public/kline?symbol={quote(market)}&type=1h&limit=1000")
    rows = (payload or {}).get("rows") if isinstance(payload, dict) else None
    if payload is not None and isinstance(payload, dict) and payload.get("success") is False:
        return None
    bars: list[Bar] = []
    if isinstance(rows, list):
        for row in rows:
            try:
                bars.append(
                    Bar(
                        time_ms=int(row["start_timestamp"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        cvd_delta=0.0,
                        volume=float(row["volume"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    bars.sort(key=lambda bar: bar.time_ms)
    info = _read_json(f"https://api.woox.io/v1/public/info/{quote(market)}")
    body = (info or {}).get("info") if isinstance(info, dict) else None
    margin, leverage = 0.004, 100
    if isinstance(body, dict):
        try:
            kept_margin = float(body["base_mmr"])
            initial = float(body["base_imr"])
        except (KeyError, TypeError, ValueError):
            kept_margin, initial = 0.0, 0.0
        if 0 < kept_margin < 1:
            margin = kept_margin
            leverage = max(1, int(round(1 / initial))) if initial > 0 else 100
    live_payload = _read_json(f"https://api.woox.io/v1/public/futures/{quote(market)}")
    live_info = (live_payload or {}).get("info") if isinstance(live_payload, dict) else None
    live = 0.0
    if isinstance(live_info, dict):
        try:
            live = float(live_info["open_interest"])
        except (KeyError, TypeError, ValueError):
            live = 0.0
    now_ms = int(time.time() * 1000)
    points = _map_oi_series(f"woo-usdt:{coin}", live, now_ms)
    return _trim_venue(bars, points, margin, leverage, "woo-usdt")


def load_liquidity(symbol: str) -> LiquidityMap:
    symbol = symbol.upper().strip()
    venues: list[VenueBook] = []
    loaders = (
        _binance_venue,
        _binance_usdc_venue,
        _binance_usd_venue,
        _bybit_venue,
        _bybit_usdc_venue,
        _bybit_usd_venue,
        _okx_venue,
        _okx_usd_venue,
        _htx_usdt_venue,
        _htx_usd_venue,
        _bitfinex_venue,
        _hyperliquid_venue,
        _deribit_venue,
        _kraken_venue,
        _dydx_venue,
        _woo_venue,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(loader, symbol) for loader in loaders]
        for job in jobs:
            try:
                venue = job.result()
            except Exception:
                venue = None
            if venue is not None:
                venues.append(venue)
    if not venues:
        raise MarketError(f"{symbol} likidasyon verisi yok")
    book = merge_venues(symbol, venues)
    if not book.times:
        raise MarketError(f"{symbol} likidasyon verisi yok")
    return book


def clip_map(book: LiquidityMap, hours: int, conserved: bool = False) -> LiquidityMap:
    """Haritayı son N kapanmış saate indirir. Kaynak seri durur, ağdan yeniden istenmez."""
    if book.venues:
        clipped: list[VenueBook] = []
        for venue in book.venues:
            bars = venue.bars[-hours:] if hours > 0 else list(venue.bars)
            start = bars[0].time_ms if bars else 0
            oi_points = [point for point in venue.oi if not bars or point.time_ms >= start - HOUR_MS]
            if bars and oi_points:
                clipped.append(
                    VenueBook(
                        bars,
                        oi_points,
                        venue.margin,
                        venue.max_leverage,
                        venue.name,
                        venue.inverse,
                    )
                )
        merged = merge_venues(book.symbol, clipped, conserved)
        merged.venues = book.venues
        return merged
    bars = book.source_bars[-hours:] if hours > 0 else list(book.source_bars)
    start = bars[0].time_ms if bars else 0
    oi_points = [point for point in book.source_oi if not bars or point.time_ms >= start - HOUR_MS]
    clipped_book = build_map(book.symbol, bars, oi_points, book.margin, book.max_leverage, conserved)
    clipped_book.source_bars = book.source_bars
    clipped_book.source_oi = book.source_oi
    clipped_book.margin = book.margin
    clipped_book.max_leverage = book.max_leverage
    return clipped_book
