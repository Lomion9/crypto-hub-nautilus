"""Kayıtlı küme listesini okur. Canlı haritaya, sinyale, emre ve LEVERAGE tablosuna yazmaz.

Başarı kriteri, sonuç görülmeden sabittir. Değişirse nedeni KRITER_DEGISIM satırına yazılır.
Konum, yüzde uzaklıktır: ufuktaki iz yoğunluğunun tepesinden aynı taraftaki en yakın kümeye.
İyi, daha küçük uzaklıktır. Aralık, giriş günlerinin 1000 kez yeniden çekilmiş yüzde 95'idir.
Harita ortalaması bu aralığın alt sınırının altındaysa dışında ve iyidir.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from desktop.liquidity import build_map, nice_step
from desktop.market import HOUR_MS, Bar, Point

KRITER = (
    "Harita konum metriğinde aynı-uzaklık rastgele tabanın %95 aralığının dışında iyi ise "
    "haritaya güven artar, değilse artmaz."
)
KRITER_DEGISIM = ""

GUN_ESIK = 30
BOOTSTRAP = 1000
BOOTSTRAP_TOHUM = 7
MARGIN = 0.004
TAVAN = 100
SAAT_GECIKME_MS = 5_000
YUKSEK = {5: 1, 10: 1, 20: 1, 25: 2, 50: 4, 100: 4}
BANTLAR = ("<-3", "-3..-2", "-2..-1", "-1..0", "0..1", "1..2", "2..3", ">3")


def _bant(uzaklik: float) -> str:
    if uzaklik < -3:
        return "<-3"
    if uzaklik < -2:
        return "-3..-2"
    if uzaklik < -1:
        return "-2..-1"
    if uzaklik < 0:
        return "-1..0"
    if uzaklik <= 1:
        return "0..1"
    if uzaklik <= 2:
        return "1..2"
    if uzaklik <= 3:
        return "2..3"
    return ">3"


def _gun(stamp: int) -> str:
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).strftime("%Y-%m-%d")


def _saat_yazi(stamp: int) -> str:
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).strftime("%Y-%m-%d %H:00 UTC")


def _tape():
    path = Path.home() / "AppData" / "Roaming" / "CryptoHub" / "liq_tape.sqlite"
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    ready = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cluster_snapshot'"
    ).fetchone()
    rows = []
    if ready is not None:
        rows = conn.execute(
            "SELECT t_ms, pencere, kaynak, fiyat, atr, kumeler FROM cluster_snapshot ORDER BY t_ms, pencere, kaynak"
        ).fetchall()
    sessions = conn.execute("SELECT acildi_ms, kapandi_ms FROM oturum").fetchall()
    bars = []
    for stamp, opened, high, low, close, volume, taker in conn.execute(
        "SELECT saat_ms, o, h, l, c, hacim, taker_alis FROM mum_saat WHERE sembol='BTCUSDT' ORDER BY saat_ms"
    ):
        if min(opened, high, low, close) <= 0:
            continue
        bars.append(Bar(int(stamp), opened, high, low, close, 2 * taker - volume, volume))
    points = [
        Point(int(stamp), float(value))
        for stamp, value in conn.execute(
            "SELECT saat_ms, oi FROM oi_saat WHERE sembol='BTCUSDT' AND oi > 0 ORDER BY saat_ms"
        )
    ]
    prints = [
        (int(stamp), str(side), float(price))
        for stamp, side, price in conn.execute(
            "SELECT t_ms, yon, fiyat FROM liq WHERE sembol='BTCUSDT' AND fiyat > 0 ORDER BY t_ms"
        )
    ]
    conn.close()
    return rows, sessions, bars, points, prints


def _acik(hour: int, sessions) -> bool:
    end = hour + HOUR_MS
    for opened, closed in sessions:
        if not isinstance(opened, int):
            continue
        if opened < end and (closed is None or closed > hour):
            return True
    return False


def _bosluk(rows, sessions, now_ms: int) -> list[tuple[int, str]]:
    hours = sorted({int(row[0]) for row in rows if row[2] == "saat"})
    if not hours:
        return []
    last = now_ms // HOUR_MS * HOUR_MS
    if now_ms % HOUR_MS < SAAT_GECIKME_MS:
        last -= HOUR_MS
    if last < hours[0]:
        return []
    found = set(hours)
    missing = []
    stamp = hours[0]
    while stamp <= last:
        if stamp not in found:
            missing.append((stamp, "kapali" if not _acik(stamp, sessions) else "acik-kayit-yok"))
        stamp += HOUR_MS
    return missing


def _kumeler(raw: str) -> list[dict]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    chosen = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        price = item.get("fiyat")
        size = item.get("boyut")
        side = item.get("yon")
        if side not in ("long", "short"):
            continue
        if not isinstance(price, (int, float)) or not isinstance(size, (int, float)):
            continue
        if float(price) <= 0 or float(size) <= 0:
            continue
        chosen.append({"fiyat": float(price), "boyut": float(size), "yon": side})
    return chosen


def _girdi(stamp: int, horizon: int, clusters: list[dict], bars: list[Bar], now_ms: int) -> list[int]:
    end = stamp + horizon * HOUR_MS
    if now_ms < end:
        return []
    touches = []
    for cluster in clusters:
        price = cluster["fiyat"]
        for bar in bars:
            if bar.time_ms <= stamp:
                continue
            if bar.time_ms > end:
                break
            if bar.low <= price <= bar.high:
                touches.append(bar.time_ms)
                break
    return touches


def _olaylar(rows, bars, now_ms: int) -> tuple[int, int, set[str], list[dict]]:
    days: set[str] = set()
    counts = {12: 0, 24: 0}
    used = []
    for stamp, horizon, kaynak, price, atr, raw in rows:
        if horizon not in counts or kaynak not in ("saat", "sinyal"):
            continue
        clusters = _kumeler(raw)
        touches = _girdi(int(stamp), int(horizon), clusters, bars, now_ms)
        if not touches:
            continue
        counts[int(horizon)] += len(touches)
        for touch in touches:
            days.add(_gun(touch))
        used.append(
            {
                "t_ms": int(stamp),
                "pencere": int(horizon),
                "kaynak": kaynak,
                "fiyat": float(price),
                "atr": float(atr) if isinstance(atr, (int, float)) and float(atr) > 0 else None,
                "kumeler": clusters,
                "gunler": {_gun(touch) for touch in touches},
            }
        )
    return counts[12] + counts[24], counts[12], days, used


def _tepe(prices: list[float], step: float) -> float | None:
    if not prices or step <= 0:
        return None
    bins: dict[int, int] = {}
    for price in prices:
        index = int(price / step)
        bins[index] = bins.get(index, 0) + 1
    best = max(bins, key=bins.get)
    return (best + 0.5) * step


def _konum(clusters: list[dict], prints: list[float], step: float, spot: float) -> float | None:
    peak = _tepe(prints, step)
    if peak is None or not clusters or spot <= 0:
        return None
    distance = min(abs(cluster["fiyat"] - peak) for cluster in clusters)
    return distance / spot * 100


def _havuz(rows) -> dict[str, list[tuple[tuple, float]]]:
    pool: dict[str, list[tuple[tuple, float]]] = {"long": [], "short": []}
    seen = set()
    for stamp, _horizon, kaynak, price, _atr, raw in rows:
        key = (int(stamp), kaynak)
        if key in seen or not isinstance(price, (int, float)) or float(price) <= 0:
            continue
        seen.add(key)
        spot = float(price)
        for cluster in _kumeler(raw):
            pool[cluster["yon"]].append((key, abs(cluster["fiyat"] - spot) / spot))
    return pool


def _rastgele(capture: dict, pool, step: float, forward: dict[str, list[float]]) -> float | None:
    key = (capture["t_ms"], capture["kaynak"])
    rng = random.Random(f"{capture['t_ms']}:{capture['pencere']}:{capture['kaynak']}")
    sides = []
    for side in ("long", "short"):
        own = [cluster for cluster in capture["kumeler"] if cluster["yon"] == side]
        if not own:
            continue
        distances = [distance for owner, distance in pool[side] if owner != key]
        if not distances:
            continue
        drawn = []
        for _ in own:
            distance = distances[rng.randrange(len(distances))]
            price = capture["fiyat"] * (1 - distance) if side == "long" else capture["fiyat"] * (1 + distance)
            if price > 0:
                drawn.append({"fiyat": price, "boyut": 1.0, "yon": side})
        found = _konum(drawn, forward[side], step, capture["fiyat"])
        if found is not None:
            sides.append(found)
    if not sides:
        return None
    return sum(sides) / len(sides)


def _harita_konum(capture: dict, forward: dict[str, list[float]], step: float) -> float | None:
    sides = []
    for side in ("long", "short"):
        own = [cluster for cluster in capture["kumeler"] if cluster["yon"] == side]
        found = _konum(own, forward[side], step, capture["fiyat"])
        if found is not None:
            sides.append(found)
    if not sides:
        return None
    return sum(sides) / len(sides)


def _ileri(capture: dict, prints) -> dict[str, list[float]]:
    end = capture["t_ms"] + capture["pencere"] * HOUR_MS
    return {
        side: [
            price
            for stamp, yon, price in prints
            if capture["t_ms"] < stamp <= end and yon == side
        ]
        for side in ("long", "short")
    }


def _gun_ortalamalari(pairs: dict[str, list[tuple[float, float]]]) -> tuple[list[float], list[float]]:
    order = sorted(pairs)
    harita = []
    taban = []
    for day in order:
        sample = pairs[day]
        harita.append(sum(item[0] for item in sample) / len(sample))
        taban.append(sum(item[1] for item in sample) / len(sample))
    return harita, taban


def _aralik(taban: list[float]) -> tuple[float, float] | None:
    count = len(taban)
    if count == 0:
        return None
    rng = random.Random(BOOTSTRAP_TOHUM)
    means = []
    for _ in range(BOOTSTRAP):
        draw = [taban[rng.randrange(count)] for _ in range(count)]
        means.append(sum(draw) / count)
    means.sort()
    return means[int(0.025 * (BOOTSTRAP - 1))], means[int(0.975 * (BOOTSTRAP - 1))]


def _karar(harita: float, alt: float) -> str:
    if harita < alt:
        return "güven artar"
    return "güven artmaz"


def _kutup(captures: list[dict], birim: str) -> dict[str, dict[str, float]]:
    sides = {name: {"long": 0.0, "short": 0.0} for name in ("mevcut", "yuksek")}
    split = {name: {side: {band: 0.0 for band in BANTLAR} for side in ("long", "short")} for name in ("mevcut", "yuksek")}
    for capture in captures:
        spot = capture["fiyat"]
        for name in ("mevcut", "yuksek"):
            clusters = capture["kumeler"] if name == "mevcut" else capture.get("yuksek") or []
            for cluster in clusters:
                if birim == "atr":
                    if capture["atr"] is None:
                        continue
                    distance = (cluster["fiyat"] - spot) / capture["atr"]
                else:
                    distance = (cluster["fiyat"] - spot) / spot * 100
                band = _bant(distance)
                sides[name][cluster["yon"]] += cluster["boyut"]
                split[name][cluster["yon"]][band] += cluster["boyut"]
    return {"taraf": sides, "bol": split}


def _yaz_kutup(title: str, packed: dict) -> None:
    print(title)
    print(f"  {'bant':<8} {'mevcut long':>12} {'yuksek long':>12} {'mevcut short':>13} {'yuksek short':>13}")
    for band in BANTLAR:
        cells = []
        for side in ("long", "short"):
            for name in ("mevcut", "yuksek"):
                whole = packed["taraf"][name][side]
                share = 0.0 if whole <= 0 else packed["bol"][name][side][band] / whole * 100
                cells.append(f"{share:12.1f}" if side == "long" else f"{share:13.1f}")
        print(f"  {band:<8} {''.join(cells)}")


def _yuksek_harita(bars, points, capture: dict) -> list[dict]:
    book = build_map(
        "BTCUSDT",
        bars,
        points,
        MARGIN,
        TAVAN,
        as_of=capture["t_ms"],
        weights=YUKSEK,
    )
    clusters = []
    for side, peaks in (("long", book.long_peaks), ("short", book.short_peaks)):
        for cluster in peaks:
            if cluster.price > 0 and cluster.usd > 0:
                clusters.append({"fiyat": cluster.price, "boyut": cluster.usd, "yon": side})
    return clusters


def _yaz_bosluk(rows, missing) -> None:
    hours = {int(row[0]) for row in rows if row[2] == "saat"}
    if not hours:
        print("eksik saat ölçülmedi (saat kaydı yok)")
        return
    closed = sum(1 for _stamp, mark in missing if mark == "kapali")
    quiet = len(missing) - closed
    print(f"eksik saat {len(missing)} (kapali {closed}, acik-kayit-yok {quiet})")
    for stamp, mark in missing:
        print(f"  {_saat_yazi(stamp)} {mark}")


def main() -> None:
    print(KRITER)
    if KRITER_DEGISIM:
        print(KRITER_DEGISIM)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows, sessions, bars, points, prints = _tape()
    missing = _bosluk(rows, sessions, now_ms)
    total, olay_12, days, used = _olaylar(rows, bars, now_ms)
    print(f"gün {len(days)}")
    print(f"olay {total} (12s {olay_12}, 24s {total - olay_12})")
    _yaz_bosluk(rows, missing)
    if len(days) < GUN_ESIK:
        print("veri yetersiz")
        return
    pool = _havuz(rows)
    pairs: dict[str, list[tuple[float, float]]] = {}
    for capture in used:
        step = nice_step(capture["fiyat"])
        forward = _ileri(capture, prints)
        harita = _harita_konum(capture, forward, step)
        taban = _rastgele(capture, pool, step, forward)
        if harita is None or taban is None:
            continue
        for day in capture["gunler"]:
            pairs.setdefault(day, []).append((harita, taban))
    harita_gun, taban_gun = _gun_ortalamalari(pairs)
    span = _aralik(taban_gun)
    if not harita_gun or span is None:
        print("güven artmaz")
        return
    ortalama = sum(harita_gun) / len(harita_gun)
    alt, ust = span
    print(f"konum gün {len(harita_gun)} harita {ortalama:.3f} taban alt {alt:.3f} üst {ust:.3f}")
    seen = set()
    unique = []
    for capture in used:
        key = (capture["t_ms"], capture["kaynak"])
        if key in seen:
            continue
        seen.add(key)
        try:
            capture["yuksek"] = _yuksek_harita(bars, points, capture)
        except (TypeError, ValueError, ZeroDivisionError):
            capture["yuksek"] = []
        unique.append(capture)
    _yaz_kutup("kütle, ATR", _kutup(unique, "atr"))
    _yaz_kutup("kütle, yüzde", _kutup(unique, "yuzde"))
    print(_karar(ortalama, alt))


if __name__ == "__main__":
    main()
