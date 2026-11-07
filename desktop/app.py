from __future__ import annotations

import bisect
import heapq
import json
import math
import sys
import html
import time
import winreg
import winsound
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import requests

from PySide6.QtCharts import (
    QChart,
    QChartView,
    QCandlestickSeries,
    QCandlestickSet,
    QLineSeries,
)
from PySide6.QtCore import QDateTime, QEvent, QMargins, QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
    QRegion,
    QShortcut,
)
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QFormLayout,
    QGraphicsItem,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QDoubleSpinBox,
    QLineEdit,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTableWidget,
    QToolTip,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.indicators import MA_TYPES, SOURCES, bollinger, bollinger_last
from desktop.indices import (
    INDEXES,
    IndexDataError,
    format_index_price,
    index_label,
    index_quotes_fresh,
    is_index,
    load_index_chart,
    load_index_quotes,
)
from desktop.liq_tape import LiquidationTape
from desktop.liquidity import LiquidityMap, clip_map, load_liquidity
from desktop.okx_trade import (
    Creds,
    OkxError,
    available_usdt as okx_available,
    execute as okx_execute,
    fetch_positions,
    ping as okx_ping,
    set_tp_sl as okx_set_tp_sl,
    set_trailing as okx_set_trailing,
)
from desktop.format import (
    format_amount,
    format_funding,
    format_percent,
    format_price,
    format_quantity,
    format_usd,
)
from desktop.market import (
    CHART_BARS,
    CHART_INTERVALS,
    CHART_PAGE,
    Bar,
    MarketError,
    Quote,
    Snapshot,
    HOUR_MS,
    base_of,
    fetch_klines,
    bars_since,
    fetch_klines_since,
    since_step,
    fetch_older_klines,
    bucket_funding,
    load_quotes,
    load_snapshot,
)

INTERVAL_LABELS = (("15m", "15dk"), ("1h", "1sa"), ("4h", "4sa"), ("1d", "1g"), ("1w", "1hft"))
PANELS = (
    ("list", "Coin listesi"),
    ("price", "Fiyat grafiği"),
    ("oi", "Open interest"),
    ("funding", "Funding"),
    ("cards", "Kartlar"),
    ("analysis", "Zaman analizi"),
)
SIGNAL_WINDOWS = ("1sa", "4sa", "24sa", "1hafta")
SIGNAL_MARK_BARS = 500
SIGNAL_BAR_MS = {
    "1sa": 3_600_000,
    "4sa": 4 * 3_600_000,
    "24sa": 86_400_000,
    "1hafta": 7 * 86_400_000,
}
CHART_SIGNAL = {"1h": "1sa", "4h": "4sa", "1d": "24sa", "1w": "1hafta"}
SIGNAL_LIQ_HOURS = {"1sa": 24, "4sa": 72, "24sa": 168, "1hafta": 720}
SIGNAL_EXPIRE_MS = {"1sa": 24 * 60 * 60 * 1000, "4sa": 3 * 24 * 60 * 60 * 1000}
SIGNAL_COOL_MS = {
    "1sa": 60 * 60 * 1000,
    "4sa": 4 * 60 * 60 * 1000,
    "24sa": 24 * 60 * 60 * 1000,
    "1hafta": 7 * 24 * 60 * 60 * 1000,
}
TP_OFFSET = 200.0
LIQ_SPANS = (
    (12, "12 saat"),
    (24, "24 saat"),
    (48, "48 saat"),
    (72, "3 gün"),
    (168, "1 hafta"),
    (336, "2 hafta"),
    (720, "Aylık"),
)


def _liq_candle_spec(hours: int) -> tuple[str, int, int]:
    """Aralığın mum süresi. Dönüş: borsa aralığı, mum uzunluğu, istenecek adet."""
    if hours <= 24:
        return "5m", 5 * 60 * 1000, hours * 12 + 2
    if hours <= 72:
        return "15m", 15 * 60 * 1000, hours * 4 + 2
    if hours <= 168:
        return "30m", 30 * 60 * 1000, hours * 2 + 2
    if hours <= 336:
        return "2h", 2 * HOUR_MS, hours // 2 + 2
    return "4h", 4 * HOUR_MS, hours // 4 + 2
WINDOW_LABELS = ("15dk", "1sa", "2sa", "4sa", "8sa", "24sa", "1hafta")
QUIET_VERDICTS = {"İşlem Açma", "Veri Bekleniyor", "—", ""}
REFRESH_MS = 15_000
BACKGROUND_MS = 60_000
FUTURE_BARS = 100
PRICE_PAN = 8
UP = QColor("#26a69a")
DOWN = QColor("#ef5350")
FLAT = QColor("#f4f4f5")
MUTED = QColor("#a1a1aa")
AMBER = QColor("#f5c451")
CHART_PLOT = QColor("#16161c")
TREND = QColor("#93c5fd")

STYLESHEET = """
QMainWindow, QWidget { background: #0b0b0d; color: #e4e4e7; }
QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {
    background: #18181b;
    border: 1px solid #3f3f46;
    border-radius: 8px;
    padding: 7px 10px;
    color: #fafafa;
}
QCheckBox { color: #e4e4e7; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #71717a; border-radius: 4px; background: #18181b;
}
QCheckBox::indicator:checked { background: #fafafa; border-color: #fafafa; }
QTableWidget {
    background: #0b0b0d;
    border: none;
    outline: none;
    gridline-color: transparent;
}
QHeaderView::section {
    background: #0b0b0d;
    color: #71717a;
    border: none;
    border-bottom: 1px solid #27272a;
    padding: 6px 4px;
    font-size: 11px;
}
QTableWidget::item { padding: 1px 6px; }
QTableWidget::item:selected {
    background: #16161c;
    color: #fafafa;
    border-left: 2px solid #26a69a;
}
QPushButton {
    background: transparent;
    color: #a1a1aa;
    border: 1px solid #3f3f46;
    border-radius: 7px;
    padding: 5px 10px;
}
QPushButton:hover { color: #fafafa; }
QPushButton:checked { background: #fafafa; color: #09090b; border-color: #fafafa; }
QPushButton#tool { padding: 4px; }
QPushButton#tool:checked { background: #27272a; color: #fafafa; border-color: #fafafa; }
QFrame#card { background: transparent; border: none; border-radius: 0; }
QWidget#panel, QChartView#panel {
    background: #121216;
    border: 1px solid #27272a;
    border-radius: 4px;
}
QWidget#topbar {
    background: #0b0b0d;
    border: none;
    border-bottom: 1px solid #27272a;
}
QWidget#panel QTableWidget, QWidget#panel QHeaderView::section {
    background: #121216;
    border: none;
}
QWidget#panel QLabel#brand, QWidget#panel QLineEdit {
    background: #121216;
    color: #e4e4e7;
}
QWidget#panel QLabel#brand { color: #71717a; border: none; }
QPushButton#page, QPushButton#interval {
    background: transparent;
    color: #a1a1aa;
    border: 1px solid #27272a;
    border-radius: 4px;
    padding: 4px 10px;
}
QPushButton#page:hover, QPushButton#interval:hover { color: #fafafa; }
QPushButton#page:checked, QPushButton#interval:checked {
    background: #fafafa;
    color: #09090b;
    border-color: #fafafa;
}
QMenuBar {
    background: #0b0b0d;
    color: #a1a1aa;
    border-bottom: 1px solid #27272a;
    padding: 0;
    font-size: 12px;
}
QMenuBar::item {
    background: transparent;
    color: #a1a1aa;
    border: none;
    padding: 3px 12px;
    margin: 0;
}
QMenuBar::item:selected { background: #18181b; color: #fafafa; }
QLabel#metricTitle { color: #71717a; font-size: 11px; }
QLabel#metricSub { color: #a1a1aa; font-size: 11px; }
QStatusBar { color: #71717a; background: #0b0b0d; }
QToolTip { background: #18181b; color: #fafafa; border: 1px solid #3f3f46; padding: 4px 8px; }
QMenu { background: #18181b; color: #e4e4e7; border: 1px solid #3f3f46; }
QMenu::item { padding: 6px 28px 6px 22px; }
QMenu::item:selected { background: #27272a; }
QSplitter::handle:horizontal { width: 4px; background: #0b0b0d; }
QSplitter::handle:vertical { height: 4px; background: #0b0b0d; }
QSplitter::handle:hover { background: #52525b; }
"""


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "CryptoHub"


def _app_command() -> str:
    path = sys.executable if getattr(sys, "frozen", False) else str(Path(sys.argv[0]).resolve())
    return f'"{path}"'


def _apply_startup(enabled: bool) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, _app_command())
            else:
                try:
                    winreg.DeleteValue(key, _RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        return


def _load_startup() -> bool:
    raw = _read_state().get("start_with_windows")
    if raw is None:
        _patch_state(start_with_windows=True)
        return True
    return bool(raw)


_STATE_PATH: Path | None = None
_STATE_TEXT: tuple[tuple[int, int], str] | None = None


def _state_path() -> Path:
    global _STATE_PATH
    if _STATE_PATH is None:
        root = Path.home() / "AppData" / "Roaming" / "CryptoHub"
        root.mkdir(parents=True, exist_ok=True)
        _STATE_PATH = root / "desktop_state.json"
    return _STATE_PATH


def _state_text() -> str:
    """Dosya değişmediyse son okunan metin; her çağıran kendi kopyasını çözer."""
    global _STATE_TEXT
    path = _state_path()
    info = path.stat()
    key = (info.st_mtime_ns, info.st_size)
    if _STATE_TEXT is not None and _STATE_TEXT[0] == key:
        return _STATE_TEXT[1]
    text = path.read_text(encoding="utf-8")
    _STATE_TEXT = (key, text)
    return text


def _read_state() -> dict:
    try:
        data = json.loads(_state_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _patch_state(**updates) -> None:
    global _STATE_TEXT
    data = _read_state()
    data.update(updates)
    text = json.dumps(data)
    path = _state_path()
    try:
        path.write_text(text, encoding="utf-8")
        info = path.stat()
    except OSError:
        _STATE_TEXT = None
        return
    _STATE_TEXT = ((info.st_mtime_ns, info.st_size), text)


def _load_state() -> tuple[str, str]:
    data = _read_state()
    symbol = str(data.get("symbol") or "BTCUSDT").upper()
    interval = str(data.get("interval") or "15m")
    if interval not in CHART_INTERVALS:
        return "BTCUSDT", "15m"
    if symbol.endswith("USDT") or is_index(symbol):
        return symbol, interval
    return "BTCUSDT", "15m"


def _save_state(symbol: str, interval: str) -> None:
    _patch_state(symbol=symbol, interval=interval)


def _load_panels() -> dict[str, bool]:
    raw = _read_state().get("panels")
    flags = {key: key != "oi" for key, _label in PANELS}
    if isinstance(raw, dict):
        for key in flags:
            if key in raw:
                flags[key] = bool(raw[key])
    if not any(flags.values()):
        return {key: key != "oi" for key in flags}
    return flags


def _load_watchlist(extra: str) -> list[str]:
    raw = _read_state().get("watchlist")
    found: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            symbol = str(item).upper()
            if (symbol.endswith("USDT") or is_index(symbol)) and symbol not in found:
                found.append(symbol)
    if found:
        return found
    found = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    if extra.endswith("USDT") and extra not in found:
        found.insert(0, extra)
    return found


def _nice_step(span: float) -> float:
    raw = span / 5
    if raw <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw))
    residual = raw / magnitude
    nice = 10
    for candidate in (1, 2, 2.5, 5, 10):
        if residual <= candidate * 1.001:
            nice = candidate
            break
    return nice * magnitude


def _decimals_for_step(step: float) -> int:
    for decimals in range(0, 8):
        if abs(step - round(step, decimals)) <= abs(step) * 1e-6:
            return decimals
    return 8


def _disarm_ticks(axis) -> None:
    from PySide6.QtCharts import QValueAxis

    axis.setTickType(QValueAxis.TickType.TicksFixed)
    axis.setTickCount(2)


def _apply_price_scale(axis, low: float, high: float) -> None:
    from PySide6.QtCharts import QValueAxis

    if high < low:
        low, high = high, low
    span = high - low
    if span <= 0:
        span = max(abs(low) * 0.001, 1e-6)
        high = low + span
    step = _nice_step(span)
    # A leftover tick interval from a cheap coin makes Qt build a tick
    # for every fraction of a BTC-sized range and the window stops responding.
    _disarm_ticks(axis)
    axis.setRange(low, high)
    axis.setLabelFormat(f"%.{_decimals_for_step(step)}f")
    anchor = math.floor(low / step) * step
    if anchor < low - step * 1e-6:
        anchor += step
    axis.setTickInterval(step)
    axis.setTickAnchor(anchor)
    axis.setTickType(QValueAxis.TickType.TicksDynamic)


def _even_pick(items: list, count: int) -> list:
    if len(items) <= count:
        return list(items)
    last = len(items) - 1
    picked: list[int] = []
    for step in range(count):
        index = round(step * last / (count - 1))
        if index not in picked:
            picked.append(index)
    return [items[index] for index in picked]


_DAY_MS = 86_400_000
_SHORT_MONTHS = (
    "",
    "Oca",
    "Şub",
    "Mar",
    "Nis",
    "May",
    "Haz",
    "Tem",
    "Ağu",
    "Eyl",
    "Eki",
    "Kas",
    "Ara",
)
_WEEKDAYS = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
ALARM = QColor("#c084fc")
TP_LINE = QColor("#facc15")


def _cross_stamp(moment: datetime) -> str:
    return (
        f"{_WEEKDAYS[moment.weekday()]} {moment.day:02d} "
        f"{_SHORT_MONTHS[moment.month]} '{moment.year % 100:02d}"
    )


def _range_stamp(moment: datetime) -> str:
    return f"{_cross_stamp(moment)} {moment:%H:%M}"


def _same_price(left: float, right: float) -> bool:
    return abs(left - right) <= max(abs(right) * 1e-6, 1e-8)


def _price_decimals(price: float) -> int:
    if abs(price) >= 1000:
        return 2
    if abs(price) >= 1:
        return 4
    return 6


def _ask_price(parent, title: str, label: str, value: float) -> float | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    text = QLabel(label)
    text.setWordWrap(True)
    layout.addWidget(text)
    spin = QDoubleSpinBox()
    decimals = _price_decimals(value)
    spin.setDecimals(decimals)
    spin.setRange(1e-8, 1e13)
    spin.setSingleStep(10 ** (-min(decimals, 4)))
    spin.setValue(value)
    layout.addWidget(spin)
    buttons = QHBoxLayout()
    save = QPushButton("Kaydet")
    cancel = QPushButton("Vazgeç")
    save.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(save)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return float(spin.value())


def _load_alarms() -> dict[str, list[float]]:
    raw = _read_state().get("alarms")
    book: dict[str, list[float]] = {}
    if not isinstance(raw, dict):
        return book
    for symbol, levels in raw.items():
        if not isinstance(levels, list):
            continue
        clean: list[float] = []
        for level in levels:
            try:
                clean.append(float(level))
            except (TypeError, ValueError):
                continue
        if clean:
            book[str(symbol).upper()] = clean
    return book


def _load_windows() -> set[str]:
    data = _read_state()
    raw = data.get("windows")
    if isinstance(raw, list):
        chosen = {str(item) for item in raw if str(item) in WINDOW_LABELS}
        if chosen or raw == []:
            if not data.get("windows_week_added"):
                chosen.add("1hafta")
                _patch_state(
                    windows=[name for name in WINDOW_LABELS if name in chosen],
                    windows_week_added=True,
                )
            return chosen
    return set(WINDOW_LABELS)


def _clean_drawing(raw) -> dict | None:
    kind = "trend"
    values = None
    if isinstance(raw, dict):
        kind = str(raw.get("kind") or "trend")
    elif isinstance(raw, (list, tuple)) and raw:
        kind = str(raw[0]) if str(raw[0]) in ("trend", "range", "level") else "trend"
    if kind == "level":
        price = None
        if isinstance(raw, dict):
            price = raw.get("price", raw.get("p1"))
        elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
            price = raw[1]
        try:
            level = float(price)
        except (TypeError, ValueError):
            return None
        if level > 0 and math.isfinite(level):
            return {"kind": "level", "price": level}
        return None
    if isinstance(raw, dict):
        if all(key in raw for key in ("t1", "p1", "t2", "p2")):
            values = (raw["t1"], raw["p1"], raw["t2"], raw["p2"])
    elif isinstance(raw, (list, tuple)) and len(raw) == 5 and str(raw[0]) in ("trend", "range"):
        values = raw[1:]
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        values = raw
    if kind not in ("trend", "range") or values is None:
        return None
    try:
        t1, p1, t2, p2 = (float(values[0]), float(values[1]), float(values[2]), float(values[3]))
    except (TypeError, ValueError):
        return None
    if p1 > 0 and p2 > 0 and math.isfinite(t1) and math.isfinite(t2):
        return {"kind": kind, "t1": t1, "p1": p1, "t2": t2, "p2": p2}
    return None


def _load_drawings() -> dict[str, list[dict]]:
    data = _read_state()
    raw = data.get("drawings")
    if not isinstance(raw, dict):
        raw = data.get("trends")
    book: dict[str, list[dict]] = {}
    if not isinstance(raw, dict):
        return book
    for symbol, lines in raw.items():
        if not isinstance(lines, list):
            continue
        clean = [item for item in (_clean_drawing(line) for line in lines) if item is not None]
        if clean:
            book[str(symbol).upper()] = clean
    return book


def _load_bollinger() -> dict:
    raw = _read_state().get("bollinger")
    settings = {"on": False, "length": 20, "ma": "SMA", "source": "close", "mult": 2.0, "offset": 0}
    if not isinstance(raw, dict):
        return settings
    settings["on"] = bool(raw.get("on"))
    try:
        settings["length"] = min(max(int(raw.get("length", 20)), 1), 500)
        settings["offset"] = min(max(int(raw.get("offset", 0)), -500), 500)
        settings["mult"] = min(max(float(raw.get("mult", 2.0)), 0.001), 50.0)
    except (TypeError, ValueError):
        pass
    if str(raw.get("ma")) in MA_TYPES:
        settings["ma"] = str(raw.get("ma"))
    if str(raw.get("source")) in SOURCES:
        settings["source"] = str(raw.get("source"))
    return settings


def _signal_window(interval: str) -> str | None:
    """Grafiğin mum süresi. 15dk'nın sinyal aralığı yoktur."""
    return CHART_SIGNAL.get(interval)


def _load_signal() -> dict:
    raw = _read_state().get("signal_draw")
    settings = {"on": False, "show_closed": True}
    if isinstance(raw, dict):
        settings["on"] = bool(raw.get("on"))
        if "show_closed" in raw:
            settings["show_closed"] = bool(raw.get("show_closed"))
    return settings


def _load_signal_marks() -> list[dict]:
    raw = _read_state().get("signal_marks")
    marks: list[dict] = []
    if not isinstance(raw, list):
        return marks
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "")
        side = str(item.get("side") or "")
        symbol = str(item.get("symbol") or "").upper()
        try:
            moment = int(item.get("time_ms") or 0)
        except (TypeError, ValueError):
            continue
        if label in SIGNAL_WINDOWS and side in ("buy", "sell") and symbol and moment > 0:
            marks.append(
                {
                    "symbol": symbol,
                    "label": label,
                    "side": side,
                    "time_ms": moment,
                    "closed": bool(item.get("closed")),
                }
            )
    return _trim_signal_marks(marks)


def _trim_signal_marks(marks: list[dict], now_ms: int | None = None) -> list[dict]:
    """Her aralığın kendi son 500 mumu kalır. Daha eskisi silinir."""
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    kept: list[dict] = []
    for item in marks:
        step = SIGNAL_BAR_MS.get(str(item.get("label") or ""))
        try:
            moment = int(item.get("time_ms") or 0)
        except (TypeError, ValueError):
            continue
        if step is None or moment <= 0 or now - moment > SIGNAL_MARK_BARS * step:
            continue
        kept.append(item)
    return kept


def _visible_marks(marks: list[dict], symbol: str, label: str, show_closed: bool) -> list[dict]:
    chosen = []
    for item in marks:
        if item.get("symbol") != symbol or item.get("label") != label:
            continue
        if not show_closed and item.get("closed"):
            continue
        chosen.append(item)
    return chosen


def _close_mark(marks: list[dict], symbol: str, label: str, episode) -> bool:
    """Kapanan sinyalin kaydını tutar. Saat yoksa oka dönüşmez."""
    if label not in SIGNAL_WINDOWS or not isinstance(episode, dict) or not episode.get("verdict"):
        return False
    side = episode.get("side") or _trade_side(str(episode.get("verdict") or ""))
    if side not in ("up", "down"):
        return False
    try:
        moment = int(episode.get("opened_ms") or 0)
    except (TypeError, ValueError):
        return False
    if moment <= 0:
        return False
    drawn = "buy" if side == "up" else "sell"
    for item in marks:
        if (
            item.get("symbol") == symbol
            and item.get("label") == label
            and item.get("side") == drawn
            and int(item.get("time_ms") or 0) == moment
        ):
            if item.get("closed"):
                return False
            item["closed"] = True
            return True
    marks.append(
        {
            "symbol": symbol,
            "label": label,
            "side": drawn,
            "time_ms": moment,
            "closed": True,
        }
    )
    return True


def _mark_from_episode(symbol: str, label: str, episode) -> dict | None:
    """Açık sinyalin saati. Alış aşağı, satış yukarı ok olur."""
    if label not in SIGNAL_WINDOWS or not isinstance(episode, dict):
        return None
    verdict = str(episode.get("verdict") or "")
    if verdict in QUIET_VERDICTS:
        return None
    side = episode.get("side") or _trade_side(verdict)
    if side not in ("up", "down"):
        return None
    try:
        moment = int(episode.get("opened_ms") or 0)
    except (TypeError, ValueError):
        return None
    if moment <= 0:
        return None
    return {
        "symbol": str(symbol).upper(),
        "label": label,
        "side": "buy" if side == "up" else "sell",
        "time_ms": moment,
        "closed": False,
    }


def _candle_follow(old_times: list[int], new_times: list[int]) -> str:
    """Aynı seri duruyorsa yalnız son mum. Coin, süre veya kopuk geçmiş baştan kurulur."""
    if not old_times or not new_times:
        return "rebuild"
    if old_times[0] != new_times[0]:
        return "rebuild"
    if len(new_times) < len(old_times) or len(new_times) > len(old_times) + 2:
        return "rebuild"
    for index in range(len(old_times) - 1):
        if old_times[index] != new_times[index]:
            return "rebuild"
    if len(new_times) == len(old_times):
        if old_times[-1] != new_times[-1]:
            return "rebuild"
        return "update"
    if len(new_times) == len(old_times) + 1 and new_times[-2] == old_times[-1]:
        return "append"
    return "rebuild"


def _candle_holding(stamps: list[float], moment: float, step: int) -> float | None:
    """Sinyal saatinin düştüğü mumun açılışı."""
    if step <= 0 or not stamps:
        return None
    return _candle_holding_sorted(sorted(stamps), moment, step)


def _candle_holding_sorted(stamps: list[float], moment: float, step: int) -> float | None:
    """Sıralı mum açılışları. Sağdaki, sinyal saatini hâlâ kapsayan mumdur."""
    if step <= 0 or not stamps:
        return None
    index = bisect.bisect_right(stamps, moment) - 1
    if index < 0:
        return None
    chosen = stamps[index]
    if moment >= chosen + step:
        return None
    return chosen


def _signal_dialog(parent, settings: dict) -> dict | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Signal")
    dialog.setMinimumWidth(320)
    form = QFormLayout()
    closed = QCheckBox("Kapanmış sinyalleri göster")
    closed.setChecked(bool(settings.get("show_closed", True)))
    form.addRow(closed)
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    buttons = QHBoxLayout()
    save = QPushButton("Kaydet")
    cancel = QPushButton("Vazgeç")
    save.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(save)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return {"on": True, "show_closed": closed.isChecked()}


def _segment_distance(pos: QPointF, start: QPointF, end: QPointF) -> float:
    vx = end.x() - start.x()
    vy = end.y() - start.y()
    span = vx * vx + vy * vy
    if span <= 1:
        return math.hypot(pos.x() - start.x(), pos.y() - start.y())
    ratio = ((pos.x() - start.x()) * vx + (pos.y() - start.y()) * vy) / span
    ratio = min(max(ratio, 0.0), 1.0)
    return math.hypot(pos.x() - (start.x() + ratio * vx), pos.y() - (start.y() + ratio * vy))


def _tool_icon(draw) -> QIcon:
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#e4e4e7"), 1.6))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    return QIcon(pixmap)


def _icon_list() -> QIcon:
    def draw(painter: QPainter) -> None:
        for y in (5, 10, 15):
            painter.drawLine(3, y, 17, y)

    return _tool_icon(draw)


def _icon_alarm() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawArc(5, 4, 10, 10, 20 * 16, 140 * 16)
        painter.drawLine(5, 11, 4, 14)
        painter.drawLine(15, 11, 16, 14)
        painter.drawLine(4, 14, 16, 14)
        painter.drawLine(10, 14, 10, 17)

    return _tool_icon(draw)


def _icon_draw() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawRoundedRect(3, 4, 9, 9, 1, 1)
        painter.drawLine(11, 12, 16, 17)
        painter.drawLine(14, 17, 17, 17)
        painter.drawLine(17, 14, 17, 17)

    return _tool_icon(draw)


def _icon_trend() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawLine(4, 15, 15, 4)
        painter.setBrush(QColor("#e4e4e7"))
        painter.drawEllipse(2, 13, 4, 4)
        painter.drawEllipse(13, 2, 4, 4)

    return _tool_icon(draw)


def _icon_indicator() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawLine(3, 6, 17, 8)
        painter.drawLine(3, 10, 17, 10)
        painter.drawLine(3, 14, 17, 12)

    return _tool_icon(draw)


def _panel_toggle(label: str) -> QPushButton:
    button = QPushButton(label)
    button.setCheckable(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        "QPushButton { background: transparent; color: #a1a1aa; border: 1px solid #3f3f46; "
        "border-radius: 7px; text-align: left; padding: 6px 8px; }"
        "QPushButton:hover { color: #fafafa; }"
        "QPushButton:checked { color: #fafafa; background: #27272a; border-color: #a78bfa; }"
    )
    return button


def _icon_close() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawLine(5, 5, 15, 15)
        painter.drawLine(15, 5, 5, 15)

    return _tool_icon(draw)


def _icon_settings() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawEllipse(6, 6, 8, 8)
        painter.drawEllipse(8, 8, 4, 4)
        painter.drawLine(10, 2, 10, 5)
        painter.drawLine(10, 15, 10, 18)
        painter.drawLine(2, 10, 5, 10)
        painter.drawLine(15, 10, 18, 10)

    return _tool_icon(draw)


_SOURCE_LABELS = {
    "close": "Kapanış",
    "open": "Açılış",
    "high": "Yüksek",
    "low": "Düşük",
    "hl2": "HL2",
    "hlc3": "HLC3",
    "ohlc4": "OHLC4",
}


def _bollinger_dialog(parent, settings: dict) -> dict | None:
    dialog = QDialog(parent)
    dialog.setWindowTitle("Bollinger Bandı")
    dialog.setMinimumWidth(320)
    form = QFormLayout()
    form.setSpacing(8)
    length = QSpinBox()
    length.setRange(1, 500)
    length.setValue(int(settings["length"]))
    average = QComboBox()
    average.addItems(list(MA_TYPES))
    average.setCurrentText(str(settings["ma"]))
    source = QComboBox()
    source.addItems(list(_SOURCE_LABELS.values()))
    source.setCurrentText(_SOURCE_LABELS.get(str(settings["source"]), "Kapanış"))
    mult = QDoubleSpinBox()
    mult.setRange(0.001, 50)
    mult.setDecimals(3)
    mult.setSingleStep(0.1)
    mult.setValue(float(settings["mult"]))
    offset = QSpinBox()
    offset.setRange(-500, 500)
    offset.setValue(int(settings["offset"]))
    form.addRow("Uzunluk", length)
    form.addRow("Ortalama", average)
    form.addRow("Kaynak", source)
    form.addRow("Std. sapma", mult)
    form.addRow("Kaydırma", offset)
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    buttons = QHBoxLayout()
    save = QPushButton("Kaydet")
    cancel = QPushButton("Vazgeç")
    save.clicked.connect(dialog.accept)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(save)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    names = {label: key for key, label in _SOURCE_LABELS.items()}
    return {
        "on": True,
        "length": length.value(),
        "ma": average.currentText(),
        "source": names.get(source.currentText(), "close"),
        "mult": mult.value(),
        "offset": offset.value(),
    }


OKX_LOG_KEEP = 50


