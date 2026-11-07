"""OKX USDT-SWAP emri. Grafik ve sinyal verisi burada okunmaz."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
from datetime import datetime, timezone

import requests

from desktop.format import format_price

OKX = "https://www.okx.com"


class OkxError(Exception):
    pass


class Creds:
    def __init__(self, key: str, secret: str, passphrase: str) -> None:
        self.key = key.strip()
        self.secret = secret.strip()
        self.passphrase = passphrase.strip()

    def ready(self) -> bool:
        return bool(self.key and self.secret and self.passphrase)


def inst_id(symbol: str) -> str:
    base = symbol[:-4] if symbol.upper().endswith("USDT") else symbol.upper()
    return f"{base}-USDT-SWAP"


def _stamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sign(secret: str, stamp: str, method: str, path: str, body: str) -> str:
    payload = f"{stamp}{method.upper()}{path}{body}"
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def request(creds: Creds, method: str, path: str, body: dict | list | None = None, simulated: bool = False) -> dict:
    raw = "" if body is None else json.dumps(body, separators=(",", ":"))
    stamp = _stamp()
    headers = {
        "OK-ACCESS-KEY": creds.key,
        "OK-ACCESS-SIGN": _sign(creds.secret, stamp, method, path, raw),
        "OK-ACCESS-TIMESTAMP": stamp,
        "OK-ACCESS-PASSPHRASE": creds.passphrase,
        "Content-Type": "application/json",
    }
    if simulated:
        headers["x-simulated-trading"] = "1"
    try:
        response = requests.request(
            method.upper(),
            OKX + path,
            data=raw if raw else None,
            headers=headers,
            timeout=12,
        )
    except requests.RequestException as exc:
        raise OkxError("OKX'e ulaşılamadı") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise OkxError(f"OKX yanıtı okunamadı ({response.status_code})") from exc
    if not isinstance(payload, dict):
        raise OkxError("OKX yanıtı boş")
    code = str(payload.get("code") or "")
    if code not in ("0", ""):
        raise OkxError(_error_text(payload))
    return payload


def _error_text(payload: dict) -> str:
    message = str(payload.get("msg") or "OKX reddetti")
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        detail = str(data[0].get("sMsg") or "")
        if detail and detail != "None":
            message = detail
    return message


def _flat(message: str) -> bool:
    text = message.lower()
    return any(
        piece in text
        for piece in (
            "don't have any positions",
            "do not have any positions",
            "position doesn't exist",
            "position does not exist",
        )
    )


def available_usdt(creds: Creds, simulated: bool) -> float:
    """İşlem hesabındaki kullanılabilir USDT."""
    balance = request(creds, "GET", "/api/v5/account/balance?ccy=USDT", simulated=simulated)
    rows = balance.get("data") or []
    if rows and isinstance(rows[0], dict):
        for item in rows[0].get("details") or []:
            if isinstance(item, dict) and item.get("ccy") == "USDT":
                for key in ("availEq", "availBal"):
                    try:
                        value = float(item.get(key))
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(value):
                        return max(0.0, value)
    return 0.0


def ping(creds: Creds, simulated: bool) -> str:
    if not creds.ready():
        raise OkxError("API anahtarı, secret ve parola gerekli")
    amount = f"{available_usdt(creds, simulated):.2f}"
    config = request(creds, "GET", "/api/v5/account/config", simulated=simulated)
    mode = "tek yön"
    cfg = config.get("data") or []
    if cfg and isinstance(cfg[0], dict) and cfg[0].get("posMode") == "long_short_mode":
        mode = "hedge"
    where = "simülasyon" if simulated else "canlı"
    if cfg and isinstance(cfg[0], dict) and str(cfg[0].get("acctLv") or "") == "1":
        raise OkxError(f"Bağlandı · {where} · USDT {amount} · ama {SPOT_ONLY}")
    return f"Bağlandı · {where} · USDT {amount} · {mode}"


def _public_instrument(inst: str) -> dict:
    try:
        response = requests.get(
            f"{OKX}/api/v5/public/instruments",
            params={"instType": "SWAP", "instId": inst},
            timeout=12,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise OkxError("Sözleşme bilgisi alınamadı") from exc
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows or not isinstance(rows[0], dict):
        raise OkxError(f"{inst} OKX'te yok")
    return rows[0]


def _step_digits(step: float) -> int:
    if step <= 0:
        return 8
    text = f"{step:.10f}".rstrip("0").rstrip(".")
    if "." not in text:
        return 0
    return min(8, len(text.split(".")[1]))


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor((value / step) + 1e-8) * step


def _round_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return round(value / step) * step


def _text_step(value: float, step: float) -> str:
    return f"{value:.{_step_digits(step)}f}"


def _contracts(margin: float, leverage: int, price: float, info: dict) -> tuple[str, float]:
    ct_val = float(info.get("ctVal") or 0)
    lot = float(info.get("lotSz") or 1)
    minimum = float(info.get("minSz") or lot or 1)
    if ct_val <= 0 or price <= 0 or lot <= 0:
        raise OkxError("Sözleşme boyu okunamadı")
    unit = ct_val if str(info.get("ctValCcy") or "").upper() == "USDT" else ct_val * price
    if unit <= 0:
        raise OkxError("Sözleşme boyu okunamadı")
    raw = (margin * leverage) / unit
    size = _floor_step(raw, lot)
    if size + 1e-12 < minimum or size <= 0:
        raise OkxError("Marjin bu kaldıraçla en küçük sözleşmenin altında")
    return _text_step(size, lot), size


SPOT_ONLY = (
    "OKX hesabın Spot modunda, vadeli işlem açılamaz. "
    "OKX'te Ayarlar > Hesap modu > Spot ve vadeli (Futures) moduna geç."
)


def _hedge(creds: Creds, simulated: bool) -> bool:
    payload = request(creds, "GET", "/api/v5/account/config", simulated=simulated)
    rows = payload.get("data") or []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    if str(row.get("acctLv") or "") == "1":
        raise OkxError(SPOT_ONLY)
    return row.get("posMode") == "long_short_mode"


def _set_leverage(creds: Creds, inst: str, lever: int, mode: str, pos_side: str, simulated: bool, cap: int) -> int:
    use = max(1, min(int(lever), cap if cap > 0 else int(lever)))
    body = {"instId": inst, "lever": str(use), "mgnMode": mode}
    if pos_side in ("long", "short"):
        body["posSide"] = pos_side
    request(creds, "POST", "/api/v5/account/set-leverage", body, simulated)
    return use


def close_position(creds: Creds, position: dict, simulated: bool) -> dict:
    """Açık pozisyonun tamamını piyasa emriyle kapatır."""
    if not position.get("inst") or not str(position.get("sz") or ""):
        raise OkxError("Pozisyon okunamadı")
    _close(creds, position, simulated)
    word = "long" if position.get("side") == "up" else "short"
    return {"message": f"{word} kapatıldı · {position.get('sz')} sözleşme"}


def _close(creds: Creds, held: dict, simulated: bool) -> None:
    pos_side = str(held.get("pos_side") or "net")
    trade_side = str(held.get("side") or "")
    if pos_side == "long" or (pos_side == "net" and trade_side == "up"):
        side = "sell"
    else:
        side = "buy"
    body = {
        "instId": str(held.get("inst") or ""),
        "tdMode": str(held.get("td_mode") or "cross"),
        "side": side,
        "ordType": "market",
        "sz": str(held.get("sz") or ""),
    }
    if pos_side in ("long", "short"):
        body["posSide"] = pos_side
    else:
        body["reduceOnly"] = True
    request(creds, "POST", "/api/v5/trade/order", body, simulated)


def _open(creds: Creds, symbol: str, side: str, tp: float, price: float, margin: float, leverage: int, mode: str, simulated: bool) -> dict:
    if side not in ("up", "down"):
        raise OkxError("Yön yok, emir yok")
    if tp <= 0 or price <= 0:
        raise OkxError("Hedef yok, emir yok")
    if side == "up" and tp <= price:
        raise OkxError("Take profit girişin altında, emir yok")
    if side == "down" and tp >= price:
        raise OkxError("Take profit girişin üstünde, emir yok")
    inst = inst_id(symbol)
    info = _public_instrument(inst)
    tick = float(info.get("tickSz") or 0)
    tp_px = _round_step(tp, tick)
    if side == "up" and tp_px <= price:
        raise OkxError("Take profit girişin altında, emir yok")
    if side == "down" and tp_px >= price:
        raise OkxError("Take profit girişin üstünde, emir yok")
    hedge = _hedge(creds, simulated)
    pos_side = ("long" if side == "up" else "short") if hedge else "net"
    try:
        cap = int(float(info.get("lever") or leverage))
    except (TypeError, ValueError):
        cap = leverage
    used = _set_leverage(creds, inst, leverage, mode, pos_side, simulated, cap)
    sz, _size = _contracts(margin, used, price, info)
    order_side = "buy" if side == "up" else "sell"
    body = {
        "instId": inst,
        "tdMode": mode,
        "side": order_side,
        "ordType": "market",
        "sz": sz,
        "attachAlgoOrds": [
            {
                "tpTriggerPx": _text_step(tp_px, tick if tick > 0 else 0.00000001),
                "tpOrdPx": "-1",
                "tpTriggerPxType": "last",
            }
        ],
    }
    if pos_side in ("long", "short"):
        body["posSide"] = pos_side
    payload = request(creds, "POST", "/api/v5/trade/order", body, simulated)
    algo_id = _algo_id(payload)
    if not algo_id:
        try:
            algo_id = _match_algo(_pending_algos(creds, inst, simulated), tp_px)
        except OkxError:
            algo_id = ""
    word = "long" if side == "up" else "short"
    return {
        "inst": inst,
        "side": side,
        "pos_side": pos_side,
        "sz": sz,
        "td_mode": mode,
        "tp": tp_px,
        "algo_id": algo_id,
        "message": f"{word} açıldı · {sz} sözleşme · TP {format_price(tp_px)}",
    }


def _algo_id(payload: dict) -> str:
    rows = payload.get("data") or []
    if not rows or not isinstance(rows[0], dict):
        return ""
    row = rows[0]
    found = str(row.get("algoId") or "")
    if found:
        return found
    attached = row.get("attachAlgoOrds") or []
    if attached and isinstance(attached[0], dict):
        return str(attached[0].get("algoId") or "")
    return ""


def _pending_algos(creds: Creds, inst: str, simulated: bool) -> list[dict]:
    found: list[dict] = []
    for kind in ("conditional", "oco"):
        payload = request(
            creds,
            "GET",
            f"/api/v5/trade/orders-algo-pending?ordType={kind}&instId={inst}",
            simulated=simulated,
        )
        for row in payload.get("data") or []:
            if isinstance(row, dict):
                found.append(row)
    return found


def _match_algo(rows: list[dict], tp_px: float) -> str:
    best = ""
    best_gap: float | None = None
    for row in rows:
        try:
            level = float(row.get("tpTriggerPx"))
        except (TypeError, ValueError):
            continue
        gap = abs(level - tp_px)
        if best_gap is None or gap < best_gap:
            best = str(row.get("algoId") or "")
            best_gap = gap
    return best


def _profit_side(side: str, tp: float, price: float) -> bool:
    if tp <= 0:
        return False
    if price <= 0:
        return True
    if side == "up":
        return tp > price
    if side == "down":
        return tp < price
    return False


def _amend_tp(creds: Creds, held: dict, tp: float, price: float, simulated: bool) -> dict:
    side = str(held.get("side") or "")
    kept = dict(held)
    if not _profit_side(side, tp, price):
        kept["message"] = "TP fiyatın yanlış tarafında, emir durdu"
        return kept
    inst = str(held.get("inst") or "")
    info = _public_instrument(inst)
    tick = float(info.get("tickSz") or 0)
    tp_px = _round_step(tp, tick)
    if not _profit_side(side, tp_px, price):
        kept["message"] = "TP fiyatın yanlış tarafında, emir durdu"
        return kept
    text = _text_step(tp_px, tick if tick > 0 else 0.00000001)
    algo_id = str(held.get("algo_id") or "")
    if not algo_id:
        algo_id = _match_algo(_pending_algos(creds, inst, simulated), float(held.get("tp") or tp_px))
    if algo_id:
        request(
            creds,
            "POST",
            "/api/v5/trade/amend-algos",
            {
                "instId": inst,
                "algoId": algo_id,
                "newTpTriggerPx": text,
                "newTpOrdPx": "-1",
            },
            simulated,
        )
    else:
        algo_id = _place_tp(creds, held, text, simulated)
    kept["tp"] = tp_px
    kept["algo_id"] = algo_id
    kept["message"] = f"TP {format_price(tp_px)}"
    return kept


def _place_tp(creds: Creds, held: dict, tp_text: str, simulated: bool) -> str:
    pos_side = str(held.get("pos_side") or "net")
    trade_side = str(held.get("side") or "")
    if pos_side == "long" or (pos_side == "net" and trade_side == "up"):
        side = "sell"
    else:
        side = "buy"
    body = {
        "instId": str(held.get("inst") or ""),
        "tdMode": str(held.get("td_mode") or "cross"),
        "side": side,
        "ordType": "conditional",
        "sz": str(held.get("sz") or ""),
        "tpTriggerPx": tp_text,
        "tpOrdPx": "-1",
        "tpTriggerPxType": "last",
    }
    if pos_side in ("long", "short"):
        body["posSide"] = pos_side
    else:
        body["reduceOnly"] = True
    payload = request(creds, "POST", "/api/v5/trade/order-algo", body, simulated)
    return _algo_id(payload)


def execute(creds: Creds, symbol: str, price: float, settings: dict, actions: list[dict]) -> list[dict]:
    """Kapat, sonra aç. Kapanış başarısızsa aynı aralığın yeni emri gitmez."""
    results: list[dict] = []
    blocked: set[str] = set()
    mode = str(settings.get("mgn_mode") or "cross")
    if mode not in ("cross", "isolated"):
        mode = "cross"
    simulated = bool(settings.get("simulated"))
    try:
        pct = float(settings.get("margin_pct") or 0)
        leverage = int(settings.get("leverage") or 0)
    except (TypeError, ValueError):
        pct, leverage = 0.0, 0
    for action in actions:
        label = str(action.get("label") or "")
        op = str(action.get("op") or "")
        if op == "close":
            held = action.get("held")
            if not isinstance(held, dict) or not held.get("sz"):
                results.append(_row(symbol, label, op, True, "açık emir yok", None))
                continue
            try:
                _close(creds, held, simulated)
                results.append(_row(symbol, label, op, True, "kapatıldı", None))
            except OkxError as exc:
                if _flat(str(exc)):
                    results.append(_row(symbol, label, op, True, "pozisyon zaten kapalı", None))
                else:
                    blocked.add(label)
                    results.append(_row(symbol, label, op, False, str(exc), None))
            continue
        if op == "amend":
            held = action.get("held")
            if not isinstance(held, dict) or not held.get("sz"):
                results.append(_row(symbol, label, op, True, "açık emir yok", None))
                continue
            try:
                raw_tp = action.get("tp")
                if not isinstance(raw_tp, (int, float)) or float(raw_tp) <= 0:
                    raise OkxError("Hedef yok, emir yok")
                spot = float(action.get("price") or price)
                updated = _amend_tp(creds, held, float(raw_tp), spot, simulated)
            except (OkxError, TypeError, ValueError) as exc:
                text = str(exc) if str(exc) else "TP güncellenemedi"
                results.append(_row(symbol, label, op, False, text, None))
                continue
            position = {
                key: updated[key]
                for key in ("inst", "side", "pos_side", "sz", "td_mode", "tp", "algo_id")
                if key in updated
            }
            results.append(_row(symbol, label, op, True, str(updated.get("message") or "TP güncellendi"), position))
            continue
        if op != "open":
            continue
        if label in blocked:
            results.append(_row(symbol, label, op, False, "önceki pozisyon kapanmadı", None))
            continue
        try:
            side = str(action.get("side") or "")
            raw_tp = action.get("tp")
            if not isinstance(raw_tp, (int, float)) or float(raw_tp) <= 0:
                raise OkxError("Hedef yok, emir yok")
            spot = float(action.get("price") or price)
            if pct <= 0:
                raise OkxError("Marjin yüzdesi seçilmedi")
            margin = available_usdt(creds, simulated) * min(pct, 100.0) / 100
            if margin <= 0:
                raise OkxError("Kullanılabilir bakiye yok")
            opened = _open(creds, symbol, side, float(raw_tp), spot, margin, leverage, mode, simulated)
        except (OkxError, TypeError, ValueError) as exc:
            text = str(exc) if str(exc) else "emir gitmedi"
            results.append(_row(symbol, label, op, False, text, None))
            continue
        position = {key: opened[key] for key in ("inst", "side", "pos_side", "sz", "td_mode", "tp", "algo_id")}
        results.append(_row(symbol, label, op, True, str(opened["message"]), position))
    return results


_INSTRUMENTS: dict[str, dict] = {}


def _instrument(inst: str) -> dict:
    cached = _INSTRUMENTS.get(inst)
    if cached is None:
        cached = _INSTRUMENTS[inst] = _public_instrument(inst)
    return cached


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close_side(position: dict) -> str:
    return "sell" if position.get("side") == "up" else "buy"


def _owns(position: dict, row: dict) -> bool:
    """Bekleyen algo emri bu pozisyonu mu kapatır."""
    if str(row.get("instId") or "") != position.get("inst"):
        return False
    pos_side = str(position.get("pos_side") or "net")
    row_side = str(row.get("posSide") or "net")
    if pos_side in ("long", "short"):
        return row_side == pos_side
    return row_side == "net" and str(row.get("side") or "") == _close_side(position)


def _pending_all(creds: Creds, kind: str, simulated: bool) -> list[dict]:
    payload = request(
        creds,
        "GET",
        f"/api/v5/trade/orders-algo-pending?ordType={kind}&instType=SWAP",
        simulated=simulated,
    )
    return [row for row in payload.get("data") or [] if isinstance(row, dict)]


def fetch_positions(creds: Creds, simulated: bool) -> list[dict]:
    """Açık SWAP pozisyonları; TP, SL ve trailing bekleyen emirlerle birlikte."""
    if not creds.ready():
        raise OkxError("API anahtarı, secret ve parola gerekli")
    payload = request(creds, "GET", "/api/v5/account/positions?instType=SWAP", simulated=simulated)
    positions: list[dict] = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict):
            continue
        amount = _number(row.get("pos")) or 0.0
        if amount == 0:
            continue
        pos_side = str(row.get("posSide") or "net")
        if pos_side == "long":
            side = "up"
        elif pos_side == "short":
            side = "down"
        else:
            side = "up" if amount > 0 else "down"
        inst = str(row.get("instId") or "")
        margin = _number(row.get("margin")) or _number(row.get("imr")) or 0.0
        positions.append(
            {
                "inst": inst,
                "symbol": inst.split("-")[0] + "USDT" if inst else "",
                "pos_side": pos_side,
                "side": side,
                "sz": str(row.get("pos") or "").strip().lstrip("-"),
                "size": abs(amount),
                "avg": _number(row.get("avgPx")),
                "mark": _number(row.get("markPx")),
                "upl": _number(row.get("upl")),
                "upl_ratio": _number(row.get("uplRatio")),
                "liq": _number(row.get("liqPx")),
                "lever": _number(row.get("lever")),
                "margin": margin,
                "td_mode": str(row.get("mgnMode") or "cross"),
                "tp": None,
                "sl": None,
                "tp_sl_ids": [],
                "trail": None,
            }
        )
    if not positions:
        return positions
    for kind in ("conditional", "oco"):
        for row in _pending_all(creds, kind, simulated):
            for position in positions:
                if not _owns(position, row):
                    continue
                position["tp_sl_ids"].append(str(row.get("algoId") or ""))
                tp = _number(row.get("tpTriggerPx"))
                sl = _number(row.get("slTriggerPx"))
                if tp and position["tp"] is None:
                    position["tp"] = tp
                if sl and position["sl"] is None:
                    position["sl"] = sl
                break
    for row in _pending_all(creds, "move_order_stop", simulated):
        for position in positions:
            if _owns(position, row) and position["trail"] is None:
                position["trail"] = {
                    "algo_id": str(row.get("algoId") or ""),
                    "ratio": _number(row.get("callbackRatio")),
                    "spread": _number(row.get("callbackSpread")),
                    "active": _number(row.get("activePx")),
                }
                break
    return positions


def _cancel(creds: Creds, inst: str, algo_ids: list[str], simulated: bool) -> None:
    rows = [{"algoId": algo_id, "instId": inst} for algo_id in algo_ids if algo_id]
    if rows:
        request(creds, "POST", "/api/v5/trade/cancel-algos", rows, simulated)


def _close_body(position: dict, ord_type: str) -> dict:
    body = {
        "instId": str(position.get("inst") or ""),
        "tdMode": str(position.get("td_mode") or "cross"),
        "side": _close_side(position),
        "ordType": ord_type,
    }
    pos_side = str(position.get("pos_side") or "net")
    if pos_side in ("long", "short"):
        body["posSide"] = pos_side
    else:
        body["reduceOnly"] = True
    return body


def _tick_text(value: float, info: dict) -> tuple[float, str]:
    tick = float(info.get("tickSz") or 0)
    rounded = _round_step(value, tick)
    return rounded, _text_step(rounded, tick if tick > 0 else 0.00000001)


def set_tp_sl(creds: Creds, position: dict, tp: float | None, sl: float | None, simulated: bool) -> dict:
    """Pozisyonun tamamı için TP/SL. Eskiler iptal edilir; ikisi de boşsa yalnız kaldırılır."""
    inst = str(position.get("inst") or "")
    side = position.get("side")
    if not inst or side not in ("up", "down"):
        raise OkxError("Pozisyon okunamadı")
    mark = _number(position.get("mark")) or _number(position.get("avg")) or 0.0
    tp = tp if tp and tp > 0 else None
    sl = sl if sl and sl > 0 else None
    info = _instrument(inst) if (tp or sl) else {}
    tp_text = sl_text = None
    if tp is not None:
        tp, tp_text = _tick_text(tp, info)
        if mark > 0 and ((side == "up" and tp <= mark) or (side == "down" and tp >= mark)):
            raise OkxError("TP fiyatın yanlış tarafında")
    if sl is not None:
        sl, sl_text = _tick_text(sl, info)
        if mark > 0 and ((side == "up" and sl >= mark) or (side == "down" and sl <= mark)):
            raise OkxError("SL fiyatın yanlış tarafında")
    _cancel(creds, inst, list(position.get("tp_sl_ids") or []), simulated)
    if tp is None and sl is None:
        return {"algo_id": "", "tp": None, "sl": None, "message": "TP/SL kaldırıldı"}
    body = _close_body(position, "oco" if tp is not None and sl is not None else "conditional")
    body["closeFraction"] = "1"
    parts = []
    if tp_text is not None:
        body.update({"tpTriggerPx": tp_text, "tpOrdPx": "-1", "tpTriggerPxType": "last"})
        parts.append(f"TP {format_price(tp)}")
    if sl_text is not None:
        body.update({"slTriggerPx": sl_text, "slOrdPx": "-1", "slTriggerPxType": "last"})
        parts.append(f"SL {format_price(sl)}")
    payload = request(creds, "POST", "/api/v5/trade/order-algo", body, simulated)
    return {"algo_id": _algo_id(payload), "tp": tp, "sl": sl, "message": " · ".join(parts)}


def set_trailing(
    creds: Creds,
    position: dict,
    callback: float,
    kind: str,
    active_px: float | None,
    simulated: bool,
) -> dict:
    """Trailing stop. kind 'ratio' yüzde (1 = %1), 'spread' USDT. callback 0 ise yalnız kaldırılır."""
    inst = str(position.get("inst") or "")
    side = position.get("side")
    if not inst or side not in ("up", "down"):
        raise OkxError("Pozisyon okunamadı")
    trail = position.get("trail") if isinstance(position.get("trail"), dict) else None
    if callback <= 0:
        if trail:
            _cancel(creds, inst, [str(trail.get("algo_id") or "")], simulated)
        return {"algo_id": "", "message": "Trailing kaldırıldı"}
    if kind == "ratio" and not 0.1 <= callback <= 100:
        raise OkxError("Geri çekilme %0,1 ile %100 arasında olmalı")
    sz = str(position.get("sz") or "")
    if not sz:
        raise OkxError("Pozisyon boyutu okunamadı")
    info = _instrument(inst)
    body = _close_body(position, "move_order_stop")
    body["sz"] = sz
    if kind == "ratio":
        body["callbackRatio"] = f"{callback / 100:.6f}".rstrip("0").rstrip(".")
        text = f"%{callback:g}"
    else:
        _rounded, body["callbackSpread"] = _tick_text(callback, info)
        text = f"{format_price(callback)} USDT"
    if active_px and active_px > 0:
        active, body["activePx"] = _tick_text(active_px, info)
        text += f" · aktivasyon {format_price(active)}"
    if trail:
        _cancel(creds, inst, [str(trail.get("algo_id") or "")], simulated)
    payload = request(creds, "POST", "/api/v5/trade/order-algo", body, simulated)
    return {"algo_id": _algo_id(payload), "message": f"Trailing {text}"}


def _row(symbol: str, label: str, op: str, ok: bool, message: str, position: dict | None) -> dict:
    return {
        "symbol": symbol,
        "label": label,
        "op": op,
        "ok": ok,
        "message": message,
        "position": position,
    }
