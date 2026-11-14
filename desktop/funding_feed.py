"""OI ağırlıklı funding.

Yalnız o turda açık pozisyona giren sözleşmeler sayılır. Oran 8 saatlik yüzdeye
çekilir. Aralık bilinmiyorsa sözleşme hem paydan hem paydadan çıkar. Kopunca son
sağlam oran taşınır. Üç boş gelişte çıkar. Eksik oran sıfır yazılmaz.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from desktop.market import FUNDING_HOURS, HOUR_MS, Point

HEADERS = {"User-Agent": "CryptoHub-Desktop"}
TIMEOUT = 12
DROP_AFTER = 3

_LOCK = threading.Lock()
_MISSING: set[str] = set()
_KEPT: dict[tuple[str, str], "Quote"] = {}
_FAILS: dict[tuple[str, str], int] = {}
_PACKS: dict[str, "FundingPack"] = {}
_PACK_AT: dict[str, int] = {}
_HIST: dict[str, tuple[int, list]] = {}
_BINANCE_HOURS: dict[str, float] = {}
_BINANCE_AT = 0.0


def release_memory() -> None:
    with _LOCK:
        _KEPT.clear()
        _PACKS.clear()
        _PACK_AT.clear()
        _HIST.clear()


@dataclass
class Quote:
    raw: float
    hours: float
    next_ms: int | None
    settled: list[tuple[int, float]]


@dataclass
class FundingPack:
    live: float | None
    next_ms: int | None
    hourly: list[tuple[int, float]]
    up: int
    known: int


def blend(parts: list[tuple[float, float]]) -> float | None:
    """(8 saatlik yüzde, coin OI) çiftlerinin ağırlıklı ortalaması. OI'si olmayan düşer."""
    num = 0.0
    den = 0.0
    for percent, weight in parts:
        try:
            percent = float(percent)
            weight = float(weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        num += percent * weight
        den += weight
    if den <= 0:
        return None
    return num / den


def apply_carry(fresh: float | None, kept: float | None, fails: int) -> tuple[float | None, int]:
    """Bir kopuşta eski oran durur. Üçüncü boş gelişte düşer. Yokluk sıfır olmaz."""
    if fresh is not None:
        return fresh, 0
    fails += 1
    if fails < DROP_AFTER and kept is not None:
        return kept, fails
    return None, fails


def _percent(raw: float, hours: float) -> float | None:
    try:
        raw = float(raw)
        hours = float(hours)
    except (TypeError, ValueError):
        return None
    if hours <= 0:
        return None
    return raw * 100.0 * (8.0 / hours)


def load_funding(symbol: str) -> FundingPack:
    from desktop.oi_feed import coin_of

    coin = coin_of(symbol)
    now = int(time.time() * 1000)
    with _LOCK:
        found = _PACKS.get(coin)
        stamp = _PACK_AT.get(coin, 0)
    if found is not None and found.live is not None and now - stamp < 60_000:
        return found
    try:
        pack = _load(symbol)
    except Exception:
        pack = None
    with _LOCK:
        if pack is not None and pack.live is not None:
            _PACKS[coin] = pack
            _PACK_AT[coin] = now
            return pack
        return _PACKS.get(coin) or FundingPack(None, None, [], 0, 0)


def _history(key: str, loader) -> list:
    now = int(time.time() * 1000)
    with _LOCK:
        found = _HIST.get(key)
    if found is not None and found[1] and now - found[0] < HOUR_MS:
        return list(found[1])
    try:
        points = loader()
    except Exception:
        points = None
    if points:
        with _LOCK:
            _HIST[key] = (now, list(points))
        return list(points)
    if found is not None:
        return list(found[1])
    return []


def _load(symbol: str) -> FundingPack:
    from desktop.oi_feed import coin_of, funding_books

    coin = coin_of(symbol)
    weights, paths = funding_books(symbol)
    names = [name for name in weights if name in _FETCH and f"{name}:{coin}" not in _MISSING]
    fresh: dict[str, tuple[str, Quote | None]] = {}
    if names:
        with ThreadPoolExecutor(max_workers=8) as pool:
            running = {pool.submit(_FETCH[name], coin): name for name in names}
            for job in running:
                name = running[job]
                try:
                    fresh[name] = job.result()
                except Exception:
                    fresh[name] = ("down", None)
    included: dict[str, Quote] = {}
    up = 0
    known = 0
    now = int(time.time() * 1000)
    with _LOCK:
        for name in names:
            status, quote = fresh.get(name, ("down", None))
            if status == "miss":
                _MISSING.add(f"{name}:{coin}")
                continue
            known += 1
            key = (name, coin)
            kept = _KEPT.get(key)
            if status == "ok" and quote is not None and quote.hours > 0 and _percent(quote.raw, quote.hours) is not None:
                if not quote.settled and kept is not None:
                    quote = Quote(quote.raw, quote.hours, quote.next_ms, list(kept.settled))
                _KEPT[key] = quote
                _FAILS[key] = 0
                included[name] = quote
                up += 1
                continue
            fails = _FAILS.get(key, 0) + 1
            _FAILS[key] = fails
            if fails < DROP_AFTER and kept is not None and kept.hours > 0:
                included[name] = kept
                up += 1
    if not included:
        return FundingPack(None, None, [], 0, known)
    parts: list[tuple[float, float]] = []
    nexts: list[int] = []
    for name, quote in included.items():
        percent = _percent(quote.raw, quote.hours)
        weight = weights.get(name) or 0
        if percent is None or weight <= 0:
            continue
        parts.append((percent, weight))
        if quote.next_ms:
            nexts.append(int(quote.next_ms))
    live = blend(parts)
    if live is None:
        return FundingPack(None, None, [], 0, known)
    hourly = _hourly(included, paths, weights, now)
    return FundingPack(live, min(nexts) if nexts else None, hourly, up, known)


def _hourly(
    included: dict[str, Quote],
    paths: dict[str, list[Point]],
    weights: dict[str, float],
    now: int,
) -> list[tuple[int, float]]:
    end = now // HOUR_MS * HOUR_MS
    start = end - (FUNDING_HOURS - 1) * HOUR_MS
    points: list[tuple[int, float]] = []
    for index in range(FUNDING_HOURS):
        moment = start + index * HOUR_MS
        cutoff = moment + HOUR_MS - 1
        parts: list[tuple[float, float]] = []
        for name, quote in included.items():
            raw = _rate_at(quote.settled, cutoff)
            percent = None if raw is None else _percent(raw, quote.hours)
            if percent is None:
                continue
            weight = _oi_at(paths.get(name) or [], moment)
            if weight is None:
                weight = weights.get(name)
            if weight is None or weight <= 0:
                continue
            parts.append((percent, weight))
        value = blend(parts)
        if value is not None:
            points.append((moment, value))
    return points


def _rate_at(settled: list[tuple[int, float]], cutoff: int) -> float | None:
    chosen = None
    for stamp, raw in settled:
        if stamp <= cutoff:
            chosen = raw
        else:
            break
    return chosen


def _oi_at(points: list[Point], moment: int) -> float | None:
    chosen = None
    for point in points:
        if point.time_ms <= moment and point.value > 0:
            chosen = point.value
        elif point.time_ms > moment:
            break
    return chosen


def _get(url: str, params: dict | None = None):
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _post(url: str, body: dict):
    try:
        response = requests.post(
            url,
            json=body,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        return response.json()
    except (requests.RequestException, ValueError):
        return None


def _gap_hours(stamps: list[int]) -> float | None:
    ordered = sorted({int(stamp) for stamp in stamps if stamp})
    gaps = [ordered[index] - ordered[index - 1] for index in range(1, len(ordered))]
    gaps = [gap for gap in gaps if gap > 0]
    if not gaps:
        return None
    gaps.sort()
    hours = gaps[len(gaps) // 2] / HOUR_MS
    for known in (1, 2, 4, 8):
        if abs(hours - known) < 0.25:
            return float(known)
    if 0.5 <= hours <= 24:
        return hours
    return None


def _next_hour(now: int | None = None) -> int:
    moment = int(time.time() * 1000) if now is None else now
    return (moment // HOUR_MS + 1) * HOUR_MS


def _iso_ms(text: str) -> int:
    stamp = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def _settled_rows(rows, stamp_key: str, rate_key: str) -> list[tuple[int, float]]:
    points: list[tuple[int, float]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            points.append((int(row[stamp_key]), float(row[rate_key])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda item: item[0])
    return points


def _binance_interval(symbol: str) -> float | None:
    global _BINANCE_AT
    now = time.time()
    with _LOCK:
        cached = _BINANCE_HOURS.get(symbol)
        fresh = now - _BINANCE_AT < 1800 and bool(_BINANCE_HOURS)
    if fresh:
        return cached if cached and cached > 0 else None
    payload = _get("https://fapi.binance.com/fapi/v1/fundingInfo")
    found: dict[str, float] = {}
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            try:
                hours = float(row["fundingIntervalHours"])
            except (KeyError, TypeError, ValueError):
                continue
            if hours > 0 and row.get("symbol"):
                found[str(row["symbol"])] = hours
    if found:
        with _LOCK:
            _BINANCE_HOURS.update(found)
            _BINANCE_AT = now
        return found.get(symbol)
    return cached if cached and cached > 0 else None


def _binance_linear(coin: str, quote: str) -> tuple[str, Quote | None]:
    symbol = f"{coin}{quote}"
    premium = _get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
    if isinstance(premium, dict) and premium.get("code") in (-1121, -1122):
        return "miss", None
    if not isinstance(premium, dict):
        return "down", None
    start = int(time.time() * 1000) - (FUNDING_HOURS + 24) * HOUR_MS
    history = _get(
        "https://fapi.binance.com/fapi/v1/fundingRate",
        {"symbol": symbol, "startTime": start, "limit": 100},
    )
    settled = _settled_rows(history if isinstance(history, list) else [], "fundingTime", "fundingRate")
    hours = _binance_interval(symbol) or _gap_hours([stamp for stamp, _raw in settled])
    try:
        raw = float(premium["lastFundingRate"])
        nxt = int(premium["nextFundingTime"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    if not hours:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


def _binance_usdt(coin: str) -> tuple[str, Quote | None]:
    return _binance_linear(coin, "USDT")


def _binance_usdc(coin: str) -> tuple[str, Quote | None]:
    return _binance_linear(coin, "USDC")


def _binance_usd(coin: str) -> tuple[str, Quote | None]:
    symbol = f"{coin}USD_PERP"
    payload = _get("https://dapi.binance.com/dapi/v1/premiumIndex", {"symbol": symbol})
    if isinstance(payload, dict) and payload.get("code") in (-1121, -1122):
        return "miss", None
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return "down", None
    start = int(time.time() * 1000) - (FUNDING_HOURS + 24) * HOUR_MS
    history = _get(
        "https://dapi.binance.com/dapi/v1/fundingRate",
        {"symbol": symbol, "startTime": start, "limit": 100},
    )
    settled = _settled_rows(history if isinstance(history, list) else [], "fundingTime", "fundingRate")
    hours = _gap_hours([stamp for stamp, _raw in settled])
    try:
        raw = float(row["lastFundingRate"])
        nxt = int(row["nextFundingTime"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    if not hours and settled:
        hours = _gap_hours([stamp for stamp, _raw in settled] + [nxt])
    if not hours:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


def _bybit(category: str, symbol: str) -> tuple[str, Quote | None]:
    payload = _get("https://api.bybit.com/v5/market/tickers", {"category": category, "symbol": symbol})
    if not isinstance(payload, dict):
        return "down", None
    if payload.get("retCode") in (10001, 10002):
        return "miss", None
    rows = (payload.get("result") or {}).get("list") or []
    if not rows:
        return "down", None
    history = _get(
        "https://api.bybit.com/v5/market/funding/history",
        {"category": category, "symbol": symbol, "limit": 200},
    )
    settled = _settled_rows(
        ((history or {}).get("result") or {}).get("list") if isinstance(history, dict) else [],
        "fundingRateTimestamp",
        "fundingRate",
    )
    try:
        raw = float(rows[0]["fundingRate"])
        hours = float(rows[0]["fundingIntervalHour"])
        nxt = int(rows[0]["nextFundingTime"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None
    if hours <= 0:
        hours = _gap_hours([stamp for stamp, _raw in settled]) or 0
    if hours <= 0:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


def _bybit_usdt(coin: str) -> tuple[str, Quote | None]:
    return _bybit("linear", f"{coin}USDT")


def _bybit_usdc(coin: str) -> tuple[str, Quote | None]:
    return _bybit("linear", f"{coin}PERP")


def _bybit_usd(coin: str) -> tuple[str, Quote | None]:
    return _bybit("inverse", f"{coin}USD")


def _okx(inst: str) -> tuple[str, Quote | None]:
    payload = _get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst})
    if not isinstance(payload, dict):
        return "down", None
    if str(payload.get("code")) == "51001":
        return "miss", None
    rows = payload.get("data") or []
    if not rows:
        return "down", None
    history = _get("https://www.okx.com/api/v5/public/funding-rate-history", {"instId": inst, "limit": "100"})
    settled = _settled_rows(
        history.get("data") if isinstance(history, dict) else [],
        "fundingTime",
        "fundingRate",
    )
    try:
        raw = float(rows[0]["fundingRate"])
        nxt = int(rows[0]["fundingTime"])
        prev = int(rows[0]["prevFundingTime"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None
    hours = (nxt - prev) / HOUR_MS if nxt > prev else 0
    if hours <= 0:
        try:
            later = int(rows[0]["nextFundingTime"])
        except (KeyError, TypeError, ValueError):
            later = 0
        hours = (later - nxt) / HOUR_MS if later > nxt else 0
    if hours <= 0:
        hours = _gap_hours([stamp for stamp, _raw in settled]) or 0
    if hours <= 0:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


def _okx_usdt(coin: str) -> tuple[str, Quote | None]:
    return _okx(f"{coin}-USDT-SWAP")


def _okx_usd(coin: str) -> tuple[str, Quote | None]:
    return _okx(f"{coin}-USD-SWAP")


def _htx(live_url: str, hist_url: str, code: str) -> tuple[str, Quote | None]:
    payload = _get(live_url, {"contract_code": code})
    if not isinstance(payload, dict):
        return "down", None
    if payload.get("status") != "ok":
        message = str(payload.get("err_msg") or "").lower()
        if "contract" in message or "exist" in message:
            return "miss", None
        return "down", None
    row = payload.get("data") if isinstance(payload.get("data"), dict) else None
    history = _get(hist_url, {"contract_code": code, "page_index": 1, "page_size": 48})
    nested = history.get("data") if isinstance(history, dict) else None
    rows = nested.get("data") if isinstance(nested, dict) else nested
    settled = _settled_rows(rows if isinstance(rows, list) else [], "funding_time", "funding_rate")
    try:
        raw = float(row["funding_rate"])
        nxt = int(row["funding_time"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    hours = _gap_hours([stamp for stamp, _rate in settled] + [nxt])
    if not hours:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


def _htx_usdt(coin: str) -> tuple[str, Quote | None]:
    return _htx(
        "https://api.hbdm.com/linear-swap-api/v1/swap_funding_rate",
        "https://api.hbdm.com/linear-swap-api/v1/swap_historical_funding_rate",
        f"{coin}-USDT",
    )


def _htx_usd(coin: str) -> tuple[str, Quote | None]:
    return _htx(
        "https://api.hbdm.com/swap-api/v1/swap_funding_rate",
        "https://api.hbdm.com/swap-api/v1/swap_historical_funding_rate",
        f"{coin}-USD",
    )


def _bitfinex_key(coin: str) -> str:
    return f"t{coin}F0:USTF0"


def _bitfinex(coin: str) -> tuple[str, Quote | None]:
    key = _bitfinex_key(coin)
    payload = _get("https://api-pub.bitfinex.com/v2/status/deriv", {"keys": key})
    if isinstance(payload, list) and payload and payload[0] == "error":
        return "miss", None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return "down", None
    row = payload[0]
    def _rows() -> list[tuple[int, float]]:
        history = _get(f"https://api-pub.bitfinex.com/v2/status/deriv/{key}/hist", {"limit": 5000, "sort": -1})
        buckets: dict[int, float] = {}
        if isinstance(history, list):
            ordered = sorted(
                (item for item in history if isinstance(item, list) and item),
                key=lambda item: int(item[0]) if item[0] else 0,
            )
            for item in ordered:
                try:
                    buckets[int(item[0]) // HOUR_MS * HOUR_MS] = float(item[11])
                except (IndexError, TypeError, ValueError):
                    continue
        return sorted(buckets.items())

    settled = _history(f"bitfinex:{coin}", _rows)
    try:
        raw = float(row[12])
        nxt = int(row[8])
    except (IndexError, TypeError, ValueError):
        return "down", None
    return "ok", Quote(raw, 8.0, nxt, settled)


def _hyperliquid(coin: str) -> tuple[str, Quote | None]:
    payload = _post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    if not isinstance(payload, list) or len(payload) < 2:
        return "down", None
    universe = (payload[0] or {}).get("universe") if isinstance(payload[0], dict) else None
    contexts = payload[1]
    if not isinstance(universe, list) or not isinstance(contexts, list):
        return "down", None
    raw = None
    for index, row in enumerate(universe):
        if isinstance(row, dict) and row.get("name") == coin and index < len(contexts):
            try:
                raw = float(contexts[index]["funding"])
            except (KeyError, TypeError, ValueError):
                return "down", None
            break
    if raw is None:
        return "miss", None
    def _rows() -> list[tuple[int, float]]:
        start = int(time.time() * 1000) - (FUNDING_HOURS + 24) * HOUR_MS
        history = _post(
            "https://api.hyperliquid.xyz/info",
            {"type": "fundingHistory", "coin": coin, "startTime": start},
        )
        points: list[tuple[int, float]] = []
        if isinstance(history, list):
            for row in history:
                if not isinstance(row, dict):
                    continue
                try:
                    points.append((int(row["time"]), float(row["fundingRate"])))
                except (KeyError, TypeError, ValueError):
                    continue
        points.sort(key=lambda item: item[0])
        return points

    return "ok", Quote(raw, 1.0, _next_hour(), _history(f"hyperliquid:{coin}", _rows))


def _deribit(coin: str) -> tuple[str, Quote | None]:
    payload = _get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_instrument",
        {"instrument_name": f"{coin}-PERPETUAL"},
    )
    rows = (payload or {}).get("result") if isinstance(payload, dict) else None
    if rows == []:
        return "miss", None
    if not rows:
        return "down", None
    def _rows() -> list[tuple[int, float]]:
        now = int(time.time() * 1000)
        history = _get(
            "https://www.deribit.com/api/v2/public/get_funding_rate_history",
            {
                "instrument_name": f"{coin}-PERPETUAL",
                "start_timestamp": now - (FUNDING_HOURS + 24) * HOUR_MS,
                "end_timestamp": now,
            },
        )
        return _settled_rows(
            history.get("result") if isinstance(history, dict) else [],
            "timestamp",
            "interest_8h",
        )

    settled = _history(f"deribit:{coin}", _rows)
    try:
        raw = float(rows[0]["funding_8h"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None
    return "ok", Quote(raw, 8.0, None, settled)


def _kraken_symbol(coin: str) -> str:
    base = "XBT" if coin == "BTC" else coin
    return f"PF_{base}USD"


def _kraken(coin: str) -> tuple[str, Quote | None]:
    payload = _get("https://futures.kraken.com/derivatives/api/v3/tickers")
    rows = (payload or {}).get("tickers") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return "down", None
    want = _kraken_symbol(coin)
    raw = None
    found = False
    for row in rows:
        if row.get("symbol") != want:
            continue
        found = True
        try:
            raw = float(row["relativeFundingRate"])
        except (KeyError, TypeError, ValueError):
            return "down", None
        break
    if not found:
        return "miss", None
    if raw is None:
        return "down", None
    def _rows() -> list[tuple[int, float]]:
        history = _get(
            "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates",
            {"symbol": want},
        )
        rates = history.get("rates") if isinstance(history, dict) else None
        cutoff = int(time.time() * 1000) - (FUNDING_HOURS + 24) * HOUR_MS
        points: list[tuple[int, float]] = []
        for row in rates or []:
            if not isinstance(row, dict):
                continue
            try:
                stamp = _iso_ms(row["timestamp"])
                rate = float(row["relativeFundingRate"])
            except (KeyError, TypeError, ValueError):
                continue
            if stamp >= cutoff:
                points.append((stamp, rate))
        points.sort(key=lambda item: item[0])
        return points

    return "ok", Quote(raw, 1.0, _next_hour(), _history(f"kraken:{coin}", _rows))


def _dydx(coin: str) -> tuple[str, Quote | None]:
    payload = _get("https://indexer.dydx.trade/v4/perpetualMarkets")
    markets = (payload or {}).get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, dict):
        return "down", None
    row = markets.get(f"{coin}-USD")
    if not isinstance(row, dict):
        return "miss", None
    try:
        raw = float(row["nextFundingRate"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    def _rows() -> list[tuple[int, float]]:
        history = _get(f"https://indexer.dydx.trade/v4/historicalFunding/{coin}-USD", {"limit": 100})
        points: list[tuple[int, float]] = []
        rows = history.get("historicalFunding") if isinstance(history, dict) else None
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            try:
                points.append((_iso_ms(item["effectiveAt"]), float(item["rate"])))
            except (KeyError, TypeError, ValueError):
                continue
        points.sort(key=lambda item: item[0])
        return points

    return "ok", Quote(raw, 1.0, _next_hour(), _history(f"dydx:{coin}", _rows))


def _woo(coin: str) -> tuple[str, Quote | None]:
    symbol = f"PERP_{coin}_USDT"
    payload = _get(f"https://api.woox.io/v1/public/funding_rate/{symbol}")
    if not isinstance(payload, dict):
        return "down", None
    if not payload.get("success"):
        return "miss", None
    history = _get("https://api.woox.io/v1/public/funding_rate_history", {"symbol": symbol, "size": 48})
    settled = _settled_rows(
        history.get("rows") if isinstance(history, dict) else [],
        "funding_rate_timestamp",
        "funding_rate",
    )
    try:
        raw = float(payload.get("est_funding_rate") if payload.get("est_funding_rate") is not None else payload["last_funding_rate"])
        hours = float(payload.get("est_funding_rate_interval") or payload.get("last_funding_rate_interval") or 0)
        nxt = int(payload["next_funding_time"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    if hours <= 0:
        hours = _gap_hours([stamp for stamp, _raw in settled]) or 0
    if hours <= 0:
        return "down", None
    return "ok", Quote(raw, hours, nxt, settled)


_FETCH = {
    "binance-usdt": _binance_usdt,
    "binance-usdc": _binance_usdc,
    "binance-usd": _binance_usd,
    "bybit-usdt": _bybit_usdt,
    "bybit-usdc": _bybit_usdc,
    "bybit-usd": _bybit_usd,
    "okx-usdt": _okx_usdt,
    "okx-usd": _okx_usd,
    "htx-usdt": _htx_usdt,
    "htx-usd": _htx_usd,
    "bitfinex-usdt": _bitfinex,
    "woo-usdt": _woo,
    "deribit-usd": _deribit,
    "kraken-usd": _kraken,
    "dydx-usd": _dydx,
    "hyperliquid-usdc": _hyperliquid,
}
