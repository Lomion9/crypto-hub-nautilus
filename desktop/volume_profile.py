"""Seans hacim profili. POC, değer alanı ve düğümler toplam hacimden çıkar.

Mumun yükselişi alış yönü değildir. Çubuğun iki rengi yalnız mum yönüdür.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import tzdata

from desktop.market import FAPI, Bar, MarketError, _bars, _get

TOKYO = ZoneInfo("Asia/Tokyo")
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")

ROWS = 64
VALUE_SHARE = 0.70
PEAK_SHARE = 0.09
TROUGH_SHARE = 0.07
NODE_FLOOR = 0.01
FIVE_MS = 5 * 60 * 1000
FIFTEEN_MS = 15 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
VOLUME_RANGES = (
    ("londra", "Londra"),
    ("newyork", "New York"),
    ("asya", "Asya"),
    ("gunluk", "Günlük"),
    ("3gun", "3 günlük"),
    ("hafta", "Haftalık"),
    ("ay", "Aylık"),
)
_SESSION_NAME = {"londra": "Londra", "newyork": "New York", "asya": "Asya"}
_BAR = {
    "londra": ("5m", FIVE_MS),
    "newyork": ("5m", FIVE_MS),
    "asya": ("5m", FIVE_MS),
    "gunluk": ("5m", FIVE_MS),
    "3gun": ("15m", FIFTEEN_MS),
    "hafta": ("15m", FIFTEEN_MS),
    "ay": ("1h", HOUR_MS),
}
_ROLLING_LABEL = {"gunluk": "Günlük", "3gun": "3 günlük", "hafta": "Haftalık", "ay": "Aylık"}


@dataclass(frozen=True)
class VolumeRow:
    low: float
    high: float
    total: float
    up: float
    kind: str


@dataclass(frozen=True)
class VolumeProfile:
    name: str
    start_ms: int
    end_ms: int
    rows: tuple[VolumeRow, ...]
    poc: float | None
    vah: float | None
    val: float | None
    developing: bool
    note: str


def _utc_day(moment: datetime) -> datetime:
    clock = moment.astimezone(timezone.utc)
    return clock.replace(hour=0, minute=0, second=0, microsecond=0)


def _inside(zone: ZoneInfo, day: datetime, hour: int, minute: int) -> datetime | None:
    """UTC gününün içine düşen ilk yerel saat."""
    end = day + timedelta(days=1)
    base = day.astimezone(zone).date()
    for extra in (0, 1):
        date = base + timedelta(days=extra)
        instant = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
        found = instant.astimezone(timezone.utc)
        if day <= found < end:
            return found
    return None


def session_bounds(moment: datetime | None = None) -> list[tuple[str, int, int]]:
    """Asya, Londra ve New York. Londra ile New York yaz ve kış saatine göre kayar."""
    day = _utc_day(datetime.now(timezone.utc) if moment is None else moment)
    end = day + timedelta(days=1)
    london = _inside(LONDON, day, 8, 0)
    york = _inside(NEW_YORK, day, 9, 30)
    if london is None or york is None:
        raise MarketError("Seans saati kurulamadı")
    asia_end = min(london, york, end)
    london_end = min(max(york, london), end)
    cuts = (
        ("Asya", day, asia_end),
        ("Londra", asia_end, london_end),
        ("New York", london_end, end),
    )
    return [
        (name, int(start.timestamp() * 1000), int(stop.timestamp() * 1000))
        for name, start, stop in cuts
        if stop > start
    ]


def _share(bar: Bar, bin_low: float, bin_high: float) -> float:
    span = bar.high - bar.low
    if span <= 0:
        return 1.0 if bin_low <= bar.close < bin_high or (bar.close == bin_high and bar.close == bar.high) else 0.0
    overlap = min(bar.high, bin_high) - max(bar.low, bin_low)
    if overlap <= 0:
        return 0.0
    return overlap / span


def build_profile(
    name: str,
    bars: list[Bar],
    start_ms: int,
    end_ms: int,
    now_ms: int,
) -> VolumeProfile:
    developing = start_ms <= now_ms < end_ms
    if now_ms < start_ms:
        return VolumeProfile(name, start_ms, end_ms, (), None, None, None, False, "henüz başlamadı")
    chosen = [
        bar for bar in bars
        if start_ms <= bar.time_ms < end_ms and bar.high > 0 and bar.low > 0 and bar.volume > 0
    ]
    if not chosen:
        return VolumeProfile(name, start_ms, end_ms, (), None, None, None, developing, "veri yok")
    low = min(bar.low for bar in chosen)
    high = max(bar.high for bar in chosen)
    if high <= low:
        high = low + max(abs(low) * 1e-6, 1e-8)
    step = (high - low) / ROWS
    total = [0.0] * ROWS
    up = [0.0] * ROWS
    for bar in chosen:
        rising = bar.close > bar.open
        start_index = min(ROWS - 1, max(0, int((bar.low - low) / step)))
        end_index = min(ROWS - 1, max(0, int((min(bar.high, high) - low) / step)))
        if bar.high >= high:
            end_index = ROWS - 1
        for index in range(start_index, end_index + 1):
            bin_low = low + index * step
            portion = bar.volume * _share(bar, bin_low, bin_low + step)
            if portion <= 0:
                continue
            total[index] += portion
            if rising:
                up[index] += portion
    peak_n = int(ROWS * PEAK_SHARE)
    trough_n = int(ROWS * TROUGH_SHARE)
    largest = max(total)
    floor = largest * NODE_FLOOR
    kind = [""] * ROWS
    if largest > 0 and peak_n > 0:
        for index in range(peak_n, ROWS - peak_n):
            if total[index] <= floor:
                continue
            if all(total[index] > total[other] for other in range(index - peak_n, index)) and all(
                total[index] > total[other] for other in range(index + 1, index + 1 + peak_n)
            ):
                kind[index] = "HVN"
    if largest > 0 and trough_n > 0:
        for index in range(trough_n, ROWS - trough_n):
            if total[index] <= floor or kind[index]:
                continue
            if all(total[index] < total[other] for other in range(index - trough_n, index)) and all(
                total[index] < total[other] for other in range(index + 1, index + 1 + trough_n)
            ):
                kind[index] = "LVN"
    poc_index = max(range(ROWS), key=lambda index: total[index])
    target = sum(total) * VALUE_SHARE
    lower = upper = poc_index
    held = total[poc_index]
    while held < target and (lower > 0 or upper < ROWS - 1):
        above = total[upper + 1] if upper < ROWS - 1 else -1.0
        below = total[lower - 1] if lower > 0 else -1.0
        if above < 0 and below < 0:
            break
        up_distance = abs((upper + 1) - poc_index) if above >= 0 else 10**9
        down_distance = abs((lower - 1) - poc_index) if below >= 0 else 10**9
        take_up = above > below or (above == below and up_distance <= down_distance)
        if take_up and above >= 0:
            upper += 1
            held += total[upper]
        elif below >= 0:
            lower -= 1
            held += total[lower]
        else:
            break
    rows = tuple(
        VolumeRow(low + index * step, low + (index + 1) * step, total[index], min(up[index], total[index]), kind[index])
        for index in range(ROWS)
    )
    return VolumeProfile(
        name,
        start_ms,
        end_ms,
        rows,
        low + (poc_index + 0.5) * step,
        low + (upper + 1) * step,
        low + lower * step,
        developing,
        "",
    )


def _day_bars(symbol: str, start_ms: int, end_ms: int, interval: str, step_ms: int) -> list[Bar]:
    collected: list[Bar] = []
    cursor = start_ms
    stop = max(start_ms, end_ms - 1)
    while cursor <= stop and len(collected) < 2000:
        raw = _get(
            f"{FAPI}/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": stop, "limit": 1500},
        )
        page = _bars(raw)
        if not page:
            break
        collected.extend(bar for bar in page if start_ms <= bar.time_ms < end_ms)
        nxt = page[-1].time_ms + step_ms
        if nxt <= cursor or len(page) < 1500:
            break
        cursor = nxt
    return collected


def _month_ago(moment: datetime) -> datetime:
    month = moment.month - 1
    year = moment.year
    if month == 0:
        month = 12
        year -= 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def profile_bounds(key: str, moment: datetime | None = None) -> tuple[str, int, int]:
    """Seçilen pencere. Seans kendi saati, diğerleri şu andan geriye."""
    now = datetime.now(timezone.utc) if moment is None else moment.astimezone(timezone.utc)
    if key in _SESSION_NAME:
        wanted = _SESSION_NAME[key]
        for name, start, end in session_window(now):
            if name == wanted:
                return name, start, end
        raise MarketError("Seans saati kurulamadı")
    if key not in _ROLLING_LABEL:
        raise MarketError("Hacim aralığı yok")
    if key == "gunluk":
        start = now - timedelta(hours=24)
    elif key == "3gun":
        start = now - timedelta(days=3)
    elif key == "hafta":
        start = now - timedelta(days=7)
    else:
        start = _month_ago(now)
    return _ROLLING_LABEL[key], int(start.timestamp() * 1000), int(now.timestamp() * 1000) + 1


def session_window(moment: datetime | None = None) -> list[tuple[str, int, int]]:
    """Asya, Londra, New York. Bugünkü açılmadıysa dünün aynı seansı kalır."""
    now = datetime.now(timezone.utc) if moment is None else moment.astimezone(timezone.utc)
    today = session_bounds(now)
    previous = {name: (name, start, end) for name, start, end in session_bounds(now - timedelta(days=1))}
    now_ms = int(now.timestamp() * 1000)
    chosen: list[tuple[str, int, int]] = []
    for name, start, end in today:
        if now_ms >= start:
            chosen.append((name, start, end))
        elif name in previous:
            chosen.append(previous[name])
    return chosen


def load_profile(symbol: str, key: str, moment: datetime | None = None) -> VolumeProfile:
    now = datetime.now(timezone.utc) if moment is None else moment.astimezone(timezone.utc)
    name, start_ms, end_ms = profile_bounds(key, now)
    interval, step = _BAR[key]
    now_ms = int(now.timestamp() * 1000)
    bars = _day_bars(symbol, start_ms, min(end_ms, now_ms + step), interval, step)
    return build_profile(name, bars, start_ms, end_ms, now_ms)


_SIGNAL_MAP = {"4sa": "3gun", "24sa": "hafta", "1hafta": "ay"}
_PREVIOUS_SESSION = {"Asya": "newyork", "Londra": "asya", "New York": "londra"}


def _session_at(moment: datetime) -> str:
    stamp = int(moment.timestamp() * 1000)
    for name, start, end in session_bounds(moment):
        if start <= stamp < end:
            return name
    return ""


def signal_map_key(label: str, moment: datetime) -> str | None:
    """1 saat bir önceki seansa, diğer pencereler daha geniş haritaya bakar."""
    if label == "1sa":
        return _PREVIOUS_SESSION.get(_session_at(moment))
    return _SIGNAL_MAP.get(label)


def _outside_value(profile: VolumeProfile, price: float) -> bool:
    if profile.val is None or profile.vah is None:
        return True
    return price < profile.val or price > profile.vah


def signal_stop_level(
    symbol: str,
    label: str,
    close_ms: int,
    price: float | None,
    side: str | None,
) -> tuple[bool, float | None]:
    """Harita bulunduysa doğru, stop fiyatı. Long VAL, short VAH. Emri değiştirmez."""
    if not isinstance(price, (int, float)) or float(price) <= 0 or close_ms <= 0:
        return False, None
    spot = float(price)
    moment = datetime.fromtimestamp(close_ms / 1000, tz=timezone.utc)
    probe = moment - timedelta(milliseconds=1) if label == "1sa" else moment
    key = signal_map_key(label, probe)
    if not key:
        return False, None
    try:
        profile = load_profile(symbol, key, probe)
        if label == "1sa" and _outside_value(profile, spot):
            profile = load_profile(symbol, "gunluk", moment)
    except Exception:
        return False, None
    if side == "up":
        return True, profile.val
    if side == "down":
        return True, profile.vah
    return True, None
