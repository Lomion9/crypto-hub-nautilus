"""CVD spot ve perp. Coin cinsinden alış eksi satış.

Hazır serisi olan kitaplar mumdan okunur. İşlem dökümü 15 dakikalık kovaya
yazılır ve sonraki turda kaldığı yerden devam eder. Pencerenin başına kadar
kovası olmayan kitap o pencereye girmez. Saatlik seri 15 dakikaya yayılmaz.
Kopuş sıfır yazılmaz. Üç boş gelişte kitap pencereden düşer.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

HEADERS = {"User-Agent": "CryptoHub-Desktop"}
TIMEOUT = 12
BAR_MS = 15 * 60 * 1000
FIVE_MS = 5 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
KEEP_MS = 16 * 24 * HOUR_MS
SLACK_MS = 30_000
DROP_AFTER = 3
TAPE_PAGES = 2

_LOCK = threading.Lock()
_LOAD = threading.Lock()
_MISSING: set[str] = set()
_FAILS: dict[str, int] = {}
_BOOKS: dict[str, dict] = {}
_LOADED = False

PERP = (
    "binance-usdt",
    "binance-usdc",
    "binance-usd",
    "okx-usdt",
    "okx-usd",
    "bitfinex-usdt",
    "kraken-usd",
    "deribit-usd",
    "dydx-usd",
)
SPOT = (
    "binance-usdt",
    "binance-usdc",
    "okx-usdt",
    "bitfinex-usdt",
    "kraken-usd",
)


@dataclass
class Series:
    step: int
    segments: list[dict] = field(default_factory=list)


@dataclass
class Venue:
    fine: Series | None = None
    hour: Series | None = None


@dataclass
class CvdPack:
    perp_day: float | None
    spot_day: float | None
    perp_up: int
    perp_known: int
    spot_up: int
    spot_known: int
    _perp: dict[str, Venue] = field(default_factory=dict)
    _spot: dict[str, Venue] = field(default_factory=dict)

    def bar(self, side: str, start: int, end: int, now: int) -> float | None:
        venues = self._perp if side == "perp" else self._spot
        total = 0.0
        used = 0
        for venue in venues.values():
            part = _piece(venue, start, end, now)
            if part is None:
                continue
            total += part
            used += 1
        if used == 0:
            return None
        return total


def load_cvd(symbol: str) -> CvdPack:
    with _LOAD:
        try:
            return _load(symbol)
        except Exception:
            return CvdPack(None, None, 0, 0, 0, 0)


def _coin(symbol: str) -> str:
    symbol = symbol.upper().strip()
    for quote in ("USDT", "USDC", "USD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)]
    return symbol


def _xbt(coin: str) -> str:
    return "XBT" if coin.upper() == "BTC" else coin.upper()


def _session_start(now_ms: int) -> int:
    moment = datetime.fromtimestamp(now_ms / 1000, timezone.utc)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def _path() -> str:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(root, "CryptoHub")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "cvd_bars.json")


def _ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        with open(_path(), encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    missing = raw.get("missing")
    if isinstance(missing, list):
        _MISSING.update(str(item) for item in missing)
    books = raw.get("books")
    if isinstance(books, dict):
        for key, book in books.items():
            if isinstance(book, dict):
                _BOOKS[str(key)] = _book_in(book)


def _save() -> None:
    payload = {
        "missing": sorted(_MISSING),
        "books": {key: _book_out(book) for key, book in _BOOKS.items()},
    }
    try:
        with open(_path(), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
    except OSError:
        pass


def _series_in(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    segments: list[dict] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        bars: dict[int, float] = {}
        for pair in row.get("bars") or []:
            try:
                bars[int(pair[0])] = float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
        try:
            lo = int(row.get("lo") or 0)
            hi = int(row.get("hi") or 0)
        except (TypeError, ValueError):
            continue
        if lo > 0 and hi > lo:
            segments.append({"lo": lo, "hi": hi, "bars": bars})
    return segments


def _book_in(raw: dict) -> dict:
    fine = raw.get("fine") if isinstance(raw.get("fine"), dict) else {}
    hour = raw.get("hour") if isinstance(raw.get("hour"), dict) else {}
    try:
        fine_step = int(fine.get("step") or BAR_MS)
        hour_step = int(hour.get("step") or HOUR_MS)
    except (TypeError, ValueError):
        fine_step, hour_step = BAR_MS, HOUR_MS
    return {
        "fine": {"step": fine_step, "segments": _series_in(fine.get("segments"))},
        "hour": {"step": hour_step, "segments": _series_in(hour.get("segments"))},
    }


def _series_out(segments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for segment in segments:
        bars = [[stamp, segment["bars"][stamp]] for stamp in sorted(segment["bars"])]
        out.append({"lo": segment["lo"], "hi": segment["hi"], "bars": bars})
    return out


def _book_out(book: dict) -> dict:
    return {
        "fine": {"step": book["fine"]["step"], "segments": _series_out(book["fine"]["segments"])},
        "hour": {"step": book["hour"]["step"], "segments": _series_out(book["hour"]["segments"])},
    }


def _empty_book(fine_step: int, hour_step: int) -> dict:
    return {
        "fine": {"step": fine_step, "segments": []},
        "hour": {"step": hour_step, "segments": []},
    }


def _trim(segments: list[dict], step: int, now: int) -> list[dict]:
    cutoff = now - KEEP_MS
    kept: list[dict] = []
    for segment in segments:
        bars = {stamp: value for stamp, value in segment["bars"].items() if stamp >= cutoff}
        lo = max(int(segment["lo"]), cutoff)
        hi = int(segment["hi"])
        if hi <= lo:
            continue
        kept.append({"lo": lo, "hi": hi, "bars": bars})
    return kept


def _coalesce(segments: list[dict], step: int, now: int) -> list[dict]:
    ordered = sorted(segments, key=lambda item: int(item["lo"]))
    merged: list[dict] = []
    for segment in ordered:
        lo = int(segment["lo"])
        hi = int(segment["hi"])
        if hi <= lo:
            continue
        if not merged or lo > merged[-1]["hi"] + step:
            merged.append({"lo": lo, "hi": hi, "bars": dict(segment["bars"])})
            continue
        merged[-1]["bars"].update(segment["bars"])
        merged[-1]["lo"] = min(merged[-1]["lo"], lo)
        merged[-1]["hi"] = max(merged[-1]["hi"], hi)
    return _trim(merged, step, now)


def _live(segments: list[dict]) -> dict | None:
    if not segments:
        return None
    return max(segments, key=lambda item: int(item["hi"]))


def _runs(points: dict[int, float], step: int) -> list[dict[int, float]]:
    stamps = sorted(points)
    if not stamps:
        return []
    groups: list[dict[int, float]] = []
    current = {stamps[0]: points[stamps[0]]}
    previous = stamps[0]
    for stamp in stamps[1:]:
        if stamp > previous + step:
            groups.append(current)
            current = {}
        current[stamp] = points[stamp]
        previous = stamp
    groups.append(current)
    return groups


def _apply_points(segments: list[dict], step: int, points: dict[int, float], now: int, hi_now: bool, exhaust: int | None) -> list[dict]:
    runs = _runs(points, step)
    if not runs:
        if exhaust is not None:
            live = _live(segments)
            if live is not None:
                live["lo"] = min(int(live["lo"]), int(exhaust))
        return _trim(segments, step, now)
    newest = max(max(run) for run in runs)
    oldest = min(min(run) for run in runs)
    fresh: list[dict] = []
    for run in runs:
        lo = min(run)
        hi = now if hi_now and max(run) == newest else max(run) + step
        if exhaust is not None and lo == oldest:
            lo = min(lo, int(exhaust))
        fresh.append({"lo": lo, "hi": hi, "bars": dict(run)})
    return _coalesce(segments + fresh, step, now)


def _get(url: str, params: dict | None = None) -> tuple[int, object]:
    try:
        response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except requests.RequestException:
        return 0, None
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def _bucket(points: dict[int, float], step: int, stamp: int, delta: float) -> None:
    if step <= 0:
        return
    key = int(stamp) // step * step
    points[key] = points.get(key, 0.0) + float(delta)


def _binance_rows(url: str, symbol: str, now: int) -> tuple[str, list]:
    start = now - KEEP_MS
    rows: list = []
    cursor = start
    for _ in range(4):
        status, payload = _get(url, {"symbol": symbol, "interval": "15m", "startTime": cursor, "limit": 1500})
        if status == 0 or payload is None:
            return "down", []
        if isinstance(payload, dict):
            code = payload.get("code")
            if code in (-1121, -1122):
                return "miss", []
            return "down", []
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        try:
            last_open = int(payload[-1][0])
            nxt = last_open + BAR_MS
        except (TypeError, ValueError, IndexError):
            break
        if last_open + BAR_MS >= now - BAR_MS or len(payload) < 1000:
            break
        cursor = nxt
    if not rows and not isinstance(payload, list):
        return "down", []
    return "ok", rows


def _kline_delta(row, volume_at: int, taker_at: int) -> tuple[int, float] | None:
    try:
        stamp = int(row[0])
        volume = float(row[volume_at])
        taker = float(row[taker_at])
    except (TypeError, ValueError, IndexError):
        return None
    return stamp, taker - (volume - taker)


def _binance_book(url: str, symbol: str, volume_at: int, taker_at: int, now: int) -> tuple[str, dict]:
    status, rows = _binance_rows(url, symbol, now)
    if status != "ok":
        return status, {}
    fine: dict[int, float] = {}
    for row in rows:
        pair = _kline_delta(row, volume_at, taker_at)
        if pair is None:
            continue
        fine[pair[0]] = pair[1]
    if not fine:
        return "down", {}
    return "ok", fine


def _okx_contract(inst: str, period: str, step: int, pages: int, before: int | None) -> tuple[str, dict[int, float]]:
    points: dict[int, float] = {}
    cursor = before
    status = "down"
    for _ in range(pages):
        params = {"instId": inst, "period": period, "unit": "0", "limit": "100"}
        if cursor:
            params["end"] = str(cursor)
        code, payload = _get("https://www.okx.com/api/v5/rubik/stat/taker-volume-contract", params)
        if code == 0 or not isinstance(payload, dict):
            return "down", points
        if str(payload.get("code")) == "51001":
            return "miss", {}
        if str(payload.get("code")) != "0":
            return "down", points
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        if not rows:
            status = "ok"
            break
        oldest = None
        for row in rows:
            try:
                stamp = int(row[0])
                sell = float(row[1])
                buy = float(row[2])
            except (TypeError, ValueError, IndexError):
                continue
            _bucket(points, step, stamp, buy - sell)
            oldest = stamp if oldest is None else min(oldest, stamp)
        status = "ok"
        if oldest is None or len(rows) < 100:
            break
        if cursor is not None and oldest >= cursor:
            break
        cursor = oldest
    return status, points


def _okx_spot(coin: str, period: str, step: int) -> tuple[str, dict[int, float]]:
    code, payload = _get(
        "https://www.okx.com/api/v5/rubik/stat/taker-volume",
        {"ccy": coin, "instType": "SPOT", "period": period},
    )
    if code == 0 or not isinstance(payload, dict):
        return "down", {}
    if str(payload.get("code")) == "51001":
        return "miss", {}
    if str(payload.get("code")) != "0":
        return "down", {}
    points: dict[int, float] = {}
    for row in payload.get("data") or []:
        try:
            stamp = int(row[0])
            sell = float(row[1])
            buy = float(row[2])
        except (TypeError, ValueError, IndexError):
            continue
        _bucket(points, step, stamp, buy - sell)
    return ("ok" if points else "down"), points


def _fetch_binance(kind: str, coin: str, now: int) -> tuple[str, dict]:
    if kind == "perp-usdt":
        return _binance_book("https://fapi.binance.com/fapi/v1/klines", f"{coin}USDT", 5, 9, now)
    if kind == "perp-usdc":
        return _binance_book("https://fapi.binance.com/fapi/v1/klines", f"{coin}USDC", 5, 9, now)
    if kind == "perp-usd":
        return _binance_book("https://dapi.binance.com/dapi/v1/klines", f"{coin}USD_PERP", 7, 10, now)
    if kind == "spot-usdt":
        return _binance_book("https://api.binance.com/api/v3/klines", f"{coin}USDT", 5, 9, now)
    return _binance_book("https://api.binance.com/api/v3/klines", f"{coin}USDC", 5, 9, now)


def _fetch_okx_perp(inst: str, book: dict, now: int) -> tuple[str, dict[int, float], dict[int, float]]:
    fine_lo = _cursor(book["fine"]["segments"])
    hour_lo = _cursor(book["hour"]["segments"])
    fine_status, fine = _okx_contract(inst, "5m", FIVE_MS, 3, None if fine_lo is None else fine_lo)
    if fine_status == "miss":
        return "miss", {}, {}
    hour_status, hour = _okx_contract(inst, "1H", HOUR_MS, 2, None if hour_lo is None else hour_lo)
    if hour_status == "miss" and fine_status != "ok":
        return "miss", {}, {}
    if fine_status != "ok" and hour_status != "ok":
        return "down", {}, {}
    return "ok", fine, hour


def _cursor(segments: list[dict]) -> int | None:
    live = _live(segments)
    if live is None:
        return None
    return int(live["lo"])


def _bfx_page(symbol: str, end: int | None, start: int | None) -> tuple[str, list]:
    params: dict[str, int] = {"limit": 1000, "sort": -1}
    if end is not None:
        params["end"] = end
    if start is not None:
        params["start"] = start
    status, payload = _get(f"https://api-pub.bitfinex.com/v2/trades/{symbol}/hist", params)
    if status == 0 or payload is None:
        return "down", []
    if isinstance(payload, dict) or (isinstance(payload, list) and payload and payload[0] == "error"):
        return "miss", []
    if not isinstance(payload, list):
        return "down", []
    return "ok", payload


def _complete(rows: list[tuple[int, float]], step: int, exhausted: bool) -> dict[int, float]:
    """Sayfanın ortasından kesilen ilk kova yazılmaz. Kısa sayfa geçmişin sonudur."""
    if not rows:
        return {}
    grouped: dict[int, float] = {}
    earliest = min(stamp for stamp, _delta in rows)
    for stamp, delta in rows:
        _bucket(grouped, step, stamp, delta)
    first = min(grouped)
    if not exhausted and earliest % step != 0:
        grouped.pop(first, None)
    return grouped


def _tape_points(rows: list[tuple[int, float]], exhausted: bool) -> dict[int, float]:
    ordered = sorted(rows, key=lambda item: item[0])
    if not ordered:
        return {}
    clusters: list[list[tuple[int, float]]] = [[ordered[0]]]
    for row in ordered[1:]:
        if row[0] // BAR_MS > clusters[-1][-1][0] // BAR_MS + 1:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    points: dict[int, float] = {}
    for cluster in clusters:
        points.update(_complete(cluster, BAR_MS, exhausted and cluster is clusters[0]))
    return points


def _bfx(symbol: str, book: dict, now: int) -> tuple[str, dict[int, float], bool]:
    rows: list[tuple[int, float]] = []
    status, latest = _bfx_page(symbol, None, None)
    if status != "ok":
        return status, {}, False
    rows.extend(_bfx_signed(latest))
    cursor = _cursor(book["fine"]["segments"])
    exhausted = False
    if cursor is None and rows:
        cursor = min(stamp for stamp, _delta in rows)
    for _ in range(TAPE_PAGES):
        if cursor is None:
            break
        status, page = _bfx_page(symbol, cursor, None)
        if status != "ok":
            break
        signed = _bfx_signed(page)
        if not signed:
            exhausted = True
            break
        rows.extend(signed)
        oldest = min(stamp for stamp, _delta in signed)
        if len(page) < 1000:
            exhausted = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
    return "ok", _tape_points(rows, exhausted), exhausted


def _bfx_signed(page: list) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    for row in page:
        try:
            rows.append((int(row[1]), float(row[2])))
        except (TypeError, ValueError, IndexError):
            continue
    return rows


def _deribit(coin: str, book: dict, now: int) -> tuple[str, dict[int, float], bool]:
    name = f"{coin}-PERPETUAL"
    rows: list[tuple[int, float]] = []
    status, latest = _deribit_page(name, now)
    if status != "ok":
        return status, {}, False
    rows.extend(latest)
    cursor = _cursor(book["fine"]["segments"])
    if cursor is None and rows:
        cursor = min(stamp for stamp, _delta in rows)
    exhausted = False
    for _ in range(TAPE_PAGES):
        if cursor is None:
            break
        status, page = _deribit_page(name, cursor)
        if status != "ok":
            break
        if not page:
            exhausted = True
            break
        rows.extend(page)
        oldest = min(stamp for stamp, _delta in page)
        if len(page) < 1000:
            exhausted = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
    return "ok", _tape_points(rows, exhausted), exhausted


def _deribit_page(name: str, end: int | None) -> tuple[str, list[tuple[int, float]]]:
    params: dict[str, object] = {"instrument_name": name, "count": 1000, "sorting": "desc"}
    if end is not None:
        params["end_timestamp"] = end
    status, payload = _get("https://www.deribit.com/api/v2/public/get_last_trades_by_instrument_and_time", params)
    if status == 0 or payload is None:
        return "down", []
    if not isinstance(payload, dict):
        return "down", []
    error = payload.get("error")
    if error:
        text = str(error).lower()
        if "not found" in text or "invalid" in text:
            return "miss", []
        return "down", []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    trades = result.get("trades") if isinstance(result.get("trades"), list) else []
    rows: list[tuple[int, float]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        try:
            price = float(trade["price"])
            amount = float(trade["amount"])
            stamp = int(trade["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0:
            continue
        coin = amount / price
        rows.append((stamp, coin if trade.get("direction") == "buy" else -coin))
    return "ok", rows


def _kraken_futures(coin: str, book: dict, now: int) -> tuple[str, dict[int, float], bool]:
    market = f"PF_{_xbt(coin)}USD"
    rows: list[tuple[int, float]] = []
    status, latest = _kraken_fut_page(market, None)
    if status != "ok":
        return status, {}, False
    rows.extend(latest)
    cursor = _cursor(book["fine"]["segments"])
    if cursor is None and rows:
        cursor = min(stamp for stamp, _delta in rows)
    exhausted = False
    for _ in range(TAPE_PAGES):
        if cursor is None:
            break
        status, page = _kraken_fut_page(market, cursor)
        if status != "ok":
            break
        if not page:
            exhausted = True
            break
        rows.extend(page)
        oldest = min(stamp for stamp, _delta in page)
        if len(page) < 1000:
            exhausted = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
    return "ok", _tape_points(rows, exhausted), exhausted


def _kraken_fut_page(market: str, before: int | None) -> tuple[str, list[tuple[int, float]]]:
    params: dict[str, object] = {"count": 1000, "sort": "desc"}
    if before is not None:
        params["before"] = before
    status, payload = _get(f"https://futures.kraken.com/api/history/v2/market/{market}/executions", params)
    if status in (400, 404):
        return "miss", []
    if status != 200 or not isinstance(payload, dict):
        return "down", []
    elements = payload.get("elements") if isinstance(payload.get("elements"), list) else []
    rows: list[tuple[int, float]] = []
    for item in elements:
        if not isinstance(item, dict):
            continue
        event = item.get("event") if isinstance(item.get("event"), dict) else {}
        body = event.get("Execution") if isinstance(event.get("Execution"), dict) else None
        if body is None:
            continue
        execution = body.get("execution") if isinstance(body.get("execution"), dict) else {}
        taker = execution.get("takerOrder") if isinstance(execution.get("takerOrder"), dict) else {}
        try:
            stamp = int(execution.get("timestamp") or item.get("timestamp"))
            qty = float(execution["quantity"])
        except (TypeError, ValueError, KeyError):
            continue
        direction = str(taker.get("direction") or "")
        if direction not in ("Buy", "Sell") or qty == 0:
            continue
        rows.append((stamp, qty if direction == "Buy" else -qty))
    return "ok", rows


def _kraken_spot(coin: str, book: dict, now: int) -> tuple[str, dict[int, float], bool]:
    pair = f"{_xbt(coin)}USD"
    status, latest = _kraken_spot_page(pair, None)
    if status != "ok":
        return status, {}, False
    rows = list(latest)
    cursor = _cursor(book["fine"]["segments"])
    if cursor is None and rows:
        cursor = min(stamp for stamp, _delta in rows)
    exhausted = False
    for _ in range(TAPE_PAGES):
        if cursor is None:
            break
        since = (cursor - 6 * 60 * 1000) * 1_000_000
        status, page = _kraken_spot_page(pair, since)
        if status != "ok":
            break
        if not page:
            exhausted = True
            break
        rows.extend(page)
        oldest = min(stamp for stamp, _delta in page)
        newest = max(stamp for stamp, _delta in page)
        if len(page) < 1000 and newest >= cursor - 1000:
            exhausted = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
    return "ok", _tape_points(rows, exhausted), exhausted


def _kraken_spot_page(pair: str, since: int | None) -> tuple[str, list[tuple[int, float]]]:
    params: dict[str, object] = {"pair": pair}
    if since is not None:
        params["since"] = str(since)
    status, payload = _get("https://api.kraken.com/0/public/Trades", params)
    if status != 200 or not isinstance(payload, dict):
        return "down", []
    error = payload.get("error")
    if error:
        return "miss", []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    rows: list[tuple[int, float]] = []
    for key, trades in result.items():
        if key == "last" or not isinstance(trades, list):
            continue
        for trade in trades:
            try:
                volume = float(trade[1])
                stamp = int(float(trade[2]) * 1000)
                side = str(trade[3])
            except (TypeError, ValueError, IndexError):
                continue
            if side not in ("b", "s"):
                continue
            rows.append((stamp, volume if side == "b" else -volume))
    return "ok", rows


def _dydx(coin: str, book: dict, now: int) -> tuple[str, dict[int, float], bool]:
    market = f"{coin}-USD"
    rows: list[tuple[int, float]] = []
    status, latest = _dydx_page(market, None)
    if status != "ok":
        return status, {}, False
    rows.extend(latest)
    cursor = _cursor(book["fine"]["segments"])
    if cursor is None and rows:
        cursor = min(stamp for stamp, _delta in rows)
    exhausted = False
    for _ in range(TAPE_PAGES):
        if cursor is None:
            break
        status, page = _dydx_page(market, cursor)
        if status != "ok":
            break
        if not page:
            exhausted = True
            break
        rows.extend(page)
        oldest = min(stamp for stamp, _delta in page)
        if len(page) < 1000:
            exhausted = True
            break
        if oldest >= cursor:
            break
        cursor = oldest
    return "ok", _tape_points(rows, exhausted), exhausted


def _dydx_page(market: str, before: int | None) -> tuple[str, list[tuple[int, float]]]:
    params: dict[str, object] = {"limit": 1000}
    if before is not None:
        moment = datetime.fromtimestamp(before / 1000, timezone.utc)
        params["createdBeforeOrAt"] = moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    status, payload = _get(f"https://indexer.dydx.trade/v4/trades/perpetualMarket/{market}", params)
    if status == 404:
        return "miss", []
    if status != 200 or not isinstance(payload, dict):
        return "down", []
    if payload.get("errors") or payload.get("error"):
        return "miss", []
    trades = payload.get("trades") if isinstance(payload.get("trades"), list) else None
    if trades is None:
        return "miss", []
    rows: list[tuple[int, float]] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        try:
            size = float(trade["size"])
            stamp = _iso_ms(str(trade["createdAt"]))
        except (KeyError, TypeError, ValueError):
            continue
        side = str(trade.get("side") or "")
        if side not in ("BUY", "SELL"):
            continue
        rows.append((stamp, size if side == "BUY" else -size))
    return "ok", rows


def _iso_ms(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def _one(side: str, name: str, coin: str, now: int) -> tuple[str, dict]:
    key = f"{side}:{name}:{coin}"
    with _LOCK:
        book = _BOOKS.get(key) or _empty_book(BAR_MS, HOUR_MS)
        stored = {
            "fine": {"step": book["fine"]["step"], "segments": [dict(item) for item in book["fine"]["segments"]]},
            "hour": {"step": book["hour"]["step"], "segments": [dict(item) for item in book["hour"]["segments"]]},
        }
    try:
        update = _pull(side, name, coin, stored, now)
    except Exception:
        update = ("down", {})
    return key, {"status": update[0], "body": update[1] if len(update) > 1 else {}}


def _pull(side: str, name: str, coin: str, book: dict, now: int) -> tuple:
    if name.startswith("binance-"):
        kind = f"{'perp' if side == 'perp' else 'spot'}-{name.split('-', 1)[1]}"
        status, fine = _fetch_binance(kind, coin, now)
        return status, {"fine": fine, "hour": {}, "fine_step": BAR_MS, "hour_step": HOUR_MS, "replace": True, "exhaust": None}
    if side == "perp" and name == "okx-usdt":
        status, fine, hour = _fetch_okx_perp(f"{coin}-USDT-SWAP", book, now)
        return status, {"fine": fine, "hour": hour, "fine_step": FIVE_MS, "hour_step": HOUR_MS, "replace": False, "exhaust": None}
    if side == "perp" and name == "okx-usd":
        status, fine, hour = _fetch_okx_perp(f"{coin}-USD-SWAP", book, now)
        return status, {"fine": fine, "hour": hour, "fine_step": FIVE_MS, "hour_step": HOUR_MS, "replace": False, "exhaust": None}
    if side == "spot" and name == "okx-usdt":
        fine_status, fine = _okx_spot(coin, "5m", FIVE_MS)
        hour_status, hour = _okx_spot(coin, "1H", HOUR_MS)
        if fine_status == "miss" and hour_status == "miss":
            return "miss", {}
        if fine_status != "ok" and hour_status != "ok":
            return "down", {}
        return "ok", {"fine": fine, "hour": hour, "fine_step": FIVE_MS, "hour_step": HOUR_MS, "replace": False, "exhaust": None}
    if name == "bitfinex-usdt":
        symbol = f"t{coin}F0:USTF0" if side == "perp" else f"t{coin}UST"
        status, fine, exhausted = _bfx(symbol, book, now)
        return status, _tape_body(fine, exhausted, now)
    if side == "perp" and name == "kraken-usd":
        status, fine, exhausted = _kraken_futures(coin, book, now)
        return status, _tape_body(fine, exhausted, now)
    if side == "spot" and name == "kraken-usd":
        status, fine, exhausted = _kraken_spot(coin, book, now)
        return status, _tape_body(fine, exhausted, now)
    if side == "perp" and name == "deribit-usd":
        status, fine, exhausted = _deribit(coin, book, now)
        return status, _tape_body(fine, exhausted, now)
    if side == "perp" and name == "dydx-usd":
        status, fine, exhausted = _dydx(coin, book, now)
        return status, _tape_body(fine, exhausted, now)
    return "miss", {}


def _tape_body(fine: dict[int, float], exhausted: bool, now: int) -> dict:
    return {
        "fine": fine,
        "hour": {},
        "fine_step": BAR_MS,
        "hour_step": HOUR_MS,
        "replace": False,
        "exhaust": now - KEEP_MS if exhausted else None,
    }


def _fresh_now(points: dict, step: int, now: int) -> bool:
    if not points or step <= 0:
        return False
    return max(points) + step + SLACK_MS >= now


def _store(key: str, body: dict, now: int) -> None:
    book = _BOOKS.get(key) or _empty_book(int(body.get("fine_step") or BAR_MS), int(body.get("hour_step") or HOUR_MS))
    fine_step = int(body.get("fine_step") or book["fine"]["step"])
    hour_step = int(body.get("hour_step") or book["hour"]["step"])
    fine_pts = body.get("fine") if isinstance(body.get("fine"), dict) else {}
    hour_pts = body.get("hour") if isinstance(body.get("hour"), dict) else {}
    exhaust = body.get("exhaust")
    fine_now = _fresh_now(fine_pts, fine_step, now)
    hour_now = _fresh_now(hour_pts, hour_step, now)
    if body.get("replace"):
        fine_segs = _apply_points([], fine_step, fine_pts, now, fine_now, None)
        hour_segs = _apply_points([], hour_step, hour_pts, now, hour_now, None)
    else:
        fine_segs = _apply_points(book["fine"]["segments"], fine_step, fine_pts, now, fine_now, exhaust if fine_pts or exhaust else None)
        hour_segs = _apply_points(book["hour"]["segments"], hour_step, hour_pts, now, hour_now, None)
    _BOOKS[key] = {"fine": {"step": fine_step, "segments": fine_segs}, "hour": {"step": hour_step, "segments": hour_segs}}


def _venue_of(book: dict) -> Venue:
    fine = book.get("fine") or {}
    hour = book.get("hour") or {}
    fine_segs = fine.get("segments") or []
    hour_segs = hour.get("segments") or []
    return Venue(
        fine=Series(int(fine.get("step") or BAR_MS), fine_segs) if fine_segs else None,
        hour=Series(int(hour.get("step") or HOUR_MS), hour_segs) if hour_segs else None,
    )


def _load(symbol: str) -> CvdPack:
    coin = _coin(symbol)
    now = int(time.time() * 1000)
    with _LOCK:
        _ensure_loaded()
        jobs = []
        for side, names in (("perp", PERP), ("spot", SPOT)):
            for name in names:
                if f"{side}:{name}:{coin}" in _MISSING:
                    continue
                jobs.append((side, name))
    fresh: dict[str, dict] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=8) as pool:
            running = {pool.submit(_one, side, name, coin, now): (side, name) for side, name in jobs}
            for job in running:
                try:
                    key, payload = job.result()
                except Exception:
                    side, name = running[job]
                    key, payload = f"{side}:{name}:{coin}", {"status": "down", "body": {}}
                fresh[key] = payload
    with _LOCK:
        for key, payload in fresh.items():
            status = payload.get("status")
            if status == "miss":
                _MISSING.add(key)
                _FAILS.pop(key, None)
                continue
            if status == "ok":
                _FAILS[key] = 0
                _store(key, payload.get("body") or {}, now)
                continue
            _FAILS[key] = _FAILS.get(key, 0) + 1
        _save()
        perp, perp_known = _view("perp", coin)
        spot, spot_known = _view("spot", coin)
    pack = CvdPack(None, None, 0, perp_known, 0, spot_known, perp, spot)
    perp_day, perp_up = _day(perp, now)
    spot_day, spot_up = _day(spot, now)
    pack.perp_day = perp_day
    pack.spot_day = spot_day
    pack.perp_up = perp_up
    pack.spot_up = spot_up
    return pack


def _view(side: str, coin: str) -> tuple[dict[str, Venue], int]:
    names = PERP if side == "perp" else SPOT
    known = 0
    venues: dict[str, Venue] = {}
    for name in names:
        key = f"{side}:{name}:{coin}"
        if key in _MISSING:
            continue
        known += 1
        if _FAILS.get(key, 0) >= DROP_AFTER:
            continue
        book = _BOOKS.get(key)
        if book is None:
            continue
        venues[name] = _venue_of(book)
    return venues, known


def _covers(series: Series, start: int, end: int, now: int) -> bool:
    need = end if end <= now else now
    if need < start:
        return False
    # Canlı uç bir kova gecikebilir. Kapanmış mumun ucu gevşetilmez.
    slack = series.step + SLACK_MS if need >= now - series.step else SLACK_MS
    for segment in series.segments:
        if int(segment["lo"]) <= start and int(segment["hi"]) + slack >= need:
            return True
    return False


def _accum(series: Series, start: int, end: int, now: int) -> float:
    need = end if end <= now else now
    step = series.step
    slack = step + SLACK_MS if need >= now - step else SLACK_MS
    for segment in series.segments:
        if int(segment["lo"]) > start or int(segment["hi"]) + slack < need:
            continue
        bars = segment["bars"]
        total = 0.0
        stamp = start
        while stamp < end:
            total += float(bars.get(stamp, 0.0))
            stamp += step
        return total
    return 0.0


def _piece(venue: Venue, start: int, end: int, now: int) -> float | None:
    span = end - start
    if span <= 0:
        return None
    fine = venue.fine
    if fine is not None and start % fine.step == 0 and _covers(fine, start, end, now):
        if span >= fine.step or end <= start + fine.step:
            return _accum(fine, start, end, now)
    hour = venue.hour
    if hour is not None and span >= hour.step and start % hour.step == 0 and _covers(hour, start, end, now):
        return _accum(hour, start, end, now)
    return None


def _day(venues: dict[str, Venue], now: int) -> tuple[float | None, int]:
    start = _session_start(now)
    total = 0.0
    used = 0
    for venue in venues.values():
        part = _piece(venue, start, now, now)
        if part is None:
            continue
        total += part
        used += 1
    if used == 0:
        return None, 0
    return total, used