def _okx_note(symbol: str, label: str, op: str, ok: bool, message: str) -> None:
    raw = _read_state().get("okx_log")
    log = [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    log.append(
        {
            "time_ms": int(time.time() * 1000),
            "symbol": symbol,
            "label": label,
            "op": op,
            "ok": bool(ok),
            "message": message,
        }
    )
    _patch_state(okx_log=log[-OKX_LOG_KEEP:])


def _okx_log_lines(limit: int = 5) -> list[tuple[str, bool]]:
    raw = _read_state().get("okx_log")
    if not isinstance(raw, list):
        return []
    lines: list[tuple[str, bool]] = []
    for item in raw[-limit:][::-1]:
        if not isinstance(item, dict):
            continue
        clock = ""
        stamp = item.get("time_ms")
        if isinstance(stamp, (int, float)) and stamp > 0:
            clock = datetime.fromtimestamp(float(stamp) / 1000).strftime("%d.%m %H:%M")
        words = [clock, base_of(str(item.get("symbol") or "")) if item.get("symbol") else "", str(item.get("label") or "")]
        head = " ".join(word for word in words if word)
        lines.append((f"{head} · {item.get('message') or ''}", bool(item.get("ok"))))
    return lines


def _okx_block(data: dict) -> str:
    """Otomatik emri engelleyen ayar. Engel yoksa boş."""
    if not data.get("okx_enabled"):
        return "otomatik işlem kapalı"
    creds = Creds(
        str(data.get("okx_api_key") or ""),
        str(data.get("okx_secret") or ""),
        str(data.get("okx_passphrase") or ""),
    )
    if not creds.ready():
        return "API bilgisi eksik"
    try:
        leverage = int(data.get("okx_leverage") or 0)
    except (TypeError, ValueError):
        leverage = 0
    if leverage < 1:
        return "kaldıraç seçilmedi"
    raw = data.get("okx_windows")
    if isinstance(raw, list) and not any(str(item) in SIGNAL_WINDOWS for item in raw):
        return "emir gidecek aralık seçilmedi"
    return ""


def _margin_pct(data: dict) -> int:
    """Pozisyona ayrılacak kullanılabilir bakiye yüzdesi, 1-100. Kayıt yoksa %10."""
    try:
        pct = int(round(float(data.get("okx_margin_pct"))))
    except (TypeError, ValueError):
        return 10
    return min(100, max(1, pct))


POSITION_COLUMNS = (
    "Coin", "Yön", "Boyut", "Giriş", "Mark", "PnL", "Likidasyon",
    "Marjin", "Kaldıraç", "TP", "SL", "Trailing", "İşlem",
)
LIQ_NEAR = QColor("#ff9800")


def _plain(value) -> str:
    """Fiyat alanına yazılacak sade sayı; binlik ayırıcı yok."""
    if not isinstance(value, (int, float)) or value <= 0:
        return ""
    return f"{float(value):.8f}".rstrip("0").rstrip(".")


def _parse_price(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace(",", ".")
    if not cleaned:
        return None
    value = float(cleaned)
    if value <= 0 or not math.isfinite(value):
        raise ValueError(text)
    return value


def _price_text(value) -> str:
    return format_price(float(value)) if isinstance(value, (int, float)) and value > 0 else "—"


def _trail_text(trail) -> str:
    if not isinstance(trail, dict):
        return "—"
    if isinstance(trail.get("ratio"), (int, float)) and trail["ratio"] > 0:
        text = f"%{trail['ratio'] * 100:g}"
    elif isinstance(trail.get("spread"), (int, float)) and trail["spread"] > 0:
        text = f"{format_price(trail['spread'])} $"
    else:
        text = "var"
    if isinstance(trail.get("active"), (int, float)) and trail["active"] > 0:
        text += f" @ {format_price(trail['active'])}"
    return text


def _position_cells(position: dict) -> list[tuple[str, QColor]]:
    up = position.get("side") == "up"
    upl = position.get("upl")
    ratio = position.get("upl_ratio")
    if isinstance(upl, (int, float)):
        pnl = f"{upl:+.2f}"
        if isinstance(ratio, (int, float)):
            pnl += f" ({ratio * 100:+.2f}%)"
        pnl_color = UP if upl > 0 else DOWN if upl < 0 else FLAT
    else:
        pnl, pnl_color = "—", MUTED
    mark = position.get("mark")
    liq = position.get("liq")
    near = (
        isinstance(mark, (int, float)) and mark > 0
        and isinstance(liq, (int, float)) and liq > 0
        and abs(mark - liq) / mark < 0.05
    )
    lever = position.get("lever")
    margin = position.get("margin")
    return [
        (base_of(str(position.get("symbol") or "")), FLAT),
        ("Long" if up else "Short", UP if up else DOWN),
        (str(position.get("sz") or "—"), FLAT),
        (_price_text(position.get("avg")), FLAT),
        (_price_text(mark), FLAT),
        (pnl, pnl_color),
        (_price_text(liq), LIQ_NEAR if near else FLAT),
        (f"{margin:.2f}" if isinstance(margin, (int, float)) and margin > 0 else "—", FLAT),
        (f"{lever:g}x" if isinstance(lever, (int, float)) and lever > 0 else "—", FLAT),
        (_price_text(position.get("tp")), UP if position.get("tp") else MUTED),
        (_price_text(position.get("sl")), DOWN if position.get("sl") else MUTED),
        (_trail_text(position.get("trail")), FLAT if position.get("trail") else MUTED),
    ]


def _pnl_per_price(position: dict) -> float | None:
    """Fiyat bir birim değişince PnL'in değişimi; açık PnL'den çıkarılır."""
    avg, mark, upl = position.get("avg"), position.get("mark"), position.get("upl")
    if not all(isinstance(value, (int, float)) for value in (avg, mark, upl)) or mark == avg:
        return None
    return float(upl) / (float(mark) - float(avg))


def _tp_sl_dialog(parent, position: dict) -> tuple[float | None, float | None] | None:
    dialog = QDialog(parent)
    word = "Long" if position.get("side") == "up" else "Short"
    dialog.setWindowTitle(f"{base_of(str(position.get('symbol') or ''))} {word} · TP / SL")
    dialog.setMinimumWidth(360)
    form = QFormLayout()
    form.setSpacing(8)
    fields: dict[str, QLineEdit] = {}
    for key, title in (("tp", "Take profit"), ("sl", "Stop loss")):
        line = QLineEdit(_plain(position.get(key)))
        line.setPlaceholderText("Boş = yok")
        clear = QPushButton("Kaldır")
        clear.clicked.connect(lambda _checked=False, field=line: field.clear())
        row = QHBoxLayout()
        row.addWidget(line, 1)
        row.addWidget(clear)
        form.addRow(title, row)
        fields[key] = line
    info = QLabel("")
    info.setWordWrap(True)
    info.setStyleSheet("color: #a1a1aa;")
    error = QLabel("")
    error.setStyleSheet("color: #ef5350;")
    per_price = _pnl_per_price(position)
    avg = position.get("avg")

    def refresh() -> None:
        parts = [
            f"Giriş {_price_text(avg)} · Mark {_price_text(position.get('mark'))} · Liq {_price_text(position.get('liq'))}"
        ]
        for key, title in (("tp", "TP"), ("sl", "SL")):
            try:
                value = _parse_price(fields[key].text())
            except ValueError:
                continue
            if value is not None and per_price is not None and isinstance(avg, (int, float)):
                parts.append(f"{title} olursa ≈ {(value - float(avg)) * per_price:+.2f} USDT")
        info.setText("\n".join(parts))

    for line in fields.values():
        line.textChanged.connect(lambda _text: refresh())
    refresh()
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    layout.addWidget(info)
    layout.addWidget(error)
    buttons = QHBoxLayout()
    save = QPushButton("Gönder")
    cancel = QPushButton("Vazgeç")
    chosen: dict[str, float | None] = {}

    def accept() -> None:
        try:
            chosen["tp"] = _parse_price(fields["tp"].text())
            chosen["sl"] = _parse_price(fields["sl"].text())
        except ValueError:
            error.setText("Fiyat sayı olmalı")
            return
        dialog.accept()

    save.clicked.connect(accept)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(save)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return chosen.get("tp"), chosen.get("sl")


def _trailing_dialog(parent, position: dict) -> dict | None:
    dialog = QDialog(parent)
    word = "Long" if position.get("side") == "up" else "Short"
    dialog.setWindowTitle(f"{base_of(str(position.get('symbol') or ''))} {word} · Trailing stop")
    dialog.setMinimumWidth(360)
    trail = position.get("trail") if isinstance(position.get("trail"), dict) else {}
    form = QFormLayout()
    form.setSpacing(8)
    mode = QComboBox()
    mode.addItem("Yüzde (%)", "ratio")
    mode.addItem("Fiyat farkı (USDT)", "spread")
    amount = QDoubleSpinBox()
    amount.setDecimals(2)

    def paint_mode() -> None:
        if mode.currentData() == "ratio":
            amount.setRange(0.1, 100)
            amount.setSingleStep(0.1)
            amount.setSuffix(" %")
        else:
            amount.setRange(0.0001, 10_000_000)
            amount.setSingleStep(10)
            amount.setSuffix(" $")

    if isinstance(trail.get("spread"), (int, float)) and trail["spread"] > 0:
        mode.setCurrentIndex(1)
        paint_mode()
        amount.setValue(float(trail["spread"]))
    else:
        paint_mode()
        ratio = trail.get("ratio")
        amount.setValue(float(ratio) * 100 if isinstance(ratio, (int, float)) and ratio > 0 else 1.0)
    mode.currentIndexChanged.connect(lambda _index: paint_mode())
    active = QLineEdit(_plain(trail.get("active")))
    active.setPlaceholderText("Boş = hemen başlar")
    form.addRow("Tür", mode)
    form.addRow("Geri çekilme", amount)
    form.addRow("Aktivasyon fiyatı", active)
    note = QLabel(
        "Fiyat lehine giderken en iyi seviyeyi izler; bu kadar geri dönünce pozisyonun tamamı piyasa fiyatından kapanır."
    )
    note.setWordWrap(True)
    note.setStyleSheet("color: #a1a1aa;")
    error = QLabel("")
    error.setStyleSheet("color: #ef5350;")
    layout = QVBoxLayout(dialog)
    layout.addLayout(form)
    layout.addWidget(note)
    layout.addWidget(error)
    buttons = QHBoxLayout()
    save = QPushButton("Gönder")
    remove = QPushButton("Trailing'i kaldır")
    cancel = QPushButton("Vazgeç")
    remove.setEnabled(bool(trail))
    chosen: dict = {}

    def accept() -> None:
        try:
            chosen["active"] = _parse_price(active.text())
        except ValueError:
            error.setText("Aktivasyon fiyatı sayı olmalı")
            return
        chosen["callback"] = float(amount.value())
        chosen["mode"] = str(mode.currentData())
        dialog.accept()

    def drop() -> None:
        chosen.update({"callback": 0.0, "mode": "ratio", "active": None})
        dialog.accept()

    save.clicked.connect(accept)
    remove.clicked.connect(drop)
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(save)
    buttons.addWidget(remove)
    buttons.addWidget(cancel)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return chosen


def _icon_level() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawLine(3, 10, 17, 10)

    return _tool_icon(draw)


def _icon_range() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawRect(3, 4, 14, 12)
        painter.drawLine(10, 14, 10, 6)
        painter.drawLine(10, 6, 8, 8)
        painter.drawLine(10, 6, 12, 8)

    return _tool_icon(draw)


def _icon_trash() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawLine(4, 5, 16, 5)
        painter.drawLine(8, 3, 12, 3)
        painter.drawRect(5, 6, 10, 11)
        painter.drawLine(8, 8, 8, 14)
        painter.drawLine(12, 8, 12, 14)

    return _tool_icon(draw)


def _icon_panel() -> QIcon:
    def draw(painter: QPainter) -> None:
        painter.drawRect(3, 3, 14, 14)
        painter.drawLine(8, 3, 8, 17)

    return _tool_icon(draw)


def _axis_unit(span_ms: float, age_ms: float) -> str:
    if span_ms >= 400 * _DAY_MS or age_ms >= 730 * _DAY_MS:
        return "year"
    if span_ms >= 45 * _DAY_MS or age_ms >= 60 * _DAY_MS:
        return "month"
    if span_ms >= 36 * 3_600_000 or age_ms >= 2 * _DAY_MS:
        return "day"
    return "hour"


def _mark_label(moment: datetime, unit: str, latest: datetime) -> str:
    if unit == "day":
        text = f"{moment.day} {_SHORT_MONTHS[moment.month]}"
        if moment.year != latest.year:
            return f"{text} {moment.year}"
        return text
    if unit == "month":
        name = _MONTHS[moment.month]
        if moment.year != latest.year:
            return f"{name} {moment.year}"
        return name
    if unit == "year":
        return str(moment.year)
    return moment.strftime("%H:%M")


def _period_key(moment: datetime, unit: str):
    if unit == "day":
        return (moment.year, moment.month, moment.day)
    if unit == "month":
        return (moment.year, moment.month)
    return (moment.year,)


def _candle_marks(
    candles,
    start_ms: int,
    end_ms: int,
    interval: str,
    count: int | None = 5,
) -> list[tuple[str, float]]:
    times = [bar.time_ms for bar in candles if start_ms <= bar.time_ms <= end_ms]
    latest_ms = candles[-1].time_ms if candles else int(end_ms)
    if len(candles) >= 2:
        step = candles[-1].time_ms - candles[-2].time_ms
        ahead = candles[-1].time_ms + step
        cap = candles[-1].time_ms + step * (FUTURE_BARS + 1)
        while step > 0 and ahead <= end_ms and ahead <= cap:
            times.append(ahead)
            ahead += step
    if not times:
        times = [bar.time_ms for bar in candles]
        count = 5
    latest = datetime.fromtimestamp(latest_ms / 1000)
    unit = _axis_unit(max(end_ms - start_ms, 0), max(latest_ms - end_ms, 0))
    if unit == "hour":
        chosen = list(times) if count is None or len(times) <= count else _even_pick(times, count)
        return [(datetime.fromtimestamp(time_ms / 1000).strftime("%H:%M"), float(time_ms)) for time_ms in chosen]
    grouped: list[tuple[str, float]] = []
    seen = set()
    for time_ms in times:
        moment = datetime.fromtimestamp(time_ms / 1000)
        key = _period_key(moment, unit)
        if key in seen:
            continue
        seen.add(key)
        grouped.append((_mark_label(moment, unit, latest), float(time_ms)))
    if count is not None and len(grouped) > count:
        grouped = _even_pick(grouped, count)
    return grouped


_MONTHS = (
    "",
    "Ocak",
    "Şubat",
    "Mart",
    "Nisan",
    "Mayıs",
    "Haziran",
    "Temmuz",
    "Ağustos",
    "Eylül",
    "Ekim",
    "Kasım",
    "Aralık",
)


def _hover_day(moment: datetime, with_year: bool) -> str:
    text = f"{moment.day} {_MONTHS[moment.month]}"
    if with_year:
        return f"{text} {moment.year}"
    return text


def _merge_bars(older, newer):
    by_time = {bar.time_ms: bar for bar in older}
    for bar in newer:
        by_time[bar.time_ms] = bar
    return [by_time[key] for key in sorted(by_time)]


def _nearest_bar(candles, time_ms: int):
    if not candles:
        return None
    step = candles[-1].time_ms - candles[-2].time_ms if len(candles) > 1 else 15 * 60 * 1000
    best = min(candles, key=lambda bar: abs(bar.time_ms - time_ms))
    if abs(best.time_ms - time_ms) > max(step, 1):
        return None
    return best


def _readable_bars(width: float) -> int:
    if width < 120:
        return 90
    return max(48, min(160, int(width / 9)))


def _label_budget(width: float, zoom: float, visible: int) -> int | None:
    slots = max(5, int(width / 76)) if width > 80 else 5
    if 0 < visible <= slots:
        return None
    count = max(5, round(5 * max(zoom, 1.0)))
    count = min(count, slots, visible or count)
    if visible and count >= visible:
        return None
    return count


def _show_candle_marks(
    view: ChartView,
    candles,
    start_ms: int,
    end_ms: int,
    interval: str,
    count: int | None = 5,
) -> None:
    view.set_marks(_candle_marks(candles, start_ms, end_ms, interval, count))


def _pane_sizes(value) -> list[int] | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        sizes = [int(item) for item in value]
    except (TypeError, ValueError):
        return None
    if any(item <= 0 for item in sizes):
        return None
    return sizes


def _tone(value: float | None) -> str:
    if value is None or value == 0:
        return "flat"
    return "up" if value > 0 else "down"


def _color(tone: str) -> str:
    return {"up": "#26a69a", "down": "#ef5350", "flat": "#fafafa"}[tone]


def _tray_icon() -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#c084fc"))
    painter.drawEllipse(4, 4, 24, 24)
    painter.end()
    return QIcon(pixmap)


class SnapshotWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        symbol: str,
        interval: str,
        with_chart: bool = True,
        enabled: set[str] | None = None,
        episodes: dict | None = None,
    ) -> None:
        super().__init__()
        self._symbol = symbol
        self._interval = interval
        self._with_chart = with_chart
        self._enabled = None if enabled is None else set(enabled)
        self._episodes = {
            label: dict(episode) if isinstance(episode, dict) else episode
            for label, episode in (episodes or {}).items()
        }

    def run(self) -> None:
        try:
            snapshot = load_snapshot(
                self._symbol,
                self._interval,
                with_chart=self._with_chart,
                enabled=self._enabled,
            )
            touches = _episode_touch(self._symbol, self._episodes) if self._episodes else {}
            self.done.emit(replace(snapshot, touches=touches))
        except MarketError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or "güncelleme başarısız")


class HistoryWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, symbol: str, interval: str, before_ms: int) -> None:
        super().__init__()
        self._symbol = symbol
        self._interval = interval
        self._before_ms = before_ms

    def run(self) -> None:
        try:
            self.done.emit(fetch_older_klines(self._symbol, self._interval, self._before_ms, CHART_PAGE))
        except MarketError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or "eski mumlar alınamadı")


class QuoteWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.done.emit(load_quotes())
        except MarketError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or "liste alınamadı")


class _IndexQuoteWorker(QThread):
    done = Signal(object)

    def run(self) -> None:
        try:
            self.done.emit(load_index_quotes())
        except Exception:
            self.done.emit([])


class _IndexChartWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, symbol: str, interval: str) -> None:
        super().__init__()
        self._symbol = symbol
        self._interval = interval

    def run(self) -> None:
        try:
            self.done.emit(load_index_chart(self._symbol, self._interval))
        except IndexDataError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Endeks alınamadı")


class _TimeGuides(QGraphicsItem):
    """Vertical hour lines drawn under the candles, same layer as the price grid."""

    def __init__(self, chart: QChart) -> None:
        super().__init__(chart)
        self.setZValue(2.5)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._xs: list[float] = []
        self._plot = QRectF()

    def boundingRect(self) -> QRectF:
        return self._plot

    def set_lines(self, xs: list[float], plot: QRectF) -> None:
        self.prepareGeometryChange()
        self._xs = list(xs)
        self._plot = QRectF(plot)
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if not self._xs or self._plot.width() <= 0:
            return
        painter.save()
        painter.setClipRect(self._plot)
        painter.setPen(QColor("#3f3f46"))
        top = self._plot.top()
        bottom = self._plot.bottom()
        for x in self._xs:
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
        painter.restore()


