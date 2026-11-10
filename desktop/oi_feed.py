"""Birden fazla borsanın açık pozisyonu, coin cinsinden.

USDT, USDC ve USD sözleşmeleri aynı coin biriminde toplanır. Kopan uç son sağlam
serisini taşır. Üç boş gelişte o uç iki mumdan da çıkar. Hepsi düşünce bir önceki
birleşik seri döner; toplama sıfır yazılmaz.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import requests

from desktop.market import HOUR_MS, Point

HEADERS = {"User-Agent": "CryptoHub-Desktop"}
TIMEOUT = 12
BAR_MS = {"15m": 15 * 60 * 1000, "1h": HOUR_MS, "2h": 2 * HOUR_MS, "4h": 4 * HOUR_MS, "1d": 24 * HOUR_MS}
WANT = {"15m": 200, "1h": 48, "2h": 40, "4h": 40, "1d": 40}
PERIODS = ("15m", "1h", "2h", "4h", "1d")
DROP_AFTER = 3

_LOCK = threading.Lock()
_MISSING: set[str] = set()
_KEPT: dict[tuple[str, str], list[Point]] = {}
_FAILS: dict[tuple[str, str], int] = {}
_MERGED: dict[tuple[str, str], list[Point]] = {}
_PACKS: dict[str, "OiPack"] = {}
_BUCKET: dict[tuple[str, str], int] = {}
_NAMES: dict[tuple[str, str], list[str]] = {}
_COUNTS: dict[tuple[str, str], tuple[int, int]] = {}
_LIVE_AT: dict[str, tuple[int, float | None, int, int]] = {}
_SAMPLES: dict[str, list[list[float]]] = {}
_SAMPLES_LOADED = False
_SAMPLES_DIRTY = False
_LIVE_PARTS: dict[str, dict[str, float]] = {}


@dataclass
class OiPack:
    series: dict[str, list[Point]]
    live: float | None
    up: int
    known: int


def coin_of(symbol: str) -> str:
    symbol = symbol.upper().strip()
    for quote in ("USDT", "USDC", "USD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def merge_books(
    fresh: dict[str, list[Point] | None],
    bar_ms: int,
    kept: dict[str, list[Point]],
    fails: dict[str, int],
    previous: list[Point] | None,
) -> tuple[list[Point], dict[str, list[Point]], dict[str, int], int, int, list[str]]:
    """Sağlam serileri toplar. None, kopuk istek demektir ve sıfır sayılmaz."""
    kept = {name: list(points) for name, points in kept.items()}
    fails = dict(fails)
    active: dict[str, list[Point]] = {}
    up = 0
    for name, points in fresh.items():
        if points:
            kept[name] = list(points)
            fails[name] = 0
            active[name] = points
            up += 1
            continue
        fails[name] = fails.get(name, 0) + 1
        if fails[name] < DROP_AFTER and kept.get(name):
            active[name] = kept[name]
    known = len(fresh)
    if not active:
        return list(previous or []), kept, fails, 0, known, []
    merged, names = _merge_active(active, bar_ms)
    if not merged:
        return list(previous or []), kept, fails, up, known, []
    return merged, kept, fails, up, known, names


def _merge_active(active: dict[str, list[Point]], bar_ms: int) -> tuple[list[Point], list[str]]:
    series: dict[str, dict[int, float]] = {}
    for name, points in active.items():
        bucket: dict[int, float] = {}
        for point in points:
            if point.value > 0:
                bucket[point.time_ms // bar_ms * bar_ms] = point.value
        if bucket:
            series[name] = bucket
    if not series:
        return [], []
    newest = max(max(bucket) for bucket in series.values())
    fresh_names = [name for name, bucket in series.items() if max(bucket) >= newest - 2 * bar_ms]
    if not fresh_names:
        fresh_names = list(series)
    last = newest
    prev = last - bar_ms
    names = [
        name
        for name in fresh_names
        if _covers(series[name], last, bar_ms) and prev in series[name]
    ]
    if not names:
        names = [name for name in fresh_names if _covers(series[name], last, bar_ms)]
    if not names:
        return [], []
    start = min(min(series[name]) for name in names)
    out: list[Point] = []
    stamp = start
    while stamp <= last:
        total = 0.0
        complete = True
        for name in names:
            value = _at(series[name], stamp, bar_ms)
            if value is None:
                complete = False
                break
            total += value
        if complete and total > 0:
            out.append(Point(stamp, total))
        stamp += bar_ms
    return out, names


def _covers(bucket: dict[int, float], stamp: int, bar_ms: int) -> bool:
    if stamp in bucket:
        return True
    return (stamp - bar_ms) in bucket and max(bucket) == stamp - bar_ms


def _at(bucket: dict[int, float], stamp: int, bar_ms: int) -> float | None:
    if stamp in bucket:
        return bucket[stamp]
    first = min(bucket)
    if stamp < first:
        return bucket[first]
    previous = stamp - bar_ms
    if previous in bucket:
        return bucket[previous]
    return None


def load_oi(symbol: str) -> OiPack:
    coin = coin_of(symbol)
    try:
        pack = _load(coin)
    except Exception:
        pack = None
    with _LOCK:
        if pack is not None and (pack.series or pack.live is not None):
            _PACKS[coin] = pack
            return pack
        return _PACKS.get(coin) or OiPack({}, None, 0, 0)


def _load(coin: str) -> OiPack:
    _load_samples()
    now = int(time.time() * 1000)
    jobs: list[tuple[str, str]] = []
    with _LOCK:
        due = [
            period
            for period in PERIODS
            if _BUCKET.get((coin, period)) != now // BAR_MS[period] or (coin, period) not in _MERGED
        ]
    for period in due:
        for name in _HISTORY:
            jobs.append((name, period))
    fresh: dict[tuple[str, str], tuple[str, list[Point]]] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            running = {
                pool.submit(_history, name, coin, period, WANT[period]): (name, period)
                for name, period in jobs
            }
            for job in running:
                name, period = running[job]
                try:
                    fresh[(name, period)] = job.result()
                except Exception:
                    fresh[(name, period)] = ("down", [])
    series: dict[str, list[Point]] = {}
    with _LOCK:
        for period in PERIODS:
            bar = BAR_MS[period]
            if period not in due and (coin, period) in _MERGED:
                series[period] = list(_MERGED[(coin, period)])
                continue
            batch: dict[str, list[Point] | None] = {}
            kept = {
                name: _KEPT[(name, f"{coin}:{period}")]
                for name in _HISTORY
                if (name, f"{coin}:{period}") in _KEPT
            }
            fails = {
                name: _FAILS[(name, f"{coin}:{period}")]
                for name in _HISTORY
                if (name, f"{coin}:{period}") in _FAILS
            }
            for name in _HISTORY:
                if (name, period) not in fresh:
                    continue
                status, points = fresh[(name, period)]
                if status == "miss":
                    _MISSING.add(f"{name}:{coin}")
                    continue
                batch[name] = points or None
            if period != "15m":
                for name in _LIVE_ONLY:
                    if f"{name}:{coin}" in _MISSING:
                        continue
                    points = _sample_points(name, coin, bar)
                    if len(points) >= 2:
                        batch[name] = points
            merged, new_kept, new_fails, period_up, period_known, names = merge_books(
                batch,
                bar,
                kept,
                fails,
                _MERGED.get((coin, period)),
            )
            for name, points in new_kept.items():
                _KEPT[(name, f"{coin}:{period}")] = points
            for name, count in new_fails.items():
                _FAILS[(name, f"{coin}:{period}")] = count
            if merged:
                _MERGED[(coin, period)] = merged
                _BUCKET[(coin, period)] = now // bar
            if names:
                _NAMES[(coin, period)] = names
                _COUNTS[(coin, period)] = (period_up, period_known)
            series[period] = list(_MERGED.get((coin, period)) or [])
        used = list(_NAMES.get((coin, "15m")) or [])
        counted = _COUNTS.get((coin, "15m")) or (0, 0)
    live, live_up, live_known = _cached_live(coin, used, now)
    _save_samples()
    if live is not None and series.get("15m"):
        bar = BAR_MS["15m"]
        stamp = now // bar * bar
        closed = [point for point in series["15m"] if point.time_ms < stamp]
        closed.append(Point(stamp, live))
        series["15m"] = closed
    shown_up = live_up if live is not None else counted[0]
    shown_known = live_known if live is not None else counted[1]
    return OiPack(series, live, shown_up, shown_known)


def _cached_live(coin: str, names: list[str], now: int) -> tuple[float | None, int, int]:
    with _LOCK:
        found = _LIVE_AT.get(coin)
    if found is not None and now - found[0] < 60_000:
        return found[1], found[2], found[3]
    live, up, known = _live_sum(coin, names)
    with _LOCK:
        if live is not None:
            _LIVE_AT[coin] = (now, live, up, known)
            return live, up, known
        if found is not None:
            return found[1], found[2], found[3]
    return live, up, known


def _history(name: str, coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    if f"{name}:{coin}" in _MISSING:
        return "miss", []
    try:
        status, points = _HISTORY[name](coin, period, limit)
    except Exception:
        return "down", []
    if status == "miss":
        with _LOCK:
            _MISSING.add(f"{name}:{coin}")
        return "miss", []
    if status != "ok" or not points:
        return "down", []
    points = [point for point in points if point.value > 0]
    points.sort(key=lambda point: point.time_ms)
    if limit > 0 and len(points) > limit:
        points = points[-limit:]
    return ("ok", points) if points else ("down", [])


def _live_sum(coin: str, names: list[str]) -> tuple[float | None, int, int]:
    if not names:
        return None, 0, 0
    results: dict[str, float | None] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        running = {pool.submit(_live_one, name, coin): name for name in names}
        for job in running:
            name = running[job]
            try:
                results[name] = job.result()
            except Exception:
                results[name] = None
    total = 0.0
    up = 0
    known = 0
    parts: dict[str, float] = {}
    for name in names:
        value = results.get(name)
        if value is not None and value > 0:
            total += value
            up += 1
            known += 1
            parts[name] = value
            continue
        carried = _latest_kept(name, coin)
        if carried is None:
            continue
        total += carried
        known += 1
        parts[name] = carried
    if known == 0:
        return None, 0, known
    with _LOCK:
        _LIVE_PARTS[coin] = parts
    _note_live_only(coin)
    return total, up, known


def funding_books(symbol: str) -> tuple[dict[str, float], dict[str, list[Point]]]:
    """Bu turda funding'e girebilecek sözleşmeler.

    Geçmişi olanlar 15 dakikalık toplama girenlerdir. Yalnız anlık verenler iki
    saatlik örnek birikmeden ne toplama ne funding'e girer.
    """
    coin = coin_of(symbol)
    load_oi(symbol)
    weights: dict[str, float] = {}
    paths: dict[str, list[Point]] = {}
    with _LOCK:
        parts = dict(_LIVE_PARTS.get(coin) or {})
        names = list(_NAMES.get((coin, "15m")) or [])
        kept_hour = {name: list(_KEPT.get((name, f"{coin}:1h")) or []) for name in names}
        kept_fine = {name: list(_KEPT.get((name, f"{coin}:15m")) or []) for name in names}
    for name in names:
        value = parts.get(name)
        if value is None or value <= 0:
            fine = kept_fine.get(name) or []
            value = fine[-1].value if fine else None
        if value is None or value <= 0:
            continue
        weights[name] = float(value)
        hour = kept_hour.get(name) or []
        if len(hour) >= 2:
            paths[name] = hour
        elif kept_fine.get(name):
            paths[name] = _rollup(kept_fine[name], HOUR_MS)
        else:
            paths[name] = []
    for name in _LIVE_ONLY:
        if f"{name}:{coin}" in _MISSING:
            continue
        points = _sample_points(name, coin, HOUR_MS)
        if len(points) < 2:
            continue
        weights[name] = points[-1].value
        paths[name] = points
    return weights, paths


def _latest_kept(name: str, coin: str) -> float | None:
    with _LOCK:
        points = _KEPT.get((name, f"{coin}:15m"))
    if not points:
        return None
    return points[-1].value


def _note_live_only(coin: str) -> None:
    hour = int(time.time() * 1000) // HOUR_MS * HOUR_MS
    with ThreadPoolExecutor(max_workers=8) as pool:
        running = {pool.submit(_live_one, name, coin): name for name in _LIVE_ONLY}
        for job in running:
            name = running[job]
            try:
                value = job.result()
            except Exception:
                value = None
            if value is None or value <= 0:
                continue
            _remember_sample(name, coin, hour, value)


def _remember_sample(name: str, coin: str, hour: int, value: float) -> None:
    global _SAMPLES_DIRTY
    key = f"{name}:{coin}"
    with _LOCK:
        rows = _SAMPLES.setdefault(key, [])
        if rows and int(rows[-1][0]) == hour:
            rows[-1][1] = value
        else:
            rows.append([hour, value])
        cutoff = hour - 40 * 24 * HOUR_MS
        _SAMPLES[key] = [row for row in rows if int(row[0]) >= cutoff]
        _SAMPLES_DIRTY = True


def _sample_points(name: str, coin: str, bar_ms: int) -> list[Point]:
    rows = _SAMPLES.get(f"{name}:{coin}") or []
    buckets: dict[int, float] = {}
    for row in rows:
        try:
            stamp = int(row[0]) // bar_ms * bar_ms
            value = float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if value > 0:
            buckets[stamp] = value
    return [Point(stamp, buckets[stamp]) for stamp in sorted(buckets)]


def _sample_path() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, "CryptoHub")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "oi_live.json")


def _load_samples() -> None:
    global _SAMPLES_LOADED
    with _LOCK:
        if _SAMPLES_LOADED:
            return
        _SAMPLES_LOADED = True
        try:
            with open(_sample_path(), encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(raw, dict):
            for key, rows in raw.items():
                if isinstance(rows, list):
                    _SAMPLES[str(key)] = rows


def _save_samples() -> None:
    global _SAMPLES_DIRTY
    with _LOCK:
        if not _SAMPLES_DIRTY:
            return
        payload = {key: rows for key, rows in _SAMPLES.items()}
        _SAMPLES_DIRTY = False
    try:
        with open(_sample_path(), "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
    except OSError:
        pass


def _get(url: str, params: dict | None = None):
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _rows(points: list[tuple[int, float]], limit: int) -> list[Point]:
    clean: dict[int, float] = {}
    for stamp, value in points:
        if stamp > 0 and value > 0:
            clean[int(stamp)] = float(value)
    ordered = [Point(stamp, clean[stamp]) for stamp in sorted(clean)]
    if limit > 0 and len(ordered) > limit:
        return ordered[-limit:]
    return ordered


def _rollup(points: list[Point], bar_ms: int) -> list[Point]:
    buckets: dict[int, float] = {}
    for point in sorted(points, key=lambda item: item.time_ms):
        if point.value > 0:
            buckets[point.time_ms // bar_ms * bar_ms] = point.value
    return [Point(stamp, buckets[stamp]) for stamp in sorted(buckets)]


def _binance_error(payload) -> str | None:
    if isinstance(payload, dict) and payload.get("code"):
        if payload.get("code") in (-1121, -1122):
            return "miss"
        return "down"
    return None


def _binance_linear(coin: str, quote: str, period: str, limit: int) -> tuple[str, list[Point]]:
    payload = _get(
        "https://fapi.binance.com/futures/data/openInterestHist",
        {"symbol": f"{coin}{quote}", "period": period, "limit": min(limit, 500)},
    )
    kind = _binance_error(payload)
    if kind:
        return kind, []
    if not isinstance(payload, list):
        return "down", []
    points: list[tuple[int, float]] = []
    for row in payload:
        try:
            points.append((int(row["timestamp"]), float(row["sumOpenInterest"])))
        except (KeyError, TypeError, ValueError):
            continue
    return "ok", _rows(points, limit)


def _binance_inverse(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    payload = _get(
        "https://dapi.binance.com/futures/data/openInterestHist",
        {"pair": f"{coin}USD", "contractType": "PERPETUAL", "period": period, "limit": min(limit, 500)},
    )
    kind = _binance_error(payload)
    if kind:
        return kind, []
    if not isinstance(payload, list):
        return "down", []
    mark = _dapi_mark(coin)
    size = 100.0 if coin == "BTC" else 10.0
    points: list[tuple[int, float]] = []
    for row in payload:
        try:
            contracts = float(row["sumOpenInterest"])
            value = float(row["sumOpenInterestValue"])
            stamp = int(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        coin_oi = _inverse_coin(contracts, value, mark, size)
        if coin_oi is not None:
            points.append((stamp, coin_oi))
    return "ok", _rows(points, limit)


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


def _dapi_mark(coin: str) -> float | None:
    payload = _get("https://dapi.binance.com/dapi/v1/premiumIndex", {"symbol": f"{coin}USD_PERP"})
    row = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(row, dict):
        return None
    try:
        return float(row["markPrice"])
    except (KeyError, TypeError, ValueError):
        return None


def _bybit(coin: str, category: str, symbol: str, period: str, limit: int, inverse: bool) -> tuple[str, list[Point]]:
    interval = {"15m": "15min", "1h": "1h", "4h": "4h", "1d": "1d"}.get(period)
    if interval is None:
        status, points = _bybit(coin, category, symbol, "1h", max(limit * 2, 2), inverse)
        if status != "ok":
            return status, []
        return "ok", _rollup(points, BAR_MS["2h"])
    payload = _get(
        "https://api.bybit.com/v5/market/open-interest",
        {"category": category, "symbol": symbol, "intervalTime": interval, "limit": min(limit, 200)},
    )
    if not isinstance(payload, dict):
        return "down", []
    code = payload.get("retCode")
    if code in (10001, 10002):
        return "miss", []
    if code not in (0, "0", None):
        return "down", []
    rows = ((payload.get("result") or {}).get("list")) or []
    if not rows:
        return "down", []
    prices = _bybit_prices(category, symbol, period, limit) if inverse else {}
    mark = _bybit_mark(category, symbol) if inverse else None
    points: list[tuple[int, float]] = []
    for row in rows:
        try:
            stamp = int(row["timestamp"])
            value = float(row["openInterest"])
        except (KeyError, TypeError, ValueError):
            continue
        if inverse:
            price = prices.get(stamp // BAR_MS[period] * BAR_MS[period]) or mark
            if not price or price <= 0:
                continue
            value = value / price
        if value > 0:
            points.append((stamp, value))
    return "ok", _rows(points, limit)


def _bybit_prices(category: str, symbol: str, period: str, limit: int) -> dict[int, float]:
    interval = {"15m": "15", "1h": "60", "2h": "120", "4h": "240", "1d": "D"}.get(period, "60")
    payload = _get(
        "https://api.bybit.com/v5/market/kline",
        {"category": category, "symbol": symbol, "interval": interval, "limit": min(limit, 200)},
    )
    rows = ((payload or {}).get("result") or {}).get("list") if isinstance(payload, dict) else None
    prices: dict[int, float] = {}
    for row in rows or []:
        try:
            prices[int(row[0])] = float(row[4])
        except (IndexError, TypeError, ValueError):
            continue
    return prices


def _bybit_mark(category: str, symbol: str) -> float | None:
    payload = _get("https://api.bybit.com/v5/market/tickers", {"category": category, "symbol": symbol})
    rows = ((payload or {}).get("result") or {}).get("list") if isinstance(payload, dict) else None
    if not rows:
        return None
    try:
        return float(rows[0]["markPrice"])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _okx(inst: str, period: str, limit: int) -> tuple[str, list[Point]]:
    bar = {"15m": "15m", "1h": "1H", "2h": "2H", "4h": "4H", "1d": "1D"}[period]
    rows: list = []
    end = ""
    for _ in range(4):
        if len(rows) >= limit:
            break
        params = {"instId": inst, "period": bar}
        if end:
            params["end"] = end
        payload = _get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history", params)
        if not isinstance(payload, dict):
            return "down", []
        code = str(payload.get("code") or "")
        if code == "51001":
            return "miss", []
        if code not in ("0", ""):
            return "down", []
        batch = payload.get("data") or []
        if not batch:
            break
        rows.extend(batch)
        try:
            end = str(int(batch[-1][0]) - 1)
        except (IndexError, TypeError, ValueError):
            break
        if len(batch) < 100:
            break
    points: list[tuple[int, float]] = []
    for row in rows:
        try:
            points.append((int(row[0]), float(row[2])))
        except (IndexError, TypeError, ValueError):
            continue
    return "ok", _rows(points, limit)


def _htx(host: str, code: str, period: str, limit: int) -> tuple[str, list[Point]]:
    interval = {"15m": "15min", "1h": "60min", "4h": "4hour", "1d": "1day"}.get(period)
    if interval is None:
        status, points = _htx(host, code, "1h", max(limit * 2, 2))
        if status != "ok":
            return status, []
        return "ok", _rollup(points, BAR_MS["2h"])
    payload = _get(
        host,
        {"contract_code": code, "period": interval, "size": min(limit, 200), "amount_type": 2},
    )
    if not isinstance(payload, dict):
        return "down", []
    if payload.get("status") != "ok":
        message = str(payload.get("err_msg") or "").lower()
        if "contract" in message or "symbol" in message or "exist" in message:
            return "miss", []
        return "down", []
    ticks = ((payload.get("data") or {}).get("tick")) or []
    points: list[tuple[int, float]] = []
    for row in ticks:
        try:
            points.append((int(row["ts"]), float(row["volume"])))
        except (KeyError, TypeError, ValueError):
            continue
    return "ok", _rows(points, limit)


def _hist_binance_usdt(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _binance_linear(coin, "USDT", period, limit)


def _hist_binance_usdc(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _binance_linear(coin, "USDC", period, limit)


def _hist_binance_usd(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _binance_inverse(coin, period, limit)


def _hist_bybit_usdt(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _bybit(coin, "linear", f"{coin}USDT", period, limit, False)


def _hist_bybit_usdc(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _bybit(coin, "linear", f"{coin}PERP", period, limit, False)


def _hist_bybit_usd(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _bybit(coin, "inverse", f"{coin}USD", period, limit, True)


def _hist_okx_usdt(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _okx(f"{coin}-USDT-SWAP", period, limit)


def _hist_okx_usd(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _okx(f"{coin}-USD-SWAP", period, limit)


def _bitfinex_key(coin: str) -> str:
    return f"t{coin}F0:USTF0"


def _bitfinex_rows(coin: str, span_ms: int):
    key = _bitfinex_key(coin)
    rows: list = []
    end = None
    pages = 1 if span_ms <= 80 * HOUR_MS else 4
    for _ in range(pages):
        params: dict[str, str | int] = {"limit": 5000, "sort": -1}
        if end is not None:
            params["end"] = end
        payload = _get(f"https://api-pub.bitfinex.com/v2/status/deriv/{key}/hist", params)
        if isinstance(payload, list) and payload and payload[0] == "error":
            return "miss" if not rows else rows
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            return None if not rows else rows
        rows.extend(payload)
        try:
            oldest = min(int(row[0]) for row in payload)
            newest = max(int(row[0]) for row in rows)
        except (TypeError, ValueError, IndexError):
            break
        if newest - oldest >= span_ms or len(payload) < 5000:
            break
        end = oldest - 1
    return rows


def _hist_bitfinex(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    rows = _bitfinex_rows(coin, limit * BAR_MS[period])
    if rows == "miss":
        return "miss", []
    if not rows:
        return "down", []
    bar = BAR_MS[period]
    buckets: dict[int, float] = {}
    ordered = sorted(rows, key=lambda row: int(row[0]) if row else 0)
    for row in ordered:
        try:
            stamp = int(row[0]) // bar * bar
            value = float(row[17])
        except (IndexError, TypeError, ValueError):
            continue
        if value > 0:
            buckets[stamp] = value
    return "ok", _rows(list(buckets.items()), limit)


def _hist_htx_usdt(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _htx("https://api.hbdm.com/linear-swap-api/v1/swap_his_open_interest", f"{coin}-USDT", period, limit)


def _hist_htx_usd(coin: str, period: str, limit: int) -> tuple[str, list[Point]]:
    return _htx("https://api.hbdm.com/swap-api/v1/swap_his_open_interest", f"{coin}-USD", period, limit)


_HISTORY = {
    "binance-usdt": _hist_binance_usdt,
    "binance-usdc": _hist_binance_usdc,
    "binance-usd": _hist_binance_usd,
    "bybit-usdt": _hist_bybit_usdt,
    "bybit-usdc": _hist_bybit_usdc,
    "bybit-usd": _hist_bybit_usd,
    "okx-usdt": _hist_okx_usdt,
    "okx-usd": _hist_okx_usd,
    "htx-usdt": _hist_htx_usdt,
    "htx-usd": _hist_htx_usd,
    "bitfinex-usdt": _hist_bitfinex,
}


def _live_one(name: str, coin: str) -> float | None:
    if f"{name}:{coin}" in _MISSING:
        return None
    try:
        status, value = _LIVE[name](coin)
    except Exception:
        return None
    if status == "miss":
        with _LOCK:
            _MISSING.add(f"{name}:{coin}")
        return None
    if status != "ok" or value is None or value <= 0:
        return None
    return value


def _live_binance_linear(coin: str, quote: str) -> tuple[str, float | None]:
    payload = _get("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": f"{coin}{quote}"})
    kind = _binance_error(payload)
    if kind:
        return kind, None
    try:
        return "ok", float(payload["openInterest"])
    except (TypeError, ValueError, KeyError):
        return "down", None


def _live_binance_usdt(coin: str) -> tuple[str, float | None]:
    return _live_binance_linear(coin, "USDT")


def _live_binance_usdc(coin: str) -> tuple[str, float | None]:
    return _live_binance_linear(coin, "USDC")


def _live_binance_usd(coin: str) -> tuple[str, float | None]:
    payload = _get("https://dapi.binance.com/dapi/v1/openInterest", {"symbol": f"{coin}USD_PERP"})
    kind = _binance_error(payload)
    if kind:
        return kind, None
    mark = _dapi_mark(coin)
    size = 100.0 if coin == "BTC" else 10.0
    try:
        contracts = float(payload["openInterest"])
    except (TypeError, ValueError, KeyError):
        return "down", None
    if not mark or mark <= 0:
        return "down", None
    return "ok", contracts * size / mark


def _live_bybit(category: str, symbol: str, inverse: bool) -> tuple[str, float | None]:
    payload = _get("https://api.bybit.com/v5/market/tickers", {"category": category, "symbol": symbol})
    if not isinstance(payload, dict):
        return "down", None
    if payload.get("retCode") in (10001, 10002):
        return "miss", None
    rows = (payload.get("result") or {}).get("list") or []
    if not rows:
        return "down", None
    row = rows[0]
    try:
        if inverse:
            value = float(row["openInterestValue"])
        else:
            value = float(row["openInterest"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    return ("ok", value) if value > 0 else ("down", None)


def _live_bybit_usdt(coin: str) -> tuple[str, float | None]:
    return _live_bybit("linear", f"{coin}USDT", False)


def _live_bybit_usdc(coin: str) -> tuple[str, float | None]:
    return _live_bybit("linear", f"{coin}PERP", False)


def _live_bybit_usd(coin: str) -> tuple[str, float | None]:
    return _live_bybit("inverse", f"{coin}USD", True)


def _live_okx(inst: str) -> tuple[str, float | None]:
    payload = _get("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst})
    if not isinstance(payload, dict):
        return "down", None
    if str(payload.get("code")) == "51001":
        return "miss", None
    if str(payload.get("code") or "") not in ("0", ""):
        return "down", None
    rows = payload.get("data") or []
    if not rows:
        return "down", None
    try:
        return "ok", float(rows[0]["oiCcy"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None


def _live_okx_usdt(coin: str) -> tuple[str, float | None]:
    return _live_okx(f"{coin}-USDT-SWAP")


def _live_okx_usd(coin: str) -> tuple[str, float | None]:
    return _live_okx(f"{coin}-USD-SWAP")


def _live_htx(url: str, code: str) -> tuple[str, float | None]:
    payload = _get(url, {"contract_code": code})
    if not isinstance(payload, dict):
        return "down", None
    if payload.get("status") != "ok":
        message = str(payload.get("err_msg") or "").lower()
        if "contract" in message or "exist" in message:
            return "miss", None
        return "down", None
    rows = payload.get("data") or []
    if not rows:
        return "down", None
    try:
        return "ok", float(rows[0]["amount"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None


def _live_htx_usdt(coin: str) -> tuple[str, float | None]:
    return _live_htx("https://api.hbdm.com/linear-swap-api/v1/swap_open_interest", f"{coin}-USDT")


def _live_htx_usd(coin: str) -> tuple[str, float | None]:
    return _live_htx("https://api.hbdm.com/swap-api/v1/swap_open_interest", f"{coin}-USD")


def _live_bitfinex(coin: str) -> tuple[str, float | None]:
    payload = _get("https://api-pub.bitfinex.com/v2/status/deriv", {"keys": _bitfinex_key(coin)})
    if isinstance(payload, list) and payload and payload[0] == "error":
        return "miss", None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        return "down", None
    row = payload[0]
    try:
        value = float(row[18])
    except (IndexError, TypeError, ValueError):
        return "down", None
    return ("ok", value) if value > 0 else ("down", None)


def _live_woo(coin: str) -> tuple[str, float | None]:
    payload = _get(f"https://api.woox.io/v1/public/futures/PERP_{coin}_USDT")
    if not isinstance(payload, dict):
        return "down", None
    if not payload.get("success"):
        return "miss", None
    info = payload.get("info")
    if not isinstance(info, dict):
        return "miss", None
    try:
        value = float(info["open_interest"])
    except (KeyError, TypeError, ValueError):
        return "down", None
    return ("ok", value) if value > 0 else ("down", None)


def _live_deribit(coin: str) -> tuple[str, float | None]:
    payload = _get(
        "https://www.deribit.com/api/v2/public/get_book_summary_by_instrument",
        {"instrument_name": f"{coin}-PERPETUAL"},
    )
    rows = (payload or {}).get("result") if isinstance(payload, dict) else None
    if rows == []:
        return "miss", None
    if not rows:
        return "down", None
    try:
        usd = float(rows[0]["open_interest"])
        mark = float(rows[0]["mark_price"])
    except (KeyError, TypeError, ValueError, IndexError):
        return "down", None
    if mark <= 0:
        return "down", None
    return "ok", usd / mark


def _live_kraken(coin: str) -> tuple[str, float | None]:
    payload = _get("https://futures.kraken.com/derivatives/api/v3/tickers")
    rows = (payload or {}).get("tickers") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return "down", None
    base = "XBT" if coin == "BTC" else coin
    want = f"PF_{base}USD"
    for row in rows:
        if row.get("symbol") != want:
            continue
        try:
            return "ok", float(row["openInterest"])
        except (KeyError, TypeError, ValueError):
            return "down", None
    return "miss", None


def _live_dydx(coin: str) -> tuple[str, float | None]:
    payload = _get("https://indexer.dydx.trade/v4/perpetualMarkets")
    markets = (payload or {}).get("markets") if isinstance(payload, dict) else None
    if not isinstance(markets, dict):
        return "down", None
    row = markets.get(f"{coin}-USD")
    if not isinstance(row, dict):
        return "miss", None
    try:
        return "ok", float(row["openInterest"])
    except (KeyError, TypeError, ValueError):
        return "down", None


def _live_hyperliquid(coin: str) -> tuple[str, float | None]:
    try:
        response = requests.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "metaAndAssetCtxs"},
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=TIMEOUT,
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        return "down", None
    if not isinstance(payload, list) or len(payload) < 2:
        return "down", None
    universe = (payload[0] or {}).get("universe") if isinstance(payload[0], dict) else None
    contexts = payload[1]
    if not isinstance(universe, list) or not isinstance(contexts, list):
        return "down", None
    for index, row in enumerate(universe):
        if isinstance(row, dict) and row.get("name") == coin and index < len(contexts):
            try:
                return "ok", float(contexts[index]["openInterest"])
            except (KeyError, TypeError, ValueError):
                return "down", None
    return "miss", None


_LIVE = {
    "binance-usdt": _live_binance_usdt,
    "binance-usdc": _live_binance_usdc,
    "binance-usd": _live_binance_usd,
    "bybit-usdt": _live_bybit_usdt,
    "bybit-usdc": _live_bybit_usdc,
    "bybit-usd": _live_bybit_usd,
    "okx-usdt": _live_okx_usdt,
    "okx-usd": _live_okx_usd,
    "htx-usdt": _live_htx_usdt,
    "htx-usd": _live_htx_usd,
    "bitfinex-usdt": _live_bitfinex,
    "woo-usdt": _live_woo,
    "deribit-usd": _live_deribit,
    "kraken-usd": _live_kraken,
    "dydx-usd": _live_dydx,
    "hyperliquid-usdc": _live_hyperliquid,
}

_LIVE_ONLY = (
    "woo-usdt",
    "deribit-usd",
    "kraken-usd",
    "dydx-usd",
    "hyperliquid-usdc",
)
