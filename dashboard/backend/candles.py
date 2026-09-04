"""OHLC aggregation aligned with bot sinir_saatleri candle closes."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from database import TIMEFRAMES, fetch_veri_rows

SINIR_SAATLERI: dict[str, list[int] | None] = {
    "15dk": None,
    "1sa": list(range(24)),
    "2sa": [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23],
    "4sa": [23, 3, 7, 11, 15, 19],
    "8sa": [3, 11, 19],
    "24sa": [3],
}


def _parse_row_time(tarih: str, saat: str) -> datetime | None:
    saat = (saat or "").strip()
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
        try:
            return datetime.strptime(f"{tarih} {saat}", fmt)
        except ValueError:
            continue
    return None


def _candle_close(dt: datetime, hours: list[int] | None) -> datetime:
    if hours is None:
        return dt.replace(second=0, microsecond=0)
    hours_set = set(hours)
    cursor = dt.replace(second=0, microsecond=0)
    if cursor.minute == 0 and cursor.hour in hours_set:
        return cursor
    cursor = cursor.replace(minute=0) + timedelta(hours=1)
    for _ in range(48):
        if cursor.hour in hours_set:
            return cursor
        cursor += timedelta(hours=1)
    return cursor


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_candles(tf: str) -> dict:
    if tf not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {tf}")

    hours = SINIR_SAATLERI[tf]
    groups: dict[datetime, list[dict]] = defaultdict(list)

    for row in fetch_veri_rows():
        dt = _parse_row_time(row["tarih"], row["saat"])
        if dt is None:
            continue
        groups[_candle_close(dt, hours)].append(row)

    price = []
    oi = []
    funding = []
    for close_dt, rows in sorted(groups.items()):
        time = int(close_dt.timestamp())

        opens = [_num(r.get("price_open")) for r in rows]
        highs = [_num(r.get("price_high")) for r in rows]
        lows = [_num(r.get("price_low")) for r in rows]
        closes = [_num(r.get("price")) for r in rows]
        opens = [v if v is not None else closes[i] for i, v in enumerate(opens)]
        highs = [v if v is not None else closes[i] for i, v in enumerate(highs)]
        lows = [v if v is not None else closes[i] for i, v in enumerate(lows)]
        if closes and all(v is not None for v in closes):
            c_highs = [v for v in highs if v is not None]
            c_lows = [v for v in lows if v is not None]
            price.append(
                {
                    "time": time,
                    "open": opens[0],
                    "high": max(c_highs) if c_highs else closes[-1],
                    "low": min(c_lows) if c_lows else closes[-1],
                    "close": closes[-1],
                }
            )

        oi_vals = [_num(r.get("oi_btc")) for r in rows]
        oi_vals = [v for v in oi_vals if v is not None]
        if oi_vals:
            oi.append(
                {
                    "time": time,
                    "open": oi_vals[0],
                    "high": max(oi_vals),
                    "low": min(oi_vals),
                    "close": oi_vals[-1],
                }
            )

        fund_vals = [_num(r.get("funding_pct")) for r in rows]
        fund_vals = [v for v in fund_vals if v is not None]
        if fund_vals:
            funding.append({"time": time, "value": fund_vals[-1] * 100})

    return {"tf": tf, "price": price, "oi": oi, "funding": funding}