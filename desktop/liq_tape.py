"""Binance USDT-M likidasyon bandı ve saatlik arşiv.

Akış sembol başına bir saniyede yalnızca son likidasyon emrini verir. Çığ saatleri
eksik sayılır; ilerideki skor mutlak değil, göreli okunur. Karşılaştırma yalnız bu
bantla ve Binance USDT-M haritasıyla yapılır. Ekrandaki dört borsa toplanmaz.

Bir saat skora girerse mum_saat ve oi_saat satırları vardır, tek bir oturum saatin
tamamını örter ve bir önceki saat de arşivdedir. Model açık pozisyon farkını
kullandığı için önceki saat olmadan o saat okunmaz. Uygulama kapalıyken veya
pong gecikince saat kapsanmış sayılmaz; suskunluk sıfır likidasyon değildir.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from desktop.market import HOUR_MS, MarketError, fetch_klines, fetch_oi_page

STREAM = "wss://fstream.binance.com/ws/!forceOrder@arr"
TAPE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
DECISION_SYMBOL = "BTCUSDT"
PONG_MS = 60_000
HEART_MS = 5_000
OI_RETRY_MS = 5 * 60 * 1000
DECIDE_WAIT_MS = 10 * 60 * 1000
BUFFER_LIMIT = 50
ARCHIVE_DAYS = 30
SHIFT_KEY = "oi_kaydirma"


def tape_path() -> Path:
    root = Path.home() / "AppData" / "Roaming" / "CryptoHub"
    root.mkdir(parents=True, exist_ok=True)
    return root / "liq_tape.sqlite"


def decide_shift(
    last_oi_stamp: int | None,
    forming_open: int,
    last_closed_open: int,
    now_ms: int,
) -> int | None:
    """0 damga mum açılışıdır, -1 saat damga dönem sonudur. Belirsizde None."""
    if last_oi_stamp is None or now_ms - forming_open < DECIDE_WAIT_MS:
        return None
    if last_oi_stamp == forming_open:
        return -HOUR_MS
    if last_oi_stamp == last_closed_open:
        return 0
    return None


def pong_fresh(last_pong_ms: int, now_ms: int) -> bool:
    return last_pong_ms > 0 and now_ms - last_pong_ms <= PONG_MS


def _event_row(payload: dict) -> tuple[int, str, str, float, float, float] | None:
    order = payload.get("o") if isinstance(payload.get("o"), dict) else None
    if order is None and isinstance(payload.get("data"), dict):
        nested = payload["data"].get("o")
        order = nested if isinstance(nested, dict) else None
    if not isinstance(order, dict):
        return None
    try:
        symbol = str(order["s"]).upper()
        side = str(order["S"]).upper()
        price = float(order["ap"])
        qty = float(order["z"])
        stamp = int(order["T"])
    except (KeyError, TypeError, ValueError):
        return None
    if side == "SELL":
        yon = "long"
    elif side == "BUY":
        yon = "short"
    else:
        return None
    if not symbol or price <= 0 or qty <= 0 or stamp <= 0:
        return None
    return stamp, symbol, yon, price, qty, price * qty


def _ensure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS liq(
            t_ms INTEGER,
            sembol TEXT,
            yon TEXT,
            fiyat REAL,
            miktar REAL,
            dolar REAL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_liq
            ON liq(t_ms, sembol, yon, fiyat, miktar);
        CREATE TABLE IF NOT EXISTS oturum(
            acildi_ms INTEGER,
            kapandi_ms INTEGER
        );
        CREATE TABLE IF NOT EXISTS meta(
            k TEXT PRIMARY KEY,
            v TEXT
        );
        CREATE TABLE IF NOT EXISTS oi_saat(
            sembol TEXT,
            saat_ms INTEGER,
            damga_ms INTEGER,
            oi REAL,
            oi_dolar REAL,
            PRIMARY KEY(sembol, saat_ms)
        );
        CREATE TABLE IF NOT EXISTS mum_saat(
            sembol TEXT,
            saat_ms INTEGER,
            o REAL,
            h REAL,
            l REAL,
            c REAL,
            hacim REAL,
            taker_alis REAL,
            PRIMARY KEY(sembol, saat_ms)
        );
        """
    )
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='force_order'"
    ).fetchone()
    if found is None:
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO liq (t_ms, sembol, yon, fiyat, miktar, dolar)
        SELECT time_ms, symbol,
               CASE side WHEN 'SELL' THEN 'long' WHEN 'BUY' THEN 'short' ELSE side END,
               price, qty, usd
        FROM force_order
        """
    )


def _hour_edges(now_ms: int, opens: list[int]) -> tuple[int, int]:
    forming = now_ms // HOUR_MS * HOUR_MS
    forming_open = next((stamp for stamp in opens if stamp >= forming), forming)
    closed = [stamp for stamp in opens if stamp < forming]
    last_closed = closed[-1] if closed else forming - HOUR_MS
    return forming_open, last_closed


def _closed_candles(symbol: str, now_ms: int, full: bool) -> list[tuple]:
    forming = now_ms // HOUR_MS * HOUR_MS
    earliest = forming - ARCHIVE_DAYS * 24 * HOUR_MS
    bars = fetch_klines(symbol, "1h", 1500 if full else 5, True)
    rows: list[tuple] = []
    for bar in bars:
        if bar.time_ms >= forming or bar.time_ms < earliest:
            continue
        taker = (bar.volume + bar.cvd_delta) / 2
        rows.append(
            (symbol, bar.time_ms, bar.open, bar.high, bar.low, bar.close, bar.volume, taker)
        )
    return rows


def _oi_hours(symbol: str, shift: int, now_ms: int, full: bool) -> list[tuple]:
    forming = now_ms // HOUR_MS * HOUR_MS
    earliest = forming - ARCHIVE_DAYS * 24 * HOUR_MS
    start = earliest if full else forming - 6 * HOUR_MS
    rows: list[tuple] = []
    while start < forming + HOUR_MS:
        page = fetch_oi_page(symbol, start, 500)
        if not page:
            break
        for damga, oi, usd in page:
            if oi <= 0:
                continue
            saat = damga + shift
            if saat >= forming or saat < earliest:
                continue
            rows.append((symbol, saat, damga, oi, usd))
        nxt = page[-1][0] + 1
        if nxt <= start or len(page) < 500:
            break
        start = nxt
    return rows


class _ArchiveWorker(QThread):
    done = Signal(object)

    def __init__(self, shift: int | None, full_candles: bool, oi_done: bool) -> None:
        super().__init__()
        self._shift = shift
        self._full_candles = full_candles
        self._oi_done = oi_done

    def run(self) -> None:
        now = int(time.time() * 1000)
        shift = self._shift
        candles: list[tuple] = []
        oi: list[tuple] = []
        full_oi = False
        completed = False
        try:
            if shift is None:
                newest_page = fetch_oi_page(DECISION_SYMBOL, now - 6 * HOUR_MS, 500)
                newest = newest_page[-1][0] if newest_page else None
                probe = fetch_klines(DECISION_SYMBOL, "1h", 3, True)
                forming_open, last_closed = _hour_edges(now, [bar.time_ms for bar in probe])
                shift = decide_shift(newest, forming_open, last_closed, now)
            full_oi = shift is not None and not self._oi_done
            for symbol in TAPE_SYMBOLS:
                candles.extend(_closed_candles(symbol, now, self._full_candles))
                if shift is not None:
                    oi.extend(_oi_hours(symbol, shift, now, full_oi))
            completed = True
        except MarketError:
            pass
        self.done.emit(
            {
                "shift": shift,
                "candles": candles,
                "oi": oi,
                "full_oi": full_oi and completed,
                "candles_complete": self._full_candles and completed,
            }
        )


class LiquidationTape(QObject):
    """USDT-M likidasyonlarını ve seçili sembollerin kapanmış saatini diske yazar."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(tape_path(), check_same_thread=False)
        _ensure(self._db)
        self._db.commit()
        self._db_ready = True
        self._buf: list[tuple] = []
        self._session_id: int | None = None
        self._last_pong = 0
        self._want = False
        self._epoch = 0
        self._oi_done = False
        self._booted = False
        self._worker: _ArchiveWorker | None = None
        self._archive_busy = False
        self._archive_next: dict | None = None
        self._archive_payload: dict | None = None
        self._candle_rows: list[tuple] = []
        self._oi_rows: list[tuple] = []
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_closed)
        self._socket.pong.connect(self._on_pong)
        self._socket.textMessageReceived.connect(self._on_text)
        self._heart = QTimer(self)
        self._heart.setInterval(HEART_MS)
        self._heart.timeout.connect(self._heartbeat)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._reopen)
        self._oi_timer = QTimer(self)
        self._oi_timer.setInterval(OI_RETRY_MS)
        self._oi_timer.timeout.connect(self._kick_archive)
        self._candle_timer = QTimer(self)
        self._candle_timer.setSingleShot(True)
        self._candle_timer.timeout.connect(self._on_hour)
        self._again = QTimer(self)
        self._again.setSingleShot(True)
        self._again.timeout.connect(self._kick_archive)

    def start(self) -> None:
        self._want = True
        self._epoch += 1
        epoch = self._epoch
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._socket.abort()
        QTimer.singleShot(50, lambda epoch=epoch: self._open(epoch))
        self._oi_timer.start()
        self._arm_candle()
        self._kick_archive()

    def stop(self) -> None:
        self._want = False
        self._epoch += 1
        self._heart.stop()
        self._retry.stop()
        self._oi_timer.stop()
        self._candle_timer.stop()
        self._again.stop()
        now = int(time.time() * 1000)
        end = now if pong_fresh(self._last_pong, now) else self._last_pong
        self._flush(end, advance=self._session_id is not None and end > 0)
        self._close_session(end if end > 0 else now)
        self._socket.blockSignals(True)
        self._socket.abort()
        worker = self._worker
        if self._worker_busy() and worker is not None:
            worker.wait(4000)
        self._db_ready = False
        with self._lock:
            self._db.close()

    def _open(self, epoch: int) -> None:
        if epoch != self._epoch or not self._want:
            return
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._socket.abort()
            QTimer.singleShot(50, lambda epoch=epoch: self._open(epoch))
            return
        self._socket.open(QUrl(STREAM))

    def _reopen(self) -> None:
        if not self._want:
            return
        self._epoch += 1
        self._open(self._epoch)

    def _on_connected(self) -> None:
        if not self._want or not self._db_ready:
            return
        now = int(time.time() * 1000)
        self._last_pong = now
        with self._lock:
            if self._session_id is None:
                cur = self._db.execute(
                    "INSERT INTO oturum (acildi_ms, kapandi_ms) VALUES (?, NULL)",
                    (now,),
                )
                self._session_id = int(cur.lastrowid)
                self._db.commit()
        self._heart.start()
        self._socket.ping()

    def _on_pong(self, _elapsed: int, _payload) -> None:
        self._last_pong = int(time.time() * 1000)

    def _on_closed(self) -> None:
        if not self._db_ready:
            return
        self._heart.stop()
        now = int(time.time() * 1000)
        end = now if pong_fresh(self._last_pong, now) else self._last_pong
        if end > 0:
            self._close_session(end)
        if self._want:
            self._retry.start(3000)

    def _heartbeat(self) -> None:
        if not self._want or not self._db_ready:
            return
        now = int(time.time() * 1000)
        if not pong_fresh(self._last_pong, now):
            self._heart.stop()
            if self._last_pong > 0:
                self._close_session(self._last_pong)
            self._epoch += 1
            self._socket.abort()
            return
        self._socket.ping()
        self._flush(now, advance=True)

    def _on_text(self, text: str) -> None:
        if not self._db_ready:
            return
        try:
            payload = json.loads(text)
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        row = _event_row(payload)
        if row is None:
            return
        self._buf.append(row)
        if len(self._buf) >= BUFFER_LIMIT:
            now = int(time.time() * 1000)
            self._flush(now, advance=pong_fresh(self._last_pong, now))

    def _flush(self, now_ms: int, advance: bool) -> None:
        if not self._db_ready:
            return
        with self._lock:
            if self._buf:
                self._db.executemany(
                    "INSERT OR IGNORE INTO liq (t_ms, sembol, yon, fiyat, miktar, dolar) VALUES (?,?,?,?,?,?)",
                    self._buf,
                )
                self._buf.clear()
            if advance and self._session_id is not None:
                self._db.execute(
                    "UPDATE oturum SET kapandi_ms=? WHERE rowid=?",
                    (int(now_ms), self._session_id),
                )
            self._db.commit()

    def _close_session(self, end_ms: int) -> None:
        if not self._db_ready:
            return
        with self._lock:
            if self._buf:
                self._db.executemany(
                    "INSERT OR IGNORE INTO liq (t_ms, sembol, yon, fiyat, miktar, dolar) VALUES (?,?,?,?,?,?)",
                    self._buf,
                )
                self._buf.clear()
            if self._session_id is not None:
                self._db.execute(
                    "UPDATE oturum SET kapandi_ms=? WHERE rowid=?",
                    (int(end_ms), self._session_id),
                )
                self._session_id = None
            self._db.commit()

    def _stored_shift(self) -> int | None:
        with self._lock:
            row = self._db.execute(
                "SELECT v FROM meta WHERE k=?",
                (SHIFT_KEY,),
            ).fetchone()
        if row is None:
            return None
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return None

    def _arm_candle(self) -> None:
        now = time.time()
        delay = HOUR_MS / 1000 - (now % 3600) + 5
        self._candle_timer.start(int(delay * 1000))

    def _on_hour(self) -> None:
        self._arm_candle()
        self._kick_archive()

    def _kick_archive(self) -> None:
        if not self._want or not self._db_ready:
            return
        if self._worker_busy():
            self._again.start(15_000)
            return
        worker = _ArchiveWorker(self._stored_shift(), not self._booted, self._oi_done)
        worker.done.connect(self._on_archive, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(lambda finished=worker: self._release_worker(finished))
        self._worker = worker
        worker.start()

    def _worker_busy(self) -> bool:
        worker = self._worker
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            self._worker = None
            return False

    def _release_worker(self, worker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.deleteLater()

    def _on_archive(self, payload: object) -> None:
        if not self._want or not self._db_ready or not isinstance(payload, dict):
            return
        if self._archive_busy:
            self._archive_next = payload
            return
        self._begin_archive(payload)

    def _begin_archive(self, payload: dict) -> None:
        self._archive_busy = True
        self._archive_payload = payload
        self._candle_rows = list(payload.get("candles") or [])
        self._oi_rows = list(payload.get("oi") or [])
        self._flush_archive()

    def _flush_archive(self) -> None:
        if not self._db_ready:
            self._archive_busy = False
            return
        chunk = 150
        nxt = None
        with self._lock:
            if self._candle_rows:
                batch = self._candle_rows[:chunk]
                self._candle_rows = self._candle_rows[chunk:]
                self._db.executemany(
                    "INSERT OR IGNORE INTO mum_saat VALUES (?,?,?,?,?,?,?,?)",
                    batch,
                )
                self._db.commit()
            elif self._oi_rows:
                batch = self._oi_rows[:chunk]
                self._oi_rows = self._oi_rows[chunk:]
                self._db.executemany(
                    "INSERT OR IGNORE INTO oi_saat VALUES (?,?,?,?,?)",
                    batch,
                )
                self._db.commit()
            else:
                payload = self._archive_payload or {}
                shift = payload.get("shift")
                if isinstance(shift, int):
                    self._db.execute(
                        "INSERT OR IGNORE INTO meta (k, v) VALUES (?, ?)",
                        (SHIFT_KEY, str(shift)),
                    )
                if payload.get("candles_complete"):
                    self._booted = True
                if payload.get("full_oi") and payload.get("oi"):
                    self._oi_done = True
                self._db.commit()
                self._archive_busy = False
                self._archive_payload = None
                nxt = self._archive_next
                self._archive_next = None
        if nxt is None and self._archive_busy:
            QTimer.singleShot(0, self._flush_archive)
            return
        if isinstance(nxt, dict) and self._want and self._db_ready:
            self._begin_archive(nxt)