class _HGrid(QGraphicsItem):
    """Horizontal rules locked to the plot rectangle, not to the price ticks."""

    def __init__(self, chart: QChart) -> None:
        super().__init__(chart)
        self.setZValue(2.2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._plot = QRectF()

    def boundingRect(self) -> QRectF:
        return self._plot

    def set_plot(self, plot: QRectF) -> None:
        rect = QRectF(plot)
        if rect == self._plot:
            return
        self.prepareGeometryChange()
        self._plot = rect
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        plot = self._plot
        if plot.width() <= 0 or plot.height() <= 0:
            return
        painter.save()
        painter.setClipRect(plot)
        painter.setPen(QColor("#27272a"))
        for slot in range(1, 5):
            y = plot.top() + plot.height() * slot / 5
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.restore()


class ChartView(QChartView):
    def __init__(
        self,
        chart: QChart,
        on_reset,
        axis_x,
        axis_y,
        interval_of,
        on_x_changed,
        on_hover=None,
        fixed_grid: bool = False,
        crosshair: bool = False,
        on_alert=None,
        on_alert_remove=None,
        on_alert_move=None,
        on_drawing=None,
        on_drawing_move=None,
        on_drawing_remove=None,
    ) -> None:
        super().__init__(chart)
        self._on_reset = on_reset
        self._axis_x = axis_x
        self._axis_y = axis_y
        self._interval_of = interval_of
        self._on_x_changed = on_x_changed
        self._on_hover = on_hover
        self._crosshair = crosshair
        self._on_alert = on_alert
        self._on_alert_remove = on_alert_remove
        self._on_alert_move = on_alert_move
        self._on_drawing = on_drawing
        self._on_drawing_move = on_drawing_move
        self._on_drawing_remove = on_drawing_remove
        self._tool: str | None = None
        self._stamp_key: tuple | None = None
        self._stamp_rows: list[tuple[float, object]] = []
        self._stamp_host = None
        self._position_lines: list[tuple[float, str, QColor]] = []
        self._drawings: list[dict] = []
        self._selected: int | None = None
        self._draft_anchor: tuple[float, float] | None = None
        self._draft_cursor: tuple[float, float] | None = None
        self._edit: tuple[int, int] | None = None
        self._edit_value: tuple[float, float] | None = None
        self._level_drag: int | None = None
        self._level_price: float | None = None
        self._level_press_y = 0.0
        self._signals: list[dict] = []
        self._signal_score = ""
        self._bands: list[tuple[float, float | None, float | None, float | None]] = []
        self._cross_pos: QPointF | None = None
        self._alarm_source: list[float] = []
        self._alarm_levels: list[float] = []
        self._drag_alarm: float | None = None
        self._alarm_drag_price: float | None = None
        self._alarm_press_y = 0.0
        self.pushed_older = False
        self._bar_step = 0
        self._fitting = False
        self._zoomed = False
        self._price_manual = False
        self._home_x: tuple[int, int] | None = None
        self._home_y: tuple[float, float] | None = None
        self._pan_y: tuple[float, float] | None = None
        self._drag_y: tuple[float, float, float] | None = None
        self._drag_x: tuple[float, float, float, float, float] | None = None
        self._drag_pan: tuple[float, float, float] | None = None
        self._drag_move: tuple[float, float, float, float, float, float] | None = None
        self._press_pos = None
        self._marks: list[tuple[str, float]] = []
        self._last_price: tuple[float, float, bool] | None = None
        self._guides = _TimeGuides(chart)
        self._hgrid = _HGrid(chart) if fixed_grid else None
        chart.plotAreaChanged.connect(self._place_guides)
        if self._hgrid is not None:
            chart.plotAreaChanged.connect(self._place_hgrid)
        self.setObjectName("panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRubberBand(QChartView.RubberBand.NoRubberBand)
        self.setBackgroundBrush(QColor("#18181b"))
        self.setViewportMargins(1, 1, 1, 1)
        self.setMouseTracking(True)

    def set_home(self, start_ms: int, end_ms: int, low: float, high: float) -> None:
        self._home_x = (start_ms, end_ms)
        self._home_y = (low, high)
        span = high - low
        mid = abs((high + low) / 2)
        room = max(span * PRICE_PAN, mid * 0.25) if span > 0 else max(mid * 0.25, 1.0)
        self._pan_y = (low - room, high + room)

    def set_bar_step(self, step: int) -> None:
        self._bar_step = max(int(step), 0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._zoomed or self._fitting:
            self._place_guides()
            return
        self._fitting = True
        try:
            self._on_reset()
        finally:
            self._fitting = False

    def _span_cap(self) -> float | None:
        if self._bar_step <= 0:
            return None
        width = self.chart().plotArea().width()
        if width < 120:
            width = max(float(self.width()) - 96, 120)
        return float(self._bar_step * _readable_bars(width))

    def _ceiling_span(self) -> float | None:
        cap = self._span_cap()
        if self._home_x is None:
            return cap
        home = float(self._home_x[1] - self._home_x[0])
        if cap is None:
            return home
        return min(home, cap)

    def set_marks(self, marks: list[tuple[str, float]]) -> None:
        self._marks = marks
        self._place_guides()
        self.viewport().update()

    def _place_guides(self, *_args) -> None:
        chart = self.chart()
        plot = chart.plotArea()
        series = chart.series()
        xs: list[float] = []
        if series and plot.width() > 0:
            drawn = series[0]
            for _label, at in self._marks:
                x = chart.mapToPosition(QPointF(float(at), 0.0), drawn).x()
                if plot.left() - 1 <= x <= plot.right() + 1:
                    xs.append(x)
        self._guides.set_lines(xs, plot)

    def _place_hgrid(self, *_args) -> None:
        if self._hgrid is not None:
            self._hgrid.set_plot(self.chart().plotArea())

    def set_last_price(self, time_ms: float, price: float, rising: bool) -> None:
        self._last_price = (float(time_ms), float(price), rising)
        self.viewport().update()

    def clear_last_price(self) -> None:
        self._last_price = None
        self.viewport().update()

    def set_alarms(self, levels: list[float]) -> None:
        self._drag_alarm = None
        self._alarm_drag_price = None
        self._alarm_source = [float(level) for level in levels]
        self._alarm_levels = list(self._alarm_source)
        self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.chart().series():
            return
        painter = QPainter(self.viewport())
        painter.setFont(QFont("Segoe UI", 8))
        plot = self.chart().plotArea()
        series = self.chart().series()[0]
        for label, at in self._marks:
            x = self.chart().mapToPosition(QPointF(at, 0), series).x()
            if x < plot.left() - 1 or x > plot.right() + 1:
                continue
            painter.setPen(MUTED)
            width = 76.0
            left = min(max(x - width / 2, 0.0), max(self.width() - width, 0.0))
            painter.drawText(
                QRectF(left, plot.bottom() + 4, width, 16),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                label,
            )
        self._paint_alarms(painter, plot, series)
        self._paint_positions(painter, plot, series)
        self._paint_last_price(painter, plot, series)
        self._paint_bands(painter, plot, series)
        self._paint_drawings(painter, plot, series)
        self._paint_signals(painter, plot, series)
        self._paint_crosshair(painter, plot, series)
        painter.end()

    def _paint_alarms(self, painter: QPainter, plot: QRectF, series) -> None:
        if not self._alarm_levels or plot.width() <= 0:
            return
        painter.save()
        for level in self._alarm_levels:
            point = self.chart().mapToPosition(QPointF(0.0, float(level)), series)
            if point.y() < plot.top() - 1 or point.y() > plot.bottom() + 1:
                continue
            pen = QPen(ALARM)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([6, 4])
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), point.y()), QPointF(plot.right(), point.y()))
            width = max(self.viewport().width() - plot.right() - 8, 56)
            rect = QRectF(plot.right() + 4, point.y() - 9, width, 18)
            painter.fillRect(rect, ALARM)
            painter.setPen(QColor("#0b0b0d"))
            painter.setFont(QFont("Consolas", 8))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_price(level))
        painter.restore()

    def set_positions(self, lines: list[tuple[float, str, QColor]]) -> None:
        if lines == self._position_lines:
            return
        self._position_lines = list(lines)
        self.viewport().update()

    def _paint_positions(self, painter: QPainter, plot: QRectF, series) -> None:
        if not self._position_lines or plot.width() <= 0:
            return
        painter.save()
        painter.setFont(QFont("Consolas", 8))
        width = max(self.viewport().width() - plot.right() - 8, 56)
        for level, label, color in self._position_lines:
            point = self.chart().mapToPosition(QPointF(0.0, float(level)), series)
            if point.y() < plot.top() - 1 or point.y() > plot.bottom() + 1:
                continue
            order = label in ("TP", "SL")
            pen = QPen(color, 1.2 if order else 1.0)
            pen.setCosmetic(True)
            pen.setStyle(Qt.PenStyle.CustomDashLine)
            pen.setDashPattern([6, 4] if order else [2, 3])
            painter.setPen(pen)
            painter.drawLine(QPointF(plot.left(), point.y()), QPointF(plot.right(), point.y()))
            text_width = painter.fontMetrics().horizontalAdvance(label) + 10
            tag = QRectF(plot.right() - text_width - 4, point.y() - 16, text_width, 14)
            painter.fillRect(tag, color)
            painter.setPen(QColor("#0b0b0d"))
            painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, label)
            rect = QRectF(plot.right() + 4, point.y() - 9, width, 18)
            painter.fillRect(rect, color)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_price(level))
        painter.restore()

    def _paint_crosshair(self, painter: QPainter, plot: QRectF, series) -> None:
        pos = self._cross_pos
        if not self._crosshair or pos is None or plot.width() <= 0 or not plot.contains(pos):
            return
        value = self.chart().mapToValue(pos, series)
        price = float(value.y())
        painter.save()
        pen = QPen(QColor("#d4d4d8"))
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([3, 3])
        painter.setPen(pen)
        painter.drawLine(QPointF(pos.x(), plot.top()), QPointF(pos.x(), plot.bottom()))
        painter.drawLine(QPointF(plot.left(), pos.y()), QPointF(plot.right(), pos.y()))
        tag_width = max(self.viewport().width() - plot.right() - 8, 56)
        tag = QRectF(plot.right() + 4, pos.y() - 9, tag_width, 18)
        painter.fillRect(tag, QColor("#3f3f46"))
        painter.setPen(QColor("#fafafa"))
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, format_price(price))
        moment = datetime.fromtimestamp(max(float(value.x()), 0) / 1000)
        stamp = _cross_stamp(moment)
        width = 118.0
        left = min(max(pos.x() - width / 2, 0.0), max(self.width() - width, 0.0))
        rect = QRectF(left, plot.bottom() + 2, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#27272a"))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#fafafa"))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, stamp)
        painter.restore()

    def set_bands(self, points) -> None:
        self._bands = list(points)
        self._band_times = [point[0] for point in self._bands]
        self.viewport().update()

    def _visible_bands(self, plot: QRectF, series) -> tuple[list, object] | None:
        """Görünen aralıktaki bant noktaları ve doğrusal değer→piksel eşlemesi."""
        chart = self.chart()
        top_left = chart.mapToValue(plot.topLeft(), series)
        bottom_right = chart.mapToValue(plot.bottomRight(), series)
        span_x = float(bottom_right.x()) - float(top_left.x())
        span_y = float(bottom_right.y()) - float(top_left.y())
        if span_x == 0 or span_y == 0:
            return None
        start_x = float(top_left.x())
        start_y = float(top_left.y())
        scale_x = plot.width() / span_x
        scale_y = plot.height() / span_y
        left = plot.left()
        top = plot.top()
        times = getattr(self, "_band_times", None)
        if times is None or len(times) != len(self._bands):
            times = self._band_times = [point[0] for point in self._bands]
        low_time = min(start_x, float(bottom_right.x()))
        high_time = max(start_x, float(bottom_right.x()))
        first = max(bisect.bisect_left(times, low_time) - 1, 0)
        last = min(bisect.bisect_right(times, high_time) + 1, len(times))

        def at(time_ms: float, value: float) -> QPointF:
            return QPointF(left + (time_ms - start_x) * scale_x, top + (value - start_y) * scale_y)

        return self._bands[first:last], at

    def _paint_bands(self, painter: QPainter, plot: QRectF, series) -> None:
        if not self._bands or plot.width() <= 0:
            return
        visible = self._visible_bands(plot, series)
        if visible is None:
            return
        points, at = visible
        painter.save()
        painter.setClipRect(plot)
        fill = QColor(33, 150, 243, 13)
        uppers: list[QPointF] = []
        lowers: list[QPointF] = []

        def flush_fill() -> None:
            if len(uppers) >= 2:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(fill)
                painter.drawPolygon(QPolygonF(uppers + lowers[::-1]))
            uppers.clear()
            lowers.clear()

        for time_ms, _basis, upper, lower in points:
            if upper is None or lower is None:
                flush_fill()
                continue
            uppers.append(at(time_ms, upper))
            lowers.append(at(time_ms, lower))
        flush_fill()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._stroke_band(painter, points, at, 1, "#2962FF")
        self._stroke_band(painter, points, at, 2, "#F23645")
        self._stroke_band(painter, points, at, 3, "#089981")
        painter.restore()

    def _stroke_band(self, painter: QPainter, points, at, slot: int, color: str) -> None:
        pen = QPen(QColor(color), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        run: list[QPointF] = []

        def flush() -> None:
            if len(run) >= 2:
                painter.drawPolyline(QPolygonF(run))
            run.clear()

        for point in points:
            value = point[slot]
            if value is None:
                flush()
                continue
            run.append(at(point[0], value))
        flush()

    def set_drawings(self, items: list[dict], selected: int | None = None) -> None:
        self._drawings = [dict(item) for item in items]
        if selected is not None:
            self._selected = selected if 0 <= selected < len(self._drawings) else None
        elif self._selected is not None and self._selected >= len(self._drawings):
            self._selected = None
        self.viewport().update()

    def set_signals(self, marks: list[dict], score: str) -> None:
        self._signals = list(marks)
        self._signal_score = score
        self.viewport().update()

    def set_draw_tool(self, tool: str | None) -> None:
        self._tool = tool if tool in ("trend", "range", "level") else None
        self._draft_anchor = None
        self._draft_cursor = None
        self.unsetCursor()
        self.viewport().update()

    def _value_at(self, pos: QPointF) -> tuple[float, float] | None:
        if not self.chart().series():
            return None
        value = self.chart().mapToValue(pos, self.chart().series()[0])
        price = float(value.y())
        moment = float(value.x())
        if price <= 0 or not math.isfinite(price) or not math.isfinite(moment):
            return None
        return moment, price

    def _shown(self, index: int) -> dict:
        item = dict(self._drawings[index])
        if item.get("kind") == "level" and self._level_drag == index and self._level_price is not None:
            item["price"] = self._level_price
        if self._edit is not None and self._edit[0] == index and self._edit_value is not None:
            if self._edit[1] == 0:
                item["t1"], item["p1"] = self._edit_value
            else:
                item["t2"], item["p2"] = self._edit_value
        return item

    def _candle_span(self) -> tuple[float, float] | None:
        first = None
        last = None
        for series in self.chart().series():
            if not isinstance(series, QCandlestickSeries):
                continue
            for candle in series.sets():
                stamp = float(candle.timestamp())
                first = stamp if first is None else min(first, stamp)
                last = stamp if last is None else max(last, stamp)
        if first is None or last is None:
            return None
        return first, last + self._bar_ms() * FUTURE_BARS

    def _level_price_of(self, index: int, item: dict) -> float:
        if self._level_drag == index and self._level_price is not None:
            return self._level_price
        return float(item.get("price") or 0)

    def _screen_pair(self, item: dict, series) -> tuple[QPointF, QPointF]:
        if item.get("kind") == "level":
            price = float(item.get("price") or item.get("p1") or 0)
            span = self._candle_span()
            if span is None:
                point = self.chart().mapToPosition(QPointF(0.0, price), series)
                return point, point
            start = self.chart().mapToPosition(QPointF(span[0], price), series)
            end = self.chart().mapToPosition(QPointF(span[1], price), series)
            return start, end
        start = self.chart().mapToPosition(QPointF(item["t1"], item["p1"]), series)
        end = self.chart().mapToPosition(QPointF(item["t2"], item["p2"]), series)
        return start, end

    def _delete_rect(self, index: int, series) -> QRectF:
        start, end = self._screen_pair(self._shown(index), series)
        right = max(start.x(), end.x())
        top = min(start.y(), end.y())
        return QRectF(right + 6, top - 24, 16, 16)

    def _handle_at(self, pos: QPointF) -> tuple[int, int] | None:
        if not self._drawings or not self.chart().series():
            return None
        series = self.chart().series()[0]
        best = None
        best_distance = 9.0
        order = list(range(len(self._drawings)))
        if self._selected is not None and self._selected in order:
            order.remove(self._selected)
            order.insert(0, self._selected)
        for index in order:
            if self._drawings[index].get("kind") == "level":
                continue
            start, end = self._screen_pair(self._shown(index), series)
            for end_index, point in ((0, start), (1, end)):
                distance = math.hypot(pos.x() - point.x(), pos.y() - point.y())
                if distance <= best_distance:
                    best_distance = distance
                    best = (index, end_index)
        return best

    def _body_at(self, pos: QPointF) -> int | None:
        if not self._drawings or not self.chart().series():
            return None
        series = self.chart().series()[0]
        for index in range(len(self._drawings) - 1, -1, -1):
            item = self._shown(index)
            start, end = self._screen_pair(item, series)
            if item["kind"] == "level":
                if self._level_near(pos) == index:
                    return index
                continue
            if item["kind"] == "trend":
                if _segment_distance(pos, start, end) <= 8:
                    return index
            rect = QRectF(start, end).normalized().adjusted(-4, -4, 4, 4)
            if item["kind"] == "range" and rect.contains(pos):
                return index
        return None

    def _on_plot(self, pos: QPointF) -> bool:
        plot = self.chart().plotArea()
        return plot.contains(pos) and not self._over_price_axis(pos) and not self._over_time_axis(pos)

    def _begin_drawing(self, pos: QPointF) -> bool:
        if not self.chart().series():
            return False
        if self._selected is not None and self._delete_rect(self._selected, self.chart().series()[0]).contains(pos):
            index = self._selected
            self._selected = None
            if self._on_drawing_remove is not None:
                self._on_drawing_remove(index)
            return True
        handle = self._handle_at(pos)
        if handle is not None:
            self._selected = handle[0]
            item = self._drawings[handle[0]]
            self._edit = handle
            self._edit_value = (item["t1"], item["p1"]) if handle[1] == 0 else (item["t2"], item["p2"])
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return True
        body = self._body_at(pos)
        if body is not None:
            self._selected = body
            self.viewport().update()
            return True
        if self._selected is not None:
            self._selected = None
            self.viewport().update()
        if self._tool is None or not self._on_plot(pos):
            return False
        value = self._value_at(pos)
        if value is None:
            return False
        self._draft_anchor = value
        self._draft_cursor = value
        self.setCursor(Qt.CursorShape.CrossCursor)
        return True

    def _bar_ms(self) -> int:
        if self._bar_step > 0:
            return self._bar_step
        return {
            "15m": 900_000,
            "1h": 3_600_000,
            "4h": 14_400_000,
            "1d": 86_400_000,
            "1w": 7 * 86_400_000,
        }.get(self._interval_of(), 900_000)

    def _paint_handle(self, painter: QPainter, point: QPointF, selected: bool) -> None:
        radius = 6.0 if selected else 5.0
        painter.setPen(QPen(TREND, 1.6))
        painter.setBrush(CHART_PLOT)
        painter.drawEllipse(point, radius, radius)

    def _paint_arrow(self, painter: QPainter, start: QPointF, end: QPointF) -> None:
        painter.drawLine(start, end)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        length = 8.0
        left = QPointF(
            end.x() - length * math.cos(angle - 0.45),
            end.y() - length * math.sin(angle - 0.45),
        )
        right = QPointF(
            end.x() - length * math.cos(angle + 0.45),
            end.y() - length * math.sin(angle + 0.45),
        )
        painter.drawLine(end, left)
        painter.drawLine(end, right)

    def _paint_drawings(self, painter: QPainter, plot: QRectF, series) -> None:
        if plot.width() <= 0:
            return
        items: list[tuple[int | None, dict]] = [
            (index, self._shown(index)) for index in range(len(self._drawings))
        ]
        if self._draft_anchor is not None and self._draft_cursor is not None and self._tool:
            items.append(
                (
                    None,
                    {
                        "kind": self._tool,
                        "t1": self._draft_anchor[0],
                        "p1": self._draft_anchor[1],
                        "t2": self._draft_cursor[0],
                        "p2": self._draft_cursor[1],
                    },
                )
            )
        if not items:
            return
        painter.save()
        painter.setClipRect(plot.adjusted(-12, -28, 36, 24))
        for index, item in items:
            start, end = self._screen_pair(item, series)
            selected = index is not None and index == self._selected
            if item["kind"] == "level":
                price = float(item.get("price") or item.get("p1") or 0)
                self._paint_level(painter, plot, start, end, price)
                continue
            if item["kind"] == "range":
                self._paint_range(painter, plot, item, start, end)
            else:
                pen = QPen(TREND, 1.8)
                pen.setCosmetic(True)
                pen.setStyle(Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawLine(start, end)
            self._paint_handle(painter, start, selected)
            self._paint_handle(painter, end, selected)
            if selected:
                self._paint_delete(painter, self._delete_rect(index, series))
        painter.restore()

    def _paint_level(
        self,
        painter: QPainter,
        plot: QRectF,
        start: QPointF,
        end: QPointF,
        price: float,
    ) -> None:
        if price <= 0:
            return
        y = start.y()
        if y < plot.top() - 1 or y > plot.bottom() + 1:
            return
        pen = QPen(TREND, 1.6)
        pen.setCosmetic(True)
        painter.setPen(pen)
        left = min(start.x(), end.x())
        right = max(start.x(), end.x())
        painter.drawLine(QPointF(max(left, plot.left()), y), QPointF(min(right, plot.right()), y))
        text = format_price(price)
        painter.setFont(QFont("Consolas", 8))
        width = painter.fontMetrics().horizontalAdvance(text) + 12
        rect = QRectF(min(right, plot.right()) - width, y - 18, width, 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#e0f2fe"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_signals(self, painter: QPainter, plot: QRectF, series) -> None:
        if plot.width() <= 0:
            return
        painter.save()
        if self._signal_score:
            painter.setFont(QFont("Segoe UI", 9))
            text = self._signal_score
            width = painter.fontMetrics().horizontalAdvance(text) + 16
            rect = QRectF(plot.left() + 8, plot.bottom() - 28, width, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(24, 24, 27, 230))
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(QColor("#e4e4e7"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if self._signals:
            painter.setClipRect(plot)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            step = self._bar_ms()
            key = tuple(
                (id(item), item.count())
                for item in self.chart().series()
                if isinstance(item, QCandlestickSeries)
            )
            if key != self._stamp_key:
                rows = []
                host = series
                for item in self.chart().series():
                    if isinstance(item, QCandlestickSeries) and item.sets():
                        host = item
                        rows.extend((float(candle.timestamp()), candle) for candle in item.sets())
                rows.sort(key=lambda row: row[0])
                self._stamp_rows = rows
                self._stamp_host = host
                self._stamp_key = key
            stamps = [stamp for stamp, _candle in self._stamp_rows]
            host = self._stamp_host or series
            buckets: dict[float, list[str]] = {}
            for mark in self._signals:
                try:
                    moment = float(mark["time_ms"])
                except (KeyError, TypeError, ValueError):
                    continue
                held = _candle_holding_sorted(stamps, moment, step)
                if held is None:
                    continue
                buckets.setdefault(held, []).append("buy" if mark.get("side") == "buy" else "sell")
            for stamp, sides in buckets.items():
                index = bisect.bisect_left(stamps, stamp)
                if index >= len(self._stamp_rows) or self._stamp_rows[index][0] != stamp:
                    continue
                candle = self._stamp_rows[index][1]
                for outward in (True, False):
                    placed = [name for name in sides if (name == "buy") == outward]
                    for offset, _name in enumerate(placed):
                        anchor = float(candle.low()) if outward else float(candle.high())
                        point = self.chart().mapToPosition(QPointF(stamp, anchor), host)
                        if point.x() < plot.left() - 2 or point.x() > plot.right() + 2:
                            continue
                        self._paint_signal_mark(painter, plot, point.x(), point.y(), outward, offset)
        painter.restore()

    def _paint_signal_mark(
        self,
        painter: QPainter,
        plot: QRectF,
        x: float,
        anchor_y: float,
        buy: bool,
        offset: int,
    ) -> None:
        label = "BUY" if buy else "SELL"
        color = QColor("#26a69a") if buy else QColor("#ef5350")
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        pill_w = painter.fontMetrics().horizontalAdvance(label) + 16
        pill_h = 18.0
        tri = 8.0
        pitch = pill_h + tri + 6
        if buy:
            tip_y = anchor_y + 5 + offset * pitch
            base_y = tip_y + tri
            pill_top = base_y - 1
        else:
            tip_y = anchor_y - 5 - offset * pitch
            base_y = tip_y - tri
            pill_top = base_y - pill_h + 1
        top = min(tip_y, pill_top)
        bottom = max(tip_y, pill_top + pill_h)
        if bottom < plot.top() or top > plot.bottom():
            return
        left = x - pill_w / 2
        path = QPainterPath()
        path.moveTo(x, tip_y)
        path.lineTo(x - 6, base_y)
        path.lineTo(x + 6, base_y)
        path.closeSubpath()
        painter.setPen(QPen(QColor("#0b0b0d"), 1))
        painter.setBrush(color)
        painter.drawPath(path)
        rect = QRectF(left, pill_top, pill_w, pill_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#fafafa"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_range(
        self,
        painter: QPainter,
        plot: QRectF,
        item: dict,
        start: QPointF,
        end: QPointF,
    ) -> None:
        rect = QRectF(start, end).normalized()
        fill = QColor("#2563eb")
        fill.setAlpha(48)
        painter.fillRect(rect, fill)
        pen = QPen(TREND, 1.4)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        low_y = max(start.y(), end.y())
        high_y = min(start.y(), end.y())
        center_x = (start.x() + end.x()) / 2
        rising = item["p2"] >= item["p1"]
        arrow_start = QPointF(center_x, low_y if rising else high_y)
        arrow_end = QPointF(center_x, high_y if rising else low_y)
        if abs(arrow_end.y() - arrow_start.y()) > 10:
            self._paint_arrow(painter, arrow_start, arrow_end)
        delta = abs(item["p2"] - item["p1"])
        percent = 0.0 if item["p1"] == 0 else abs(item["p2"] - item["p1"]) / abs(item["p1"]) * 100
        bars = int(round(abs(item["t2"] - item["t1"]) / self._bar_ms()))
        text = f"{format_price(delta)} ({percent:.2f}%) {bars}"
        painter.setFont(QFont("Consolas", 8))
        width = max(painter.fontMetrics().horizontalAdvance(text) + 16, 120)
        label_y = rect.top() - 22
        if label_y < plot.top():
            label_y = min(rect.top() + 4, plot.bottom() - 20)
        left = min(max(rect.center().x() - width / 2, plot.left()), max(plot.right() - width, plot.left()))
        label = QRectF(left, label_y, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(label, 3, 3)
        painter.setPen(QColor("#e0f2fe"))
        painter.drawText(label, Qt.AlignmentFlag.AlignCenter, text)
        painter.setFont(QFont("Segoe UI", 8))
        self._paint_time_pill(painter, plot, start.x(), item["t1"])
        self._paint_time_pill(painter, plot, end.x(), item["t2"])

    def _paint_time_pill(self, painter: QPainter, plot: QRectF, x: float, time_ms: float) -> None:
        moment = datetime.fromtimestamp(max(time_ms, 0) / 1000)
        text = _range_stamp(moment)
        width = painter.fontMetrics().horizontalAdvance(text) + 14
        left = min(max(x - width / 2, 0.0), max(self.width() - width, 0.0))
        rect = QRectF(left, plot.bottom() + 2, width, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#2563eb"))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QColor("#fafafa"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_delete(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#7f1d1d"))
        painter.drawEllipse(rect)
        painter.setPen(QPen(QColor("#fee2e2"), 1.5))
        painter.drawLine(rect.topLeft() + QPointF(4, 4), rect.bottomRight() - QPointF(4, 4))
        painter.drawLine(rect.topRight() + QPointF(-4, 4), rect.bottomLeft() + QPointF(4, -4))

    def _paint_last_price(self, painter: QPainter, plot: QRectF, series) -> None:
        if self._last_price is None or plot.width() <= 0:
            return
        time_ms, price, rising = self._last_price
        point = self.chart().mapToPosition(QPointF(time_ms, price), series)
        if point.y() < plot.top() - 1 or point.y() > plot.bottom() + 1:
            return
        color = UP if rising else DOWN
        pen = QPen(color)
        pen.setCosmetic(True)
        pen.setStyle(Qt.PenStyle.CustomDashLine)
        pen.setDashPattern([4, 3])
        painter.setPen(pen)
        start = min(max(point.x(), plot.left()), plot.right())
        painter.drawLine(QPointF(start, point.y()), QPointF(plot.right(), point.y()))
        width = max(self.viewport().width() - plot.right() - 8, 56)
        rect = QRectF(plot.right() + 4, point.y() - 9, width, 18)
        painter.fillRect(rect, color)
        painter.setPen(QColor("#0b0b0d"))
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, format_price(price))

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            return
        pos = event.position()
        on_price = self._over_price_axis(pos)
        on_time = self._over_time_axis(pos)
        self._zoomed = True
        if on_price and not on_time:
            self._price_manual = True
            self._zoom_y(pos.y(), delta)
        elif on_time and not on_price:
            self._zoom_x(pos.x(), delta)
            self._on_x_changed()
        else:
            self._price_manual = True
            self._zoom_y(pos.y(), delta)
            self._zoom_x(pos.x(), delta)
            self._on_x_changed()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.RightButton and self._level_menu(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self._alarm_menu(event):
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._grab_level(event.position()):
            self._press_pos = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._begin_drawing(event.position()):
            self._press_pos = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._grab_alarm(event.position()):
            self._press_pos = None
            event.accept()
            return
        self._press_pos = event.position()
        if event.button() == Qt.MouseButton.LeftButton and self._over_price_axis(event.position()):
            self._drag_y = (event.position().y(), self._axis_y.min(), self._axis_y.max())
            self._price_manual = True
            self._zoomed = True
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._over_time_axis(event.position()):
            start, end, _datetime_axis = self._read_x()
            self._drag_x = (
                event.position().x(),
                event.position().y(),
                start,
                end,
                self._x_ratio(event.position().x()),
            )
            self._zoomed = True
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._edit is not None or self._draft_anchor is not None:
            value = self._value_at(event.position())
            if value is not None:
                if self._edit is not None:
                    self._edit_value = value
                else:
                    self._draft_cursor = value
                if self._crosshair:
                    self._cross_pos = QPointF(event.position())
                self.viewport().update()
            event.accept()
            return
        if self._level_drag is not None:
            self._clear_cross()
            price = self._price_at(event.position())
            if price is not None:
                self._level_price = price
                self.viewport().update()
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            event.accept()
            return
        if self._drag_alarm is not None:
            self._clear_cross()
            price = self._price_at(event.position())
            if price is not None:
                self._alarm_drag_price = price
                self._alarm_levels = [
                    price if _same_price(level, self._drag_alarm) else level for level in self._alarm_source
                ]
                self.viewport().update()
            self.setCursor(Qt.CursorShape.SizeVerCursor)
            event.accept()
            return
        if self._drag_y is not None:
            self._clear_cross()
            self._scale_y(event.position().y())
            event.accept()
            return
        if self._drag_x is not None:
            self._clear_cross()
            self._pan_x(event.position().x(), event.position().y())
            self._on_x_changed()
            event.accept()
            return
        if self._drag_move is not None:
            self._clear_cross()
            self._shift_xy(event.position().x(), event.position().y())
            event.accept()
            return
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._press_pos is not None
            and not self._over_price_axis(self._press_pos)
            and not self._over_time_axis(self._press_pos)
        ):
            delta = event.position() - self._press_pos
            if abs(delta.x()) > 4 or abs(delta.y()) > 4:
                start, end, _datetime_axis = self._read_x()
                self._drag_move = (
                    self._press_pos.x(),
                    self._press_pos.y(),
                    start,
                    end,
                    float(self._axis_y.min()),
                    float(self._axis_y.max()),
                )
                self._zoomed = True
                self._clear_cross()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self._shift_xy(event.position().x(), event.position().y())
                event.accept()
                return
        if self._handle_at(event.position()) is not None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif (
            self._selected is not None
            and self.chart().series()
            and self._delete_rect(self._selected, self.chart().series()[0]).contains(event.position())
        ):
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._tool and self._on_plot(event.position()):
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self._body_at(event.position()) is not None:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._level_near(event.position()) is not None:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._alarm_near(event.position()) is not None:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._over_price_axis(event.position()):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif self._over_time_axis(event.position()):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.chart().plotArea().contains(event.position()):
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        self._hover(event)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._clear_cross()
        if self._on_hover is not None:
            self._on_hover(None, None)
        super().leaveEvent(event)

    def _clear_cross(self) -> None:
        if self._cross_pos is None:
            return
        region = self._cross_region(self._cross_pos)
        self._cross_pos = None
        self.viewport().update(region)

    def _price_at(self, pos) -> float | None:
        if not self.chart().series():
            return None
        price = float(self.chart().mapToValue(pos, self.chart().series()[0]).y())
        if price <= 0 or not math.isfinite(price):
            return None
        return price

    def _level_near(self, pos) -> int | None:
        if not self._drawings or not self.chart().series():
            return None
        series = self.chart().series()[0]
        span = self._candle_span()
        best = None
        best_dy = 8.0
        for index, raw in enumerate(self._drawings):
            if raw.get("kind") != "level":
                continue
            price = self._level_price_of(index, raw)
            if price <= 0:
                continue
            y = self.chart().mapToPosition(QPointF(0.0, price), series).y()
            if span is not None:
                left = self.chart().mapToPosition(QPointF(span[0], price), series).x()
                right = self.chart().mapToPosition(QPointF(span[1], price), series).x()
                if pos.x() < min(left, right) - 4 or pos.x() > max(left, right) + 4:
                    continue
            dy = abs(y - pos.y())
            if dy <= best_dy:
                best_dy = dy
                best = index
        return best

    def _grab_level(self, pos) -> bool:
        index = self._level_near(pos)
        if index is None:
            return False
        self._level_drag = index
        self._level_price = float(self._drawings[index].get("price") or 0)
        self._level_press_y = pos.y()
        self._clear_cross()
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        return True

    def _level_menu(self, event) -> bool:
        index = self._level_near(event.position())
        if index is None or self._on_drawing_remove is None:
            return False
        menu = QMenu(self)
        remove = menu.addAction("Yatay çizgiyi sil")
        chosen = menu.exec(event.globalPosition().toPoint())
        if chosen == remove:
            self._on_drawing_remove(index)
        return True

    def _grab_alarm(self, pos) -> bool:
        if self._on_alert_move is None:
            return False
        level = self._alarm_near(pos)
        if level is None:
            return False
        self._drag_alarm = level
        self._alarm_drag_price = level
        self._alarm_press_y = pos.y()
        self._clear_cross()
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        return True

    def _alarm_menu(self, event) -> bool:
        if self._on_alert is None or not self.chart().series():
            return False
        pos = event.position()
        plot = self.chart().plotArea()
        near = self._alarm_near(pos)
        on_plot = plot.contains(pos) and not self._over_time_axis(pos)
        if not on_plot and near is None:
            return False
        price = self._price_at(pos)
        if price is None:
            return False
        menu = QMenu(self)
        add = menu.addAction(f"Alarm ekle  {format_price(price)}")
        remove = None
        if near is not None and self._on_alert_remove is not None:
            remove = menu.addAction(f"Alarmı sil  {format_price(near)}")
        chosen = menu.exec(event.globalPosition().toPoint())
        if chosen is None:
            return True
        if chosen == add:
            self._on_alert(price)
        elif remove is not None and chosen == remove:
            self._on_alert_remove(near)
        return True

    def _track_cross(self, pos) -> None:
        plot = self.chart().plotArea()
        over = plot.contains(pos) and not self._over_price_axis(pos) and bool(self.chart().series())
        if not over:
            self._clear_cross()
            return
        previous = self._cross_pos
        self._cross_pos = QPointF(pos)
        region = self._cross_region(self._cross_pos)
        if previous is not None:
            region = region.united(self._cross_region(previous))
        self.viewport().update(region)

    def _cross_region(self, pos: QPointF) -> QRegion:
        """Artı imlecinin boyadığı şeritler: dikey çizgi, yatay çizgi, fiyat ve zaman etiketi."""
        view = self.viewport().rect()
        plot = self.chart().plotArea()
        x = int(pos.x())
        y = int(pos.y())
        region = QRegion(x - 3, view.top(), 7, view.height())
        region = region.united(QRegion(view.left(), y - 12, view.width(), 25))
        bottom = int(plot.bottom())
        return region.united(QRegion(view.left(), bottom, view.width(), view.bottom() - bottom + 1))

    def _alarm_near(self, pos) -> float | None:
        if not self._alarm_levels or not self.chart().series():
            return None
        plot = self.chart().plotArea()
        if pos.x() < plot.left() or pos.x() > self.viewport().width():
            return None
        series = self.chart().series()[0]
        best = None
        best_dy = 10.0
        for level in self._alarm_levels:
            y = self.chart().mapToPosition(QPointF(0.0, float(level)), series).y()
            dy = abs(y - pos.y())
            if dy <= best_dy:
                best_dy = dy
                best = float(level)
        return best

    def _hover(self, event) -> None:
        if self._crosshair:
            if not event.buttons():
                self._track_cross(event.position())
            return
        if self._on_hover is None or event.buttons():
            return
        pos = event.position()
        over = self._over_time_axis(pos) or (
            self.chart().plotArea().contains(pos) and not self._over_price_axis(pos)
        )
        if not over or not self.chart().series():
            self._on_hover(None, None)
            return
        moment = self.chart().mapToValue(pos, self.chart().series()[0])
        self._on_hover(event.globalPosition().toPoint(), int(moment.x()))

    def mouseReleaseEvent(self, event) -> None:
        if self._edit is not None and event.button() == Qt.MouseButton.LeftButton:
            index, end_index = self._edit
            value = self._edit_value
            self._edit = None
            self._edit_value = None
            if (
                value is not None
                and 0 <= index < len(self._drawings)
                and self._on_drawing_move is not None
                and self.chart().series()
            ):
                item = self._drawings[index]
                original = (item["t1"], item["p1"]) if end_index == 0 else (item["t2"], item["p2"])
                series = self.chart().series()[0]
                a = self.chart().mapToPosition(QPointF(original[0], original[1]), series)
                b = self.chart().mapToPosition(QPointF(value[0], value[1]), series)
                if math.hypot(b.x() - a.x(), b.y() - a.y()) > 3:
                    if end_index == 0:
                        self._on_drawing_move(index, value, (item["t2"], item["p2"]))
                    else:
                        self._on_drawing_move(index, (item["t1"], item["p1"]), value)
            self.viewport().update()
            event.accept()
            return
        if self._draft_anchor is not None and event.button() == Qt.MouseButton.LeftButton:
            start = self._draft_anchor
            end = self._draft_cursor
            kind = self._tool
            self._draft_anchor = None
            self._draft_cursor = None
            if end is not None and kind and self._on_drawing is not None and self.chart().series():
                series = self.chart().series()[0]
                a = self.chart().mapToPosition(QPointF(start[0], start[1]), series)
                b = self.chart().mapToPosition(QPointF(end[0], end[1]), series)
                if kind == "level" or math.hypot(b.x() - a.x(), b.y() - a.y()) > 6:
                    self._on_drawing(kind, start, end)
            self.unsetCursor()
            self.viewport().update()
            event.accept()
            return
        if self._level_drag is not None and event.button() == Qt.MouseButton.LeftButton:
            index = self._level_drag
            price = self._level_price
            moved = abs(event.position().y() - self._level_press_y) > 3
            self._level_drag = None
            self._level_price = None
            if (
                moved
                and price is not None
                and 0 <= index < len(self._drawings)
                and self._on_drawing_move is not None
            ):
                self._on_drawing_move(index, (0.0, price), (0.0, price))
            else:
                self.viewport().update()
            event.accept()
            return
        if self._drag_alarm is not None and event.button() == Qt.MouseButton.LeftButton:
            old = self._drag_alarm
            new = self._alarm_drag_price if self._alarm_drag_price is not None else old
            moved = abs(event.position().y() - self._alarm_press_y) > 3
            self._drag_alarm = None
            self._alarm_drag_price = None
            if moved and self._on_alert_move is not None and not _same_price(old, new):
                self._on_alert_move(old, new)
            else:
                self._alarm_levels = list(self._alarm_source)
                self.viewport().update()
            event.accept()
            return
        if self._drag_y is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = None
            event.accept()
            return
        if self._drag_x is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_x = None
            self._on_x_changed()
            event.accept()
            return
        if self._drag_move is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_move = None
            self.unsetCursor()
            self._on_x_changed()
            event.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._tool or self._handle_at(event.position()) is not None or self._body_at(event.position()) is not None:
            event.accept()
            return
        if self._alarm_near(event.position()) is not None:
            event.accept()
            return
        self._zoomed = False
        self._price_manual = False
        self._on_reset()
        event.accept()

    def mark_fresh(self) -> None:
        self._zoomed = False
        self._price_manual = False
        self.pushed_older = False

    @property
    def zoomed(self) -> bool:
        return self._zoomed

    @property
    def price_manual(self) -> bool:
        return self._price_manual

    def _chart_pos(self, pos) -> QPointF:
        return self.chart().mapFromScene(self.mapToScene(pos.toPoint()))

    def _over_price_axis(self, pos) -> bool:
        plot = self.chart().plotArea()
        local = self._chart_pos(pos)
        on_scale = local.x() >= plot.right() - 40 and plot.top() - 16 <= local.y() <= plot.bottom() + 16
        on_edge = pos.x() >= self.viewport().width() - 96
        return on_scale or on_edge

    def _over_time_axis(self, pos) -> bool:
        plot = self.chart().plotArea()
        return pos.y() >= plot.bottom() - 8 and pos.x() < plot.right() - 2

    def _x_ratio(self, cursor_x: float) -> float:
        plot = self.chart().plotArea()
        width = plot.width()
        if width <= 0:
            return 0.5
        return min(max((cursor_x - plot.left()) / width, 0.0), 1.0)

    def _zoom_x(self, cursor_x: float, delta: int) -> None:
        start, end, datetime_axis = self._read_x()
        span = end - start
        if span <= 0:
            return
        plot = self.chart().plotArea()
        width = plot.width()
        if width <= 0:
            return
        ratio = min(max((cursor_x - plot.left()) / width, 0.0), 1.0)
        new_span = span * self._wheel_factor(delta)
        new_start, new_end = self._bound_span(start, end, new_span, ratio)
        self._write_x(new_start, new_end, datetime_axis)

    def _pan_x(self, cursor_x: float, cursor_y: float) -> None:
        if self._drag_x is None:
            return
        origin_x, origin_y, start, end, ratio = self._drag_x
        span = end - start
        if span <= 0:
            return
        dx = cursor_x - origin_x
        dy = cursor_y - origin_y
        delta = dx if abs(dx) >= abs(dy) else dy
        factor = 2 ** (-delta / 120)
        new_span = span * factor
        new_start, new_end = self._bound_span(start, end, new_span, ratio)
        _start, _end, datetime_axis = self._read_x()
        self._write_x(new_start, new_end, datetime_axis)

    def _bound_span(self, start: float, end: float, new_span: float, ratio: float) -> tuple[float, float]:
        ceiling = self._ceiling_span()
        if ceiling is not None:
            floor = min(ceiling, 3 * 60 * 1000)
            new_span = min(max(new_span, floor), ceiling)
        else:
            new_span = max(new_span, 3 * 60 * 1000)
        anchor = start + (end - start) * ratio
        new_start = anchor - new_span * ratio
        new_end = new_start + new_span
        if self._home_x is None:
            return new_start, new_end
        home_start, home_end = self._home_x
        home_span = home_end - home_start
        if ceiling is not None and ceiling >= home_span - 1000 and new_span >= home_span - 1000:
            return float(home_start), float(home_end)
        if new_start < home_start:
            new_end += home_start - new_start
            new_start = home_start
        elif new_end > home_end:
            new_start -= new_end - home_end
            new_end = home_end
        return float(new_start), float(new_end)

    def _shift_x(self, cursor_x: float) -> None:
        if self._drag_pan is None:
            return
        origin_x, start, end = self._drag_pan
        span = end - start
        plot = self.chart().plotArea()
        width = plot.width()
        if span <= 0 or width <= 0:
            return
        shift = (origin_x - cursor_x) / width * span
        new_start, new_end = self._clamp_window(start + shift, end + shift)
        home_start = self._home_x[0] if self._home_x is not None else new_start
        self.pushed_older = shift < 0 and new_start <= home_start + 1000
        _start, _end, datetime_axis = self._read_x()
        self._write_x(new_start, new_end, datetime_axis)

    def _shift_xy(self, cursor_x: float, cursor_y: float) -> None:
        if self._drag_move is None:
            return
        origin_x, origin_y, start, end, low, high = self._drag_move
        plot = self.chart().plotArea()
        y_span = high - low
        height = plot.height()
        if abs(cursor_y - origin_y) >= 4 and y_span > 0 and height > 0:
            self._price_manual = True
            shift_y = (origin_y - cursor_y) / height * y_span
            new_low, new_high = self._clamp_y(low + shift_y, high + shift_y)
            _apply_price_scale(self._axis_y, new_low, new_high)
        x_span = end - start
        width = plot.width()
        if x_span > 0 and width > 0:
            shift_x = (origin_x - cursor_x) / width * x_span
            new_start, new_end = self._clamp_window(start + shift_x, end + shift_x)
            home_start = self._home_x[0] if self._home_x is not None else new_start
            self.pushed_older = shift_x < 0 and new_start <= home_start + 1000
            _start, _end, datetime_axis = self._read_x()
            self._write_x(new_start, new_end, datetime_axis)
        self._on_x_changed()

    def _clamp_y(self, new_low: float, new_high: float) -> tuple[float, float]:
        if self._pan_y is None:
            return new_low, new_high
        home_low, home_high = self._pan_y
        span = new_high - new_low
        if span >= home_high - home_low:
            return float(home_low), float(home_high)
        if new_low < home_low:
            return float(home_low), float(home_low + span)
        if new_high > home_high:
            return float(home_high - span), float(home_high)
        return new_low, new_high

    def _clamp_window(self, new_start: float, new_end: float) -> tuple[float, float]:
        if self._home_x is None:
            return new_start, new_end
        home_start, home_end = self._home_x
        span = new_end - new_start
        home_span = home_end - home_start
        if span >= home_span - 1000:
            return float(home_start), float(home_end)
        if new_start < home_start:
            return float(home_start), float(home_start + span)
        if new_end > home_end:
            return float(home_end - span), float(home_end)
        return new_start, new_end

    def _read_x(self) -> tuple[float, float, bool]:
        start = self._axis_x.min()
        end = self._axis_x.max()
        if hasattr(start, "toMSecsSinceEpoch"):
            return float(start.toMSecsSinceEpoch()), float(end.toMSecsSinceEpoch()), True
        return float(start), float(end), False

    def _write_x(self, start: float, end: float, datetime_axis: bool) -> None:
        if datetime_axis:
            self._axis_x.setRange(
                QDateTime.fromMSecsSinceEpoch(int(start)),
                QDateTime.fromMSecsSinceEpoch(int(end)),
            )
            self._axis_x.setFormat(_axis_time_format(self._interval_of(), int(start), int(end)))
            return
        self._axis_x.setRange(start, end)

    def _zoom_y(self, cursor_y: float, delta: int) -> None:
        low = float(self._axis_y.min())
        high = float(self._axis_y.max())
        span = high - low
        if span <= 0:
            return
        plot = self.chart().plotArea()
        height = plot.height()
        if height <= 0:
            return
        ratio = min(max((plot.bottom() - cursor_y) / height, 0.0), 1.0)
        new_span = self._clamp_span(span * self._wheel_factor(delta), low + span * ratio, span)
        anchor = low + span * ratio
        new_low = anchor - new_span * ratio
        new_high = new_low + new_span
        _apply_price_scale(self._axis_y, new_low, new_high)

    def _scale_y(self, cursor_y: float) -> None:
        if self._drag_y is None:
            return
        origin_y, low, high = self._drag_y
        plot = self.chart().plotArea()
        height = plot.height()
        if height <= 0:
            return
        span = high - low
        if span <= 0:
            return
        ratio = min(max((plot.bottom() - origin_y) / height, 0.0), 1.0)
        # Drag down opens the gap between two prices (10 and 11 become 10.1, 10.2).
        # Drag up closes it. A short drag has to be enough to reach the next decimal.
        travel = max(height / 6.5, 32)
        factor = 2 ** (-(cursor_y - origin_y) / travel)
        new_span = self._clamp_span(span * factor, low + span * ratio, span, 80)
        anchor = low + span * ratio
        _apply_price_scale(self._axis_y, anchor - new_span * ratio, anchor + new_span * (1 - ratio))

    def _wheel_factor(self, delta: int) -> float:
        return 0.8 ** (delta / 120)

    def _clamp_span(self, span: float, anchor: float, original: float, ceiling_factor: float = 6) -> float:
        floor = max(abs(anchor) * 1e-5, 1e-8)
        ceiling = original * 8
        if self._home_y is not None:
            ceiling = max((self._home_y[1] - self._home_y[0]) * ceiling_factor, floor * 2)
        return min(max(span, floor), ceiling)


def _prepare_chart(chart: QChart, title: str, candle_labels: bool = False) -> tuple[object, object]:
    from PySide6.QtCharts import QDateTimeAxis, QValueAxis

    chart.setTitle(title)
    chart.setTitleBrush(MUTED)
    chart.legend().hide()
    chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
    chart.setBackgroundVisible(True)
    chart.setBackgroundBrush(QColor("#18181b"))
    chart.setPlotAreaBackgroundVisible(True)
    chart.setPlotAreaBackgroundBrush(CHART_PLOT)
    chart.setMargins(QMargins(8, 8, 72, 28 if candle_labels else 8))
    axis_x = QDateTimeAxis()
    axis_x.setFormat("HH:mm")
    axis_x.setTickCount(5)
    if candle_labels:
        axis_x.setLabelsVisible(False)
        axis_x.setGridLineVisible(False)
        axis_x.setTickCount(2)
    axis_x.setLabelsColor(MUTED)
    axis_x.setLabelsFont(QFont("Segoe UI", 8))
    axis_x.setGridLineColor(QColor("#27272a"))
    axis_x.setLinePenColor(QColor("#3f3f46"))
    axis_y = QValueAxis()
    axis_y.setLabelsColor(MUTED)
    axis_y.setGridLineColor(QColor("#27272a"))
    axis_y.setLinePenColor(QColor("#3f3f46"))
    axis_y.setLabelsFont(QFont("Segoe UI", 8))
    if hasattr(axis_y, "setTruncateLabels"):
        axis_y.setTruncateLabels(False)
    if candle_labels:
        axis_y.setGridLineVisible(False)
    chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
    chart.addAxis(axis_y, Qt.AlignmentFlag.AlignRight)
    return axis_x, axis_y


def _axis_time_format(interval: str, start_ms: int, end_ms: int) -> str:
    unit = _axis_unit(max(end_ms - start_ms, 0), 0)
    if unit == "year":
        return "yyyy"
    if unit == "month":
        return "MM.yyyy"
    if unit == "day":
        return "dd.MM"
    return "HH:mm"


def _fit_x(axis, start_ms: int, end_ms: int, interval: str) -> None:
    axis.setFormat(_axis_time_format(interval, start_ms, end_ms))
    axis.setRange(
        QDateTime.fromMSecsSinceEpoch(start_ms),
        QDateTime.fromMSecsSinceEpoch(end_ms),
    )


class _TelegramSend(QThread):
    finished_ok = Signal(bool)

    def __init__(self, token: str, chat_id: str, text: str) -> None:
        super().__init__()
        self._token = token
        self._chat_id = chat_id
        self._text = text

    def run(self) -> None:
        self.finished_ok.emit(_post_telegram(self._token, self._chat_id, self._text))


class _OkxPing(QThread):
    done = Signal(bool, str)

    def __init__(self, creds: Creds, simulated: bool) -> None:
        super().__init__()
        self._creds = creds
        self._simulated = simulated

    def run(self) -> None:
        try:
            self.done.emit(True, okx_ping(self._creds, self._simulated))
        except OkxError as exc:
            self.done.emit(False, str(exc))
        except Exception:
            self.done.emit(False, "Bağlantı kurulamadı")


class _OkxWorker(QThread):
    done = Signal(object)

    def __init__(self, creds: Creds, symbol: str, price: float, settings: dict, actions: list[dict]) -> None:
        super().__init__()
        self._creds = creds
        self._symbol = symbol
        self._price = price
        self._settings = settings
        self._actions = actions

    def run(self) -> None:
        try:
            results = okx_execute(self._creds, self._symbol, self._price, self._settings, self._actions)
        except Exception as exc:
            results = [
                {
                    "symbol": self._symbol,
                    "label": "",
                    "op": "",
                    "ok": False,
                    "message": f"Emir gönderilemedi: {type(exc).__name__}: {exc}",
                    "position": None,
                }
            ]
        self.done.emit(results)


class _OkxPositions(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, creds: Creds, simulated: bool) -> None:
        super().__init__()
        self._creds = creds
        self._simulated = simulated

    def run(self) -> None:
        try:
            positions = fetch_positions(self._creds, self._simulated)
            available = okx_available(self._creds, self._simulated)
            self.done.emit({"positions": positions, "available": available})
        except OkxError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Pozisyonlar okunamadı")


class _OkxAction(QThread):
    """Elle TP/SL veya trailing emri. request: {'kind': 'tpsl'|'trail', 'position': dict, ...}"""

    done = Signal(bool, str, object)

    def __init__(self, creds: Creds, simulated: bool, request: dict) -> None:
        super().__init__()
        self._creds = creds
        self._simulated = simulated
        self._request = request

    def run(self) -> None:
        job = self._request
        try:
            if job.get("kind") == "tpsl":
                result = okx_set_tp_sl(self._creds, job["position"], job.get("tp"), job.get("sl"), self._simulated)
            else:
                result = okx_set_trailing(
                    self._creds,
                    job["position"],
                    float(job.get("callback") or 0),
                    str(job.get("mode") or "ratio"),
                    job.get("active"),
                    self._simulated,
                )
        except OkxError as exc:
            self.done.emit(False, str(exc), job)
            return
        except Exception:
            self.done.emit(False, "Emir gönderilemedi", job)
            return
        job = dict(job)
        job["result"] = result
        self.done.emit(True, str(result.get("message") or "Tamam"), job)


def _post_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id or not text:
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text},
            timeout=12,
        )
    except requests.RequestException:
        return False
    return bool(response.ok)


def _episode_text(snapshot: Snapshot, event: _SignalEvent, window) -> str:
    if event.kind in ("devam", "hedef"):
        head = f"{event.verdict} devam ediyor"
    elif event.kind == "kapandi":
        head = f"{event.verdict} kapandı · take profit"
    elif event.kind == "sure":
        head = f"{event.verdict} süresi doldu"
    else:
        head = f"Yeni · {event.verdict}"
    hedef = "Hedef fiyat yok" if event.target is None else f"Hedef fiyat {format_price(event.target)}"
    tp = _take_profit(_trade_side(event.verdict), event.target)
    hedef = f"{hedef}\n{'Take profit yok' if tp is None else f'Take profit {_format_tp(tp)}'}"
    price = format_percent(None if window is None else window.price_pct)
    oi = format_percent(None if window is None else window.oi_pct)
    perp = format_amount(None if window is None else window.cvd_perp)
    spot = format_amount(None if window is None else window.cvd_spot)
    funding = format_funding(snapshot.funding_percent)
    return (
        f"{snapshot.symbol} · {event.label}\n"
        f"{head}\n"
        f"{hedef}\n"
        f"Fiyat {price} · OI {oi}\n"
        f"Funding {funding} · {snapshot.funding_status}\n"
        f"CVD perp {perp} · CVD spot {spot}"
    )


def _signal_text(snapshot: Snapshot, label: str, verdict: str, window) -> str:
    price = format_percent(None if window is None else window.price_pct)
    oi = format_percent(None if window is None else window.oi_pct)
    perp = format_amount(None if window is None else window.cvd_perp)
    spot = format_amount(None if window is None else window.cvd_spot)
    funding = format_funding(snapshot.funding_percent)
    return (
        f"{snapshot.symbol} · {label}\n"
        f"{verdict}\n"
        f"Fiyat {price} · OI {oi}\n"
        f"Funding {funding} · {snapshot.funding_status}\n"
        f"CVD perp {perp} · CVD spot {spot}"
    )


class _SettingsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ayarlar")
        self.setMinimumWidth(440)
        data = _read_state()
        layout = QVBoxLayout(self)
        note = QLabel(
            "1sa, 4sa, 24sa ve 1hafta sonuçları kapanmış mumda bu sohbete gider. "
            "Hedef fiyat mesajda yazar. İşlem Açma yeni sinyal sayılmaz. "
            "Aynı sinyal duruyorsa ve take profit'e değmediyse devam ettiği tekrar yazılır. "
            "Long yönlü işlem hedefin 200$ altında, short yönlü işlem hedefin 200$ üstünde kapanır."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a1a1aa;")
        self.token = QLineEdit(str(data.get("telegram_token") or ""))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("123456:ABC...")
        self.chat = QLineEdit(str(data.get("telegram_chat_id") or ""))
        self.chat.setPlaceholderText("123456789")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(note)
        layout.addWidget(QLabel("Telegram bot token"))
        layout.addWidget(self.token)
        layout.addWidget(QLabel("Telegram chat id"))
        layout.addWidget(self.chat)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        save = QPushButton("Kaydet")
        test = QPushButton("Deneme mesajı")
        save.clicked.connect(self._save)
        test.clicked.connect(self._test)
        row.addWidget(save)
        row.addWidget(test)
        layout.addLayout(row)

    def _save(self) -> None:
        _patch_state(
            telegram_token=self.token.text().strip(),
            telegram_chat_id=self.chat.text().strip(),
        )
        self.status.setText("Kaydedildi.")
        self.status.setStyleSheet("color: #26a69a;")

    def _test(self) -> None:
        token = self.token.text().strip()
        chat_id = self.chat.text().strip()
        _patch_state(telegram_token=token, telegram_chat_id=chat_id)
        if not token or not chat_id:
            self.status.setText("Token ve chat id gerekli.")
            self.status.setStyleSheet("color: #ef5350;")
            return
        self.status.setText("Gönderiliyor…")
        self.status.setStyleSheet("color: #a1a1aa;")
        job = _TelegramSend(token, chat_id, "CryptoHub bağlandı. 1sa, 4sa, 24sa ve 1hafta sinyalleri bu sohbete gelecek.")
        host = self.parent() or self
        job.setParent(host)
        job.finished_ok.connect(self._tested)
        job.start()

    def _tested(self, ok: bool) -> None:
        if ok:
            self.status.setText("Deneme mesajı gitti.")
            self.status.setStyleSheet("color: #26a69a;")
        else:
            self.status.setText("Gönderilemedi. Token ve chat id'yi kontrol et.")
            self.status.setStyleSheet("color: #ef5350;")


class _TradeFeed(QObject):
    """Last trade prices. One socket carries every symbol that has an alarm, plus the open coin."""

    prices = Signal(str, list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._socket = QWebSocket()
        self._symbols: tuple[str, ...] = ()
        self._pending: dict[str, list[float]] = {}
        self._flush = QTimer(self)
        self._flush.setSingleShot(True)
        self._flush.timeout.connect(self._emit_pending)
        self._want = False
        self._epoch = 0
        self._socket.textMessageReceived.connect(self._on_text)
        self._socket.disconnected.connect(self._on_closed)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reopen)

    def follow(self, symbols: list[str]) -> None:
        chosen = tuple(sorted({symbol.upper() for symbol in symbols if symbol}))
        if chosen == self._symbols and self._socket.state() == QAbstractSocket.SocketState.ConnectedState:
            return
        self._symbols = chosen
        self._want = bool(chosen)
        self._retry.stop()
        self._epoch += 1
        epoch = self._epoch
        if not chosen:
            self._socket.abort()
            return
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._socket.abort()
        QTimer.singleShot(50, lambda epoch=epoch: self._open(epoch))

    def stop(self) -> None:
        self._want = False
        self._symbols = ()
        self._epoch += 1
        self._retry.stop()
        self._flush.stop()
        self._pending = {}
        self._socket.abort()

    def _open(self, epoch: int) -> None:
        if epoch != self._epoch or not self._want or not self._symbols:
            return
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._socket.abort()
            QTimer.singleShot(50, lambda epoch=epoch: self._open(epoch))
            return
        if len(self._symbols) == 1:
            url = f"wss://fstream.binance.com/market/ws/{self._symbols[0].lower()}@aggTrade"
        else:
            streams = "/".join(f"{symbol.lower()}@aggTrade" for symbol in self._symbols)
            url = f"wss://fstream.binance.com/market/stream?streams={streams}"
        self._socket.open(QUrl(url))

    def _reopen(self) -> None:
        if not self._want or not self._symbols:
            return
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            return
        self._open(self._epoch)

    def _on_closed(self) -> None:
        if not self._want or self._retry.isActive():
            return
        self._retry.start(1500)

    def _on_text(self, text: str) -> None:
        try:
            data = json.loads(text)
            if isinstance(data.get("data"), dict):
                data = data["data"]
            price = float(data["p"])
            symbol = str(data.get("s") or "").upper()
        except (TypeError, ValueError, KeyError):
            return
        if price <= 0 or symbol not in self._symbols:
            return
        path = self._pending.setdefault(symbol, [])
        if path and path[-1] == price:
            return
        if len(path) >= 2 and (path[-2] < path[-1] < price or path[-2] > path[-1] > price):
            path[-1] = price
        else:
            path.append(price)
        if not self._flush.isActive():
            self._flush.start(100)

    def _emit_pending(self) -> None:
        """Fiyat yolunu dönüş noktalarıyla verir; alarm geçişleri tek tek işlemle aynı kalır."""
        pending, self._pending = self._pending, {}
        for symbol, path in pending.items():
            if path and symbol in self._symbols:
                self.prices.emit(symbol, path)


class _SignalEvent:
    def __init__(self, label: str, kind: str, verdict: str, target: float | None) -> None:
        self.label = label
        self.kind = kind
        self.verdict = verdict
        self.target = target


def _take_profit(side: str | None, target) -> float | None:
    """Long hedefin 200$ altı, short hedefin 200$ üstü."""
    if side not in ("up", "down"):
        return None
    try:
        level = float(target)
    except (TypeError, ValueError):
        return None
    if level <= 0:
        return None
    price = level - TP_OFFSET if side == "up" else level + TP_OFFSET
    if price <= 0:
        return None
    if price >= 1:
        return float(round(price))
    return price


def _tp_reachable(side: str | None, tp, entry) -> bool:
    if not isinstance(tp, (int, float)) or float(tp) <= 0:
        return False
    if not isinstance(entry, (int, float)) or float(entry) <= 0:
        return True
    if side == "up":
        return float(tp) > float(entry)
    if side == "down":
        return float(tp) < float(entry)
    return False


def _trade_side(verdict: str) -> str | None:
    """Long Trap aşağı, Short Trap yukarı. Diğer long sonuçlar yukarı, short sonuçlar aşağı."""
    if verdict.startswith("Short Trap") or verdict in {"Sağlıklı Long", "Short Squeeze", "Akümülasyon"}:
        return "up"
    if verdict.startswith("Long Trap") or verdict in {"Sağlıklı Short", "Long Squeeze", "Dağıtım"}:
        return "down"
    return None


def _episode_open(verdict: str, target, candle: int, spot) -> dict:
    side = _trade_side(verdict)
    tp = _take_profit(side, target)
    entry_px = float(spot) if isinstance(spot, (int, float)) and float(spot) > 0 else None
    return {
        "verdict": verdict,
        "quiet": False,
        "target": target,
        "side": side,
        "tp": tp,
        "entry": entry_px,
        "candle": candle,
        "opened_ms": int(time.time() * 1000),
    }


def _signal_clock(opened_ms) -> str:
    if not isinstance(opened_ms, (int, float)) or float(opened_ms) <= 0:
        return ""
    return datetime.fromtimestamp(float(opened_ms) / 1000).strftime("%H:%M")


def _tp_rest(label: str, verdict: str, closed_ms: int) -> dict:
    """Doğru kapanış. Aynı sonuç, pencere süresi dolmadan yeniden açılmaz."""
    return {
        "verdict": "",
        "quiet": True,
        "target": None,
        "side": None,
        "tp": None,
        "entry": None,
        "candle": closed_ms,
        "wait": verdict,
        "cool_until": int(time.time() * 1000) + SIGNAL_COOL_MS.get(label, 0),
    }


def _cooling(episode: dict | None) -> bool:
    if not isinstance(episode, dict):
        return False
    until = episode.get("cool_until")
    if not isinstance(until, (int, float)) or float(until) <= 0:
        return False
    return int(time.time() * 1000) < float(until)


def _cool_ready(episode: dict | None, verdict: str) -> bool:
    if _cooling(episode) or not isinstance(episode, dict):
        return False
    until = episode.get("cool_until")
    waiting = str(episode.get("wait") or "")
    if not isinstance(until, (int, float)) or float(until) <= 0 or not waiting:
        return False
    return verdict == waiting and verdict not in QUIET_VERDICTS


def _cool_bypass(episode: dict | None, verdict: str) -> bool:
    """Süre dolduysa kilit işlenir. Beklerken yalnızca farklı sonuç aynı mumda ilerler."""
    if not isinstance(episode, dict):
        return False
    until = episode.get("cool_until")
    if not isinstance(until, (int, float)) or float(until) <= 0:
        return False
    if _cooling(episode):
        waiting = str(episode.get("wait") or "")
        return verdict not in QUIET_VERDICTS and verdict != waiting
    return True


def _signal_expired(label: str, episode: dict) -> bool:
    limit = SIGNAL_EXPIRE_MS.get(label)
    opened = episode.get("opened_ms")
    if limit is None or not isinstance(opened, (int, float)) or float(opened) <= 0:
        return False
    return int(time.time() * 1000) - float(opened) >= limit


def _tp_reached(side, tp, entry, high, low) -> bool:
    if not _tp_reachable(side, tp, entry) or high is None or low is None:
        return False
    if side == "up" and float(high) >= float(tp):
        return True
    return side == "down" and float(low) <= float(tp)


def _episode_touch(symbol: str, episodes: dict) -> dict[str, tuple[float, float]]:
    """Açık sinyalin geldiği saatten sonraki en yüksek ve en düşük fiyat."""
    starts: list[tuple[str, int]] = []
    for label, episode in episodes.items():
        if not isinstance(episode, dict) or not episode.get("verdict"):
            continue
        opened = episode.get("opened_ms")
        if not isinstance(opened, (int, float)) or float(opened) <= 0:
            opened = episode.get("candle")
        if isinstance(opened, (int, float)) and float(opened) > 0:
            starts.append((label, int(opened)))
    if not starts:
        return {}
    oldest = min(stamp for _label, stamp in starts)
    step = since_step(oldest)
    try:
        bars = fetch_klines_since(symbol, oldest)
    except MarketError:
        return {}
    found: dict[str, tuple[float, float]] = {}
    for label, stamp in starts:
        chosen = bars_since(bars, stamp, step)
        if not chosen:
            continue
        found[label] = (max(bar.high for bar in chosen), min(bar.low for bar in chosen))
    return found


def _advance_signal(
    book: dict,
    symbol: str,
    candles: dict[str, tuple[int, float | None, float | None]],
    verdicts: dict[str, str],
    targets: dict[str, float | None],
    spot: float | None = None,
    touches: dict[str, tuple[float, float]] | None = None,
) -> tuple[dict, list[_SignalEvent], list[tuple[str, str]]]:
    """Her pencere kendi kapanmış mumunda ilerler. Aynı mum ikinci kez boş döner.

    Üçüncü dönüş, kapanan sinyallerdir: take profit'e değen doğru,
    yönü değişen veya süresi dolan yanlış. Take profit, sinyal geldikten
    sonraki fiyata bakar.
    """
    entry = dict(book.get(symbol) or {})
    events: list[_SignalEvent] = []
    notes: list[tuple[str, str]] = []
    dirty = False
    for label in SIGNAL_WINDOWS:
        if label not in verdicts or label not in candles:
            continue
        closed_ms, high, low = candles[label]
        episode = entry.get(label)
        if not isinstance(episode, dict):
            episode = None
        verdict = verdicts[label]
        klines = None if touches is None else touches.get(label)
        span = klines
        if isinstance(spot, (int, float)) and float(spot) > 0:
            price = float(spot)
            if span is None:
                span = (price, price)
            else:
                span = (max(float(span[0]), price), min(float(span[1]), price))
        same_candle = episode is not None and episode.get("candle") == closed_ms
        if episode is not None and episode.get("verdict") and span is not None:
            side = episode.get("side") or _trade_side(str(episode.get("verdict") or ""))
            tp = episode.get("tp")
            if not isinstance(tp, (int, float)) or float(tp) <= 0:
                tp = _take_profit(side, episode.get("target"))
            if _tp_reached(side, tp, episode.get("entry"), span[0], span[1]):
                dirty = True
                shown = episode.get("target")
                events.append(
                    _SignalEvent(
                        label,
                        "kapandi",
                        str(episode["verdict"]),
                        float(shown) if isinstance(shown, (int, float)) and float(shown) > 0 else None,
                    )
                )
                notes.append((label, "hit"))
                entry[label] = _tp_rest(label, str(episode["verdict"]), closed_ms)
                continue
        if same_candle and not (
            episode is not None and episode.get("verdict") and _signal_expired(label, episode)
        ) and not _cool_bypass(episode, verdict):
            continue
        dirty = True
        if klines is not None:
            high, low = None, None
        if episode is not None and episode.get("verdict"):
            target = episode.get("target")
            side = episode.get("side") or _trade_side(str(episode.get("verdict") or ""))
            tp = episode.get("tp")
            if not isinstance(tp, (int, float)) or float(tp) <= 0:
                tp = _take_profit(side, target)
            hit = _tp_reached(side, tp, episode.get("entry"), high, low)
            if hit:
                shown = float(target) if isinstance(target, (int, float)) and float(target) > 0 else None
                events.append(_SignalEvent(label, "kapandi", str(episode["verdict"]), shown))
                notes.append((label, "hit"))
                entry[label] = _tp_rest(label, str(episode["verdict"]), closed_ms)
                continue
            if _signal_expired(label, episode):
                notes.append((label, "miss"))
                failed = str(episode.get("verdict") or "")
                shown = episode.get("target")
                failed_target = float(shown) if isinstance(shown, (int, float)) and float(shown) > 0 else None
                entry[label] = {
                    "verdict": "",
                    "quiet": True,
                    "target": None,
                    "side": None,
                    "tp": None,
                    "entry": None,
                    "candle": closed_ms,
                    "wait": failed,
                }
                events.append(_SignalEvent(label, "sure", failed, failed_target))
                continue
        real = verdict not in QUIET_VERDICTS
        if episode is None:
            if real:
                target = targets.get(label)
                entry[label] = _episode_open(verdict, target, closed_ms, spot)
                events.append(_SignalEvent(label, "yeni", verdict, target))
            else:
                entry[label] = {
                    "verdict": "",
                    "quiet": True,
                    "target": None,
                    "side": None,
                    "tp": None,
                    "entry": None,
                    "candle": closed_ms,
                }
            continue
        if not real:
            kept = dict(episode)
            kept["quiet"] = True
            kept["candle"] = closed_ms
            if not _cooling(episode):
                kept["wait"] = ""
                if episode.get("cool_until"):
                    kept["cool_until"] = None
            entry[label] = kept
            continue
        waiting = str(episode.get("wait") or "")
        if waiting and verdict == waiting:
            until = episode.get("cool_until")
            if isinstance(until, (int, float)) and float(until) > 0 and not _cooling(episode):
                target = targets.get(label)
                entry[label] = _episode_open(verdict, target, closed_ms, spot)
                events.append(_SignalEvent(label, "yeni", verdict, target))
                continue
            kept = dict(episode)
            kept["candle"] = closed_ms
            kept["quiet"] = True
            entry[label] = kept
            continue
        if verdict == episode.get("verdict"):
            kept = dict(episode)
            kept["candle"] = closed_ms
            kept["quiet"] = False
            stored = kept.get("target")
            if not isinstance(stored, (int, float)) or stored <= 0:
                stored = targets.get(label)
                kept["target"] = stored
                kept["side"] = _trade_side(verdict)
                kept["tp"] = _take_profit(kept.get("side"), stored)
                if not isinstance(kept.get("entry"), (int, float)):
                    kept["entry"] = float(spot) if isinstance(spot, (int, float)) and float(spot) > 0 else None
            elif not isinstance(kept.get("tp"), (int, float)) or float(kept.get("tp") or 0) <= 0:
                kept["tp"] = _take_profit(kept.get("side") or _trade_side(verdict), stored)
            events.append(
                _SignalEvent(label, "devam", verdict, float(stored) if isinstance(stored, (int, float)) else None)
            )
            entry[label] = kept
            continue
        target = targets.get(label)
        new_side = _trade_side(verdict)
        old_side = episode.get("side") or _trade_side(str(episode.get("verdict") or ""))
        if new_side and new_side == old_side:
            kept = dict(episode)
            kept["verdict"] = verdict
            kept["quiet"] = False
            kept["candle"] = closed_ms
            kept["side"] = new_side
            if isinstance(target, (int, float)) and float(target) > 0:
                kept["target"] = float(target)
                kept["tp"] = _take_profit(new_side, target)
            shown = kept.get("target")
            events.append(
                _SignalEvent(
                    label,
                    "hedef",
                    verdict,
                    float(shown) if isinstance(shown, (int, float)) and float(shown) > 0 else None,
                )
            )
            entry[label] = kept
            continue
        if episode.get("verdict"):
            notes.append((label, "miss"))
        entry[label] = _episode_open(verdict, target, closed_ms, spot)
        events.append(_SignalEvent(label, "yeni", verdict, target))
    if not dirty:
        return book, [], []
    book = dict(book)
    book[symbol] = entry
    return book, events, notes


def _bump_score(raw, symbol: str, notes: list[tuple[str, str]]) -> dict:
    """Kapanan sinyali coin ve aralık bazında doğru ya da yanlış sayar."""
    score = dict(raw) if isinstance(raw, dict) else {}
    entry = dict(score.get(symbol) or {})
    for label, kind in notes:
        if kind not in ("hit", "miss") or label not in SIGNAL_WINDOWS:
            continue
        row = dict(entry.get(label) or {})
        try:
            count = int(row.get(kind) or 0)
        except (TypeError, ValueError):
            count = 0
        row[kind] = count + 1
        entry[label] = row
    score[symbol] = entry
    return score


def _score_line(raw, label: str) -> str:
    row = raw.get(label) if isinstance(raw, dict) else None
    hit = miss = 0
    if isinstance(row, dict):
        try:
            hit = max(0, int(row.get("hit") or 0))
            miss = max(0, int(row.get("miss") or 0))
        except (TypeError, ValueError):
            hit = miss = 0
    closed = hit + miss
    if closed <= 0:
        return "doğruluk henüz yok"
    return f"TP doğru {hit}/{closed} · %{round(100 * hit / closed)}"


def _price_window(lows: list[float], highs: list[float], step: float) -> tuple[float, float] | None:
    """Mum dibi, mum tepesi ve aralığın %12’si. Haritanın çizdiği dikey pencere."""
    if not lows or not highs:
        return None
    low = min(lows)
    high = max(highs)
    if high <= 0:
        return None
    span = max(high - low, step * 8, high * 0.01)
    pad = span * 0.12
    return low - pad, high + pad


HEAT_GROUND = QColor(68, 1, 84)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * min(1.0, max(0.0, fraction))
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    mix = rank - low
    return ordered[low] * (1.0 - mix) + ordered[high] * mix


def _heat_amount(usd: float, floor: float, ceiling: float) -> float:
    if ceiling <= floor:
        return 0.0
    return min(1.0, max(0.0, (usd - floor) / (ceiling - floor)))


def _heat_color(amount: float) -> QColor:
    stops = (0.0, 0.20, 0.45, 0.70, 1.0)
    colors = (
        (68, 1, 84),
        (59, 82, 139),
        (33, 145, 140),
        (94, 201, 98),
        (253, 231, 37),
    )
    amount = min(1.0, max(0.0, amount))
    index = 0
    while index < len(stops) - 2 and amount > stops[index + 1]:
        index += 1
    span = stops[index + 1] - stops[index]
    blend = 0.0 if span <= 0 else (amount - stops[index]) / span
    left, right = colors[index], colors[index + 1]
    return QColor(
        int(left[0] + (right[0] - left[0]) * blend),
        int(left[1] + (right[1] - left[1]) * blend),
        int(left[2] + (right[2] - left[2]) * blend),
    )


def _tick_step(span: float) -> float:
    if span <= 0:
        return 1.0
    raw = span / 8
    exponent = math.floor(math.log10(raw))
    base = 10**exponent
    mantissa = raw / base
    for nice in (1, 2, 2.5, 5, 10):
        if mantissa <= nice:
            return nice * base
    return 10 * base


def _round_step(raw: float) -> float:
    if raw <= 0:
        return 1e-8
    exponent = math.floor(math.log10(raw))
    base = 10**exponent
    mantissa = raw / base
    for nice in (1, 2, 2.5, 5, 10):
        if mantissa <= nice:
            return nice * base
    return 10 * base


def _view_step(y_min: float, y_max: float, height: int, floor_step: float) -> float:
    """Görünen aralıkta kova yaklaşık 3.5 piksel olsun. Hesap adımından ince bölünmez."""
    span = y_max - y_min
    if span <= 0 or floor_step <= 0:
        return floor_step if floor_step > 0 else 1.0
    rows = max(1.0, height / 3.5)
    step = _round_step(span / rows)
    return floor_step if step < floor_step else step


def _group_column(
    book: LiquidityMap,
    column_index: int,
    column: dict[int, float],
    draw_step: float,
) -> dict[int, tuple[float, float]]:
    """İnce kovaları görüntü adımına toplar: dolar toplamı ve ağırlıklı fiyat."""
    fine = book.step
    if fine <= 0 or draw_step <= 0:
        return {}
    grouped: dict[int, list[float]] = {}
    for index, usd in column.items():
        if usd <= 0:
            continue
        price = _cell_price(book, column_index, index)
        slot = int(math.floor((index * fine) / draw_step + 1e-9))
        bucket = grouped.get(slot)
        if bucket is None:
            grouped[slot] = [usd, usd * price]
        else:
            bucket[0] += usd
            bucket[1] += usd * price
    return {slot: (pair[0], pair[1] / pair[0]) for slot, pair in grouped.items() if pair[0] > 0}


def _bucket_pixels(index: int, step: float, y_min: float, y_max: float, height: int) -> tuple[int, int]:
    span = max(y_max - y_min, 1e-9)
    top = int((y_max - (index + 1) * step) / span * (height - 1))
    bottom = int((y_max - index * step) / span * (height - 1))
    if bottom < top:
        top, bottom = bottom, top
    return top, max(top + 1, bottom)


def _cell_price(book: LiquidityMap, column_index: int, index: int) -> float:
    """Kovanın görünen fiyatı, ham likidasyonun miktar ortalamasıdır."""
    if 0 <= column_index < len(book.levels):
        raw = book.levels[column_index].get(index)
        if isinstance(raw, (int, float)) and float(raw) > 0:
            return float(raw)
    return (index + 0.5) * book.step


def _format_tp(value) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "—"
    if price >= 1:
        return f"{round(price):,.0f}"
    return format_price(price)


def _heat_time_bounds(book: LiquidityMap | None, candles: list[Bar]) -> tuple[int, int] | None:
    """Haritanın zaman aralığı. Mumlar içinde bulunulan saate kadar sürer."""
    if book is None or not book.times:
        return None
    start = int(book.times[0])
    end = int(book.times[-1]) + HOUR_MS
    if candles:
        step = candles[-1].time_ms - candles[-2].time_ms if len(candles) > 1 else HOUR_MS
        if step <= 0:
            step = HOUR_MS
        end = max(end, int(candles[-1].time_ms) + int(step))
    return start, max(end, start + 1)


def _heat_column_edges(book: LiquidityMap, index: int, width: int, start: int, end: int) -> tuple[int, int]:
    span_ms = max(1, end - start)
    left_ms = int(book.times[index])
    if index + 1 < len(book.times):
        right_ms = int(book.times[index + 1])
    else:
        right_ms = left_ms + HOUR_MS
    left = int((left_ms - start) / span_ms * width)
    right = int((right_ms - start) / span_ms * width)
    return left, max(left + 1, right)


class _CandleRanges:
    """Mumların 32'lik bloklarda en düşük ve en yüksek değeri; değme aramasını kısaltır."""

    BLOCK = 32

    def __init__(self, candles: list[Bar]) -> None:
        self.candles = candles
        self.times = [int(candle.time_ms) for candle in candles]
        self.blocks: list[tuple[float, float]] = []
        for start in range(0, len(candles), self.BLOCK):
            chunk = candles[start : start + self.BLOCK]
            self.blocks.append((min(bar.low for bar in chunk), max(bar.high for bar in chunk)))

    def first_touch(self, after_ms: int, bottom: float, top: float) -> int | None:
        index = bisect.bisect_left(self.times, after_ms)
        candles = self.candles
        size = self.BLOCK
        while index < len(candles):
            block = index // size
            low, high = self.blocks[block]
            if low > top or high < bottom:
                index = (block + 1) * size
                continue
            end = min(len(candles), (block + 1) * size)
            for cursor in range(index, end):
                candle = candles[cursor]
                if candle.low <= top and candle.high >= bottom:
                    return self.times[cursor]
            index = end
        return None


def _band_segments(
    book: LiquidityMap,
    hours: list[tuple[int, float, float]],
    still_open: bool,
    ranges: _CandleRanges,
    bottom: float,
    top: float,
) -> list[list]:
    """Bir fiyat bandının çizilecek saatleri. Mum bandın aralığına değince bant orada biter;
    sonra yeni pozisyon eklenirse yeniden başlar."""
    drawn: list[list] = []
    touched: int | None = None
    started = False
    previous_usd = 0.0
    last_open: list | None = None
    for column_index, usd, price in hours:
        time_ms = int(book.times[column_index])
        hidden = started and touched is not None and time_ms >= touched
        if not started or (hidden and usd > previous_usd * (1 + 1e-6)):
            if last_open is not None and touched is not None:
                last_open[3] = touched
            started = True
            touched = ranges.first_touch(time_ms + HOUR_MS, bottom, top)
            hidden = False
            last_open = None
        previous_usd = usd
        if hidden:
            if last_open is not None:
                last_open[3] = touched
                last_open = None
            continue
        entry = [column_index, usd, price, None]
        drawn.append(entry)
        last_open = entry
    if last_open is not None:
        if touched is not None:
            column_end = int(book.times[last_open[0]]) + HOUR_MS
            last_open[3] = touched if still_open else max(touched, column_end)
        elif still_open:
            last_open[3] = -1
    return drawn


def _render_heat(
    book: LiquidityMap,
    candles: list[Bar],
    width: int,
    height: int,
    threshold: float,
    hide: bool,
) -> dict | None:
    """Renkli likidite resmi. Arayüz sırasında çağrılmaz."""
    if book is None or not book.has_mass():
        return None
    lows = list(book.lows or book.closes)
    highs = list(book.highs or book.closes)
    if candles:
        lows.append(min(bar.low for bar in candles))
        highs.append(max(bar.high for bar in candles))
    bounds = _price_window(lows, highs, book.step)
    if bounds is None or not book.closes:
        return None
    y_min, y_max = bounds
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(HEAT_GROUND)
    time_bounds = _heat_time_bounds(book, candles)
    start_ms, end_ms = time_bounds if time_bounds is not None else (book.times[0], book.times[-1] + HOUR_MS)
    draw_step = _view_step(y_min, y_max, height, book.step)
    shown = [_group_column(book, index, column, draw_step) for index, column in enumerate(book.columns)]
    active: dict[int, list[tuple[int, float, float]]] = {}
    runs: list[tuple[int, list[tuple[int, float, float]], bool]] = []
    for column_index, column in enumerate(shown):
        alive: set[int] = set()
        for index, (usd, price) in column.items():
            alive.add(index)
            active.setdefault(index, []).append((column_index, usd, price))
        for index in [index for index in active if index not in alive]:
            runs.append((index, active.pop(index), False))
    for index, hours in active.items():
        runs.append((index, hours, True))
    visible: list[float] = []
    for index, hours, _open in runs:
        top = (index + 1) * draw_step
        bottom = index * draw_step
        if top < y_min or bottom > y_max:
            continue
        visible.extend(usd for _column, usd, _price in hours if usd > 0)
    ceiling = _percentile(visible, 0.99)
    floor = _percentile(visible, threshold)
    if ceiling <= floor:
        ceiling = floor + 1.0
    painter = QPainter(image)
    painter.setPen(Qt.PenStyle.NoPen)
    bands: list[tuple[float, float, int, int, int, int, int]] = []
    ranges = _CandleRanges(sorted(candles, key=lambda bar: bar.time_ms))
    span_ms = max(1, end_ms - start_ms)
    for index, hours, still_open in runs:
        top = (index + 1) * draw_step
        bottom = index * draw_step
        if top < y_min or bottom > y_max or not hours:
            continue
        y0, y1 = _bucket_pixels(index, draw_step, y_min, y_max, height)
        if y1 <= 0 or y0 >= height:
            continue
        y0 = max(0, y0)
        y1 = min(height, y1)
        band_h = max(1, y1 - y0)
        for column_index, usd, price, right_edge in _band_segments(
            book, hours, still_open, ranges, bottom, top
        ):
            if hide and usd <= floor:
                continue
            left, right = _heat_column_edges(book, column_index, width, start_ms, end_ms)
            if right_edge is not None:
                right = width if right_edge < 0 else min(width, int((right_edge - start_ms) / span_ms * width))
            if right <= left:
                continue
            amount = _heat_amount(usd, floor, ceiling)
            if amount <= 0:
                continue
            painter.fillRect(left, y0, right - left, band_h, _heat_color(amount))
            bands.append((price, usd, left, right, y0, y1, column_index))
    painter.end()
    return {
        "image": image,
        "bands": bands,
        "floor": floor,
        "hi": ceiling,
        "ymin": y_min,
        "ymax": y_max,
        "width": width,
        "height": height,
    }


class _HeatPaint(QThread):
    done = Signal(int, object)

    def __init__(self, generation: int, book, candles, width: int, height: int, threshold: float, hide: bool) -> None:
        super().__init__()
        self._generation = generation
        self._book = book
        self._candles = candles
        self._width = width
        self._height = height
        self._threshold = threshold
        self._hide = hide

    def run(self) -> None:
        try:
            payload = _render_heat(
                self._book, self._candles, self._width, self._height, self._threshold, self._hide
            )
        except Exception:
            payload = None
        self.done.emit(self._generation, payload)


class HeatMap(QWidget):
    """Zaman × fiyat likidasyon haritası. Sol listeyi değiştirmez."""

    def __init__(self) -> None:
        super().__init__()
        self._map: LiquidityMap | None = None
        self._candles: list[Bar] = []
        self._threshold = 0.45
        self._hide = False
        self._message = "Yükleniyor"
        self._image: QImage | None = None
        self._stale = False
        self._live = False
        self._rebuild = QTimer(self)
        self._rebuild.setSingleShot(True)
        self._rebuild.setInterval(60)
        self._rebuild.timeout.connect(self._rebuild_image)
        self._bands: list[tuple[float, float, int, int, int, int, int]] = []
        self._scale_floor = 0.0
        self._scale_hi = 0.0
        self._scale_hook = None
        self._plot = QRectF()
        self._y_min = 0.0
        self._y_max = 1.0
        self._heat_gen = 0
        self._heat_job: QThread | None = None
        self._heat_sig: tuple | None = None
        self._heat_again = False
        self._heat_pending: dict[int, tuple] = {}
        self._map_serial = 0
        self._band_grid: tuple[list, dict[int, list[int]]] | None = None
        self._candle_times: tuple[list, list[int]] | None = None
        self._legend: QImage | None = None
        self.setMouseTracking(True)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_live(self, live: bool) -> None:
        self._live = bool(live)
        if self._live and self._map is not None and (self._stale or self._image is None):
            self._stale = True
            self._arm_rebuild()

    def _mark_stale(self) -> None:
        self._stale = True
        self._arm_rebuild()
        self.update()

    def _arm_rebuild(self) -> None:
        if self._live and not self._rebuild.isActive():
            self._rebuild.start()

    def _rebuild_image(self) -> None:
        if not self._live or not self.isVisible():
            return
        signature = self._heat_signature()
        if signature is None:
            return
        if signature == self._heat_sig and self._image is not None:
            self._stale = False
            return
        if self._heat_busy():
            self._heat_again = True
            return
        self._heat_gen += 1
        self._heat_again = False
        width = signature[1]
        height = signature[2]
        job = _HeatPaint(self._heat_gen, self._map, list(self._candles), width, height, self._threshold, self._hide)
        self._heat_pending[self._heat_gen] = signature
        job.done.connect(self._on_heat)
        job.finished.connect(lambda finished=job: self._on_heat_finished(finished))
        self._heat_job = job
        job.start()

    def _heat_busy(self) -> bool:
        job = self._heat_job
        if job is None:
            return False
        try:
            return bool(job.isRunning())
        except RuntimeError:
            self._heat_job = None
            return False

    def _on_heat_finished(self, job) -> None:
        if self._heat_job is job:
            self._heat_job = None
        job.deleteLater()
        if self._heat_again:
            self._heat_again = False
            self._stale = True
            self._arm_rebuild()

    def _heat_signature(self) -> tuple | None:
        book = self._map
        if book is None or not book.has_mass():
            return None
        bounds = self._view_range()
        if bounds is None:
            return None
        width = max(1, self.width() - 156)
        height = max(1, self.height() - 36)
        times = book.times
        candles = self._candles
        return (
            self._map_serial,
            width,
            height,
            round(float(self._threshold), 4),
            bool(self._hide),
            int(times[0]) if times else 0,
            int(times[-1]) if times else 0,
            len(times),
            int(candles[0].time_ms) if candles else 0,
            int(candles[-1].time_ms) if candles else 0,
            len(candles),
            (candles[-1].high, candles[-1].low) if candles else None,
            round(bounds[0], 4),
            round(bounds[1], 4),
        )

    def _on_heat(self, generation: int, payload) -> None:
        signature = self._heat_pending.pop(generation, None)
        if generation != self._heat_gen:
            return
        if isinstance(payload, dict) and payload.get("image") is not None:
            self._image = payload["image"]
            self._bands = payload["bands"]
            self._scale_floor = payload["floor"]
            self._scale_hi = payload["hi"]
            self._y_min = payload["ymin"]
            self._y_max = payload["ymax"]
            self._plot = QRectF(72, 8, payload["width"], payload["height"])
            self._heat_sig = signature
            if self._scale_hook is not None:
                self._scale_hook()
        self._stale = self._heat_again
        self.update()

    def set_state(self, book: LiquidityMap | None, message: str) -> None:
        self._map = book
        self._map_serial += 1
        self._message = message
        self._heat_gen += 1
        self._heat_sig = None
        self._bands = []
        if book is None:
            self._image = None
            self._heat_again = False
            self._stale = False
            self.update()
            return
        self._mark_stale()

    def set_candles(self, bars: list[Bar]) -> None:
        self._candles = list(bars)
        self._mark_stale()

    def set_threshold(self, value: float) -> None:
        self._threshold = min(1.0, max(0.0, value))
        self._mark_stale()

    def set_hide(self, hide: bool) -> None:
        self._hide = bool(hide)
        self._mark_stale()

    def resizeEvent(self, event) -> None:
        self._mark_stale()
        super().resizeEvent(event)

    def _view_range(self) -> tuple[float, float] | None:
        book = self._map
        if book is None or not book.closes:
            return None
        lows = list(book.lows or book.closes)
        highs = list(book.highs or book.closes)
        if self._candles:
            lows.append(min(bar.low for bar in self._candles))
            highs.append(max(bar.high for bar in self._candles))
        return _price_window(lows, highs, book.step)

    def _time_bounds(self) -> tuple[int, int] | None:
        return _heat_time_bounds(self._map, self._candles)

    def _column_edges(self, index: int, width: int, start: int, end: int) -> tuple[int, int]:
        book = self._map
        if book is None:
            return 0, 1
        return _heat_column_edges(book, index, width, start, end)

    def _price_y(self, price: float) -> float:
        span = self._y_max - self._y_min
        if span <= 0:
            return self._plot.top()
        return self._plot.top() + (self._y_max - price) / span * self._plot.height()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0b0b0d"))
        book = self._map
        if book is None or not book.has_mass():
            painter.setPen(MUTED)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message or "Veri yok")
            self._paint_frame(painter)
            painter.end()
            return
        if self._stale:
            self._arm_rebuild()
        if self._image is None:
            painter.setPen(MUTED)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message or "Yükleniyor")
            self._paint_frame(painter)
            painter.end()
            return
        painter.drawImage(self._plot.topLeft(), self._image)
        painter.fillRect(
            QRectF(self._plot.right(), self._plot.top(), max(0, self.width() - self._plot.right()), self._plot.height()),
            QColor("#18181b"),
        )
        gap = 8
        label_h = 14
        bar_top = self._plot.top() + label_h + gap
        bar_bottom = self._plot.bottom() - label_h - gap
        if bar_bottom - bar_top < 24:
            bar_top = self._plot.top()
            bar_bottom = self._plot.bottom()
        legend = QRectF(8, bar_top, 12, max(1.0, bar_bottom - bar_top))
        painter.drawImage(legend.topLeft(), self._legend_image(legend.height()))
        painter.setPen(MUTED)
        painter.setFont(QFont("Consolas", 8))
        painter.drawText(
            QRectF(0, bar_top - gap - label_h, 70, label_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            format_usd(self._scale_hi),
        )
        painter.drawText(
            QRectF(0, bar_bottom + gap, 70, label_h),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            format_usd(self._scale_floor),
        )
        painter.setClipRect(self._plot)
        candles = self._candles
        if candles and book.times:
            bounds = self._time_bounds()
            start, end = bounds if bounds is not None else (book.times[0], book.times[-1] + HOUR_MS)
            span_ms = max(1, end - start)
            bar_ms = candles[1].time_ms - candles[0].time_ms if len(candles) > 1 else HOUR_MS
            body = max(1.0, self._plot.width() * bar_ms / span_ms * 0.72)
            for candle in candles:
                x = self._plot.left() + (candle.time_ms + bar_ms / 2 - start) / span_ms * self._plot.width()
                if x < self._plot.left() or x > self._plot.right():
                    continue
                color = UP if candle.close >= candle.open else DOWN
                painter.setPen(QPen(color, 1))
                painter.drawLine(
                    QPointF(x, self._price_y(candle.high)),
                    QPointF(x, self._price_y(candle.low)),
                )
                top = self._price_y(max(candle.open, candle.close))
                bottom = self._price_y(min(candle.open, candle.close))
                body_rect = QRectF(x - body / 2, top, body, max(1.0, bottom - top))
                painter.fillRect(body_rect, color)
        painter.setClipping(False)
        painter.setPen(MUTED)
        painter.setFont(QFont("Consolas", 8))
        step = _tick_step(self._y_max - self._y_min)
        price = math.ceil(self._y_min / step - 1e-9) * step
        guard = 0
        while price <= self._y_max + step * 1e-9 and guard < 24:
            y = self._price_y(price)
            painter.drawText(QRectF(self._plot.right() + 6, y - 8, 74, 16), format_price(price))
            price += step
            guard += 1
        painter.setPen(MUTED)
        if book.times:
            bounds = self._time_bounds()
            start, end = bounds if bounds is not None else (book.times[0], book.times[-1] + HOUR_MS)
            span_ms = max(1, end - start)
            first = datetime.fromtimestamp(start / 1000)
            day = datetime(first.year, first.month, first.day)
            if int(day.timestamp() * 1000) < start:
                day += timedelta(days=1)
            last_x = -10_000.0
            while int(day.timestamp() * 1000) <= end:
                stamp = int(day.timestamp() * 1000)
                text = f"{day.day} {_SHORT_MONTHS[day.month]}"
                x = self._plot.left() + (stamp - start) / span_ms * self._plot.width()
                if x - last_x >= 40:
                    painter.drawText(QRectF(x, self._plot.bottom() + 4, 64, 16), text)
                    last_x = x
                day += timedelta(days=1)
        self._paint_frame(painter)
        painter.end()

    def _paint_frame(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor("#3f3f46"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

    def _legend_image(self, height: float) -> QImage:
        rows = int(height)
        cached = self._legend
        if cached is not None and cached.height() == max(rows, 1):
            return cached
        image = QImage(12, max(rows, 1), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        for step in range(rows):
            amount = 1 - step / max(1, height - 1)
            painter.fillRect(QRectF(0, step, 12, 1), _heat_color(amount))
        painter.end()
        self._legend = image
        return image

    def _candle_at(self, pos: QPointF) -> Bar | None:
        book = self._map
        candles = self._candles
        if book is None or not book.times or not candles or not self._plot.contains(pos):
            return None
        bounds = self._time_bounds()
        if bounds is None:
            return None
        start, end = bounds
        span_ms = max(1, end - start)
        bar_ms = candles[1].time_ms - candles[0].time_ms if len(candles) > 1 else HOUR_MS
        body = max(4.0, self._plot.width() * bar_ms / span_ms * 0.72)
        if self._plot.width() > 0:
            cached = self._candle_times
            if cached is None or cached[0] is not candles:
                cached = self._candle_times = (candles, [candle.time_ms for candle in candles])
            times = cached[1]
            at = start + (pos.x() - self._plot.left()) / self._plot.width() * span_ms - bar_ms / 2
            reach = (body / 2 + 3) / self._plot.width() * span_ms
            first = bisect.bisect_left(times, at - reach)
            last = bisect.bisect_right(times, at + reach)
            candles = candles[first:last]
        for candle in candles:
            x = self._plot.left() + (candle.time_ms + bar_ms / 2 - start) / span_ms * self._plot.width()
            if abs(pos.x() - x) > body / 2 + 2:
                continue
            top = self._price_y(candle.high) - 4
            bottom = self._price_y(candle.low) + 4
            if top <= pos.y() <= bottom:
                return candle
        return None

    def _band_at(self, pos: QPointF) -> tuple[int, float, float] | None:
        book = self._map
        if book is None or not book.times or not book.step or not self._plot.contains(pos):
            return None
        if self._plot.width() <= 0:
            return None
        image_x = pos.x() - self._plot.left()
        image_y = pos.y() - self._plot.top()
        bands = self._bands
        cached = self._band_grid
        if cached is None or cached[0] is not bands:
            grid: dict[int, list[int]] = {}
            for index, band in enumerate(bands):
                for cell in range(math.floor(band[2] / 16), math.floor(band[3] / 16) + 1):
                    grid.setdefault(cell, []).append(index)
            cached = self._band_grid = (bands, grid)
        for index in reversed(cached[1].get(math.floor(image_x / 16), ())):
            price, usd, left, right, top, bottom, column_index = bands[index]
            if left <= image_x <= right and top <= image_y <= bottom:
                return (column_index, price, usd)
        return None

    def mouseMoveEvent(self, event) -> None:
        book = self._map
        local = event.position()
        if book is None or not book.times or not self._plot.contains(local):
            QToolTip.hideText()
            return
        band = self._band_at(local)
        if band is not None and 0 <= band[0] < len(book.times):
            column_index, price, usd = band
            moment = datetime.fromtimestamp(book.times[column_index] / 1000)
            text = (
                f"{_range_stamp(moment)}\n"
                f"Fiyat {format_price(price)}\n"
                f"Likidite {format_usd(usd)}"
            )
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
            return
        candle = self._candle_at(local)
        if candle is None:
            QToolTip.hideText()
            return
        moment = datetime.fromtimestamp(candle.time_ms / 1000)
        text = (
            f"{_range_stamp(moment)}\n"
            f"Açılış {format_price(candle.open)}\n"
            f"Yüksek {format_price(candle.high)}\n"
            f"Düşük {format_price(candle.low)}\n"
            f"Kapanış {format_price(candle.close)}"
        )
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


TARGET_TOP = 4000


class _TargetTable:
    """Bir saat aralığının hedef adayları. En büyük kovalar, eski tarama sırasıyla."""

    def __init__(self, shown: LiquidityMap) -> None:
        self.symbol = shown.symbol
        self.shown = shown
        self.window = None
        self.top: list[tuple[float, float]] = []
        self.complete = True
        if not shown.columns or shown.step <= 0:
            return
        self.window = _price_window(list(shown.lows or shown.closes), list(shown.highs or shown.closes), shown.step)
        if self.window is None:
            return
        counter = [0]

        def entries():
            for level, usd in self._entries():
                counter[0] += 1
                yield level, usd

        self.top = heapq.nlargest(TARGET_TOP, entries(), key=lambda entry: entry[1])
        self.complete = counter[0] <= TARGET_TOP

    def _entries(self):
        shown = self.shown
        low, high = self.window
        step = shown.step
        for offset, column in enumerate(shown.columns):
            prices = shown.levels[offset] if offset < len(shown.levels) else {}
            for index, usd in column.items():
                if usd <= 0:
                    continue
                level = prices.get(index, (index + 0.5) * step)
                if level < low or level > high:
                    continue
                yield level, usd

    def pick(self, side: str, price: float | None) -> float | None:
        shown = self.shown
        if self.window is None:
            return None
        spot = price if isinstance(price, (int, float)) and price > 0 else (shown.closes[-1] if shown.closes else 0)
        if spot <= 0:
            return None
        fallback = self.top[0][0] if self.top else None
        for level, _usd in self.top:
            if side == "up" and level <= spot:
                continue
            if side == "down" and level >= spot:
                continue
            return level
        if self.complete:
            return fallback
        best: float | None = None
        best_usd = 0.0
        for level, usd in self._entries():
            if side == "up" and level <= spot:
                continue
            if side == "down" and level >= spot:
                continue
            if best is None or usd > best_usd:
                best_usd = usd
                best = level
        return best if best is not None else fallback


def _clip_set(book: LiquidityMap, spans: set[int], target_hours: set[int], conserved: bool) -> tuple[dict, dict]:
    clips: dict[tuple[int, bool], LiquidityMap] = {}
    targets: dict[tuple[int, bool], _TargetTable] = {}
    for hours in sorted(spans | target_hours):
        shown = clip_map(book, hours, conserved) if book.source_bars or book.venues else book
        clips[(hours, conserved)] = shown
        if hours in target_hours:
            targets[(hours, conserved)] = _TargetTable(shown)
    return clips, targets


class LiquidityWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, symbol: str, span: int = 0, conserved: bool = False) -> None:
        super().__init__()
        self._symbol = symbol
        self._span = span
        self._conserved = conserved

    def run(self) -> None:
        try:
            book = load_liquidity(self._symbol)
            clips, targets = _clip_set(
                book, {self._span} if self._span > 0 else set(), set(SIGNAL_LIQ_HOURS.values()), self._conserved
            )
            self.done.emit({"book": book, "clips": clips, "targets": targets})
        except MarketError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(str(exc) or "likidasyon alınamadı")


class _ClipWorker(QThread):
    done = Signal(object)

    def __init__(self, book: LiquidityMap, spans: set[int], target_hours: set[int], conserved: bool) -> None:
        super().__init__()
        self._book = book
        self._spans = set(spans)
        self._target_hours = set(target_hours)
        self._conserved = conserved

    def run(self) -> None:
        try:
            clips, targets = _clip_set(self._book, self._spans, self._target_hours, self._conserved)
        except Exception:
            clips, targets = {}, {}
        self.done.emit({"book": self._book, "clips": clips, "targets": targets})


class _LiqCandleWorker(QThread):
    done = Signal(object)

    def __init__(self, symbol: str, interval: str, limit: int, bar_ms: int) -> None:
        super().__init__()
        self._symbol = symbol
        self._interval = interval
        self._limit = limit
        self._bar_ms = bar_ms

    def run(self) -> None:
        try:
            bars = fetch_klines(self._symbol, self._interval, self._limit, True)
        except MarketError:
            bars = []
        self.done.emit(bars)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._symbol, self._interval = _load_state()
        self._watch = _load_watchlist(self._symbol)
        if not isinstance(_read_state().get("watchlist"), list):
            _patch_state(watchlist=self._watch)
        self._quotes: list[Quote] = []
        self._by_symbol: dict[str, Quote] = {}
        self._snap: SnapshotWorker | None = None
        self._liq_worker: LiquidityWorker | None = None
        self._liq_candle_worker: _LiqCandleWorker | None = None
        self._liq_candle_gen = 0
        self._liq_gen = 0
        self._liq_map: LiquidityMap | None = None
        self._liq_full: LiquidityMap | None = None
        self._liq_clips: dict[tuple[int, bool], LiquidityMap] = {}
        self._liq_targets: dict[tuple[int, bool], _TargetTable] = {}
        self._clip_worker: _ClipWorker | None = None
        self._clip_gen = 0
        self._liq_hour: int | None = None
        saved_span = _read_state().get("liq_span")
        self._liq_span = int(saved_span) if saved_span in {hours for hours, _label in LIQ_SPANS} else 72
        saved_threshold = _read_state().get("liq_threshold")
        raw_threshold = (
            float(saved_threshold)
            if isinstance(saved_threshold, (int, float)) and 0 <= float(saved_threshold) <= 1
            else 0.45
        )
        self._liq_threshold = round(raw_threshold * 20) / 20
        saved_mode = str(_read_state().get("liq_scale_mode") or "kirp")
        self._liq_scale_mode = saved_mode if saved_mode in ("kirp", "gizle") else "kirp"
        self._liq_conserved = bool(_read_state().get("liq_conserved"))
        self._list_worker: QuoteWorker | None = None
        self._retained: list[QThread] = []
        self._snap_gen = 0
        self._list_gen = 0
        self._adjusting = False
        self._last_candles = []
        self._last_oi = []
        self._last_funding = []
        self._funding_shown = []
        self._telegram_jobs: list[QThread] = []
        self._index_mode = False
        self._index_gen = 0
        self._index_worker: QThread | None = None
        self._index_quote_worker: QThread | None = None
        self._index_quote_map: dict[str, Quote] = {}
        self._okx_worker: QThread | None = None
        self._okx_ping: QThread | None = None
        self._okx_wait: list[dict] = []
        self._okx_live: list[dict] = []
        self._okx_live_error = ""
        self._okx_available: float | None = None
        self._okx_polled = 0.0
        self._okx_row_keys: list[str] = []
        self._okx_positions_worker: _OkxPositions | None = None
        self._okx_action_worker: _OkxAction | None = None
        self._oi_scale = (1.0, "")
        self._recent_start_ms = 0
        self._history_done = False
        self._history_gen = 0
        self._history_worker: QThread | None = None
        self._paging = False
        self._session_change: float | None = None
        self._alarms = _load_alarms()
        self._windows_on = _load_windows()
        self._bb = _load_bollinger()
        self._signal = _load_signal()
        self._signal_marks = _load_signal_marks()
        self._band_points: list[tuple[float, float | None, float | None, float | None]] = []
        self._drawings = _load_drawings()
        self._last_trades: dict[str, float] = {}
        self._background = False
        self._page = "market"
        self._quitting = False
        self._live_price: float | None = None
        self._live_set: QCandlestickSet | None = None
        self._live_up = True
        self._up_series: QCandlestickSeries | None = None
        self._down_series: QCandlestickSeries | None = None
        self._drawn_times: list[int] = []
        self._drawn_symbol: str | None = None
        self._drawn_interval: str | None = None
        self._drawn_sets: dict[int, QCandlestickSet] = {}
        self._live_15_anchor: float | None = None
        self.setWindowTitle("CryptoHub")
        self.resize(1280, 820)
        self.setMinimumSize(1020, 680)
        self._build()
        self._render_list(True)
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.timeout.connect(self._flush_live)
        self._okx_timer = QTimer(self)
        self._okx_timer.setInterval(5_000)
        self._okx_timer.timeout.connect(self._poll_okx)
        self._okx_timer.start()
        QTimer.singleShot(1_500, lambda: self._poll_okx(True))
        self._feed = _TradeFeed(self)
        self._feed.prices.connect(self._on_trades)
        self._liq_tape = LiquidationTape(self)
        self._liq_tape.start()
        self._build_tray()
        self._show_symbol(self._symbol)
        self._start_list()
        self._start_snapshot(force=True)

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self._h_split = QSplitter(Qt.Orientation.Horizontal)
        self._h_split.setChildrenCollapsible(False)
        self._h_split.setHandleWidth(0)
        layout.addWidget(self._h_split)
        self._list_panel = self._build_list()
        self._list_host = QWidget()
        self._list_host.setFixedWidth(280)
        host_layout = QVBoxLayout(self._list_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        host_layout.addWidget(self._list_panel)
        self._h_split.addWidget(self._list_host)
        self._h_split.addWidget(self._build_detail())
        self._h_split.setStretchFactor(0, 0)
        self._h_split.setStretchFactor(1, 1)
        self._panel_widgets = {
            "list": self._list_host,
            "price": self.price_view,
            "oi": self.oi_view,
            "funding": self._funding_panel,
            "cards": self._cards_panel,
            "analysis": self._analysis_panel,
        }
        self._panel_hold = {
            "list": 300,
            "price": 340,
            "oi": 160,
            "funding": 140,
            "cards": 100,
            "analysis": 260,
        }
        self._restore_panes()
        self._build_panel_menu()
        self._build_settings_menu()
        self.menuBar().setNativeMenuBar(False)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(250)
        self._layout_timer.timeout.connect(self._remember_layout)
        self._h_split.splitterMoved.connect(lambda *_args: self._layout_timer.start())
        self._v_split.splitterMoved.connect(lambda *_args: self._layout_timer.start())
        self.setStatusBar(QStatusBar())
        self._find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self._find_shortcut.activated.connect(self._focus_search)
        self._sync_indicator_caption()

    def _build_list(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setMinimumHeight(160)
        column = QVBoxLayout(panel)
        column.setContentsMargins(6, 8, 6, 6)
        column.setSpacing(6)
        brand = QLabel("CRYPTOHUB")
        brand.setObjectName("brand")
        brand.setStyleSheet(
            "background: #121216; color: #71717a; font-size: 11px; letter-spacing: 2px; border: none;"
        )
        def tool_button(icon: QIcon, tip: str, checkable: bool = True) -> QPushButton:
            button = QPushButton()
            button.setObjectName("tool")
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(34, 34)
            button.setToolTip(tip)
            button.setCheckable(checkable)
            return button

        self._list_button = tool_button(_icon_list(), "Liste")
        self._alarm_button = tool_button(_icon_alarm(), self._alarm_caption())
        self._draw_button = tool_button(_icon_draw(), "Çizim")
        self._indicator_button = tool_button(_icon_indicator(), "İndikatör")
        self._clear_button = tool_button(_icon_trash(), "Grafiği temizle", checkable=False)
        self._list_button.setChecked(True)
        tab_group = QButtonGroup(panel)
        tab_group.setExclusive(True)
        tab_group.addButton(self._list_button)
        tab_group.addButton(self._alarm_button)
        tab_group.addButton(self._draw_button)
        tab_group.addButton(self._indicator_button)
        self._list_button.clicked.connect(self._show_coin_list)
        self._alarm_button.clicked.connect(self._show_alarm_list)
        self._draw_button.clicked.connect(self._show_draw)
        self._indicator_button.clicked.connect(self._show_indicators)
        self._clear_button.clicked.connect(self._clear_chart_marks)
        rail = QVBoxLayout()
        rail.setSpacing(6)
        rail.addWidget(self._list_button)
        rail.addWidget(self._alarm_button)
        rail.addWidget(self._draw_button)
        rail.addWidget(self._indicator_button)
        rail.addStretch(1)
        rail.addWidget(self._clear_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Coin ara, ★ ile listeye ekle")
        self.search.textChanged.connect(lambda _text: self._render_list(True))
        self.search.returnPressed.connect(self._select_first)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Coin", "Fiyat", "24s"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.setFont(QFont("Consolas", 10))
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 28)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self.table.cellClicked.connect(self._on_cell)
        _darken(self.table)
        self._coin_page = QWidget()
        coin_layout = QVBoxLayout(self._coin_page)
        coin_layout.setContentsMargins(0, 0, 0, 0)
        coin_layout.setSpacing(8)
        coin_layout.addWidget(self.search)
        coin_layout.addWidget(self.table)
        self._alarm_page = QWidget()
        alarm_layout = QVBoxLayout(self._alarm_page)
        alarm_layout.setContentsMargins(0, 0, 0, 0)
        alarm_layout.setSpacing(8)
        self.alarm_empty = QLabel("Kurulu alarm yok. Grafiğe sağ tıklayıp Alarm ekle.")
        self.alarm_empty.setStyleSheet("color: #71717a; font-size: 12px;")
        self.alarm_empty.setWordWrap(True)
        self.alarm_table = QTableWidget(0, 3)
        self.alarm_table.setHorizontalHeaderLabels(["Coin", "Fiyat", ""])
        self.alarm_table.verticalHeader().setVisible(False)
        self.alarm_table.verticalHeader().setDefaultSectionSize(32)
        self.alarm_table.setShowGrid(False)
        self.alarm_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.alarm_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.alarm_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.alarm_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        alarm_header = self.alarm_table.horizontalHeader()
        alarm_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        alarm_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        alarm_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.alarm_table.setColumnWidth(2, 52)
        self.alarm_table.cellClicked.connect(self._on_alarm_cell)
        self.alarm_table.cellDoubleClicked.connect(self._edit_alarm_cell)
        _darken(self.alarm_table)
        alarm_layout.addWidget(self.alarm_empty)
        alarm_layout.addWidget(self.alarm_table)
        self._draw_page = QWidget()
        draw_layout = QVBoxLayout(self._draw_page)
        draw_layout.setContentsMargins(0, 0, 0, 0)
        draw_layout.setSpacing(6)

        def menu_button(icon: QIcon, text: str) -> QPushButton:
            button = QPushButton(text)
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                "QPushButton { background: transparent; color: #a1a1aa; border: 1px solid #3f3f46; "
                "border-radius: 7px; text-align: left; padding: 6px 8px; }"
                "QPushButton:hover { color: #fafafa; }"
                "QPushButton:checked { background: #fafafa; color: #09090b; border-color: #fafafa; }"
            )
            return button

        self._trend_button = menu_button(_icon_trend(), "Trend çizgisi")
        self._range_button = menu_button(_icon_range(), "Fiyat aralığı")
        self._level_button = menu_button(_icon_level(), "Yatay çizgi")
        tool_group = QButtonGroup(self._draw_page)
        tool_group.setExclusive(True)
        tool_group.addButton(self._trend_button)
        tool_group.addButton(self._range_button)
        tool_group.addButton(self._level_button)
        self._trend_button.clicked.connect(self._apply_draw_tool)
        self._range_button.clicked.connect(self._apply_draw_tool)
        self._level_button.clicked.connect(self._apply_draw_tool)
        draw_layout.addWidget(self._trend_button)
        draw_layout.addWidget(self._range_button)
        draw_layout.addWidget(self._level_button)
        draw_layout.addStretch(1)
        self._indicator_page = QWidget()
        indicator_layout = QVBoxLayout(self._indicator_page)
        indicator_layout.setContentsMargins(0, 0, 0, 0)
        indicator_layout.setSpacing(8)
        self._panel_toggles = {
            "oi": _panel_toggle("Open interest"),
            "funding": _panel_toggle("Funding"),
        }
        add_bb = QPushButton("Bollinger Bandı")
        add_bb.setIcon(_icon_indicator())
        add_bb.setIconSize(QSize(16, 16))
        add_bb.setCursor(Qt.CursorShape.PointingHandCursor)
        add_bb.setStyleSheet(
            "QPushButton { background: transparent; color: #a1a1aa; border: 1px solid #3f3f46; "
            "border-radius: 7px; text-align: left; padding: 6px 8px; }"
            "QPushButton:hover { color: #fafafa; }"
        )
        add_bb.clicked.connect(self._add_bollinger)
        add_signal = QPushButton("Signal")
        add_signal.setIcon(_icon_indicator())
        add_signal.setIconSize(QSize(16, 16))
        add_signal.setCursor(Qt.CursorShape.PointingHandCursor)
        add_signal.setStyleSheet(add_bb.styleSheet())
        add_signal.clicked.connect(self._add_signal)
        for button in self._panel_toggles.values():
            button.setIcon(_icon_indicator())
            button.setIconSize(QSize(16, 16))
        indicator_layout.addWidget(add_bb)
        indicator_layout.addWidget(add_signal)
        indicator_layout.addWidget(self._panel_toggles["oi"])
        indicator_layout.addWidget(self._panel_toggles["funding"])
        indicator_layout.addStretch(1)
        column.addWidget(brand)
        body = QHBoxLayout()
        body.setSpacing(8)
        body.addLayout(rail)
        pages = QVBoxLayout()
        pages.setSpacing(0)
        self._coin_page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._alarm_page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._draw_page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._indicator_page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        pages.addWidget(self._coin_page, 1)
        pages.addWidget(self._alarm_page, 1)
        pages.addWidget(self._draw_page, 1)
        pages.addWidget(self._indicator_page, 1)
        body.addLayout(pages, 1)
        column.addLayout(body, 1)
        self._alarm_page.hide()
        self._draw_page.hide()
        self._indicator_page.hide()
        return panel

    def _build_active(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        column = QVBoxLayout(panel)
        column.setContentsMargins(10, 10, 10, 10)
        column.setSpacing(8)
        self._active_title = QLabel("Aktif işlem")
        self._active_title.setObjectName("metricTitle")
        self._active_body = QLabel("—")
        self._active_body.setWordWrap(True)
        self._active_body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._active_body.setStyleSheet(
            "color: #e4e4e7; font-size: 14px; font-family: Consolas, monospace; border: none; background: transparent;"
        )
        column.addWidget(self._active_title)
        column.addWidget(self._active_body, 1)
        return panel

    def _alarm_caption(self) -> str:
        count = sum(len(levels) for levels in self._alarms.values())
        if count:
            return f"Alarmlar ({count})"
        return "Alarmlar"

    def _focus_search(self) -> None:
        self._show_coin_list()
        self.search.setFocus()

    def _show_coin_list(self) -> None:
        self._list_button.setChecked(True)
        self._alarm_page.hide()
        self._draw_page.hide()
        self._indicator_page.hide()
        self._coin_page.show()
        self._apply_draw_tool()

    def _show_alarm_list(self) -> None:
        self._alarm_button.setChecked(True)
        self._coin_page.hide()
        self._draw_page.hide()
        self._indicator_page.hide()
        self._alarm_page.show()
        self._apply_draw_tool()
        self._fill_alarm_table()

    def _show_draw(self) -> None:
        self._draw_button.setChecked(True)
        self._coin_page.hide()
        self._alarm_page.hide()
        self._indicator_page.hide()
        self._draw_page.show()
        self._apply_draw_tool()

    def _show_indicators(self) -> None:
        self._indicator_button.setChecked(True)
        self._coin_page.hide()
        self._alarm_page.hide()
        self._draw_page.hide()
        self._indicator_page.show()
        self._apply_draw_tool()

    def _apply_draw_tool(self) -> None:
        tool = None
        if self._draw_page.isVisible():
            if self._level_button.isChecked():
                tool = "level"
            elif self._range_button.isChecked():
                tool = "range"
            elif self._trend_button.isChecked():
                tool = "trend"
        self.price_view.set_draw_tool(tool)

    def _add_bollinger(self) -> None:
        if self._bb.get("on"):
            self.statusBar().showMessage("Bollinger Bandı zaten grafikte")
            return
        self._bb["on"] = True
        self._store_bb()

    def _close_bollinger(self) -> None:
        self._bb["on"] = False
        self._store_bb()

    def _edit_bollinger(self) -> None:
        updated = _bollinger_dialog(self, self._bb)
        if updated is None:
            return
        self._bb = updated
        self._store_bb()

    def _add_signal(self) -> None:
        if self._signal.get("on"):
            self.statusBar().showMessage("Signal zaten grafikte")
            return
        self._signal["on"] = True
        self._store_signal()

    def _close_signal(self) -> None:
        self._signal["on"] = False
        self._store_signal()

    def _edit_signal(self) -> None:
        updated = _signal_dialog(self, self._signal)
        if updated is None:
            return
        self._signal = updated
        self._store_signal()

    def _store_signal(self) -> None:
        _patch_state(signal_draw=self._signal)
        self._sync_indicator_caption()
        self._sync_signals()

    def _capture_open_marks(self) -> None:
        """Grafiğe eklenince açık sinyallerin saati oka dönüşür."""
        stored = _read_state().get("signal_open")
        entry = stored.get(self._symbol) if isinstance(stored, dict) else None
        if not isinstance(entry, dict):
            return
        seen = {
            (item.get("symbol"), item.get("label"), item.get("time_ms"), item.get("side"))
            for item in self._signal_marks
        }
        fresh = []
        for label in SIGNAL_WINDOWS:
            mark = _mark_from_episode(self._symbol, label, entry.get(label))
            if mark is None:
                continue
            key = (mark["symbol"], mark["label"], mark["time_ms"], mark["side"])
            if key in seen:
                continue
            seen.add(key)
            fresh.append(mark)
        if fresh:
            self._signal_marks.extend(fresh)
        trimmed = _trim_signal_marks(self._signal_marks)
        if not fresh and len(trimmed) == len(self._signal_marks):
            self._signal_marks = trimmed
            return
        self._signal_marks = trimmed
        _patch_state(signal_marks=self._signal_marks)

    def _sync_signals(self) -> None:
        if not hasattr(self, "price_view"):
            return
        self._paint_position_lines()
        if not self._signal.get("on") or self._index_mode:
            self.price_view.set_signals([], "")
            return
        label = _signal_window(self._interval)
        if label is None:
            self.price_view.set_signals([], "")
            return
        self._capture_open_marks()
        marks = _visible_marks(
            self._signal_marks,
            self._symbol,
            label,
            bool(self._signal.get("show_closed", True)),
        )
        score = _read_state().get("signal_score")
        coin = score.get(self._symbol) if isinstance(score, dict) else {}
        text = f"{label}  {_score_line(coin if isinstance(coin, dict) else {}, label)}"
        self.price_view.set_signals(marks, text)

    def _store_bb(self) -> None:
        _patch_state(bollinger=self._bb)
        self._sync_indicator_caption()
        self._refresh_bands()
        if not self.price_view.zoomed and not self.price_view.price_manual:
            self._fit_price()

    def _sync_indicator_caption(self) -> None:
        if not hasattr(self, "_indicator_rows"):
            return
        while self._indicator_rows.count():
            item = self._indicator_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if self._bb.get("on"):
            self._indicator_chip("Bollinger Bandı", self._close_bollinger, self._edit_bollinger)
        if self._signal.get("on"):
            self._indicator_chip("Signal", self._close_signal, self._edit_signal)
        self._place_caption()

    def _indicator_chip(self, title: str, close_action, settings_action) -> None:
        row = QWidget()
        row.setObjectName("chip")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        row.setStyleSheet(
            "QWidget#chip { background: #27272a; border-radius: 5px; }"
            "QLabel { color: #e4e4e7; font-size: 11px; background: transparent; }"
            "QPushButton { background: transparent; color: #a1a1aa; border: none; padding: 0; }"
            "QPushButton:hover { color: #fafafa; }"
        )
        line = QHBoxLayout(row)
        line.setContentsMargins(8, 2, 4, 2)
        line.setSpacing(2)
        name = QLabel(title)
        close = QPushButton()
        settings = QPushButton()
        for button, icon, tip, action in (
            (close, _icon_close(), "Kaldır", close_action),
            (settings, _icon_settings(), "Ayarlar", settings_action),
        ):
            button.setIcon(icon)
            button.setIconSize(QSize(12, 12))
            button.setFixedSize(18, 18)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(action)
        line.addWidget(name)
        line.addWidget(close)
        line.addWidget(settings)
        line.addStretch(1)
        self._indicator_rows.addWidget(row)

    def _place_caption(self) -> None:
        if not hasattr(self, "_chart_caption"):
            return
        self._chart_caption.adjustSize()
        self._chart_caption.move(8, 4)
        self._chart_caption.raise_()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "price_view", None) and event.type() == QEvent.Type.Resize:
            self._place_caption()
        return super().eventFilter(watched, event)

    def _toggle_window(self, label: str, checked: bool) -> None:
        if checked:
            self._windows_on.add(label)
        else:
            self._windows_on.discard(label)
        _patch_state(windows=[name for name in WINDOW_LABELS if name in self._windows_on])
        self._paint_window_menu()
        self._start_snapshot(force=True)

    def _paint_interval_menu(self) -> None:
        buttons = getattr(self, "_interval_buttons", None)
        if not buttons:
            return
        for key, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(key == self._interval)
            button.blockSignals(False)

    def _paint_window_menu(self) -> None:
        if not hasattr(self, "_window_menu_button"):
            return
        names = [name for name in WINDOW_LABELS if name in self._windows_on]
        self._window_menu_button.setText(", ".join(names) if names else "Aralık yok")

    def _toggle_list_panel(self) -> None:
        visible = not self._panel_on.get("list", True)
        action = self._panel_actions.get("list")
        if action is not None:
            action.setChecked(visible)
        else:
            self._set_panel("list", visible)

    def _add_drawing(self, kind: str, start: tuple[float, float], end: tuple[float, float]) -> None:
        lines = self._drawings.setdefault(self._symbol, [])
        if kind == "level":
            lines.append({"kind": "level", "price": float(start[1])})
        else:
            lines.append({"kind": kind, "t1": start[0], "p1": start[1], "t2": end[0], "p2": end[1]})
        self.price_view.set_drawings(lines, selected=len(lines) - 1)
        self._save_drawings()

    def _move_drawing(self, index: int, start: tuple[float, float], end: tuple[float, float]) -> None:
        lines = self._drawings.get(self._symbol, [])
        if not 0 <= index < len(lines):
            return
        if lines[index].get("kind") == "level":
            lines[index]["price"] = float(end[1])
        else:
            lines[index]["t1"], lines[index]["p1"] = start
            lines[index]["t2"], lines[index]["p2"] = end
        self.price_view.set_drawings(lines, selected=index)
        self._save_drawings()

    def _remove_drawing(self, index: int) -> None:
        lines = self._drawings.get(self._symbol, [])
        if not 0 <= index < len(lines):
            return
        del lines[index]
        if not lines:
            self._drawings.pop(self._symbol, None)
        self.price_view.set_drawings(self._drawings.get(self._symbol, []))
        self._save_drawings()

    def _save_drawings(self) -> None:
        payload = {}
        for symbol, lines in self._drawings.items():
            if not lines:
                continue
            packed = []
            for item in lines:
                if item.get("kind") == "level":
                    packed.append(["level", item["price"]])
                else:
                    packed.append([item["kind"], item["t1"], item["p1"], item["t2"], item["p2"]])
            payload[symbol] = packed
        _patch_state(drawings=payload, trends={})

    def _clear_chart_marks(self) -> None:
        self._alarms.pop(self._symbol, None)
        self._drawings.pop(self._symbol, None)
        self._save_drawings()
        _patch_state(alarms=self._alarms)
        self.price_view.set_alarms([])
        self.price_view.set_drawings([])
        self._refresh_alarms()
        self._sync_feed()
        self.statusBar().showMessage(
            f"{base_of(self._symbol)} grafiğindeki alarmlar ve çizimler temizlendi"
        )

    def _refresh_alarms(self) -> None:
        self._alarm_button.setToolTip(self._alarm_caption())
        if self._alarm_page.isVisible():
            QTimer.singleShot(0, self._fill_alarm_table)

    def _fill_alarm_table(self) -> None:
        rows: list[tuple[str, float]] = []
        for symbol in sorted(self._alarms):
            for price in sorted(self._alarms[symbol]):
                rows.append((symbol, price))
        self.alarm_empty.setVisible(not rows)
        self.alarm_table.setVisible(bool(rows))
        self.alarm_table.setRowCount(len(rows))
        for index, (symbol, price) in enumerate(rows):
            coin = QTableWidgetItem(base_of(symbol))
            coin.setData(Qt.ItemDataRole.UserRole, symbol)
            coin.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            coin.setForeground(FLAT if symbol == self._symbol else MUTED)
            price_item = QTableWidgetItem(format_price(price))
            price_item.setData(Qt.ItemDataRole.UserRole, price)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            price_item.setForeground(ALARM)
            self.alarm_table.setItem(index, 0, coin)
            self.alarm_table.setItem(index, 1, price_item)
            button = QPushButton("Sil")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("padding: 1px 6px; font-size: 11px;")
            button.clicked.connect(
                lambda _checked=False, symbol=symbol, price=price: self._remove_alarm(price, symbol)
            )
            self.alarm_table.setCellWidget(index, 2, button)

    def _on_alarm_cell(self, row: int, column: int) -> None:
        if column != 0:
            return
        item = self.alarm_table.item(row, 0)
        if item is None:
            return
        symbol = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not symbol or symbol == self._symbol:
            return
        self._symbol = symbol
        _save_state(self._symbol, self._interval)
        self._show_symbol(symbol)
        self._start_snapshot(force=True)

    def _edit_alarm_cell(self, row: int, column: int) -> None:
        if column != 1:
            return
        coin = self.alarm_table.item(row, 0)
        price_item = self.alarm_table.item(row, 1)
        if coin is None or price_item is None:
            return
        symbol = str(coin.data(Qt.ItemDataRole.UserRole) or "")
        try:
            price = float(price_item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return
        updated = _ask_price(self, "Alarm fiyatı", f"{base_of(symbol)} alarm fiyatı", price)
        if updated is None:
            return
        self._move_alarm_to(symbol, price, updated)

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)

        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header = QHBoxLayout(bar)
        header.setContentsMargins(8, 6, 10, 6)
        header.setSpacing(8)
        self._list_toggle = QPushButton()
        self._list_toggle.setObjectName("tool")
        self._list_toggle.setIcon(_icon_panel())
        self._list_toggle.setIconSize(QSize(16, 16))
        self._list_toggle.setFixedSize(30, 30)
        self._list_toggle.setToolTip("Sol paneli gizle")
        self._list_toggle.clicked.connect(self._toggle_list_panel)
        header.addWidget(self._list_toggle)
        self._page_group = QButtonGroup(self)
        self._page_group.setExclusive(True)
        self._page_market = QPushButton("Piyasa")
        self._page_liq = QPushButton("Likidasyon")
        self._page_exchange = QPushButton("Borsa")
        for button, page, checked in (
            (self._page_market, "market", True),
            (self._page_liq, "liq", False),
            (self._page_exchange, "exchange", False),
        ):
            button.setObjectName("page")
            button.setCheckable(True)
            button.setChecked(checked)
            self._page_group.addButton(button)
            button.clicked.connect(lambda _checked=False, chosen=page: self._set_page(chosen))
            header.addWidget(button)
        self.heading = QLabel(base_of(self._symbol))
        self.heading.setStyleSheet("font-size: 15px; font-weight: 600; color: #fafafa; background: transparent;")
        self.header_price = QLabel("—")
        self.header_price.setFont(QFont("Consolas", 15))
        self.header_price.setStyleSheet("color: #fafafa; background: transparent;")
        self.header_change = QLabel("")
        self.header_change.setFont(QFont("Consolas", 12))
        header.addSpacing(12)
        header.addWidget(self.heading)
        header.addWidget(self.header_price)
        header.addWidget(self.header_change)
        header.addStretch(1)
        self._interval_buttons: dict[str, QPushButton] = {}
        self._interval_group = QButtonGroup(self)
        self._interval_group.setExclusive(True)
        for key, label in INTERVAL_LABELS:
            button = QPushButton(label)
            button.setObjectName("interval")
            button.setCheckable(True)
            button.setChecked(key == self._interval)
            self._interval_group.addButton(button)
            button.clicked.connect(lambda _checked=False, chosen=key: self._set_interval(chosen))
            self._interval_buttons[key] = button
            header.addWidget(button)
        refresh = QPushButton("Güncelle")
        refresh.clicked.connect(lambda: self._start_snapshot(force=True))
        header.addWidget(refresh)
        column.addWidget(bar)

        market_page = QWidget()
        market_column = QVBoxLayout(market_page)
        market_column.setContentsMargins(6, 6, 8, 6)
        market_column.setSpacing(4)

        self.price_chart = QChart()
        self.price_x, self.price_y = _prepare_chart(self.price_chart, "", candle_labels=True)
        self.price_view = ChartView(
            self.price_chart,
            self._fit_price,
            self.price_x,
            self.price_y,
            lambda: self._interval,
            self._fit_visible_price,
            self._hover_date,
            fixed_grid=True,
            crosshair=True,
            on_alert=self._add_alarm,
            on_alert_remove=self._remove_alarm,
            on_alert_move=self._move_open_alarm,
            on_drawing=self._add_drawing,
            on_drawing_move=self._move_drawing,
            on_drawing_remove=self._remove_drawing,
        )
        self.price_view.setObjectName("panel")
        self.price_view.setMinimumHeight(120)
        self.price_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._chart_caption = QWidget(self.price_view.viewport())
        self._chart_caption.setStyleSheet("background: transparent;")
        caption_layout = QVBoxLayout(self._chart_caption)
        caption_layout.setContentsMargins(0, 0, 0, 0)
        caption_layout.setSpacing(4)
        caption_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._chart_title = QLabel(f"Fiyat  ·  {self._symbol}")
        self._chart_title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._chart_title.setStyleSheet(
            "color: #e4e4e7; font-size: 12px; background: #27272a; border-radius: 5px; padding: 3px 8px;"
        )
        self._indicator_rows = QVBoxLayout()
        self._indicator_rows.setSpacing(0)
        caption_layout.addWidget(self._chart_title)
        caption_layout.addLayout(self._indicator_rows)
        self.price_view.installEventFilter(self)

        self.oi_chart = QChart()
        self.oi_x, self.oi_y = _prepare_chart(self.oi_chart, "Open interest")
        self.oi_view = ChartView(
            self.oi_chart,
            self._fit_oi,
            self.oi_x,
            self.oi_y,
            lambda: "15m",
            self._fit_visible_oi,
        )
        self.oi_view.setObjectName("panel")
        self.oi_view.setMinimumHeight(90)

        self.funding_chart = QChart()
        self.funding_x, self.funding_y = _prepare_chart(self.funding_chart, "Funding")
        self.funding_view = ChartView(
            self.funding_chart,
            self._fit_funding,
            self.funding_x,
            self.funding_y,
            lambda: "1h",
            self._fit_visible_funding,
        )
        self.funding_view.setMinimumHeight(0)

        self._funding_panel = QWidget()
        self._funding_panel.setObjectName("panel")
        self._funding_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fund_column = QVBoxLayout(self._funding_panel)
        fund_column.setContentsMargins(0, 0, 0, 0)
        fund_column.setSpacing(4)
        fund_bar = QHBoxLayout()
        fund_bar.setContentsMargins(0, 0, 0, 0)
        fund_bar.addStretch(1)
        saved_step = str(_read_state().get("funding_step") or "1h")
        self._funding_step = saved_step if saved_step in ("1h", "8h") else "1h"
        self._funding_step_group = QButtonGroup(self._funding_panel)
        self._funding_step_group.setExclusive(True)
        for key, label in (("1h", "1sa"), ("8h", "8sa")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(key == self._funding_step)
            self._funding_step_group.addButton(button)
            button.clicked.connect(lambda _checked=False, chosen=key: self._set_funding_step(chosen))
            fund_bar.addWidget(button)
        fund_column.addLayout(fund_bar)
        fund_column.addWidget(self.funding_view, 1)
        self._funding_panel.setMinimumHeight(88)

        self._v_split = QSplitter(Qt.Orientation.Vertical)
        self._v_split.setChildrenCollapsible(False)
        self._v_split.setHandleWidth(4)
        self._v_split.addWidget(self.price_view)
        self._v_split.addWidget(self.oi_view)
        self._v_split.addWidget(self._funding_panel)
        self._cards_panel = self._build_cards_panel()
        self._v_split.addWidget(self._cards_panel)
        self._analysis_panel = self._build_analysis()
        self._v_split.addWidget(self._analysis_panel)
        self._v_split.setStretchFactor(0, 8)
        self._v_split.setStretchFactor(1, 1)
        self._v_split.setStretchFactor(2, 1)
        self._v_split.setStretchFactor(3, 0)
        self._v_split.setStretchFactor(4, 1)
        market_column.addWidget(self._v_split, 1)

        heat_page = QWidget()
        heat_column = QVBoxLayout(heat_page)
        heat_column.setContentsMargins(0, 0, 0, 0)
        heat_column.setSpacing(6)
        heat_bar = QHBoxLayout()
        heat_bar.setSpacing(8)
        heat_bar.addStretch(1)
        self._liq_span_box = QComboBox()
        self._liq_span_box.setMinimumWidth(120)
        for hours, label in LIQ_SPANS:
            self._liq_span_box.addItem(label, hours)
        current = next((index for index, (hours, _label) in enumerate(LIQ_SPANS) if hours == self._liq_span), 2)
        self._liq_span_box.blockSignals(True)
        self._liq_span_box.setCurrentIndex(current)
        self._liq_span_box.blockSignals(False)
        self._liq_span_box.currentIndexChanged.connect(self._on_liq_span)
        heat_bar.addWidget(self._liq_span_box, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._liq_conserved_box = QCheckBox("Eşit böl")
        self._liq_conserved_box.setChecked(self._liq_conserved)
        self._liq_conserved_box.setToolTip(
            "Açıkken long ve short, open interest artışını alış ve satış payına göre böler."
        )
        self._liq_conserved_box.toggled.connect(self._on_liq_conserved)
        heat_bar.addWidget(self._liq_conserved_box)
        threshold_label = QLabel("Alt sınır")
        threshold_label.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        self._liq_threshold_value = QLabel(f"%{int(round(self._liq_threshold * 100))}")
        self._liq_threshold_value.setStyleSheet("color: #e4e4e7; font-size: 11px;")
        self._liq_threshold_value.setMinimumWidth(108)
        self._liq_threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._liq_threshold_slider.setRange(0, 20)
        self._liq_threshold_slider.setSingleStep(1)
        self._liq_threshold_slider.setPageStep(1)
        self._liq_threshold_slider.setValue(int(round(self._liq_threshold * 20)))
        self._liq_threshold_slider.setFixedWidth(280)
        self._liq_threshold_slider.setFixedHeight(28)
        self._liq_threshold_slider.setStyleSheet(
            "QSlider::groove:horizontal { height: 10px; background: #3f3f46; border-radius: 5px; }"
            "QSlider::sub-page:horizontal { background: #3b82f6; border-radius: 5px; }"
            "QSlider::handle:horizontal { width: 22px; height: 22px; margin: -6px 0;"
            " background: #f4f4f5; border-radius: 11px; }"
        )
        self._liq_threshold_slider.setToolTip(
            "Rengin alt sınırı. Kırp bu sınırın altını mora indirir, Gizle hiç çizmez."
        )
        self._liq_threshold_slider.valueChanged.connect(self._on_liq_threshold)
        self._liq_mode_box = QComboBox()
        self._liq_mode_box.addItem("Kırp", "kirp")
        self._liq_mode_box.addItem("Gizle", "gizle")
        self._liq_mode_box.setCurrentIndex(0 if self._liq_scale_mode == "kirp" else 1)
        self._liq_mode_box.setToolTip("Kırp altındakileri mora indirir. Gizle onları çizmez.")
        self._liq_mode_box.currentIndexChanged.connect(self._on_liq_scale_mode)
        heat_bar.addWidget(threshold_label)
        heat_bar.addWidget(self._liq_threshold_slider)
        heat_bar.addWidget(self._liq_threshold_value)
        heat_bar.addWidget(self._liq_mode_box)
        self._heat = HeatMap()
        self._heat.set_threshold(self._liq_threshold)
        self._heat.set_hide(self._liq_scale_mode == "gizle")
        self._heat._scale_hook = self._paint_liq_threshold
        heat_column.addLayout(heat_bar)
        heat_column.addWidget(self._heat, 1)

        self._right_stack = QStackedWidget()
        self._right_stack.addWidget(market_page)
        self._right_stack.addWidget(heat_page)
        self._right_stack.addWidget(self._build_exchange())
        self._right_stack.setCurrentIndex(0)
        column.addWidget(self._right_stack, 1)
        return panel

    def _build_exchange(self) -> QWidget:
        data = _read_state()
        page = QWidget()
        page.setObjectName("exchangePage")
        page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        page.setStyleSheet("QWidget#exchangePage { background: #0b0b0d; }")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self._build_positions())
        panels = QHBoxLayout()
        panels.setSpacing(8)
        outer.addLayout(panels, 1)
        form = QWidget()
        form.setObjectName("panel")
        form.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        form.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        form.setMinimumWidth(420)
        column = QVBoxLayout(form)
        column.setContentsMargins(10, 10, 10, 10)
        column.setSpacing(8)
        title = QLabel("OKX")
        title.setObjectName("metricTitle")
        column.addWidget(title)
        note = QLabel(
            "OKX USDT-SWAP hesabın. Otomatik işlem açıksa seçili aralıkta yeni sinyal gelince "
            "kullanılabilir bakiyenin seçtiğin yüzdesi marjin olarak, seçtiğin kaldıraçla emir gider. Take profit, long hedefin 200$ altı, "
            "short hedefin 200$ üstüdür. Doğruluk oranı bu take profit fiyatına göredir. "
            "Hedef girişe 200$'dan yakınsa emir gitmez. "
            "BTC'de bir sözleşme fiyatın yaklaşık %1'i kadardır; marjin × kaldıraç bunun altında kalırsa emir gitmez."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #a1a1aa;")
        column.addWidget(note)
        self._okx_key = QLineEdit(str(data.get("okx_api_key") or ""))
        self._okx_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._okx_key.setPlaceholderText("API anahtarı")
        self._okx_secret = QLineEdit(str(data.get("okx_secret") or ""))
        self._okx_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._okx_secret.setPlaceholderText("Secret")
        self._okx_phrase = QLineEdit(str(data.get("okx_passphrase") or ""))
        self._okx_phrase.setEchoMode(QLineEdit.EchoMode.Password)
        self._okx_phrase.setPlaceholderText("Parola")
        for widget, title in (
            (self._okx_key, "API anahtarı"),
            (self._okx_secret, "Secret"),
            (self._okx_phrase, "Parola"),
        ):
            column.addWidget(QLabel(title))
            column.addWidget(widget)
            widget.editingFinished.connect(self._save_okx_form)
        row = QHBoxLayout()
        save = QPushButton("Kaydet")
        test = QPushButton("Bağlantıyı dene")
        save.clicked.connect(self._save_okx_clicked)
        test.clicked.connect(self._test_okx)
        row.addWidget(save)
        row.addWidget(test)
        column.addLayout(row)
        self._okx_status = QLabel("")
        self._okx_status.setWordWrap(True)
        self._okx_status.setStyleSheet("color: #a1a1aa;")
        column.addWidget(self._okx_status)
        self._okx_enabled = QCheckBox("Otomatik işlem")
        self._okx_enabled.setChecked(bool(data.get("okx_enabled")))
        self._okx_simulated = QCheckBox("Simülasyon (demo API anahtarı)")
        self._okx_simulated.setChecked(bool(data.get("okx_simulated")))
        self._okx_enabled.toggled.connect(lambda _on: self._save_okx_form())
        self._okx_simulated.toggled.connect(lambda _on: self._save_okx_form())
        column.addWidget(self._okx_enabled)
        column.addWidget(self._okx_simulated)
        spins = QHBoxLayout()
        lever_box = QVBoxLayout()
        lever_box.addWidget(QLabel("Kaldıraç"))
        self._okx_lever = QSpinBox()
        self._okx_lever.setRange(1, 125)
        try:
            lever = int(data.get("okx_leverage") or 5)
        except (TypeError, ValueError):
            lever = 5
        self._okx_lever.setValue(min(125, max(1, lever)))
        self._okx_lever.valueChanged.connect(lambda _value: self._save_okx_form())
        lever_box.addWidget(self._okx_lever)
        spins.addLayout(lever_box)
        spins.addStretch(1)
        column.addLayout(spins)
        column.addWidget(QLabel("Marjin (kullanılabilir bakiyenin yüzdesi)"))
        margin_row = QHBoxLayout()
        self._okx_margin = QSlider(Qt.Orientation.Horizontal)
        self._okx_margin.setRange(1, 100)
        self._okx_margin.setSingleStep(1)
        self._okx_margin.setPageStep(10)
        self._okx_margin.setValue(_margin_pct(data))
        self._okx_margin.valueChanged.connect(lambda _value: self._paint_okx_size())
        self._okx_margin.sliderReleased.connect(self._save_okx_form)
        self._okx_margin.valueChanged.connect(
            lambda _value: None if self._okx_margin.isSliderDown() else self._save_okx_form()
        )
        self._okx_margin_value = QLabel("")
        self._okx_margin_value.setMinimumWidth(190)
        self._okx_margin_value.setStyleSheet("color: #e4e4e7; font-family: Consolas, monospace;")
        margin_row.addWidget(self._okx_margin, 1)
        margin_row.addWidget(self._okx_margin_value)
        column.addLayout(margin_row)
        self._okx_size = QLabel("")
        self._okx_size.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        column.addWidget(self._okx_size)
        self._okx_mode = QComboBox()
        self._okx_mode.addItem("Çapraz", "cross")
        self._okx_mode.addItem("İzole", "isolated")
        saved_mode = str(data.get("okx_mgn_mode") or "cross")
        self._okx_mode.setCurrentIndex(1 if saved_mode == "isolated" else 0)
        self._okx_mode.currentIndexChanged.connect(lambda _index: self._save_okx_form())
        column.addWidget(QLabel("Marjin modu"))
        column.addWidget(self._okx_mode)
        windows = data.get("okx_windows")
        chosen = set(SIGNAL_WINDOWS) if windows is None else {str(item) for item in windows} if isinstance(windows, list) else set(SIGNAL_WINDOWS)
        window_row = QHBoxLayout()
        self._okx_windows: dict[str, QCheckBox] = {}
        for label in SIGNAL_WINDOWS:
            box = QCheckBox(label)
            box.setChecked(label in chosen)
            box.toggled.connect(lambda _on: self._save_okx_form())
            self._okx_windows[label] = box
            window_row.addWidget(box)
        window_row.addStretch(1)
        column.addWidget(QLabel("Emir gidecek aralıklar"))
        column.addLayout(window_row)
        self._okx_log = QLabel("Son emir yok")
        self._okx_log.setWordWrap(True)
        self._okx_log.setStyleSheet("color: #e4e4e7;")
        column.addWidget(QLabel("Son emir kayıtları"))
        column.addWidget(self._okx_log)
        self._show_okx_log()
        column.addStretch(1)
        self._paint_okx_size()
        active = self._build_active()
        active.setMinimumWidth(280)
        panels.addWidget(form, 1)
        panels.addWidget(active, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: #0b0b0d; border: none; }")
        scroll.viewport().setStyleSheet("background: #0b0b0d;")
        scroll.setWidget(page)
        return scroll

    def _build_positions(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column = QVBoxLayout(panel)
        column.setContentsMargins(10, 10, 10, 10)
        column.setSpacing(6)
        title = QLabel("Açık pozisyonlar · OKX")
        title.setObjectName("metricTitle")
        column.addWidget(title)
        table = QTableWidget(0, len(POSITION_COLUMNS))
        table.setHorizontalHeaderLabels(list(POSITION_COLUMNS))
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        table.setMinimumHeight(150)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for name in ("PnL", "Trailing", "İşlem"):
            header.setSectionResizeMode(POSITION_COLUMNS.index(name), QHeaderView.ResizeMode.ResizeToContents)
        table.setMaximumHeight(240)
        self._okx_table = table
        column.addWidget(table)
        self._okx_live_note = QLabel("")
        self._okx_live_note.setStyleSheet("color: #a1a1aa;")
        column.addWidget(self._okx_live_note)
        self._render_okx_positions()
        return panel

    def _okx_creds(self) -> Creds:
        return Creds(self._okx_key.text(), self._okx_secret.text(), self._okx_phrase.text())

    def _paint_okx_size(self) -> None:
        if not hasattr(self, "_okx_size"):
            return
        pct = int(self._okx_margin.value())
        lever = int(self._okx_lever.value())
        available = self._okx_available
        if available is None:
            self._okx_margin_value.setText(f"%{pct} · bakiye bekleniyor")
            self._okx_size.setText(f"Pozisyon büyüklüğü = bakiyenin %{pct}'i × {lever}x")
            return
        margin = available * pct / 100
        self._okx_margin_value.setText(f"%{pct} · {margin:,.2f} / {available:,.2f} USDT")
        self._okx_size.setText(
            f"Pozisyon büyüklüğü ≈ {margin:,.2f} USDT × {lever}x = {margin * lever:,.2f} USDT · emir anındaki bakiyeyle hesaplanır"
        )

    def _save_okx_form(self) -> None:
        if not hasattr(self, "_okx_key"):
            return
        windows = [label for label, box in self._okx_windows.items() if box.isChecked()]
        _patch_state(
            okx_api_key=self._okx_key.text().strip(),
            okx_secret=self._okx_secret.text().strip(),
            okx_passphrase=self._okx_phrase.text().strip(),
            okx_enabled=self._okx_enabled.isChecked(),
            okx_simulated=self._okx_simulated.isChecked(),
            okx_leverage=int(self._okx_lever.value()),
            okx_margin_pct=int(self._okx_margin.value()),
            okx_mgn_mode=str(self._okx_mode.currentData() or "cross"),
            okx_windows=windows,
        )
        self._paint_okx_size()

    def _save_okx_clicked(self) -> None:
        self._save_okx_form()
        self._okx_status.setText("Kaydedildi.")
        self._okx_status.setStyleSheet("color: #26a69a;")

    def _test_okx(self) -> None:
        self._save_okx_form()
        creds = self._okx_creds()
        if not creds.ready():
            self._okx_status.setText("API anahtarı, secret ve parola gerekli.")
            self._okx_status.setStyleSheet("color: #ef5350;")
            return
        self._okx_status.setText("Bağlanıyor…")
        self._okx_status.setStyleSheet("color: #a1a1aa;")
        worker = _OkxPing(creds, self._okx_simulated.isChecked())
        worker.done.connect(self._on_okx_ping)
        self._launch(worker, "_okx_ping")

    def _on_okx_ping(self, ok: bool, message: str) -> None:
        self._okx_status.setText(message)
        self._okx_status.setStyleSheet("color: #26a69a;" if ok else "color: #ef5350;")

    def _build_analysis(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setMinimumHeight(120)
        column = QVBoxLayout(panel)
        column.setContentsMargins(6, 4, 6, 4)
        column.setSpacing(4)
        windows = QHBoxLayout()
        windows.setSpacing(6)
        self._window_menu = QMenu(panel)
        self._window_actions: dict[str, object] = {}
        self._window_menu_button = QPushButton()
        self._window_menu_button.setMenu(self._window_menu)
        for label in WINDOW_LABELS:
            action = self._window_menu.addAction(label)
            action.setCheckable(True)
            action.blockSignals(True)
            action.setChecked(label in self._windows_on)
            action.blockSignals(False)
            action.toggled.connect(lambda on, name=label: self._toggle_window(name, on))
            self._window_actions[label] = action
        self._paint_window_menu()
        windows.addWidget(self._window_menu_button)
        windows.addStretch(1)
        column.addLayout(windows)
        self.analysis = QTableWidget(0, 6)
        self.analysis.setHorizontalHeaderLabels(
            ["Pencere", "Fiyat", "OI", "CVD perp", "CVD spot", "Sonuç"]
        )
        self.analysis.verticalHeader().setVisible(False)
        self.analysis.verticalHeader().setDefaultSectionSize(22)
        self.analysis.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.analysis.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.analysis.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.analysis.setShowGrid(False)
        self.analysis.setFrameShape(QFrame.Shape.NoFrame)
        self.analysis.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        ahead = self.analysis.horizontalHeader()
        ahead.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        ahead.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.analysis.verticalHeader().setMinimumSectionSize(24)
        self.analysis.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        _darken(self.analysis)
        column.addWidget(self.analysis, 1)
        return panel

    def _restore_panes(self) -> None:
        data = _read_state()
        horizontal = _pane_sizes(data.get("panes_h"))
        vertical = _pane_sizes(data.get("panes_v"))
        self._h_split.setSizes(horizontal or [280, 1000])
        fresh = [640, 110, 100, 56, 160]
        previous = [300, 120, 140, 100, 240]
        if vertical and len(vertical) == self._v_split.count() and vertical != previous:
            self._v_split.setSizes(vertical)
        else:
            self._v_split.setSizes(fresh)

    def _remember_layout(self) -> None:
        if any(not widget.isVisible() for widget in self._panel_widgets.values()):
            return
        _patch_state(
            panes_h=self._h_split.sizes(),
            panes_v=self._v_split.sizes(),
        )

    def _build_panel_menu(self) -> None:
        flags = _load_panels()
        self._panel_on = dict(flags)
        self._panel_actions = {}
        menu = self.menuBar().addMenu("Paneller")
        for key, label in PANELS:
            toggle = self._panel_toggles.get(key)
            if toggle is not None:
                toggle.blockSignals(True)
                toggle.setChecked(flags.get(key, key != "oi"))
                toggle.blockSignals(False)
                toggle.toggled.connect(lambda on, name=key: self._set_panel(name, on))
                if not toggle.isChecked():
                    self._set_panel(key, False)
                continue
            action = menu.addAction(label)
            action.setCheckable(True)
            action.blockSignals(True)
            action.setChecked(flags.get(key, key != "oi"))
            action.blockSignals(False)
            action.toggled.connect(lambda on, name=key: self._set_panel(name, on))
            self._panel_actions[key] = action
            if not action.isChecked():
                self._set_panel(key, False)

    def _build_settings_menu(self) -> None:
        menu = self.menuBar().addMenu("Ayarlar")
        action = menu.addAction("Telegram")
        action.triggered.connect(self._open_settings)
        self._startup_action = menu.addAction("Windows ile başlat")
        self._startup_action.setCheckable(True)
        enabled = _load_startup()
        _apply_startup(enabled)
        self._startup_action.blockSignals(True)
        self._startup_action.setChecked(enabled)
        self._startup_action.blockSignals(False)
        self._startup_action.toggled.connect(self._toggle_startup)

    def _toggle_startup(self, enabled: bool) -> None:
        _patch_state(start_with_windows=bool(enabled))
        _apply_startup(bool(enabled))

    def _open_settings(self) -> None:
        _SettingsDialog(self).exec()

    def _set_funding_step(self, step: str) -> None:
        if step not in ("1h", "8h") or step == self._funding_step:
            return
        self._funding_step = step
        _patch_state(funding_step=step)
        self.funding_view.mark_fresh()
        self._draw_funding(self._last_funding)

    def _set_panel(self, key: str, visible: bool) -> None:
        self._panel_on[key] = visible
        toggle = getattr(self, "_panel_toggles", {}).get(key)
        if toggle is not None and toggle.isChecked() != visible:
            toggle.blockSignals(True)
            toggle.setChecked(visible)
            toggle.blockSignals(False)
        self._layout_panel(key, visible)
        if key == "list":
            self._list_toggle.setToolTip("Sol paneli göster" if not visible else "Sol paneli gizle")
        _patch_state(panels=dict(self._panel_on))

    def _layout_panel(self, key: str, visible: bool) -> None:
        widget = self._panel_widgets[key]
        if key == "list":
            self._list_panel.setMinimumHeight(160 if visible else 0)
            self._list_host.setVisible(visible)
            if visible:
                self._list_host.setMinimumWidth(280)
                self._list_host.setMaximumWidth(280)
            else:
                self._list_host.setMinimumWidth(0)
                self._list_host.setMaximumWidth(0)
            return
        split = self._v_split
        index = split.indexOf(widget)
        if visible:
            if key != "list":
                widget.setMinimumHeight(
                    {"price": 120, "oi": 90, "funding": 110, "cards": 78, "analysis": 160}[key]
                )
            else:
                widget.setMinimumHeight(160)
            widget.setVisible(True)
            sizes = split.sizes()
            if 0 <= index < len(sizes) and sizes[index] < 80:
                sizes[index] = self._panel_hold.get(key, 200)
                split.setSizes(sizes)
            return
        sizes = split.sizes()
        if 0 <= index < len(sizes) and sizes[index] >= 80:
            self._panel_hold[key] = sizes[index]
        widget.setMinimumHeight(0)
        widget.setVisible(False)
        sizes = split.sizes()
        if 0 <= index < len(sizes):
            sizes[index] = 0
            split.setSizes(sizes)

    def _apply_index_mode(self, active: bool) -> None:
        if active == self._index_mode:
            return
        self._index_mode = active
        hidden = ("oi", "funding", "cards", "analysis")
        if active:
            self._page_liq.setEnabled(False)
            if self._right_stack.currentIndex() == 1:
                self._page_market.setChecked(True)
                self._set_page("market")
            for key in hidden:
                self._layout_panel(key, False)
            self._sync_signals()
            return
        self._page_liq.setEnabled(True)
        for key in hidden:
            self._layout_panel(key, bool(self._panel_on.get(key, key != "oi")))
        self._sync_signals()

    def _build_cards_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("panel")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setMinimumHeight(52)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        host = QWidget()
        host.setLayout(self._build_cards())
        host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        column.addWidget(host)
        return panel

    def _build_cards(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(0)
        self._cards: dict[str, tuple[QLabel, QLabel]] = {}
        for key, title in (
            ("price", "Fiyat"),
            ("oi", "Open interest"),
            ("funding", "Funding"),
            ("cvd_perp", "CVD perp"),
            ("cvd_spot", "CVD spot"),
        ):
            frame = QFrame()
            frame.setObjectName("card")
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(10, 2, 10, 2)
            inner.setSpacing(0)
            label = QLabel(title)
            label.setObjectName("metricTitle")
            value = QLabel("—")
            value.setStyleSheet("color: #fafafa; font-size: 14px; font-family: Consolas, monospace;")
            sub = QLabel("")
            sub.setObjectName("metricSub")
            inner.addWidget(label)
            inner.addWidget(value)
            inner.addWidget(sub)
            self._cards[key] = (value, sub)
            row.addWidget(frame)
        return row

    def _paint(self, label: QLabel, text: str, tone: str) -> None:
        if label.text() != text:
            label.setText(text)
        style = f"color: {_color(tone)}; font-size: 14px; font-family: Consolas, monospace;"
        if label.property("_tone_style") != style:
            label.setProperty("_tone_style", style)
            label.setStyleSheet(style)

    def _set_page(self, page: str) -> None:
        if page == "liq" and is_index(self._symbol):
            self._page_market.setChecked(True)
            page = "market"
        index = {"market": 0, "liq": 1, "exchange": 2}.get(page, 0)
        self._page = page
        self._right_stack.setCurrentIndex(index)
        if hasattr(self, "_heat"):
            self._heat.set_live(page == "liq" and not self._background)
        if page == "liq":
            self._ensure_liquidity(False)

    def _ensure_liquidity(self, force: bool) -> None:
        if is_index(self._symbol) or not hasattr(self, "_heat"):
            return
        hour = int(time.time() * 1000) // HOUR_MS
        if (
            not force
            and self._liq_map is not None
            and self._liq_map.symbol == self._symbol
            and self._liq_hour == hour
        ):
            return
        if self._thread_running("_liq_worker") and not force:
            worker = self._liq_worker
            if worker is not None and getattr(worker, "_symbol", None) == self._symbol:
                return
        self._liq_gen += 1
        generation = self._liq_gen
        if self._liq_map is None or self._liq_map.symbol != self._symbol:
            self._heat.set_state(None, "Yükleniyor")
        worker = LiquidityWorker(self._symbol, self._liq_span, self._liq_conserved)
        worker.done.connect(lambda payload, gen=generation: self._on_liquidity(gen, payload))
        worker.failed.connect(lambda message, gen=generation: self._on_liquidity_fail(gen, message))
        self._launch(worker, "_liq_worker")

    def _on_liquidity(self, generation: int, payload: dict) -> None:
        book = payload.get("book") if isinstance(payload, dict) else None
        if generation != self._liq_gen or book is None or book.symbol != self._symbol:
            return
        self._liq_hour = int(time.time() * 1000) // HOUR_MS
        self._liq_full = book
        self._liq_clips = dict(payload.get("clips") or {})
        self._liq_targets = dict(payload.get("targets") or {})
        self._clip_gen += 1
        self._apply_liq_span()

    def _need_clips(self) -> None:
        book = self._liq_full
        if book is None or book.symbol != self._symbol:
            return
        conserved = self._liq_conserved
        spans = {self._liq_span} if (self._liq_span, conserved) not in self._liq_clips else set()
        hours = {value for value in SIGNAL_LIQ_HOURS.values() if (value, conserved) not in self._liq_targets}
        if not spans and not hours:
            return
        self._clip_gen += 1
        generation = self._clip_gen
        worker = _ClipWorker(book, spans, hours, conserved)
        worker.done.connect(lambda payload, gen=generation: self._on_clips(gen, payload))
        self._launch(worker, "_clip_worker")

    def _on_clips(self, generation: int, payload: dict) -> None:
        if generation != self._clip_gen or payload.get("book") is not self._liq_full:
            return
        self._liq_clips.update(payload.get("clips") or {})
        self._liq_targets.update(payload.get("targets") or {})
        self._apply_liq_span()

    def _targets_ready(self) -> bool:
        book = self._liq_full
        if book is None or book.symbol != self._symbol:
            return False
        return all((hours, self._liq_conserved) in self._liq_targets for hours in SIGNAL_LIQ_HOURS.values())

    def _on_liq_span(self, _index: int) -> None:
        hours = self._liq_span_box.currentData()
        if hours not in {span for span, _label in LIQ_SPANS}:
            return
        self._liq_span = int(hours)
        _patch_state(liq_span=self._liq_span)
        if self._liq_full is not None:
            self._apply_liq_span()

    def _on_liq_threshold(self, value: int) -> None:
        self._liq_threshold = value / 20
        self._paint_liq_threshold()
        self._heat.set_threshold(self._liq_threshold)
        _patch_state(liq_threshold=self._liq_threshold)

    def _on_liq_scale_mode(self, _index: int) -> None:
        mode = str(self._liq_mode_box.currentData() or "kirp")
        if mode not in ("kirp", "gizle"):
            mode = "kirp"
        self._liq_scale_mode = mode
        _patch_state(liq_scale_mode=mode)
        self._heat.set_hide(mode == "gizle")

    def _paint_liq_threshold(self) -> None:
        if not hasattr(self, "_liq_threshold_value"):
            return
        percent = int(round(self._liq_threshold * 100))
        floor = self._heat._scale_floor if hasattr(self, "_heat") else 0.0
        if floor > 0:
            self._liq_threshold_value.setText(f"%{percent} · {format_usd(floor)}")
        else:
            self._liq_threshold_value.setText(f"%{percent}")

    def _on_liq_conserved(self, on: bool) -> None:
        self._liq_conserved = bool(on)
        _patch_state(liq_conserved=self._liq_conserved)
        if self._liq_full is not None:
            self._apply_liq_span()

    def _apply_liq_span(self) -> None:
        if self._liq_full is None or self._liq_full.symbol != self._symbol:
            return
        self._need_clips()
        if self._targets_ready():
            self._fill_missing_targets()
        shown = self._liq_clips.get((self._liq_span, self._liq_conserved))
        if shown is None or shown is self._liq_map:
            return
        self._liq_map = shown
        self._heat.set_candles([])
        if shown.has_mass():
            self._heat.set_state(shown, "")
        else:
            self._heat.set_state(None, "Veri yok")
        self._load_liq_candles()

    def _load_liq_candles(self) -> None:
        interval, bar_ms, limit = _liq_candle_spec(self._liq_span)
        self._liq_candle_gen += 1
        generation = self._liq_candle_gen
        worker = _LiqCandleWorker(self._symbol, interval, limit, bar_ms)
        worker.done.connect(
            lambda bars, gen=generation, kind=interval: self._on_liq_candles(gen, kind, bars)
        )
        self._launch(worker, "_liq_candle_worker")

    def _on_liq_candles(self, generation: int, interval: str, bars: list[Bar]) -> None:
        if generation != self._liq_candle_gen:
            return
        expected, _bar_ms, _limit = _liq_candle_spec(self._liq_span)
        if interval != expected:
            return
        book = self._liq_map
        if book is not None and book.times:
            start = book.times[0]
            bars = [bar for bar in bars if bar.time_ms >= start]
        self._heat.set_candles(bars)

    def _fill_missing_targets(self) -> None:
        data = _read_state()
        stored = data.get("signal_open")
        if not isinstance(stored, dict):
            return
        entry = stored.get(self._symbol)
        if not isinstance(entry, dict):
            return
        changed = False
        updated = dict(entry)
        for label in SIGNAL_WINDOWS:
            episode = updated.get(label)
            if not isinstance(episode, dict):
                continue
            verdict = str(episode.get("verdict") or "")
            if verdict in QUIET_VERDICTS:
                continue
            target = self._liquidity_target(verdict, SIGNAL_LIQ_HOURS.get(label, 72), self._live_price)
            stored_target = episode.get("target")
            same = stored_target == target or (
                isinstance(stored_target, (int, float))
                and isinstance(target, (int, float))
                and abs(float(stored_target) - float(target)) <= max(abs(float(target)) * 1e-6, 1e-6)
            )
            if same:
                continue
            kept = dict(episode)
            kept["target"] = target
            kept["side"] = _trade_side(verdict)
            positions = data.get("okx_positions")
            coin = positions.get(self._symbol) if isinstance(positions, dict) else None
            held = coin.get(label) if isinstance(coin, dict) else None
            if not isinstance(held, dict):
                kept["tp"] = _take_profit(kept["side"], target)
            updated[label] = kept
            changed = True
        if not changed:
            return
        book = dict(stored)
        book[self._symbol] = updated
        _patch_state(signal_open=book)
        self._paint_active()

    def _on_liquidity_fail(self, generation: int, message: str) -> None:
        if generation != self._liq_gen:
            return
        if self._liq_map is None or self._liq_map.symbol != self._symbol:
            self._heat.set_state(None, "Veri yok")
        if not self._background:
            self.statusBar().showMessage(message)

    def _paint_active(self) -> None:
        if not hasattr(self, "_active_body"):
            return
        self._active_title.setText(f"Aktif işlem · {base_of(self._symbol)}")
        stored = _read_state().get("signal_open")
        entry = stored.get(self._symbol) if isinstance(stored, dict) else None
        if not isinstance(entry, dict):
            entry = {}
        score = _read_state().get("signal_score")
        coin = score.get(self._symbol) if isinstance(score, dict) else {}
        if not isinstance(coin, dict):
            coin = {}
        lines: list[str] = []
        for label in SIGNAL_WINDOWS:
            episode = entry.get(label)
            verdict = ""
            target = None
            if isinstance(episode, dict):
                verdict = str(episode.get("verdict") or "")
                target = episode.get("target")
            if verdict in QUIET_VERDICTS:
                lines.append(f"{label}   —")
            elif isinstance(target, (int, float)) and float(target) > 0:
                side = episode.get("side") if isinstance(episode, dict) else None
                tp = episode.get("tp") if isinstance(episode, dict) else None
                if not isinstance(tp, (int, float)) or float(tp) <= 0:
                    tp = _take_profit(side if isinstance(side, str) else _trade_side(verdict), target)
                tp_text = "TP yok" if tp is None else f"TP {_format_tp(tp)}"
                clock = _signal_clock(episode.get("opened_ms") if isinstance(episode, dict) else None)
                when = f"{clock}   " if clock else ""
                lines.append(f"{label}   {when}{verdict}   {format_price(float(target))}   {tp_text}")
            else:
                clock = _signal_clock(episode.get("opened_ms") if isinstance(episode, dict) else None)
                when = f"{clock}   " if clock else ""
                lines.append(f"{label}   {when}{verdict}   Hedef yok")
            lines.append(_score_line(coin, label))
        self._active_body.setText("\n".join(lines))

    def _show_symbol(self, symbol: str) -> None:
        self._live_price = None
        self._live_set = None
        self._sync_feed()
        self.price_view.set_alarms(self._alarms.get(symbol, []))
        self.price_view.set_drawings(self._drawings.get(symbol, []))
        self._paint_position_lines()
        self.heading.setText(index_label(symbol) if is_index(symbol) else base_of(symbol))
        self.header_price.setText("…")
        self._session_change = None
        self._paint_change()
        for value, sub in self._cards.values():
            self._paint(value, "…", "flat")
            sub.setText("")
        self.analysis.setRowCount(0)
        self._last_candles = []
        self._last_oi = []
        self._last_funding = []
        self._recent_start_ms = 0
        self._history_done = False
        self._history_gen += 1
        self.price_view.mark_fresh()
        self.oi_view.mark_fresh()
        self.funding_view.mark_fresh()
        self._draw_candles([])
        self._draw_oi([])
        self._draw_funding([])
        self._refresh_alarms()
        self._paint_active()
        self._apply_index_mode(is_index(symbol))
        if is_index(symbol):
            self._history_done = True
            self.statusBar().showMessage(f"{index_label(symbol)} güncelleniyor")
            self._start_index(True)
            return
        self.statusBar().showMessage(f"{symbol} güncelleniyor")
        self._ensure_liquidity(True)

    def _set_interval(self, interval: str) -> None:
        if interval == self._interval:
            self._paint_interval_menu()
            return
        self._interval = interval
        _save_state(self._symbol, self._interval)
        self._paint_interval_menu()
        self.price_view.mark_fresh()
        self._start_snapshot(force=True)

    def _feed_symbols(self) -> list[str]:
        symbols = {symbol for symbol, levels in self._alarms.items() if levels}
        if not self._background and not is_index(self._symbol):
            symbols.add(self._symbol)
        return sorted(symbols)

    def _sync_feed(self) -> None:
        self._feed.follow(self._feed_symbols())

    def _build_tray(self) -> None:
        self._tray = QSystemTrayIcon(_tray_icon(), self)
        self._tray.setToolTip("CryptoHub")
        menu = QMenu(self)
        menu.addAction("Aç", self._leave_background)
        menu.addAction("Çıkış", self._quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    def _on_tray(self, reason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._leave_background()

    def _enter_background(self) -> None:
        if self._quitting:
            return
        self._background = True
        if hasattr(self, "_heat"):
            self._heat.set_live(False)
        self._timer.setInterval(BACKGROUND_MS)
        self._sync_feed()
        self._tray.setToolTip("CryptoHub arka planda · sinyal ve alarmlar açık")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        elif not self.isMinimized():
            self.showMinimized()

    def _leave_background(self) -> None:
        was_hidden = self._background
        self._background = False
        if hasattr(self, "_heat"):
            self._heat.set_live(getattr(self, "_page", "market") == "liq")
        self._timer.setInterval(REFRESH_MS)
        self._tray.setToolTip("CryptoHub")
        self.showMaximized()
        self.raise_()
        self.activateWindow()
        self._sync_feed()
        if was_hidden:
            self._start_list()
            self._start_snapshot(force=True, with_chart=True)

    def _quit(self) -> None:
        self._quitting = True
        self._tray.hide()
        self.close()

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange and not self._quitting and self.isMinimized():
            QTimer.singleShot(0, self._enter_background)
        super().changeEvent(event)

    def _on_tick(self) -> None:
        if self._background:
            self._start_snapshot(force=False, with_chart=False)
            return
        self._start_list()
        self._start_snapshot(force=False)

    def _thread_running(self, attr: str) -> bool:
        worker = getattr(self, attr)
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            setattr(self, attr, None)
            return False

    def _launch(self, worker: QThread, attr: str) -> None:
        self._retained.append(worker)

        def release(finished=worker) -> None:
            if finished in self._retained:
                self._retained.remove(finished)
            if getattr(self, attr) is finished:
                setattr(self, attr, None)

        worker.finished.connect(release)
        worker.finished.connect(worker.deleteLater)
        setattr(self, attr, worker)
        worker.start()

    def _start_list(self) -> None:
        if self._thread_running("_list_worker"):
            return
        self._list_gen += 1
        generation = self._list_gen
        worker = QuoteWorker()
        worker.done.connect(lambda payload, gen=generation: self._on_quotes(gen, payload))
        worker.failed.connect(lambda message, gen=generation: self._on_list_fail(gen, message))
        self._launch(worker, "_list_worker")
        self._start_index_quotes()

    def _start_index_quotes(self) -> None:
        if index_quotes_fresh() or self._thread_running("_index_quote_worker"):
            return
        worker = _IndexQuoteWorker()
        worker.done.connect(self._on_index_quotes)
        self._launch(worker, "_index_quote_worker")

    def _on_index_quotes(self, rows: object) -> None:
        if not isinstance(rows, list) or not rows:
            return
        self._index_quote_map = {item.symbol: item for item in rows if isinstance(item, Quote)}
        known = self._index_quote_map.get(self._symbol)
        if is_index(self._symbol) and known is not None and known.price > 0 and self.header_price.text() == "…":
            self.header_price.setText(format_index_price(known.symbol, known.price))
            self._session_change = known.change_pct
        self._render_list(False)
        if is_index(self._symbol):
            self._paint_change()

    def _start_index(self, force: bool) -> None:
        if not is_index(self._symbol):
            return
        if self._thread_running("_index_worker") and not force:
            return
        self._index_gen += 1
        generation = self._index_gen
        worker = _IndexChartWorker(self._symbol, self._interval)
        worker.done.connect(lambda chart, gen=generation: self._on_index_chart(gen, chart))
        worker.failed.connect(lambda message, gen=generation: self._on_index_fail(gen, message))
        self._launch(worker, "_index_worker")

    def _on_index_chart(self, generation: int, chart) -> None:
        if generation != self._index_gen or not is_index(self._symbol) or chart.symbol != self._symbol:
            return
        self.heading.setText(chart.label)
        self.header_price.setText(format_index_price(chart.symbol, chart.price))
        self._session_change = chart.change_pct
        self._paint_change()
        self._history_done = True
        self._band_points = []
        self.price_view.set_bands([])
        self._last_candles = list(chart.candles)
        self._draw_candles(self._last_candles)
        self._chart_title.setText(chart.caption)
        self._place_caption()
        self.statusBar().showMessage(chart.label)

    def _on_index_fail(self, generation: int, message: str) -> None:
        if generation != self._index_gen:
            return
        self.statusBar().showMessage(message)

    def _start_snapshot(self, force: bool, with_chart: bool = True) -> None:
        if is_index(self._symbol):
            if not self._background:
                self._start_index(force)
            return
        if self._thread_running("_snap") and not force:
            return
        self._snap_gen += 1
        generation = self._snap_gen
        if force and not self._background:
            self.statusBar().showMessage(f"{self._symbol} güncelleniyor")
        stored = _read_state().get("signal_open")
        entry = stored.get(self._symbol) if isinstance(stored, dict) else None
        episodes = entry if isinstance(entry, dict) else {}
        worker = SnapshotWorker(
            self._symbol,
            self._interval,
            with_chart=with_chart and not self._background,
            enabled=set(self._windows_on),
            episodes=episodes,
        )
        worker.done.connect(lambda payload, gen=generation: self._on_snapshot(gen, payload))
        worker.failed.connect(lambda message, gen=generation: self._on_snap_fail(gen, message))
        self._launch(worker, "_snap")

    def _on_quotes(self, generation: int, quotes: list[Quote]) -> None:
        if generation != self._list_gen:
            return
        self._quotes = quotes
        self._by_symbol = {item.symbol: item for item in quotes}
        self.search.setPlaceholderText("Coin ara, ★ ile listeye ekle")
        self._render_list(False)
        self._paint_change()

    def _on_list_fail(self, generation: int, message: str) -> None:
        if generation != self._list_gen:
            return
        if not self._quotes:
            self.statusBar().showMessage(f"Coin listesi alınamadı: {message}")

    def _search_text(self) -> str:
        return self.search.text().strip().upper().replace("/", "").replace(" ", "")

    def _rows_for_list(self) -> list[Quote]:
        text = self._search_text()
        if not text:
            rows: list[Quote] = []
            for symbol in self._watch:
                if is_index(symbol):
                    known = self._index_quote_map.get(symbol)
                    rows.append(known or Quote(symbol, symbol, 0.0, 0.0, 0.0))
                    continue
                found = self._by_symbol.get(symbol)
                rows.append(found or Quote(symbol, base_of(symbol), 0.0, 0.0, 0.0))
            return rows
        matched = [
            item
            for item in self._quotes
            if text in item.base.upper() or text in item.symbol
        ]
        matched.sort(
            key=lambda item: (
                0 if item.base.upper().startswith(text) or item.symbol.startswith(text) else 1,
                -item.quote_volume,
            )
        )
        return self._index_rows(text) + matched[:30]

    def _index_rows(self, text: str) -> list[Quote]:
        rows: list[Quote] = []
        for spec in INDEXES:
            label = spec.symbol.upper()
            if text not in label:
                continue
            known = self._index_quote_map.get(spec.symbol)
            rows.append(known or Quote(spec.symbol, spec.symbol, 0.0, 0.0, 0.0))
        return rows

    def _render_list(self, reorder: bool = True) -> None:
        rows = self._rows_for_list()
        existing = [
            str(self.table.item(index, 1).data(Qt.ItemDataRole.UserRole))
            for index in range(self.table.rowCount())
            if self.table.item(index, 1) is not None
        ]
        incoming = [item.symbol for item in rows]
        if not reorder and existing == incoming:
            for index, quote in enumerate(rows):
                self._paint_quote_row(index, quote)
            return
        scrollbar = self.table.verticalScrollBar()
        scroll = scrollbar.value()
        self._adjusting = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        selected = -1
        for index, quote in enumerate(rows):
            self._paint_quote_row(index, quote, create=True)
            if quote.symbol == self._symbol:
                selected = index
        if selected >= 0:
            self.table.selectRow(selected)
        else:
            self.table.clearSelection()
        self.table.blockSignals(False)
        self._adjusting = False
        if not reorder:
            scrollbar.setValue(scroll)

    def _paint_quote_row(self, index: int, quote: Quote, create: bool = False) -> None:
        if create or self.table.item(index, 1) is None:
            star = QTableWidgetItem()
            star.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            name = QTableWidgetItem(quote.base)
            name.setData(Qt.ItemDataRole.UserRole, quote.symbol)
            name.setToolTip(quote.symbol)
            name.setForeground(FLAT)
            mono = QFont("Consolas", 10)
            price_item = QTableWidgetItem()
            price_item.setFont(mono)
            price_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            change_item = QTableWidgetItem()
            change_item.setFont(mono)
            change_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(index, 0, star)
            self.table.setItem(index, 1, name)
            self.table.setItem(index, 2, price_item)
            self.table.setItem(index, 3, change_item)
        star = self.table.item(index, 0)
        pinned = quote.symbol in self._watch
        star.setText("★" if pinned else "☆")
        star.setForeground(QColor("#f5c451") if pinned else MUTED)
        price_item = self.table.item(index, 2)
        change_item = self.table.item(index, 3)
        if quote.price <= 0:
            price_item.setText("—")
            price_item.setForeground(MUTED)
            change_item.setText("—")
            change_item.setForeground(MUTED)
            return
        if is_index(quote.symbol):
            price_item.setText(format_index_price(quote.symbol, quote.price))
        else:
            price_item.setText(format_price(quote.price))
        price_item.setForeground(FLAT)
        change = self._shown_change(quote)
        change_item.setText(f"{change:+.2f}%")
        change_item.setForeground(UP if change > 0 else DOWN if change < 0 else MUTED)

    def _select_first(self) -> None:
        if self.table.rowCount() == 0:
            return
        self._on_cell(0, 1)

    def _on_cell(self, row: int, column: int) -> None:
        if self._adjusting:
            return
        item = self.table.item(row, 1)
        if item is None:
            return
        symbol = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not symbol:
            return
        if column == 0:
            self._toggle_watch(symbol)
            return
        if symbol == self._symbol:
            return
        self._symbol = symbol
        _save_state(self._symbol, self._interval)
        self._show_symbol(symbol)
        self._start_snapshot(force=True)

    def _toggle_watch(self, symbol: str) -> None:
        if symbol in self._watch:
            self._watch.remove(symbol)
        else:
            self._watch.append(symbol)
        _patch_state(watchlist=self._watch)
        self._render_list(True)

    def _paint_change(self) -> None:
        quote = self._by_symbol.get(self._symbol) or self._index_quote_map.get(self._symbol)
        change = self._session_change
        if change is None and quote is not None:
            change = quote.change_pct
        if change is None:
            self.header_change.setText("")
            return
        self.header_change.setText(f"{change:+.2f}%")
        tone = _color(_tone(change))
        self.header_price.setStyleSheet(
            f"color: {tone}; font-size: 16px; font-family: Consolas, monospace; background: transparent;"
        )
        self.header_change.setStyleSheet(
            f"color: {tone}; font-size: 13px; font-family: Consolas, monospace; background: transparent;"
        )

    def _shown_change(self, quote: Quote) -> float:
        if quote.symbol == self._symbol and self._session_change is not None:
            return self._session_change
        return quote.change_pct

    def _on_snapshot(self, generation: int, snapshot: Snapshot) -> None:
        if generation != self._snap_gen or snapshot.symbol != self._symbol:
            return
        if self._background:
            self._maybe_telegram(snapshot)
            self._ensure_liquidity(False)
            return
        self.heading.setText(snapshot.base)
        self.header_price.setText(format_price(snapshot.price))
        self._session_change = snapshot.session_change
        self._paint_change()
        self._paint(self._cards["price"][0], format_price(snapshot.price), "flat")
        self._cards["price"][1].setText(snapshot.symbol)
        self._paint(self._cards["oi"][0], format_usd(snapshot.oi_usd), "flat")
        oi_sub = "—" if snapshot.oi_base is None else f"{format_quantity(snapshot.oi_base)} {snapshot.base}"
        self._cards["oi"][1].setText(oi_sub)
        self._paint(self._cards["funding"][0], format_funding(snapshot.funding_percent), _tone(snapshot.funding_percent))
        eta = _funding_eta(snapshot.next_funding_ms)
        status = snapshot.funding_status
        self._cards["funding"][1].setText(f"{status} · {eta}" if eta else status)
        self._paint(
            self._cards["cvd_perp"][0],
            _with_base(snapshot.cvd_perp_day, snapshot.base),
            _tone(snapshot.cvd_perp_day),
        )
        self._cards["cvd_perp"][1].setText("gün içi · 03:00")
        self._paint(
            self._cards["cvd_spot"][0],
            _with_base(snapshot.cvd_spot_day, snapshot.base),
            _tone(snapshot.cvd_spot_day),
        )
        self._cards["cvd_spot"][1].setText("gün içi · 03:00" if snapshot.cvd_spot_day is not None else "spot yok")
        self._render_list(False)
        self._fill_windows(snapshot)
        self._live_15_anchor = next((item.anchor for item in snapshot.windows if item.label == "15dk"), None)
        self._maybe_telegram(snapshot)
        fresh = list(snapshot.candles)[-CHART_BARS:]
        if fresh:
            self._recent_start_ms = fresh[0].time_ms
        viewing_older = (
            self.price_view.zoomed
            and self._last_candles
            and fresh
            and self._last_candles[0].time_ms < fresh[0].time_ms
        )
        if viewing_older:
            older = [bar for bar in self._last_candles if bar.time_ms < fresh[0].time_ms]
            self._last_candles = _merge_bars(older, fresh)
        else:
            self._last_candles = fresh
            self._history_done = False
        self._last_oi = snapshot.oi_points
        self._last_funding = snapshot.funding_hourly
        self._draw_candles(self._last_candles)
        self._draw_oi(snapshot.oi_points)
        self._draw_funding(snapshot.funding_hourly)
        stamp = datetime.now().strftime("%H:%M:%S")
        message = f"{snapshot.symbol}  {stamp}  ·  fiyat, OI, funding, CVD güncellendi"
        if snapshot.warning:
            message = f"{message}  ·  {snapshot.warning}"
        self.statusBar().showMessage(message)
        self._ensure_liquidity(False)
        book = self._liq_map
        if (
            self._page == "liq"
            and book is not None
            and book.symbol == self._symbol
            and not self._thread_running("_liq_candle_worker")
        ):
            self._load_liq_candles()

    def _fill_windows(self, snapshot: Snapshot) -> None:
        self.analysis.setRowCount(len(snapshot.windows))
        reads = {item.label: item for item in snapshot.reads}
        for index, window in enumerate(snapshot.windows):
            read = reads.get(window.label)
            verdict = read.trap or read.result if read is not None else "—"
            cells = [
                (window.label, None, False, FLAT),
                (format_percent(window.price_pct), window.price_pct, True, None),
                (format_percent(window.oi_pct), window.oi_pct, True, None),
                (format_amount(window.cvd_perp), window.cvd_perp, True, None),
                (format_amount(window.cvd_spot), window.cvd_spot, True, None),
                (verdict, None, False, _verdict_color(verdict)),
            ]
            for column, (text, value, colored, tone) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if colored:
                    item.setForeground(UP if (value or 0) > 0 else DOWN if (value or 0) < 0 else MUTED)
                else:
                    item.setForeground(tone or FLAT)
                self.analysis.setItem(index, column, item)

    def _compute_bands(self) -> None:
        candles = self._last_candles
        if is_index(self._symbol) or not self._bb.get("on") or len(candles) < 2:
            self._band_points = []
            return
        step = candles[-1].time_ms - candles[-2].time_ms
        self._band_points = bollinger(
            candles,
            length=int(self._bb["length"]),
            ma_type=str(self._bb["ma"]),
            source=str(self._bb["source"]),
            mult=float(self._bb["mult"]),
            offset=int(self._bb["offset"]),
            bar_ms=step,
        )

    def _refresh_bands(self) -> None:
        self._compute_bands()
        self.price_view.set_bands(self._band_points)

    def _refresh_last_band(self) -> None:
        candles = self._last_candles
        if is_index(self._symbol) or not self._bb.get("on") or len(candles) < 2 or not self._band_points:
            self._refresh_bands()
            return
        self._band_points = bollinger_last(
            candles,
            self._band_points,
            length=int(self._bb["length"]),
            ma_type=str(self._bb["ma"]),
            source=str(self._bb["source"]),
            mult=float(self._bb["mult"]),
            offset=int(self._bb["offset"]),
            bar_ms=candles[-1].time_ms - candles[-2].time_ms,
        )
        self.price_view.set_bands(self._band_points)

    def _include_bands(self, lows: float, highs: float, start: float | None = None, end: float | None = None):
        for time_ms, _basis, upper, lower in self._band_points:
            if upper is None or lower is None:
                continue
            if start is not None and end is not None and not start <= time_ms <= end:
                continue
            lows = min(lows, lower)
            highs = max(highs, upper)
        return lows, highs

    def _draw_candles(self, candles) -> None:
        if candles and self._live_price is not None:
            last = candles[-1]
            price = self._live_price
            high = max(last.high, price)
            low = min(last.low, price)
            if price != last.close or high != last.high or low != last.low:
                candles[-1] = Bar(
                    last.time_ms, last.open, high, low, price, last.cvd_delta, last.volume
                )
        if not candles:
            self._live_set = None
            self._drawn_times = []
            self._drawn_sets = {}
            self.price_chart.removeAllSeries()
            self.price_view.clear_last_price()
            self.price_view.set_bands([])
            self._chart_title.setText("Fiyat")
            self._place_caption()
            self._sync_signals()
            return
        times = [int(bar.time_ms) for bar in candles]
        if self._patch_candles(candles, times):
            self._finish_candles(candles, appended=len(times) > len(self._drawn_times))
            return
        self._live_set = None
        self.price_chart.removeAllSeries()

        def candle_series(color: QColor) -> QCandlestickSeries:
            made = QCandlestickSeries()
            made.setIncreasingColor(color)
            made.setDecreasingColor(color)
            made.setBodyOutlineVisible(False)
            made.setCapsVisible(False)
            made.setBodyWidth(0.6)
            made.setPen(QPen(color, 1))
            return made

        up_series = candle_series(UP)
        down_series = candle_series(DOWN)
        self._up_series = up_series
        self._down_series = down_series
        self._drawn_sets = {}
        _disarm_ticks(self.price_y)
        for bar in candles:
            target = up_series if bar.close >= bar.open else down_series
            candle_set = QCandlestickSet(bar.open, bar.high, bar.low, bar.close, bar.time_ms)
            target.append(candle_set)
            self._drawn_sets[int(bar.time_ms)] = candle_set
        for series in (up_series, down_series):
            self.price_chart.addSeries(series)
            series.attachAxis(self.price_x)
            series.attachAxis(self.price_y)
        self._drawn_times = times
        self._drawn_symbol = self._symbol
        self._drawn_interval = self._interval
        self._chart_title.setText(f"Fiyat  ·  {self._symbol}")
        self._place_caption()
        self._finish_candles(candles, appended=True)

    def _patch_candles(self, candles, times: list[int]) -> bool:
        if self._up_series is None or self._down_series is None or not self._drawn_sets:
            return False
        if self._drawn_symbol != self._symbol or self._drawn_interval != self._interval:
            return False
        kind = _candle_follow(self._drawn_times, times)
        if kind not in ("update", "append"):
            return False
        known = set(self._drawn_sets)
        if any(stamp not in known for stamp in times[: len(self._drawn_times)]):
            return False
        for bar in candles:
            self._write_candle(bar)
        return True

    def _write_candle(self, bar) -> None:
        stamp = int(bar.time_ms)
        up = bar.close >= bar.open
        existing = self._drawn_sets.get(stamp)
        if existing is None:
            made = QCandlestickSet(bar.open, bar.high, bar.low, bar.close, bar.time_ms)
            (self._up_series if up else self._down_series).append(made)
            self._drawn_sets[stamp] = made
            return
        was_up = float(existing.close()) >= float(existing.open())
        if was_up != up and self._up_series is not None and self._down_series is not None:
            source = self._up_series if was_up else self._down_series
            target = self._up_series if up else self._down_series
            source.remove(existing)
            made = QCandlestickSet(bar.open, bar.high, bar.low, bar.close, bar.time_ms)
            target.append(made)
            self._drawn_sets[stamp] = made
            return
        if float(existing.open()) != bar.open:
            existing.setOpen(bar.open)
        if float(existing.high()) != bar.high:
            existing.setHigh(bar.high)
        if float(existing.low()) != bar.low:
            existing.setLow(bar.low)
        if float(existing.close()) != bar.close:
            existing.setClose(bar.close)

    def _finish_candles(self, candles, appended: bool) -> None:
        last = candles[-1]
        self._live_up = last.close >= last.open
        self._live_set = self._drawn_sets.get(int(last.time_ms))
        self.price_view.set_last_price(last.time_ms, last.close, last.close >= last.open)
        self.price_view.set_bands(self._band_points)
        self._sync_signals()
        self._note_price_home()
        if appended:
            self._drawn_times = [int(bar.time_ms) for bar in candles]
            if not self.price_view.zoomed:
                self._fit_price()
            else:
                self._fit_visible_price()
            return
        self._drawn_times = [int(bar.time_ms) for bar in candles]
        if self.price_view.zoomed or self.price_view.price_manual:
            return
        price = last.close
        if price < float(self.price_y.min()) or price > float(self.price_y.max()):
            self._fit_price()

    def _draw_oi(self, points) -> None:
        if not points:
            self.oi_chart.removeAllSeries()
            self.oi_chart.setTitle("Open interest")
            return
        scale, unit = _oi_unit(max(point.value for point in points))
        values = [point.value / scale for point in points]
        same_scale = getattr(self, "_oi_scale", None) == (scale, unit)
        same_symbol = getattr(self, "_oi_drawn_symbol", None) == self._symbol
        kind = self._patch_line(self.oi_chart, points, values) if same_scale and same_symbol else None
        if kind is None:
            self.oi_chart.removeAllSeries()
            series = QLineSeries()
            pen = series.pen()
            pen.setColor(QColor("#7dd3fc"))
            pen.setWidth(2)
            series.setPen(pen)
            for point, value in zip(points, values):
                series.append(float(point.time_ms), value)
            self.oi_chart.addSeries(series)
            series.attachAxis(self.oi_x)
            series.attachAxis(self.oi_y)
            kind = "append"
        self._oi_scale = (scale, unit)
        self._oi_drawn_symbol = self._symbol
        unit_label = f" ({unit})" if unit else ""
        self.oi_chart.setTitle(f"Open interest  ·  {base_of(self._symbol)}  ·  15dk{unit_label}")
        if kind == "update":
            return
        if not self.oi_view.zoomed:
            self._fit_oi()
        elif not self.oi_view.price_manual:
            self._fit_visible_oi()

    def _patch_line(self, chart, points, values: list[float]) -> str | None:
        series_list = [item for item in chart.series() if isinstance(item, QLineSeries)]
        if len(series_list) != 1 or not points or len(points) != len(values):
            return None
        series = series_list[0]
        count = series.count()
        if count < 1 or abs(series.at(0).x() - float(points[0].time_ms)) > 1:
            return None
        if count == len(points):
            last = count - 1
            series.replace(last, float(points[last].time_ms), values[last])
            return "update"
        if count + 1 == len(points) and abs(series.at(count - 1).x() - float(points[count - 1].time_ms)) < 1:
            series.replace(count - 1, float(points[count - 1].time_ms), values[count - 1])
            series.append(float(points[-1].time_ms), values[-1])
            return "append"
        return None

    def _draw_funding(self, points) -> None:
        shown = points if self._funding_step != "8h" else bucket_funding(points, 8)
        self._funding_shown = list(shown)
        step = "8sa" if self._funding_step == "8h" else "1sa"
        if len(shown) < 2:
            self.funding_chart.removeAllSeries()
            self.funding_chart.setTitle("Funding")
            return
        values = [point.value for point in shown]
        drawn = getattr(self, "_funding_drawn", None) == (self._symbol, self._funding_step)
        kind = self._patch_line(self.funding_chart, shown, values) if drawn else None
        if kind is None:
            self.funding_chart.removeAllSeries()
            series = QLineSeries()
            pen = series.pen()
            pen.setColor(AMBER)
            pen.setWidth(2)
            series.setPen(pen)
            for point, value in zip(shown, values):
                series.append(float(point.time_ms), value)
            self.funding_chart.addSeries(series)
            series.attachAxis(self.funding_x)
            series.attachAxis(self.funding_y)
            kind = "append"
        self._funding_drawn = (self._symbol, self._funding_step)
        self.funding_chart.setTitle(f"Funding  ·  {base_of(self._symbol)}  ·  72sa · {step}")
        if kind == "update":
            return
        if not self.funding_view.zoomed:
            self._fit_funding()
        elif not self.funding_view.price_manual:
            self._fit_visible_funding()

    def _liquidity_target(self, verdict: str, hours: int, price: float | None) -> float | None:
        side = _trade_side(verdict)
        if side is None:
            return None
        table = self._liq_targets.get((hours, self._liq_conserved))
        if table is None or table.symbol != self._symbol:
            return None
        return table.pick(side, price)

    def _maybe_telegram(self, snapshot: Snapshot) -> None:
        data = _read_state()
        reads = {item.label: item for item in snapshot.reads}
        windows = {item.label: item for item in snapshot.windows}
        verdicts: dict[str, str] = {}
        map_ready = self._targets_ready()
        for label in SIGNAL_WINDOWS:
            read = reads.get(label)
            if read is None:
                continue
            verdict = read.trap or read.result
            if _trade_side(verdict) and not map_ready:
                continue
            verdicts[label] = verdict
        targets = {
            label: self._liquidity_target(verdict, SIGNAL_LIQ_HOURS[label], snapshot.price)
            for label, verdict in verdicts.items()
            if label in SIGNAL_LIQ_HOURS
        }
        candles: dict[str, tuple[int, float | None, float | None]] = {}
        for label in SIGNAL_WINDOWS:
            window = windows.get(label)
            if window is None or window.closed_ms is None:
                continue
            candles[label] = (window.closed_ms, window.closed_high, window.closed_low)
        stored = data.get("signal_open")
        book = dict(stored) if isinstance(stored, dict) else {}
        previous = book.get(snapshot.symbol) if isinstance(book.get(snapshot.symbol), dict) else {}
        touches = snapshot.touches if isinstance(snapshot.touches, dict) else {}
        book, events, notes = _advance_signal(
            book, snapshot.symbol, candles, verdicts, targets, snapshot.price, touches
        )
        current = book.get(snapshot.symbol) if isinstance(book.get(snapshot.symbol), dict) else {}

        def _stamp(entry: dict, label: str):
            episode = entry.get(label)
            return episode.get("candle") if isinstance(episode, dict) else None

        patch: dict = {}
        moved = any(_stamp(previous, label) != _stamp(current, label) for label in SIGNAL_WINDOWS)
        if moved or events or notes:
            sent = data.get("telegram_sent")
            sent_book = dict(sent) if isinstance(sent, dict) else {}
            sent_book[snapshot.symbol] = verdicts
            patch["signal_open"] = book
            if moved:
                patch["telegram_sent"] = sent_book
        if notes:
            patch["signal_score"] = _bump_score(data.get("signal_score"), snapshot.symbol, notes)
        fresh_marks = []
        closed_changed = False
        seen = {
            (item["symbol"], item["label"], item["time_ms"], item["side"])
            for item in self._signal_marks
        }
        for event in events:
            if event.kind in ("kapandi", "sure", "yeni"):
                old = previous.get(event.label) if isinstance(previous, dict) else None
                if event.kind != "yeni" or (isinstance(old, dict) and old.get("verdict")):
                    if _close_mark(self._signal_marks, snapshot.symbol, event.label, old):
                        closed_changed = True
            if event.kind != "yeni":
                continue
            episode = current.get(event.label)
            side = episode.get("side") if isinstance(episode, dict) else None
            if side not in ("up", "down"):
                side = _trade_side(event.verdict)
            if side not in ("up", "down"):
                continue
            moment = episode.get("opened_ms") if isinstance(episode, dict) else None
            try:
                stamp = int(moment)
            except (TypeError, ValueError):
                stamp = int(time.time() * 1000)
            mark = {
                "symbol": snapshot.symbol,
                "label": event.label,
                "side": "buy" if side == "up" else "sell",
                "time_ms": stamp,
                "closed": False,
            }
            key = (mark["symbol"], mark["label"], mark["time_ms"], mark["side"])
            if key in seen or stamp <= 0:
                continue
            seen.add(key)
            fresh_marks.append(mark)
        if fresh_marks:
            self._signal_marks.extend(fresh_marks)
        before = len(self._signal_marks)
        self._signal_marks = _trim_signal_marks(self._signal_marks)
        if fresh_marks or closed_changed or len(self._signal_marks) != before:
            patch["signal_marks"] = self._signal_marks
        if patch:
            _patch_state(**patch)
        try:
            self._queue_okx(snapshot, events, current)
        except Exception as exc:
            _okx_note(snapshot.symbol, "", "", False, f"emir sıraya alınamadı: {type(exc).__name__}: {exc}")
            self._show_okx_log()
        try:
            lines = [
                _episode_text(snapshot, event, windows.get(event.label))
                for event in events
            ]
            token = str(data.get("telegram_token") or "").strip()
            chat_id = str(data.get("telegram_chat_id") or "").strip()
            if lines and token and chat_id:
                self._queue_telegram(token, chat_id, "\n\n".join(lines))
        except Exception:
            pass
        for paint in (self._paint_active, self._sync_signals):
            try:
                paint()
            except Exception:
                pass

    def _okx_settings(self) -> dict | None:
        data = _read_state()
        if _okx_block(data):
            return None
        creds = Creds(
            str(data.get("okx_api_key") or ""),
            str(data.get("okx_secret") or ""),
            str(data.get("okx_passphrase") or ""),
        )
        if not creds.ready():
            return None
        pct = _margin_pct(data)
        try:
            leverage = int(data.get("okx_leverage") or 0)
        except (TypeError, ValueError):
            return None
        if pct <= 0 or leverage < 1:
            return None
        raw = data.get("okx_windows")
        if raw is None:
            windows = set(SIGNAL_WINDOWS)
        elif isinstance(raw, list):
            windows = {str(item) for item in raw if str(item) in SIGNAL_WINDOWS}
        else:
            windows = set(SIGNAL_WINDOWS)
        if not windows:
            return None
        mode = str(data.get("okx_mgn_mode") or "cross")
        return {
            "creds": creds,
            "margin_pct": pct,
            "leverage": leverage,
            "mgn_mode": mode if mode in ("cross", "isolated") else "cross",
            "simulated": bool(data.get("okx_simulated")),
            "windows": windows,
            "positions": data.get("okx_positions") if isinstance(data.get("okx_positions"), dict) else {},
        }

    def _queue_okx(self, snapshot: Snapshot, events: list[_SignalEvent], current: dict) -> None:
        events = [event for event in events if event.kind in ("yeni", "kapandi", "sure", "hedef")]
        if not events:
            return
        settings = self._okx_settings()
        if settings is None:
            reason = _okx_block(_read_state()) or "ayarlar eksik"
            for event in events:
                if event.kind == "yeni":
                    _okx_note(snapshot.symbol, event.label, "open", False, f"emir gitmedi: {reason}")
            self._show_okx_log()
            return
        for event in events:
            if event.kind == "yeni" and event.label not in settings["windows"]:
                _okx_note(snapshot.symbol, event.label, "open", False, f"emir gitmedi: {event.label} seçili değil")
                self._show_okx_log()
        coin = settings["positions"].get(snapshot.symbol)
        if not isinstance(coin, dict):
            coin = {}
        price = float(snapshot.price) if isinstance(snapshot.price, (int, float)) and snapshot.price > 0 else 0.0
        actions: list[dict] = []
        for event in events:
            if event.label not in settings["windows"]:
                continue
            if event.kind in ("kapandi", "sure"):
                held = coin.get(event.label)
                if isinstance(held, dict):
                    actions.append({"op": "close", "label": event.label, "held": dict(held)})
                continue
            if event.kind == "hedef":
                episode = current.get(event.label) if isinstance(current.get(event.label), dict) else {}
                held = coin.get(event.label)
                if not isinstance(held, dict) or held.get("manual"):
                    continue
                new_tp = episode.get("tp")
                old_tp = held.get("tp")
                if not isinstance(new_tp, (int, float)) or float(new_tp) <= 0:
                    continue
                if isinstance(old_tp, (int, float)) and _same_price(float(old_tp), float(new_tp)):
                    continue
                actions.append(
                    {
                        "op": "amend",
                        "label": event.label,
                        "held": dict(held),
                        "tp": float(new_tp),
                        "side": episode.get("side"),
                        "price": price,
                    }
                )
                continue
            if event.kind != "yeni":
                continue
            episode = current.get(event.label) if isinstance(current.get(event.label), dict) else {}
            held = coin.get(event.label)
            if isinstance(held, dict):
                actions.append({"op": "close", "label": event.label, "held": dict(held)})
            tp = episode.get("tp")
            entry = episode.get("entry")
            spot = float(entry) if isinstance(entry, (int, float)) and float(entry) > 0 else price
            actions.append(
                {
                    "op": "open",
                    "label": event.label,
                    "side": episode.get("side"),
                    "tp": tp,
                    "price": spot,
                }
            )
        if not actions:
            return
        payload = {
            "creds": settings["creds"],
            "symbol": snapshot.symbol,
            "price": price,
            "settings": {
                "margin_pct": settings["margin_pct"],
                "leverage": settings["leverage"],
                "mgn_mode": settings["mgn_mode"],
                "simulated": settings["simulated"],
            },
            "actions": actions,
        }
        if self._thread_running("_okx_worker"):
            self._okx_wait.append(payload)
            _okx_note(snapshot.symbol, "", "", True, "önceki emir sürüyor, sıraya alındı")
            self._show_okx_log()
            return
        self._start_okx(payload)

    def _start_okx(self, payload: dict) -> None:
        worker = _OkxWorker(
            payload["creds"],
            payload["symbol"],
            payload["price"],
            payload["settings"],
            payload["actions"],
        )
        worker.done.connect(self._on_okx)
        self._launch(worker, "_okx_worker")

    def _on_okx(self, results: object) -> None:
        if not isinstance(results, list):
            results = []
        data = _read_state()
        positions = dict(data.get("okx_positions") or {}) if isinstance(data.get("okx_positions"), dict) else {}
        lines: list[str] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            symbol = str(row.get("symbol") or "")
            label = str(row.get("label") or "")
            coin = dict(positions.get(symbol) or {}) if symbol else {}
            if row.get("op") == "close" and row.get("ok") and label:
                coin.pop(label, None)
            elif row.get("op") == "open" and row.get("ok") and isinstance(row.get("position"), dict) and label:
                coin[label] = row["position"]
            elif row.get("op") == "amend" and row.get("ok") and isinstance(row.get("position"), dict) and label:
                coin[label] = row["position"]
            if symbol:
                if coin:
                    positions[symbol] = coin
                else:
                    positions.pop(symbol, None)
            text = str(row.get("message") or "")
            if text:
                lines.append(f"{base_of(symbol)} {label} {text}".strip())
        if results:
            _patch_state(okx_positions=positions)
        for row in results:
            if isinstance(row, dict) and row.get("message"):
                _okx_note(
                    str(row.get("symbol") or ""),
                    str(row.get("label") or ""),
                    str(row.get("op") or ""),
                    bool(row.get("ok")),
                    str(row["message"]),
                )
        if lines:
            self.statusBar().showMessage(" · ".join(lines))
        self._show_okx_log()
        if self._okx_wait:
            self._start_okx(self._okx_wait.pop(0))

    def _show_okx_log(self) -> None:
        if not hasattr(self, "_okx_log"):
            return
        lines = _okx_log_lines()
        if not lines:
            self._okx_log.setText("Son emir yok")
            self._okx_log.setStyleSheet("color: #e4e4e7;")
            return
        rows = [
            f'<span style="color: {"#26a69a" if ok else "#ef5350"};">{html.escape(text)}</span>'
            for text, ok in lines
        ]
        self._okx_log.setTextFormat(Qt.TextFormat.RichText)
        self._okx_log.setText("<br>".join(rows))
        self._okx_log.setStyleSheet("")

    def _okx_account(self) -> tuple[Creds, bool]:
        data = _read_state()
        creds = Creds(
            str(data.get("okx_api_key") or ""),
            str(data.get("okx_secret") or ""),
            str(data.get("okx_passphrase") or ""),
        )
        return creds, bool(data.get("okx_simulated"))

    def _poll_okx(self, force: bool = False) -> None:
        creds, simulated = self._okx_account()
        if not creds.ready():
            if self._okx_live or self._okx_live_error:
                self._okx_live = []
                self._okx_live_error = ""
                self._render_okx_positions()
                self._paint_position_lines()
            return
        if self._thread_running("_okx_positions_worker"):
            return
        now = time.monotonic()
        if not force and self._background and now - self._okx_polled < 30:
            return
        self._okx_polled = now
        worker = _OkxPositions(creds, simulated)
        worker.done.connect(self._on_okx_positions)
        worker.failed.connect(self._on_okx_positions_fail)
        self._launch(worker, "_okx_positions_worker")

    def _on_okx_positions(self, payload: object) -> None:
        rows = payload.get("positions") if isinstance(payload, dict) else payload
        self._okx_live = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        self._okx_live_error = ""
        if isinstance(payload, dict) and isinstance(payload.get("available"), (int, float)):
            self._okx_available = float(payload["available"])
            self._paint_okx_size()
        self._render_okx_positions()
        self._paint_position_lines()

    def _on_okx_positions_fail(self, message: str) -> None:
        self._okx_live_error = message
        self._render_okx_positions()

    @staticmethod
    def _okx_row_key(position: dict) -> str:
        return f"{position.get('inst')}|{position.get('pos_side')}"

    def _okx_position(self, key: str) -> dict | None:
        for position in self._okx_live:
            if self._okx_row_key(position) == key:
                return position
        return None

    def _render_okx_positions(self) -> None:
        if not hasattr(self, "_okx_table"):
            return
        table = self._okx_table
        positions = self._okx_live
        keys = [self._okx_row_key(position) for position in positions]
        if keys != self._okx_row_keys:
            self._okx_row_keys = keys
            table.setRowCount(len(positions))
            for row, key in enumerate(keys):
                for column in range(table.columnCount() - 1):
                    item = QTableWidgetItem("")
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    table.setItem(row, column, item)
                table.setCellWidget(row, table.columnCount() - 1, self._okx_buttons(key))
        for row, position in enumerate(positions):
            for column, (text, color) in enumerate(_position_cells(position)):
                item = table.item(row, column)
                if item is None:
                    continue
                if item.text() != text:
                    item.setText(text)
                if item.foreground().color() != color:
                    item.setForeground(color)
        if self._okx_live_error:
            self._okx_live_note.setText(self._okx_live_error)
            self._okx_live_note.setStyleSheet("color: #ef5350;")
        elif not positions:
            creds, _simulated = self._okx_account()
            self._okx_live_note.setText("Açık pozisyon yok" if creds.ready() else "OKX API bilgileri girilmedi")
            self._okx_live_note.setStyleSheet("color: #a1a1aa;")
        else:
            total = sum(float(position.get("upl") or 0) for position in positions)
            self._okx_live_note.setText(f"Toplam PnL {total:+.2f} USDT")
            self._okx_live_note.setStyleSheet(f"color: {'#26a69a' if total >= 0 else '#ef5350'};")

    def _okx_buttons(self, key: str) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)
        tp_sl = QPushButton("TP/SL")
        trail = QPushButton("Trailing")
        for button in (tp_sl, trail):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("padding: 1px 8px;")
            row.addWidget(button)
        tp_sl.clicked.connect(lambda _checked=False, key=key: self._edit_tp_sl(key))
        trail.clicked.connect(lambda _checked=False, key=key: self._edit_trailing(key))
        return holder

    def _okx_busy(self) -> bool:
        if self._thread_running("_okx_action_worker"):
            self._okx_live_note.setText("Önceki emir sürüyor, biraz bekle")
            self._okx_live_note.setStyleSheet("color: #ff9800;")
            return True
        return False

    def _edit_tp_sl(self, key: str) -> None:
        position = self._okx_position(key)
        if position is None or self._okx_busy():
            return
        chosen = _tp_sl_dialog(self, position)
        if chosen is None:
            return
        tp, sl = chosen
        self._send_okx_action({"kind": "tpsl", "position": dict(position), "tp": tp, "sl": sl})

    def _edit_trailing(self, key: str) -> None:
        position = self._okx_position(key)
        if position is None or self._okx_busy():
            return
        chosen = _trailing_dialog(self, position)
        if chosen is None:
            return
        job = {"kind": "trail", "position": dict(position)}
        job.update(chosen)
        self._send_okx_action(job)

    def _send_okx_action(self, job: dict) -> None:
        creds, simulated = self._okx_account()
        if not creds.ready():
            return
        self._okx_live_note.setText("Emir gönderiliyor…")
        self._okx_live_note.setStyleSheet("color: #a1a1aa;")
        worker = _OkxAction(creds, simulated, job)
        worker.done.connect(self._on_okx_action)
        self._launch(worker, "_okx_action_worker")

    def _on_okx_action(self, ok: bool, message: str, job: object) -> None:
        position = job.get("position") if isinstance(job, dict) else None
        name = base_of(str(position.get("symbol") or "")) if isinstance(position, dict) else ""
        text = f"{name} {message}".strip()
        self.statusBar().showMessage(text)
        if hasattr(self, "_okx_log"):
            self._okx_log.setText(text)
            self._okx_log.setStyleSheet("color: #26a69a;" if ok else "color: #ef5350;")
        if ok and isinstance(job, dict) and job.get("kind") == "tpsl" and isinstance(position, dict):
            self._mark_manual(position, job.get("result") or {})
        self._poll_okx(True)

    def _mark_manual(self, position: dict, result: dict) -> None:
        """Elle TP/SL girilen pozisyonda bot TP'yi artık değiştirmez."""
        data = _read_state()
        stored = data.get("okx_positions")
        if not isinstance(stored, dict):
            return
        symbol = str(position.get("symbol") or "")
        coin = stored.get(symbol)
        if not isinstance(coin, dict):
            return
        changed = False
        for label, held in list(coin.items()):
            if not isinstance(held, dict):
                continue
            if held.get("inst") != position.get("inst") or held.get("side") != position.get("side"):
                continue
            updated = dict(held)
            updated["manual"] = True
            updated["tp"] = result.get("tp")
            updated["algo_id"] = str(result.get("algo_id") or "")
            coin[label] = updated
            changed = True
        if changed:
            stored[symbol] = coin
            _patch_state(okx_positions=stored)

    def _paint_position_lines(self) -> None:
        if not hasattr(self, "price_view"):
            return
        lines: list[tuple[float, str, QColor]] = []
        for position in self._okx_live:
            if position.get("symbol") != self._symbol:
                continue
            word = "Long" if position.get("side") == "up" else "Short"
            upl = position.get("upl")
            entry = position.get("avg")
            if isinstance(entry, (int, float)) and entry > 0:
                pnl = f" {float(upl):+.2f}" if isinstance(upl, (int, float)) else ""
                lines.append((float(entry), f"{word}{pnl}", QColor("#a1a1aa")))
            liq = position.get("liq")
            if isinstance(liq, (int, float)) and liq > 0:
                lines.append((float(liq), "Liq", QColor("#ff7043")))
            if self._signal.get("on"):
                if isinstance(position.get("tp"), (int, float)) and position["tp"] > 0:
                    lines.append((float(position["tp"]), "TP", TP_LINE))
                if isinstance(position.get("sl"), (int, float)) and position["sl"] > 0:
                    lines.append((float(position["sl"]), "SL", DOWN))
            trail = position.get("trail")
            if isinstance(trail, dict) and isinstance(trail.get("active"), (int, float)) and trail["active"] > 0:
                lines.append((float(trail["active"]), "Trail", QColor("#42a5f5")))
        self.price_view.set_positions(lines)

    def _queue_telegram(self, token: str, chat_id: str, text: str) -> None:
        job = _TelegramSend(token, chat_id, text)
        job.setParent(self)
        job.finished_ok.connect(self._on_telegram)
        job.finished.connect(lambda job=job: self._drop_telegram(job))
        self._telegram_jobs.append(job)
        job.start()

    def _drop_telegram(self, job: QThread) -> None:
        if job in self._telegram_jobs:
            self._telegram_jobs.remove(job)

    def _on_telegram(self, ok: bool) -> None:
        if not ok:
            self.statusBar().showMessage("Telegram iletilemedi")

    def _add_alarm(self, price: float) -> None:
        price = float(price)
        if price <= 0:
            return
        levels = self._alarms.setdefault(self._symbol, [])
        if any(_same_price(level, price) for level in levels):
            self.statusBar().showMessage("Bu fiyatta alarm zaten var")
            return
        levels.append(price)
        levels.sort()
        _patch_state(alarms=self._alarms)
        self.price_view.set_alarms(levels)
        self._refresh_alarms()
        self._sync_feed()
        self.statusBar().showMessage(f"Alarm {format_price(price)}")

    def _remove_alarm(self, price: float, symbol: str | None = None) -> None:
        symbol = symbol or self._symbol
        levels = [level for level in self._alarms.get(symbol, []) if not _same_price(level, price)]
        if levels:
            self._alarms[symbol] = levels
        else:
            self._alarms.pop(symbol, None)
        _patch_state(alarms=self._alarms)
        if symbol == self._symbol:
            self.price_view.set_alarms(self._alarms.get(symbol, []))
        self._refresh_alarms()
        self._sync_feed()
        self.statusBar().showMessage(f"Alarm kaldırıldı · {base_of(symbol)} {format_price(price)}")

    def _move_open_alarm(self, old: float, new: float) -> None:
        self._move_alarm_to(self._symbol, old, new)

    def _move_alarm_to(self, symbol: str, old: float, new: float) -> None:
        if new <= 0 or _same_price(old, new):
            if symbol == self._symbol:
                self.price_view.set_alarms(self._alarms.get(symbol, []))
            return
        levels = list(self._alarms.get(symbol, []))
        if any(_same_price(level, new) for level in levels if not _same_price(level, old)):
            if symbol == self._symbol:
                self.price_view.set_alarms(levels)
            self.statusBar().showMessage("Bu fiyatta alarm zaten var")
            return
        updated: list[float] = []
        replaced = False
        for level in levels:
            if not replaced and _same_price(level, old):
                updated.append(new)
                replaced = True
            else:
                updated.append(level)
        if not replaced:
            return
        updated.sort()
        self._alarms[symbol] = updated
        _patch_state(alarms=self._alarms)
        if symbol == self._symbol:
            self.price_view.set_alarms(updated)
        self._refresh_alarms()
        self.statusBar().showMessage(f"{base_of(symbol)} alarmı {format_price(new)}")

    def _on_trades(self, symbol: str, path: list) -> None:
        for price in path:
            self._check_alarms(symbol, price)
        if self._background or symbol != self._symbol or not path:
            return
        self._live_price = path[-1]
        if not self._live_timer.isActive():
            self._live_timer.start(100)

    def _flush_live(self) -> None:
        if self._background:
            return
        price = self._live_price
        if price is None:
            return
        self.header_price.setText(format_price(price))
        self._paint(self._cards["price"][0], format_price(price), "flat")
        self._paint_row_price(price)
        self._paint_15_price(price)
        self._touch_candle(price)

    def _find_row(self, table: QTableWidget, cached: str, matches) -> int:
        row = getattr(self, cached, -1)
        if 0 <= row < table.rowCount() and matches(table.item(row, 0), table.item(row, 1)):
            return row
        for row in range(table.rowCount()):
            if matches(table.item(row, 0), table.item(row, 1)):
                setattr(self, cached, row)
                return row
        setattr(self, cached, -1)
        return -1

    def _paint_row_price(self, price: float) -> None:
        symbol = self._symbol
        row = self._find_row(
            self.table,
            "_price_row",
            lambda _star, item: item is not None and item.data(Qt.ItemDataRole.UserRole) == symbol,
        )
        if row < 0:
            return
        cell = self.table.item(row, 2)
        if cell is not None:
            text = format_price(price)
            if cell.text() != text:
                cell.setText(text)

    def _paint_15_price(self, price: float) -> None:
        anchor = self._live_15_anchor
        if anchor is None or anchor == 0 or not hasattr(self, "analysis"):
            return
        pct = (price - anchor) / anchor * 100
        row = self._find_row(
            self.analysis, "_row_15", lambda label, _cell: label is not None and label.text() == "15dk"
        )
        if row < 0:
            return
        cell = self.analysis.item(row, 1)
        if cell is None:
            return
        text = format_percent(pct)
        if cell.text() != text:
            cell.setText(text)
            cell.setForeground(UP if pct > 0 else DOWN if pct < 0 else MUTED)

    def _touch_candle(self, price: float) -> None:
        candles = self._last_candles
        candle_set = self._live_set
        if not candles or candle_set is None:
            return
        bar = candles[-1]
        high = max(bar.high, price)
        low = min(bar.low, price)
        if price == bar.close and high == bar.high and low == bar.low:
            self.price_view.set_last_price(bar.time_ms, price, price >= bar.open)
            return
        up = price >= bar.open
        if up != self._live_up and self._up_series is not None and self._down_series is not None:
            source = self._up_series if self._live_up else self._down_series
            target = self._up_series if up else self._down_series
            source.remove(candle_set)
            candle_set = QCandlestickSet(bar.open, high, low, price, bar.time_ms)
            target.append(candle_set)
            self._live_set = candle_set
            self._drawn_sets[int(bar.time_ms)] = candle_set
            self._live_up = up
        candles[-1] = Bar(bar.time_ms, bar.open, high, low, price, bar.cvd_delta, bar.volume)
        self._refresh_last_band()
        candle_set.setHigh(high)
        candle_set.setLow(low)
        candle_set.setClose(price)
        self.price_view.set_last_price(bar.time_ms, price, price >= bar.open)
        if self.price_view.zoomed or self.price_view.price_manual:
            return
        if price < float(self.price_y.min()) or price > float(self.price_y.max()):
            self._fit_price()

    def _check_alarms(self, symbol: str, price: float) -> None:
        previous = self._last_trades.get(symbol)
        self._last_trades[symbol] = price
        if previous is None or previous == price:
            return
        levels = list(self._alarms.get(symbol, []))
        hit = [level for level in levels if (previous < level <= price) or (previous > level >= price)]
        if not hit:
            return
        kept = [level for level in levels if level not in hit]
        if kept:
            self._alarms[symbol] = kept
        else:
            self._alarms.pop(symbol, None)
        _patch_state(alarms=self._alarms)
        if symbol == self._symbol:
            self.price_view.set_alarms(self._alarms.get(symbol, []))
        self._refresh_alarms()
        self._sync_feed()
        self._play_alarm()
        text = self._alarm_text(symbol, hit, price)
        names = ", ".join(format_price(level) for level in hit)
        data = _read_state()
        token = str(data.get("telegram_token") or "").strip()
        chat_id = str(data.get("telegram_chat_id") or "").strip()
        if token and chat_id:
            self.statusBar().showMessage(f"{symbol} alarmı tetiklendi · {names}")
            self._queue_telegram(token, chat_id, text)
        else:
            self.statusBar().showMessage(f"{symbol} alarmı tetiklendi · {names} · Telegram ayarı yok")

    def _alarm_text(self, symbol: str, levels: list[float], price: float) -> str:
        rows = "\n".join(format_price(level) for level in levels)
        return f"{symbol} fiyat alarmı\nSeviye:\n{rows}\nFiyat: {format_price(price)}"

    def _play_alarm(self) -> None:
        try:
            winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except RuntimeError:
            QApplication.beep()

    def _hover_date(self, global_pos, time_ms: int | None) -> None:
        if global_pos is None or time_ms is None or not self._last_candles:
            QToolTip.hideText()
            return
        bar = _nearest_bar(self._last_candles, time_ms)
        latest = datetime.fromtimestamp(self._last_candles[-1].time_ms / 1000)
        if bar is None:
            step = self._bar_step()
            last_ms = self._last_candles[-1].time_ms
            if step <= 0 or not last_ms < time_ms <= last_ms + step * (FUTURE_BARS + 1):
                QToolTip.hideText()
                return
            moment = datetime.fromtimestamp(time_ms / 1000)
        else:
            moment = datetime.fromtimestamp(bar.time_ms / 1000)
        QToolTip.showText(global_pos, _hover_day(moment, moment.year != latest.year), self.price_view)

    def _bar_step(self) -> int:
        candles = self._last_candles
        if len(candles) < 2:
            return 15 * 60 * 1000
        return max(candles[-1].time_ms - candles[-2].time_ms, 1)

    def _trim_history(self) -> bool:
        if is_index(self._symbol) or len(self._last_candles) <= CHART_BARS:
            return False
        self._last_candles = self._last_candles[-CHART_BARS:]
        self._recent_start_ms = self._last_candles[0].time_ms
        self._history_done = False
        self._history_gen += 1
        self.price_view.pushed_older = False
        return True

    def _maybe_history(self) -> None:
        if is_index(self._symbol) or self._paging or not self.price_view.zoomed:
            return
        candles = self._last_candles
        if len(candles) < 2:
            return
        start = int(self.price_x.min().toMSecsSinceEpoch())
        end = int(self.price_x.max().toMSecsSinceEpoch())
        step = self._bar_step()
        latest = candles[-1].time_ms
        oldest = candles[0].time_ms
        back_at_end = end >= latest - 20 * step and start >= self._recent_start_ms - step
        if len(candles) > CHART_BARS and back_at_end:
            self._paging = True
            try:
                if self._trim_history():
                    self._draw_candles(self._last_candles)
            finally:
                self._paging = False
            return
        near_old = start <= oldest + 40 * step
        away_from_now = end < latest - 5 * step
        if self.price_view.pushed_older or (near_old and away_from_now):
            self._load_older()

    def _load_older(self) -> None:
        if is_index(self._symbol) or self._history_done or self._thread_running("_history_worker") or not self._last_candles:
            return
        self._history_gen += 1
        generation = self._history_gen
        before = self._last_candles[0].time_ms
        worker = HistoryWorker(self._symbol, self._interval, before)
        worker.done.connect(lambda bars, gen=generation, mark=before: self._on_older(gen, mark, bars))
        worker.failed.connect(lambda _message, gen=generation: self._on_older_fail(gen))
        self._launch(worker, "_history_worker")

    def _on_older(self, generation: int, before_ms: int, bars) -> None:
        if generation != self._history_gen or not self._last_candles:
            return
        self.price_view.pushed_older = False
        fresh = [bar for bar in bars if bar.time_ms < before_ms]
        if len(fresh) < CHART_PAGE:
            self._history_done = True
        if not fresh:
            return
        self._last_candles = _merge_bars(fresh, self._last_candles)
        self._paging = True
        try:
            self._draw_candles(self._last_candles)
        finally:
            self._paging = False

    def _on_older_fail(self, generation: int) -> None:
        if generation != self._history_gen:
            return
        self.price_view.pushed_older = False

    def _note_price_home(self) -> None:
        candles = self._last_candles
        if len(candles) < 2:
            return
        step = candles[-1].time_ms - candles[-2].time_ms
        self.price_view.set_bar_step(step)
        self._compute_bands()
        lows = min(bar.low for bar in candles)
        highs = max(bar.high for bar in candles)
        lows, highs = self._include_bands(lows, highs)
        pad = (highs - lows) * 0.06 or highs * 0.002 or 1
        future_end = candles[-1].time_ms + step * (FUTURE_BARS + 1)
        self.price_view.set_home(candles[0].time_ms, future_end, lows - pad, highs + pad)

    def _fit_price(self) -> None:
        if self._trim_history():
            self._draw_candles(self._last_candles)
            return
        candles = self._last_candles
        if len(candles) < 2:
            return
        step = candles[-1].time_ms - candles[-2].time_ms
        self.price_view.set_bar_step(step)
        full_start = candles[0].time_ms
        end_ms = candles[-1].time_ms + step
        width = self.price_view.chart().plotArea().width()
        visible_span = min(end_ms - full_start, step * _readable_bars(width))
        start_ms = end_ms - visible_span
        shown = [bar for bar in candles if bar.time_ms >= start_ms]
        self._compute_bands()
        lows = min(bar.low for bar in shown)
        highs = max(bar.high for bar in shown)
        lows, highs = self._include_bands(lows, highs, start_ms, end_ms)
        pad = (highs - lows) * 0.06 or highs * 0.002 or 1
        y_low, y_high = lows - pad, highs + pad
        self.price_view.set_home(full_start, candles[-1].time_ms + step * (FUTURE_BARS + 1), y_low, y_high)
        _fit_x(self.price_x, int(start_ms), end_ms, self._interval)
        _show_candle_marks(self.price_view, candles, int(start_ms), end_ms, self._interval)
        _apply_price_scale(self.price_y, y_low, y_high)

    def _fit_visible_price(self) -> None:
        candles = self._last_candles
        if not candles:
            return
        start = self.price_x.min().toMSecsSinceEpoch()
        end = self.price_x.max().toMSecsSinceEpoch()
        visible = [bar for bar in candles if start <= bar.time_ms <= end]
        step = self._bar_step()
        slots = len(visible)
        if step > 0 and candles:
            future_until = min(end, candles[-1].time_ms + step * (FUTURE_BARS + 1))
            if future_until > candles[-1].time_ms:
                slots += max(0, int((future_until - max(start, candles[-1].time_ms)) / step))
        home = self.price_view._home_x
        zoom = 1.0
        if home is not None and end > start:
            zoom = max((candles[-1].time_ms - candles[0].time_ms) / (end - start), 1.0)
        budget = _label_budget(self.price_view.chart().plotArea().width(), zoom, slots)
        _show_candle_marks(self.price_view, candles, start, end, self._interval, budget)
        self._maybe_history()
        if self.price_view.price_manual:
            return
        visible = [bar for bar in candles if start <= bar.time_ms <= end]
        if not visible:
            return
        self._compute_bands()
        lows = min(bar.low for bar in visible)
        highs = max(bar.high for bar in visible)
        lows, highs = self._include_bands(lows, highs, start, end)
        pad = (highs - lows) * 0.08 or highs * 0.002 or 1
        _apply_price_scale(self.price_y, lows - pad, highs + pad)

    def _fit_oi(self) -> None:
        points = self._last_oi
        if len(points) < 2:
            return
        step = points[-1].time_ms - points[-2].time_ms or BAR_FALLBACK
        scale = self._oi_scale[0] or 1.0
        values = [point.value / scale for point in points]
        low, high = min(values), max(values)
        pad = (high - low) * 0.08 or abs(high) * 0.002 or 0.01
        start_ms = points[0].time_ms
        end_ms = points[-1].time_ms + step
        _fit_x(self.oi_x, start_ms, end_ms, "15m")
        y_low = max(0, low - pad)
        y_high = high + pad
        _fixed_ticks(self.oi_y, 4)
        self.oi_y.setRange(y_low, y_high)
        self.oi_y.setLabelFormat("%.2f" if scale > 1 else _axis_format(high))
        self.oi_view.set_home(start_ms, end_ms, y_low, y_high)

    def _fit_visible_oi(self) -> None:
        points = self._last_oi
        if len(points) < 2:
            return
        start = self.oi_x.min().toMSecsSinceEpoch()
        end = self.oi_x.max().toMSecsSinceEpoch()
        self.oi_x.setFormat(_axis_time_format("15m", start, end))
        if self.oi_view.price_manual:
            return
        visible = [point.value / (self._oi_scale[0] or 1.0) for point in points if start <= point.time_ms <= end]
        if len(visible) < 2:
            return
        low, high = min(visible), max(visible)
        pad = (high - low) * 0.08 or abs(high) * 0.002 or 0.01
        _fixed_ticks(self.oi_y, 4)
        self.oi_y.setRange(max(0, low - pad), high + pad)

    def _fit_funding(self) -> None:
        points = self._funding_shown
        if len(points) < 2:
            return
        values = [point.value for point in points]
        low, high = min(values), max(values)
        pad = (high - low) * 0.15 or max(abs(high), abs(low), 0.01) * 0.2
        start_ms = points[0].time_ms
        step_ms = 8 * 60 * 60 * 1000 if self._funding_step == "8h" else 60 * 60 * 1000
        end_ms = points[-1].time_ms + step_ms
        self.funding_x.setFormat("dd.MM HH:mm")
        self.funding_x.setRange(
            QDateTime.fromMSecsSinceEpoch(start_ms),
            QDateTime.fromMSecsSinceEpoch(end_ms),
        )
        _apply_price_scale(self.funding_y, low - pad, high + pad)
        self.funding_y.setLabelFormat("%.4f")
        self.funding_view.set_home(start_ms, end_ms, low - pad, high + pad)

    def _fit_visible_funding(self) -> None:
        points = self._funding_shown
        if len(points) < 2:
            return
        start = self.funding_x.min().toMSecsSinceEpoch()
        end = self.funding_x.max().toMSecsSinceEpoch()
        self.funding_x.setFormat("dd.MM HH:mm")
        if self.funding_view.price_manual:
            return
        visible = [point.value for point in points if start <= point.time_ms <= end]
        if len(visible) < 2:
            return
        low, high = min(visible), max(visible)
        pad = (high - low) * 0.15 or max(abs(high), abs(low), 0.01) * 0.2
        _apply_price_scale(self.funding_y, low - pad, high + pad)
        self.funding_y.setLabelFormat("%.4f")

    def _on_snap_fail(self, generation: int, message: str) -> None:
        if generation != self._snap_gen:
            return
        self.statusBar().showMessage(f"{self._symbol} alınamadı: {message}")

    def closeEvent(self, event) -> None:
        if not self._quitting and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self._enter_background()
            return
        if getattr(self, "_closed", False):
            event.accept()
            return
        self._closed = True
        self._timer.stop()
        self._live_timer.stop()
        self._feed.stop()
        if getattr(self, "_liq_tape", None) is not None:
            self._liq_tape.stop()
        self._snap_gen += 1
        self._list_gen += 1
        for worker in list(self._retained) + list(self._telegram_jobs):
            try:
                if worker.isRunning():
                    worker.wait(4000)
            except RuntimeError:
                continue
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()


BAR_FALLBACK = 15 * 60 * 1000


def _oi_unit(high: float) -> tuple[float, str]:
    if high >= 1_000_000_000:
        return 1_000_000_000, "milyar"
    if high >= 1_000_000:
        return 1_000_000, "milyon"
    if high >= 10_000:
        return 1_000, "bin"
    return 1.0, ""


def _verdict_color(text: str) -> QColor:
    if "Trap" in text:
        return AMBER
    if text in {"Sağlıklı Long", "Short Squeeze", "Akümülasyon"}:
        return UP
    if text in {"Sağlıklı Short", "Long Squeeze", "Dağıtım"}:
        return DOWN
    return MUTED


def _fixed_ticks(axis, count: int) -> None:
    from PySide6.QtCharts import QValueAxis

    axis.setTickType(QValueAxis.TickType.TicksFixed)
    axis.setTickCount(count)


def _axis_format(high: float) -> str:
    if high >= 1000:
        return "%.0f"
    if high >= 1:
        return "%.2f"
    if high >= 0.01:
        return "%.4f"
    return "%.6f"


def _funding_eta(next_ms: int | None) -> str:
    if not next_ms:
        return ""
    moment = datetime.fromtimestamp(next_ms / 1000)
    return f"sonraki {moment.strftime('%H:%M')}"


def _with_base(value: float | None, base: str) -> str:
    if value is None:
        return "—"
    return f"{format_amount(value)} {base}"


def configure_app(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(STYLESHEET)
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#0b0b0d"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#0b0b0d"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e4e4e7"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e4e4e7"))
    app.setPalette(palette)


def _darken(widget: QWidget) -> None:
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor("#0b0b0d"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e4e4e7"))
    palette.setColor(QPalette.ColorRole.Window, QColor("#0b0b0d"))
    widget.setPalette(palette)


def main() -> int:
    # Arka plan hesapları GIL'i tutarken her Qt geri dönüşü 5 ms bekler; kısa aralık arayüzü öne alır.
    sys.setswitchinterval(0.0005)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    configure_app(app)
    window = MainWindow()
    window.showMaximized()
    return app.exec()
