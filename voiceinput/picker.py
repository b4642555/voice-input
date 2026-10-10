"""Small dark menu that never takes focus, so the target app keeps its caret and selection.

Two uses (2026-10-11; the look is the one from before the lean branch, a redesign is planned): the 選字 candidate
list, and a notice that a hotword was added with a 撤銷 button. Both are answered with the mouse only (decided
2026-09-29: short spoken answers like 對 / 第二個 were often misheard). A thin bar on the bottom edge shrinks as the
timeout runs out."""
import time

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStyle, QStyleOption, QVBoxLayout, QWidget

_STYLE = """
QWidget#picker { background: #202024; border: 1px solid #3a3a40; border-radius: 8px; }
QLabel { color: #9a9aa2; padding: 2px 4px; font-size: 12px; }
QLabel#text { color: #f2f2f2; font-size: 14px; }
QPushButton { color: #f2f2f2; background: transparent; border: none; border-radius: 5px;
              padding: 4px 10px; text-align: left; font-size: 15px; }
QPushButton:hover { background: #3a3a44; }
QPushButton#close { color: #9a9aa2; padding: 2px 6px; font-size: 12px; }
QPushButton#undo { color: #f0b37e; font-size: 14px; padding: 2px 10px; text-align: center; }
QPushButton#undo:hover { background: #4a3a2c; }
"""
_MAX_WIDTH = 360
_RADIUS = 8
_BAR = 3                              # countdown bar height (px)
_BAR_COLOR = QColor(200, 200, 206)


class _Frame(QWidget):
    """The menu's rounded panel, with the countdown bar painted along its bottom edge."""

    def __init__(self, parent):
        super().__init__(parent, objectName="picker")
        self.remaining = 0.0          # fraction of the timeout left, 0 hides the bar

    def paintEvent(self, e):
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)   # the stylesheet background and border
        if self.remaining > 0:
            p.setRenderHint(QPainter.Antialiasing)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), _RADIUS - 1, _RADIUS - 1)
            p.setClipPath(clip)
            w = (self.width() - 2) * self.remaining
            p.fillRect(QRectF(1, self.height() - 1 - _BAR, w, _BAR), _BAR_COLOR)
        p.end()


class Picker(QWidget):
    picked = Signal(int)      # 0-based row
    undone = Signal()         # 撤銷 clicked in a notice

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.candidates: list[str] = []
        self._anchor = None         # (top-left, bottom y) of the selection the menu belongs to, logical pixels
        self.timeout_ms = 10_000    # every menu closes after this (a setting)
        self._timer = QTimer(self, singleShot=True, interval=self.timeout_ms, timeout=self.hide)
        self._tick = QTimer(self, interval=40, timeout=self._countdown)
        self._deadline = self._total = 0.0

        self.frame = _Frame(self)
        self.frame.setStyleSheet(_STYLE)
        self.frame.setMaximumWidth(_MAX_WIDTH)
        self.title = QLabel()
        close = QPushButton("✕", objectName="close", clicked=self.hide)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.title, 1)
        head.addWidget(close, 0, Qt.AlignTop)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(6, 4, 6, 6 + _BAR)
        inner.setSpacing(2)
        inner.addLayout(head)
        inner.addLayout(self.rows)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

    def show_loading(self, word: str, rect):
        self._fill(f"選字：{word}　候選產生中…", [])
        self._place(rect)

    def show_candidates(self, word: str, candidates: list[str]):
        self.candidates = candidates
        self._fill(f"選字：{word}" if candidates else f"選字：{word}　找不到同音的候選", candidates)
        if self.isVisible():
            QTimer.singleShot(0, self._refit)

    def _refit(self):
        """Rows added to a visible window are shown, and sized, on the next event loop pass."""
        if self.isVisible():
            self._shrink()
            self._clamp()

    def show_notice(self, title: str, text: str, rect, button: str = ""):
        """A notice: title, text and optionally a button (撤銷) that emits undone; ✕ or the timeout just close it."""
        self.candidates = []
        self._clear()
        self.title.setText(title)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(4, 0, 0, 0)
        h.addWidget(QLabel(text, objectName="text"), 1)
        if button:
            h.addWidget(QPushButton(button, objectName="undo", clicked=self._undo))
        self.rows.addWidget(row)
        self._shrink()
        self._start_timer(self.timeout_ms)
        self._place(rect)

    def _start_timer(self, ms: int):
        self._timer.start(ms)
        self._total = ms / 1000
        self._deadline = time.monotonic() + self._total
        self._tick.start()
        self._countdown()

    def _countdown(self):
        left = self._deadline - time.monotonic()
        self.frame.remaining = max(0.0, left / self._total) if self._total else 0.0
        self.frame.update()
        if left <= 0:
            self._tick.stop()

    def _clear(self):
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            w.hide()               # removed now, not at deleteLater: the old rows kept the window large
            w.setParent(None)
            w.deleteLater()

    def _shrink(self):
        self.frame.layout().activate()
        self.layout().activate()
        self.resize(self.sizeHint())

    def _fill(self, title: str, candidates: list[str]):
        self._clear()
        self.title.setText(title)
        for i, word in enumerate(candidates):
            self.rows.addWidget(QPushButton(word, clicked=lambda _=False, i=i: self._pick(i)))
        self._shrink()
        self._start_timer(self.timeout_ms)

    def _pick(self, i: int):
        self.hide()
        self.picked.emit(i)

    def _undo(self):
        self.hide()
        self.undone.emit()

    def _place(self, rect):
        """rect: selection bounds in physical screen pixels (x, y, w, h) from UI Automation, or None (then next
        to the mouse)."""
        self._anchor = None
        if rect:
            x, y, w, h = rect
            top = self._to_logical(QPoint(int(x), int(y)))
            self._anchor = (top, self._to_logical(QPoint(int(x), int(y + h))).y())
            pos = QPoint(top.x(), self._anchor[1] + 6)
        else:
            pos = QCursor.pos() + QPoint(0, 6)
        self.move(pos)
        self.show()
        self._no_activate()
        self._clamp()
        QTimer.singleShot(0, self._refit)   # rows added while an earlier menu was still open are sized later

    @staticmethod
    def _to_logical(p: QPoint) -> QPoint:
        # Qt keeps each screen's origin in device pixels and scales only within the screen
        for s in QGuiApplication.screens():
            g, r = s.geometry(), s.devicePixelRatio()
            if g.x() <= p.x() < g.x() + g.width() * r and g.y() <= p.y() < g.y() + g.height() * r:
                return QPoint(g.x() + int((p.x() - g.x()) / r), g.y() + int((p.y() - g.y()) / r))
        return p

    def _clamp(self):
        """Keep the menu on screen. Under a selection too close to the bottom it opens above it instead, like an
        IME candidate window."""
        anchor = self._anchor
        screen = QGuiApplication.screenAt(anchor[0] if anchor else self.pos()) or QGuiApplication.primaryScreen()
        a = screen.availableGeometry()
        y = self.y()
        if anchor:
            y = anchor[1] + 6
            if y + self.height() > a.bottom():
                y = anchor[0].y() - 6 - self.height()
        x = min(max(self.x(), a.left()), a.right() - self.width())
        y = min(max(y, a.top()), a.bottom() - self.height())
        self.move(x, y)

    def _no_activate(self):
        import ctypes
        hwnd = int(self.winId())
        GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOPMOST = -20, 0x08000000, 0x8
        user32 = ctypes.windll.user32
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                              | WS_EX_NOACTIVATE | WS_EX_TOPMOST)

    def hideEvent(self, e):
        self._timer.stop()
        self._tick.stop()
        super().hideEvent(e)
