"""The 選字 menu: a small dark window that does not take focus, so the target app keeps its caret and selection.

The candidate list has the look from before the lean branch (2026-10-11). Since the same day (the user's choice)
its top row is an input box ("輸入加入熱詞", as tall as a row) instead of a title: a click there activates the menu
so an input method can be used, Enter replaces the selection with the typed text and saves it as a hotword, and
focus goes back to the app the menu was opened in. Rows are answered with the mouse only (decided 2026-09-29: short
spoken answers like 對 / 第二個 were often misheard). There is no timeout any more: the app closes the menu when
the user's next click or key is somewhere else, and an activated menu closes when it loses focus.
"""
import ctypes

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QLineEdit, QPushButton, QVBoxLayout, QWidget

_STYLE = """
QWidget#picker { background: #202024; border: 1px solid #3a3a40; border-radius: 8px; }
QPushButton { color: #f2f2f2; background: transparent; border: none; border-radius: 5px;
              padding: 4px 10px; text-align: left; font-size: 15px; }
QPushButton:hover { background: #3a3a44; }
QLineEdit { color: #f2f2f2; background: #2a2a30; border: 1px solid #3a3a40; border-radius: 5px;
            padding: 3px 9px; font-size: 15px; }
QLineEdit:focus { border-color: #6a6a78; }
"""
_MAX_WIDTH = 360
_MIN_WIDTH = 150
_GWL_EXSTYLE, _WS_EX_NOACTIVATE, _WS_EX_TOPMOST = -20, 0x08000000, 0x8


class _Input(QLineEdit):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class Picker(QWidget):
    picked = Signal(int)      # 0-based row
    typed = Signal(str)       # text entered in the input box
    closed = Signal()         # hidden without a pick

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.candidates: list[str] = []
        self._anchor = None         # (top-left, bottom y) of the selection the menu belongs to, logical pixels
        self._prev = 0              # the window that was in front when the input box took focus
        self._answered = False

        self.frame = QWidget(self, objectName="picker")
        self.frame.setAttribute(Qt.WA_StyledBackground)
        self.frame.setStyleSheet(_STYLE)
        self.frame.setMaximumWidth(_MAX_WIDTH)
        self.frame.setMinimumWidth(_MIN_WIDTH)
        self.input = _Input(placeholderText="輸入加入熱詞")
        self.input.clicked.connect(self._activate)
        self.input.returnPressed.connect(self._submit)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(0)
        inner = QVBoxLayout(self.frame)
        inner.setContentsMargins(6, 6, 6, 6)
        inner.setSpacing(2)
        inner.addWidget(self.input)
        inner.addLayout(self.rows)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.frame)

    # --- public API ---
    def show_loading(self, word: str, rect):
        self.candidates = []
        self._fill([])
        self.input.clear()
        self._answered = False
        self._place(rect)

    def show_candidates(self, word: str, candidates: list[str]):
        self.candidates = candidates
        self._fill(candidates)
        if self.isVisible():
            QTimer.singleShot(0, self._refit)

    def active(self) -> bool:
        """The input box has focus (keys typed now are meant for it)."""
        return self.isVisible() and self.isActiveWindow()

    def owns(self, hwnd: int) -> bool:
        return bool(hwnd) and ctypes.windll.user32.GetAncestor(hwnd, 2) == int(self.winId())   # GA_ROOT

    def dismiss(self):
        """Close without an answer, giving focus back if the menu had taken it."""
        if self.isVisible():
            self.hide()

    # --- internals ---
    def _activate(self):
        """A click in the input box: take focus so an input method works."""
        if self.isActiveWindow():
            return
        user32 = ctypes.windll.user32
        hwnd = int(self.winId())
        self._prev = user32.GetForegroundWindow()
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & ~_WS_EX_NOACTIVATE)
        user32.SetForegroundWindow(hwnd)
        self.activateWindow()
        self.input.setFocus()

    def _restore_focus(self):
        prev, self._prev = self._prev, 0
        if prev:
            ctypes.windll.user32.SetForegroundWindow(prev)

    def _submit(self):
        text = self.input.text().strip()
        if not text:
            return
        self._answered = True
        self.hide()
        self.typed.emit(text)

    def _pick(self, i: int):
        self._answered = True
        self.hide()
        self.picked.emit(i)

    def _refit(self):
        """Rows added to a visible window are shown, and sized, on the next event loop pass."""
        if self.isVisible():
            self._shrink()
            self._clamp()

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

    def _fill(self, candidates: list[str]):
        self._clear()
        for i, word in enumerate(candidates):
            self.rows.addWidget(QPushButton(word, clicked=lambda _=False, i=i: self._pick(i)))
        self._shrink()

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
        self._no_activate()
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
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                              | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def changeEvent(self, e):
        if e.type() == QEvent.ActivationChange and not self.isActiveWindow() and self.isVisible():
            self._prev = 0         # focus went elsewhere on its own: nothing to give back
            self.hide()
        super().changeEvent(e)

    def hideEvent(self, e):
        if self._prev:
            self._restore_focus()
        if not self._answered:
            self._answered = True
            self.closed.emit()
        super().hideEvent(e)
