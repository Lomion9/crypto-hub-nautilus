from __future__ import annotations


def format_price(value: float | None) -> str:
    if value is None:
        return "—"
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_usd(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 1e12:
        return f"{sign}${abs_value / 1e12:.2f}T"
    if abs_value >= 1e9:
        return f"{sign}${abs_value / 1e9:.2f}B"
    if abs_value >= 1e6:
        return f"{sign}${abs_value / 1e6:.2f}M"
    if abs_value >= 1e3:
        return f"{sign}${abs_value / 1e3:.2f}K"
    return f"{sign}${abs_value:,.0f}"


def format_quantity(value: float | None) -> str:
    if value is None:
        return "—"
    abs_value = abs(value)
    if abs_value >= 1000:
        digits = 2
    elif abs_value >= 1:
        digits = 2
    else:
        digits = 4
    return f"{value:,.{digits}f}"


def format_amount(value: float | None) -> str:
    if value is None:
        return "—"
    abs_value = abs(value)
    if abs_value >= 1000:
        digits = 2
    elif abs_value >= 1:
        digits = 3
    else:
        digits = 4
    return f"{value:+,.{digits}f}"


def format_percent(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}%"


def format_funding(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:+.4f}%"
