"""「複製最後的轉錄」: shown instead of pasting when no input field has the focus (Typeless-style, 2026-10-14).

The text, a 複製 button with its shortcut as a key cap, and a close button. It never takes focus (clicks still
reach it); the shortcut is caught by the keyboard hook (app._on_other_key).
"""
import ctypes
import ctypes.wintypes as wt

from PySide6.QtCore import QPointF, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .overlay import PAD

CARD_W = 400
SHOW_SECONDS = 20
ACCENT = QColor(150, 135, 255)


def _no_activate(widget):
    hwnd = int(widget.winId())
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, style | 0x08000000 | 0x8)   # WS_EX_NOACTIVATE | WS_EX_TOPMOST


def _font(size: int, bold: bool = False) -> QFont:
    f = QFont("Microsoft JhengHei UI")
    f.setPixelSize(size)
    f.setBold(bold)
    return f


class CopyCard(QWidget):
    copy_requested = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self._title_font, self._body_font, self._small_font = _font(15, True), _font(14), _font(12)
        self._text = ""
        self._key = "Enter"
        self._copied = False
        self._hover = None
        self._body = self._btn = self._close = QRectF()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=180)
        self._fade.finished.connect(self._faded)
        self._timer = QTimer(self, singleShot=True, timeout=self.dismiss)

    @property
    def active(self) -> bool:
        """Showing and still waiting for the copy (the shortcut is taken only then)."""
        return self.isVisible() and not self._copied

    def show_text(self, text: str, key: str = "Enter"):
        self._text, self._key, self._copied, self._hover = text, key, False, None
        inner = CARD_W - 44
        fm = QFontMetrics(self._body_font)
        lines = min(5, max(1, -(-fm.horizontalAdvance(text) // (inner - 14)) + text.count("\n")))
        body_h = fm.lineSpacing() * lines + 4
        card_h = 22 + 22 + 14 + body_h + 18 + 36 + 20
        self.setFixedSize(CARD_W + 2 * PAD, int(card_h) + 2 * PAD)
        self._body = QRectF(PAD + 22, PAD + 22 + 22 + 14, inner, body_h)
        self._btn = QRectF(PAD + 22 + inner / 2 - 70, self._body.bottom() + 18, 140, 36)
        self._close = QRectF(PAD + CARD_W - 44, PAD + 18, 28, 28)
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 36)
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            _no_activate(self)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(SHOW_SECONDS * 1000)
        self.update()

    def copied(self):
        """The text is on the clipboard: say so, then go away."""
        self._copied = True
        self.update()
        self._timer.start(900)

    def dismiss(self):
        if not self.isVisible():
            return
        self._timer.stop()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        self.closed.emit()

    def contains_screen_point(self, x: int, y: int) -> bool:
        """Physical screen pixels (mouse hook thread)."""
        if not self.isVisible():
            return False
        rect = wt.RECT()
        if not ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(rect)):
            return False
        return rect.left <= x < rect.right and rect.top <= y < rect.bottom

    def _faded(self):
        if self.windowOpacity() < 0.05:
            self.hide()

    # --- mouse ---
    def mouseMoveEvent(self, e):
        pos = e.position()
        hover = "close" if self._close.contains(pos) else "copy" if self._btn.contains(pos) else None
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._close.contains(e.position()):
            self.dismiss()
        elif self._btn.contains(e.position()) and not self._copied:
            self.copy_requested.emit()

    # --- painting ---
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(PAD, PAD, self.width() - 2 * PAD, self.height() - 2 * PAD)
        r = 20
        p.setPen(Qt.NoPen)
        for i in range(PAD, 0, -2):
            p.setBrush(QColor(0, 0, 0, int(30 * (1 - i / PAD) ** 2) + 2))
            p.drawRoundedRect(card.adjusted(-i, -i + 4, i, i + 4), r + i, r + i)
        path = QPainterPath()
        path.addRoundedRect(card, r, r)
        g = QLinearGradient(card.topLeft(), card.bottomLeft())
        g.setColorAt(0, QColor(40, 40, 47, 248))
        g.setColorAt(1, QColor(20, 20, 25, 248))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 36), 1))
        p.drawPath(path)

        # header: a round icon (two sheets), the title, the close button
        top = card.top() + 22
        icon = QPointF(card.left() + 33, top + 11)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(150, 135, 255, 52))
        p.drawEllipse(icon, 11, 11)
        p.setPen(QPen(ACCENT, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(icon.x() - 1.5, icon.y() - 5, 7, 8.5), 1.6, 1.6)
        p.drawPolyline([QPointF(icon.x() - 1.5, icon.y() - 2), QPointF(icon.x() - 4.5, icon.y() - 2),
                        QPointF(icon.x() - 4.5, icon.y() + 5), QPointF(icon.x() + 1.5, icon.y() + 5)])
        p.setPen(QColor(244, 244, 247))
        p.setFont(self._title_font)
        p.drawText(QRectF(card.left() + 52, top, 250, 22), Qt.AlignVCenter | Qt.AlignLeft, "複製最後的轉錄")
        cc = self._close.center()
        if self._hover == "close":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 28))
            p.drawEllipse(cc, 13, 13)
        p.setPen(QPen(QColor(190, 190, 198), 1.8, Qt.SolidLine, Qt.RoundCap))
        d = 4.2
        p.drawLine(QPointF(cc.x() - d, cc.y() - d), QPointF(cc.x() + d, cc.y() + d))
        p.drawLine(QPointF(cc.x() - d, cc.y() + d), QPointF(cc.x() + d, cc.y() - d))

        # the text, with an accent bar like a quotation
        body = self._body
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(150, 135, 255, 150))
        p.drawRoundedRect(QRectF(body.left(), body.top() + 2, 3, body.height() - 4), 1.5, 1.5)
        p.setPen(QColor(214, 214, 222))
        p.setFont(self._body_font)
        fm = QFontMetrics(self._body_font)
        width, limit = int(body.width()) - 14, fm.lineSpacing() * max(1, int(body.height() // fm.lineSpacing()))

        def height(s):
            return fm.boundingRect(0, 0, width, 10000, Qt.TextWordWrap, s).height()

        shown = self._text
        if height(shown) > limit:
            while len(shown) > 1 and height(shown + "…") > limit:
                shown = shown[:-1]
            shown += "…"
        p.drawText(body.adjusted(14, 0, 0, 0), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, shown)

        # the button: 複製 and the shortcut as a key cap; 已複製 once done
        b = self._btn
        shape = QPainterPath()
        shape.addRoundedRect(b, 18, 18)
        p.setFont(self._title_font)
        if self._copied:
            p.fillPath(shape, QColor(150, 135, 255, 70))
            p.setPen(ACCENT)
            p.drawText(b, Qt.AlignCenter, "已複製  ✓")
        else:
            p.fillPath(shape, QColor(255, 255, 255, 235 if self._hover == "copy" else 214))
            p.setPen(QColor(24, 24, 28))
            p.drawText(QRectF(b.left() + 18, b.top(), 60, b.height()), Qt.AlignVCenter | Qt.AlignLeft, "複製")
            cap_w = max(40, QFontMetrics(self._small_font).horizontalAdvance(self._key) + 16)
            cap = QRectF(b.right() - 12 - cap_w, b.center().y() - 10, cap_w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 30))
            p.drawRoundedRect(cap, 6, 6)
            p.setPen(QColor(60, 60, 68))
            p.setFont(self._small_font)
            p.drawText(cap, Qt.AlignCenter, self._key)
        p.end()
