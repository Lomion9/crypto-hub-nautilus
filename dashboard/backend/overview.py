from __future__ import annotations

from database import fetch_latest_veri, fetch_timeframe_states
from liquidity import nearest_liquidity_levels


def build_overview() -> dict | None:
    latest = fetch_latest_veri()
    if latest is None:
        return None

    price = latest.get("price")
    timeframes = fetch_timeframe_states()
    funding_status = None
    for item in timeframes:
        if item["tf"] == "15dk" and item["durum"]:
            funding_status = item["durum"].get("funding_durum")
            break

    try:
        liquidity = nearest_liquidity_levels(float(price) if price is not None else None)
    except Exception:
        liquidity = {"above": None, "below": None}

    return {
        "snapshot": latest,
        "funding_status": funding_status,
        "liquidity": liquidity,
        "timeframes": timeframes,
    }