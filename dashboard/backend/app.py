from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from candles import build_candles
from database import (
    DB_PATH,
    SETTING_TELEGRAM_CHAT_ID,
    SETTING_TELEGRAM_TOKEN,
    TIMEFRAMES,
    fetch_closed_signals,
    fetch_history_series,
    fetch_latest_veri,
    fetch_price_near_hours_ago,
    fetch_realized_liquidations,
    get_settings,
    parse_iso_date,
    telegram_configured,
    upsert_settings,
)
from history import signal_stats
from liquidity import serialize_estimated_map
from overview import build_overview

app = FastAPI(title="CryptoHub Dashboard API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_SYMBOL_RE = re.compile(r"^[A-Z0-9]{4,20}$")


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH), "db_exists": DB_PATH.exists()}


@app.get("/api/price")
def ticker_price(symbol: str = "BTCUSDT"):
    symbol = symbol.upper().strip()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="invalid symbol")
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={quote(symbol)}"
    try:
        req = Request(url, headers={"User-Agent": "CryptoHub-Dashboard"})
        with urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Binance error: {exc.code}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail="Binance ticker unavailable") from exc
    try:
        return {"symbol": str(payload["symbol"]), "price": float(payload["price"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="unexpected ticker payload") from exc


@app.get("/api/latest")
def latest():
    try:
        row = fetch_latest_veri()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database read failed: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="veri table is empty")
    return row


@app.get("/api/overview")
def overview():
    try:
        data = build_overview()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Overview failed: {exc}") from exc
    if data is None:
        raise HTTPException(status_code=404, detail="veri table is empty")
    return data


@app.get("/api/liquidation-map")
def liquidation_map(layer: str = "linear", window: int = 12):
    if layer not in ("linear", "inverse", "all"):
        raise HTTPException(status_code=400, detail="layer must be linear, inverse, or all")
    if window not in (12, 24):
        raise HTTPException(status_code=400, detail="window must be 12 or 24")
    try:
        latest = fetch_latest_veri()
        price = float(latest["price"]) if latest and latest.get("price") is not None else None
        reference = fetch_price_near_hours_ago(window)
        return serialize_estimated_map(layer, window, price, reference)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Liquidation map failed: {exc}") from exc


@app.get("/api/liquidations")
def liquidations(start: str | None = None, end: str | None = None):
    try:
        start_d = parse_iso_date(start)
        end_d = parse_iso_date(end)
        return {"events": fetch_realized_liquidations(start_d, end_d)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Liquidations failed: {exc}") from exc


@app.get("/api/history")
def history(start: str | None = None, end: str | None = None):
    try:
        start_d = parse_iso_date(start)
        end_d = parse_iso_date(end)
        if start_d and end_d and start_d > end_d:
            raise ValueError("start after end")
        series = fetch_history_series(start_d, end_d)
        signals = fetch_closed_signals(start_d, end_d)
        return {"series": series, "signals": signals, "stats": signal_stats(signals)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"History failed: {exc}") from exc


@app.get("/api/candles")
def candles(tf: str = "15dk"):
    if tf not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unknown timeframe: {tf}")
    try:
        return build_candles(tf)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Candles failed: {exc}") from exc


class TelegramSettingsIn(BaseModel):
    telegram_bot_token: str = Field(min_length=1)
    telegram_chat_id: str = Field(min_length=1)


def _settings_payload(data: dict[str, str]) -> dict:
    return {
        "telegram_bot_token": data.get(SETTING_TELEGRAM_TOKEN, ""),
        "telegram_chat_id": data.get(SETTING_TELEGRAM_CHAT_ID, ""),
        "configured": telegram_configured(data),
    }


@app.get("/api/settings")
def read_settings():
    try:
        data = get_settings()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Settings read failed: {exc}") from exc
    return _settings_payload(data)


@app.put("/api/settings")
def write_settings(body: TelegramSettingsIn):
    try:
        data = upsert_settings(
            {
                SETTING_TELEGRAM_TOKEN: body.telegram_bot_token.strip(),
                SETTING_TELEGRAM_CHAT_ID: body.telegram_chat_id.strip(),
            }
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Settings write failed: {exc}") from exc
    return _settings_payload(data)
