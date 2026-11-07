"""Piyasa okuması. sinyal.genel_durum ile aynı kurallar, veritabanı yok."""

from __future__ import annotations

from dataclasses import dataclass

# 0.0100 hâlâ pozitif sayılır; aşırı ancak bunun üstüdür. 15dk oi gürültüsü ayrı.
EXTREME_FUNDING = 0.01
OI_NOISE_15M = 0.31

PERIODS: tuple[tuple[str, float | None], ...] = (
    ("15dk", OI_NOISE_15M),
    ("1sa", None),
    ("2sa", None),
    ("4sa", None),
    ("8sa", None),
    ("24sa", None),
    ("1hafta", None),
)

LONGS = {"Sağlıklı Long", "Short Squeeze", "Akümülasyon"}
SHORTS = {"Sağlıklı Short", "Long Squeeze", "Dağıtım"}


@dataclass(frozen=True)
class Read:
    label: str
    price_state: str
    oi_state: str
    cvd_text: str
    result: str
    trap: str | None
    summary: str


def funding_label(percent: float | None) -> str:
    if percent is None:
        return "Veri Yok"
    value = round(float(percent), 4)
    if value > EXTREME_FUNDING:
        return "Aşırı Pozitif"
    if value > 0:
        return "Pozitif"
    if value < -EXTREME_FUNDING:
        return "Aşırı Negatif"
    if value < 0:
        return "Negatif"
    return "Nötr"


def cvd_durumu(cvd_spot: float, cvd_perp: float) -> str:
    spot_yon = "Long" if cvd_spot > 0 else ("Short" if cvd_spot < 0 else "Nötr")
    perp_yon = "Long" if cvd_perp > 0 else ("Short" if cvd_perp < 0 else "Nötr")
    if spot_yon == "Nötr" or perp_yon == "Nötr":
        etiket = "Zayıf Sinyal"
    elif spot_yon == perp_yon:
        etiket = "Uyumlu"
    else:
        etiket = "Diverjans"
    return f"Spot {spot_yon} / Perp {perp_yon} ({etiket})"


def genel_durum(fund_status: str, oi_status: str, price_status: str, cvd_spot: float, cvd_perp: float):
    fund_positive = fund_status in {"Pozitif", "Aşırı Pozitif"}
    fund_negative = fund_status in {"Negatif", "Aşırı Negatif"}

    long_trap = fund_positive and oi_status == "Artıyor" and price_status == "Düşüyor"
    long_squeeze = fund_positive and oi_status == "Düşüyor" and price_status == "Düşüyor"
    short_trap = fund_negative and oi_status == "Artıyor" and price_status == "Artıyor"
    short_squeeze = fund_negative and oi_status == "Düşüyor" and price_status == "Artıyor"

    if short_squeeze:
        return ("Sağlıklı Long" if cvd_spot > 0 else "Short Squeeze"), None

    if long_squeeze:
        absorption_riski = cvd_spot > 0 and cvd_spot > abs(cvd_perp)
        if absorption_riski:
            return "İşlem Açma", None
        return ("Sağlıklı Short" if cvd_spot < 0 else "Long Squeeze"), None

    if long_trap:
        absorption_riski = cvd_spot > 0 and cvd_spot > abs(cvd_perp)
        etiket = "Long Trap (Olası Absorption)" if absorption_riski else "Long Trap"
        return "İşlem Açma", etiket

    if short_trap:
        dagitim_riski = cvd_spot < 0 and abs(cvd_spot) > abs(cvd_perp)
        etiket = "Short Trap (Olası Dağıtım)" if dagitim_riski else "Short Trap"
        return "İşlem Açma", etiket

    if price_status == "Nötr" and oi_status == "Artıyor":
        if cvd_spot > 0 and cvd_spot >= abs(cvd_perp):
            return "Akümülasyon", None
        if cvd_spot < 0 and abs(cvd_spot) >= abs(cvd_perp):
            return "Dağıtım", None

    return "İşlem Açma", None


def price_state(bars, periods: int) -> str:
    need = periods * 2
    if len(bars) < need:
        return "Veri Bekleniyor"
    current = bars[-periods:]
    previous = bars[-need:-periods]
    cur_high = max(bar.high for bar in current)
    cur_low = min(bar.low for bar in current)
    prev_high = max(bar.high for bar in previous)
    prev_low = min(bar.low for bar in previous)
    if cur_low > prev_low and cur_high >= prev_high:
        return "Artıyor"
    if cur_high < prev_high and cur_low <= prev_low:
        return "Düşüyor"
    return "Nötr"


def oi_state(points, periods: int, noise: float | None) -> str:
    if len(points) < periods + 1:
        return "Veri Bekleniyor"
    current = points[-1].value
    window = [point.value for point in points[-(periods + 1) : -1]]
    if current <= 0 or any(value <= 0 for value in window):
        return "Veri Bekleniyor"
    if noise is not None:
        low, high = min(window), max(window)
        rise = (current - low) / low * 100
        fall = (high - current) / high * 100
        if rise <= noise and fall <= noise:
            return "Nötr"
        return "Artıyor" if rise >= fall else "Düşüyor"
    if current > max(window):
        return "Artıyor"
    if current < min(window):
        return "Düşüyor"
    return "Nötr"


def _move_state(value: float | None) -> str:
    if value is None:
        return "Veri Bekleniyor"
    if value > 0:
        return "Artıyor"
    if value < 0:
        return "Düşüyor"
    return "Nötr"


def _phrase(word: str) -> str:
    return {"Artıyor": "artıyor", "Düşüyor": "düşüyor", "Nötr": "nötr"}.get(word, word)


def _summary(fund: str, price: str, oi: str, cvd: str) -> str:
    if "Veri Bekleniyor" in (price, oi, cvd) or fund == "Veri Yok":
        return "Veri bekleniyor"
    return f"Funding {fund.lower()} · Fiyat {_phrase(price)} · OI {_phrase(oi)} · {cvd}"


def build_reads(
    bundles: dict[str, tuple],
    funding_percent: float | None,
    enabled: set[str] | None = None,
) -> tuple[str, list[Read]]:
    """Her aralık kendi kapanmış mum serisini getirir: (mumlar, oi, cvd spot, cvd perp)."""
    fund = funding_label(funding_percent)
    reads: list[Read] = []
    for label, noise in PERIODS:
        if enabled is not None and label not in enabled:
            continue
        bars, oi_points, spot, perp_cvd = bundles.get(label, ([], [], None, None))
        price = price_state(bars, 1)
        oi = oi_state(oi_points, 1, noise)
        if spot is None or perp_cvd is None:
            cvd = "Veri Bekleniyor"
            result, trap = "Veri Bekleniyor", None
        elif price == "Veri Bekleniyor" or oi == "Veri Bekleniyor":
            cvd = cvd_durumu(spot, perp_cvd)
            result, trap = "Veri Bekleniyor", None
        else:
            cvd = cvd_durumu(spot, perp_cvd)
            result, trap = genel_durum(fund, oi, price, spot, perp_cvd)
        reads.append(
            Read(
                label=label,
                price_state=price,
                oi_state=oi,
                cvd_text=cvd,
                result=result,
                trap=trap,
                summary=_summary(fund, price, oi, cvd),
            )
        )
    return fund, reads
