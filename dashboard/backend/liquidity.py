"""Nearest estimated liquidation clusters. Imports bot likidasyon.py without modifying it."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

_CACHE: dict | None = None
_CACHE_AT = 0.0
_CACHE_TTL_SEC = 30.0


def get_harita(*, force: bool = False) -> dict:
    global _CACHE, _CACHE_AT
    now = time.monotonic()
    if force or _CACHE is None or now - _CACHE_AT > _CACHE_TTL_SEC:
        from likidasyon import tum_haritalari_hesapla

        _CACHE = tum_haritalari_hesapla()
        _CACHE_AT = now
    return _CACHE


def serialize_estimated_map(layer: str, window_h: int, current_price: float | None) -> dict:
    harita = get_harita()
    katmanlar = harita.get("katmanlar") or {}
    pencereler = katmanlar.get(layer) or {}
    kumeler = pencereler.get(window_h) or pencereler.get(int(window_h)) or {}
    levels = []
    for anahtar, miktar in (kumeler or {}).items():
        fiyat, yon = anahtar
        levels.append(
            {
                "price": float(fiyat),
                "side": str(yon),
                "amount_btc": float(miktar),
            }
        )
    levels.sort(key=lambda item: item["price"])
    guncel_oi = {}
    for key, value in (harita.get("guncel_oi") or {}).items():
        guncel_oi[key] = float(value) if value is not None else None
    return {
        "layer": layer,
        "window_h": int(window_h),
        "current_price": current_price,
        "guncel_oi": guncel_oi,
        "levels": levels,
    }


def _flatten_clusters(harita: dict) -> list[dict]:
    rows: list[dict] = []
    katmanlar = harita.get("katmanlar") or {}
    for katman, pencereler in katmanlar.items():
        for pencere, kumeler in (pencereler or {}).items():
            for anahtar, miktar in (kumeler or {}).items():
                fiyat, yon = anahtar
                rows.append(
                    {
                        "price": float(fiyat),
                        "side": yon,
                        "amount_btc": float(miktar),
                        "layer": katman,
                        "window_h": int(pencere),
                    }
                )
    return rows


def nearest_liquidity_levels(current_price: float | None) -> dict:
    """Closest cluster above and below the current price (12h linear+inverse combined)."""
    clusters = [
        c
        for c in _flatten_clusters(get_harita())
        if c["window_h"] == 12
    ]
    merged: dict[tuple[float, str], float] = {}
    for c in clusters:
        key = (round(c["price"], 2), c["side"])
        merged[key] = merged.get(key, 0.0) + c["amount_btc"]

    combined = [{"price": price, "side": side, "amount_btc": amount} for (price, side), amount in merged.items()]

    above = None
    below = None
    if current_price:
        for item in combined:
            price = item["price"]
            if price > current_price:
                if above is None or price < above["price"]:
                    above = item
            elif price < current_price:
                if below is None or price > below["price"]:
                    below = item

    if above and current_price:
        above = {
            **above,
            "distance_pct": (above["price"] - current_price) / current_price * 100,
        }
    if below and current_price:
        below = {
            **below,
            "distance_pct": (below["price"] - current_price) / current_price * 100,
        }

    return {"above": above, "below": below}