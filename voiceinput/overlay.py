"""The floating pill shown while recording / transcribing / polishing (Typeless-style, redrawn 2026-10-13).

A dark glass capsule near the bottom of the screen: while recording a symmetric waveform that follows the
microphone level (smoothed, so it breathes instead of flickering); while the recognizer runs three soft dots; while
AI 潤稿 runs a light sweep with a small sparkle. It never takes focus. While recording it shows a cancel and a done button (2026-10-14); the clicks on them are
caught by the mouse hook (button_at), everywhere else the pill is click-through.
"""
import math
import time

from PySide6.QtCore import QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

PAD = 14                 # room for the shadow around the capsule
CW, CH = 156, 42         # the capsule
CWR = 214                # wider while recording: a cancel and a done button either side of the waveform
W, H = CWR + 2 * PAD, CH + 2 * PAD
BARS = 21
BTN_R = 15               # radius of the cancel / done buttons


def _click_through(widget):
    import ctypes
    hwnd = int(widget.winId())
    GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_TOPMOST = -20, 0x08000000, 0x20, 0x8
    user32 = ctypes.windll.user32
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT | WS_EX_TOPMOST)


class Overlay(QWidget):
    IDLE, RECORDING, THINKING, POLISHING = range(4)

    def __init__(self, level_source):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self._level_source = level_source   # callable -> float 0..1
        self._state = self.IDLE
        self._level = 0.0                   # smoothed level
        self._history = [0.0] * BARS        # recent smoothed levels, newest in the middle of the waveform
        self._t0 = time.monotonic()
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=160)
        self._fade.finished.connect(self._on_fade_done)

    # --- public API ---
    def show_recording(self):
        self._history = [0.0] * BARS
        self._set_state(self.RECORDING)

    def show_thinking(self):
        self._set_state(self.THINKING)

    def show_polishing(self):
        """AI 潤稿 is running (only shown when the pill is already up: a selection edit may have hidden it)."""
        if self._state != self.IDLE:
            self._set_state(self.POLISHING)

    def hide_overlay(self):
        if self._state == self.IDLE:
            return
        self._shown = self._state          # the fade-out keeps showing it (an empty capsule looked broken)
        self._state = self.IDLE
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        QTimer.singleShot(600, self._hide_if_idle)   # never an empty pill left on screen if the fade stalls

    def _hide_if_idle(self):
        if self._state == self.IDLE and self.isVisible():
            import logging
            logging.getLogger("voiceinput").info("overlay: still visible after the fade, hidden")
            self._timer.stop()
            self.hide()

    def hide_now(self):
        """Hide without the fade. Before a terminal edit: it blocks the main thread while UI Automation reads the
        terminal, and a fading overlay over the point it reads (Claude Code's input box sits at the bottom of the
        screen, like the overlay) made UIA wait for our own frozen window until the 4s timeout (2026-10-11: the
        edit failed with 找不到選取的文字 and the three dots stood still)."""
        self._state = self.IDLE
        self._fade.stop()
        self._timer.stop()
        self.hide()

    # --- internals ---
    def _set_state(self, state):
        was_hidden = self._state == self.IDLE or not self.isVisible()
        self._state = state
        self._t0 = time.monotonic()
        if was_hidden:
            self._reposition()
            self.setWindowOpacity(0.0)
            self.show()
            _click_through(self)
            self._fade.stop()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()
        self._timer.start()
        self.update()

    def _on_fade_done(self):
        if self._state == self.IDLE:
            self._timer.stop()
            self.hide()

    def _reposition(self):
        screen = QGuiApplication.screenAt(self.cursor().pos()) or QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.center().x() - W // 2, g.bottom() - H - 36)

    def _tick(self):
        if self._state == self.RECORDING:
            target = min(1.0, max(0.0, self._level_source()))
            k = 0.45 if target > self._level else 0.18      # rise fast, fall slowly
            self._level += (target - self._level) * k
            self._history = self._history[1:] + [self._level]
        self.update()

    def paintEvent(self, e):
        try:
            self._paint()
        except Exception:          # a failed paint left an empty capsule on screen (2026-10-15): say why
            import logging
            logging.getLogger("voiceinput").exception("overlay paint failed (state %s)", self._state)

    def _paint(self):
        state = self._state if self._state != self.IDLE else getattr(self, "_shown", self.IDLE)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cw = CWR if state == self.RECORDING else CW
        cap = QRectF((W - cw) / 2, PAD, cw, CH)
        r = CH / 2

        # soft shadow
        p.setPen(Qt.NoPen)
        for i in range(PAD, 0, -2):
            p.setBrush(QColor(0, 0, 0, int(26 * (1 - i / PAD) ** 2) + 2))
            p.drawRoundedRect(cap.adjusted(-i, -i + 3, i, i + 3), r + i, r + i)

        path = QPainterPath()
        path.addRoundedRect(cap, r, r)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        g.setColorAt(0, QColor(38, 38, 44, 245))
        g.setColorAt(1, QColor(16, 16, 20, 245))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawPath(path)
        p.setPen(Qt.NoPen)

        t = time.monotonic() - self._t0
        cx, cy = cap.center().x(), cap.center().y()
        if state == self.RECORDING:
            self._paint_wave(p, cx, cy, t)
            self._paint_buttons(p)
        elif state == self.THINKING:
            for i in range(3):
                a = 0.5 + 0.5 * math.sin(t * 5.5 - i * 0.9)
                p.setBrush(QColor(255, 255, 255, int(70 + 170 * a)))
                rr = 2.6 + 1.4 * a
                x = cx + (i - 1) * 14
                p.drawEllipse(QPointF(x, cy), rr, rr)
        elif state == self.POLISHING:
            self._paint_polish(p, path, cap, cx, cy, t)
        p.end()

    def _paint_wave(self, p, cx, cy, t):
        gap, bw = 5.4, 2.6
        max_h = CH - 16
        mid = BARS // 2
        for i in range(BARS):
            d = abs(i - mid)
            lvl = self._history[BARS - 1 - d * 2] if d * 2 < BARS else 0.0   # newest in the middle
            shape = math.cos(d / (mid + 1) * math.pi / 2) ** 1.2            # taller in the middle
            idle = 0.06 + 0.04 * math.sin(t * 4 + i * 0.7)
            h = max(3.0, max_h * max(idle, min(1.0, lvl * 1.6)) * (0.35 + 0.65 * shape))
            x = cx + (i - mid) * gap - bw / 2
            alpha = int(120 + 135 * shape)
            p.setBrush(QColor(255, 255, 255, alpha))
            p.drawRoundedRect(QRectF(x, cy - h / 2, bw, h), bw / 2, bw / 2)

    @staticmethod
    def _button_centers():
        y = PAD + CH / 2
        return QPointF(W / 2 - CWR / 2 + 6 + BTN_R, y), QPointF(W / 2 + CWR / 2 - 6 - BTN_R, y)

    def _paint_buttons(self, p):
        cancel, ok = self._button_centers()
        p.setBrush(QColor(84, 84, 92))
        p.drawEllipse(cancel, BTN_R, BTN_R)
        p.setPen(QPen(QColor(255, 255, 255), 2.2, Qt.SolidLine, Qt.RoundCap))
        d = 4.6
        p.drawLine(QPointF(cancel.x() - d, cancel.y() - d), QPointF(cancel.x() + d, cancel.y() + d))
        p.drawLine(QPointF(cancel.x() - d, cancel.y() + d), QPointF(cancel.x() + d, cancel.y() - d))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(250, 250, 252))
        p.drawEllipse(ok, BTN_R, BTN_R)
        p.setPen(QPen(QColor(20, 20, 24), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(ok.x() - 5.5, ok.y() + 0.5), QPointF(ok.x() - 1.5, ok.y() + 4.5),
                        QPointF(ok.x() + 6, ok.y() - 4.5)])
        p.setPen(Qt.NoPen)

    def button_at(self, x: int, y: int) -> str | None:
        """"cancel" / "ok" when the screen point (physical pixels, from the mouse hook) is on one of the recording
        buttons. Any thread."""
        if self._state != self.RECORDING or not self.isVisible():
            return None
        import ctypes
        import ctypes.wintypes as wt
        rect = wt.RECT()
        if not ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(rect)) or rect.right <= rect.left:
            return None
        scale = (rect.right - rect.left) / W
        lx, ly = (x - rect.left) / scale, (y - rect.top) / scale
        for name, c in zip(("cancel", "ok"), self._button_centers()):
            if math.hypot(lx - c.x(), ly - c.y()) <= BTN_R + 3:
                return name
        return None

    def _paint_polish(self, p, path, cap, cx, cy, t):
        # a light band sweeping across, and three dots in the accent colour
        p.save()
        p.setClipPath(path)
        x = cap.left() - 60 + ((t * 140) % (cap.width() + 120))
        g = QLinearGradient(x - 50, 0, x + 50, 0)
        g.setColorAt(0, QColor(140, 120, 255, 0))
        g.setColorAt(0.5, QColor(160, 140, 255, 70))
        g.setColorAt(1, QColor(140, 120, 255, 0))
        p.fillRect(cap, g)
        p.restore()
        # sparkle
        s = 5 + 1.2 * math.sin(t * 6)
        sx, sy = cx - 26, cy
        star = QPainterPath()
        star.moveTo(sx, sy - s)
        star.quadTo(sx, sy, sx + s, sy)
        star.quadTo(sx, sy, sx, sy + s)
        star.quadTo(sx, sy, sx - s, sy)
        star.quadTo(sx, sy, sx, sy - s)
        p.fillPath(star, QColor(196, 184, 255))
        for i in range(3):
            a = 0.5 + 0.5 * math.sin(t * 5.5 - i * 0.9)
            p.setBrush(QColor(210, 200, 255, int(80 + 170 * a)))
            rr = 2.4 + 1.2 * a
            p.drawEllipse(QPointF(cx - 4 + i * 12, cy), rr, rr)


class Toast(QWidget):
    """A short notice in the pill's place (Typeless-style, 2026-10-14): 「程式」已加入關鍵字 after a manual correction
    was learned. Dark capsule, fades in, stays a few seconds, fades out; never takes focus or clicks."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._text = ""
        self._fade = QPropertyAnimation(self, b"windowOpacity", self, duration=180)
        self._fade.finished.connect(self._faded)
        self._timer = QTimer(self, singleShot=True, timeout=self._out)
        from PySide6.QtGui import QFont
        self._font = QFont("Microsoft JhengHei UI")
        self._font.setPixelSize(17)
        self._font.setWeight(QFont.DemiBold)

    @staticmethod
    def _window_spot():
        """Bottom centre of the window in front, in logical pixels, and its screen (None when unknown)."""
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            r = wintypes.RECT()
            if not hwnd or not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return None
            screen = None
            for sc in QGuiApplication.screens():
                ratio = sc.devicePixelRatio()
                g = sc.geometry()
                if g.left() * ratio <= (r.left + r.right) / 2 < (g.left() + g.width()) * ratio or len(QGuiApplication.screens()) == 1:
                    screen = sc
                    break
            screen = screen or QGuiApplication.primaryScreen()
            ratio = screen.devicePixelRatio()
            return ((r.left + r.right) / 2 / ratio, r.bottom / ratio, screen)
        except Exception:
            return None

    def show_text(self, text: str, seconds: float = 4.0):
        from PySide6.QtCore import QRect
        from PySide6.QtGui import QFontMetrics
        self._text = text
        spot = self._window_spot()          # near what the user is looking at: the bottom of the window in front
        screen = spot[2] if spot is not None else (QGuiApplication.screenAt(self.cursor().pos())
                                                    or QGuiApplication.primaryScreen())
        g = screen.availableGeometry()
        # nothing is cut off (the user, 2026-10-20): the capsule grows with the text, and when the text is wider than
        # the screen it wraps onto more lines and grows taller
        fm = QFontMetrics(self._font)
        pad_x = 54 + 24 + 2 * PAD
        room = max(240, g.width() - 40 - pad_x)
        flat = fm.horizontalAdvance(text)
        if flat <= room:
            w, lines_h = flat + pad_x, 0
        else:
            w = room + pad_x
            box = fm.boundingRect(QRect(0, 0, room, 10000), int(Qt.TextWordWrap), text)
            lines_h = max(0, box.height() - fm.height())
        self._extra = lines_h
        self.setFixedSize(w, CH + 14 + 2 * PAD + lines_h)
        if spot is not None:
            cx, bottom, screen = spot
            x = int(min(max(cx - self.width() / 2, g.left()), g.right() - self.width()))
            y = int(min(bottom - self.height() - 70, g.bottom() - self.height() - 20))
            self.move(x, max(g.top(), y))
        else:
            self.move(g.center().x() - self.width() // 2, g.bottom() - self.height() - 36 - H)
        self._fade.stop()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            _click_through(self)
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(int(seconds * 1000))
        self.update()

    def _out(self):
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _faded(self):
        if self.windowOpacity() < 0.05:
            self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        extra = getattr(self, "_extra", 0)
        cap = QRectF(PAD, PAD, self.width() - 2 * PAD, CH + 14 + extra)
        r = min((CH + 14) / 2, cap.height() / 2)
        p.setPen(Qt.NoPen)
        for i in range(PAD, 0, -2):
            p.setBrush(QColor(0, 0, 0, int(26 * (1 - i / PAD) ** 2) + 2))
            p.drawRoundedRect(cap.adjusted(-i, -i + 3, i, i + 3), r + i, r + i)
        path = QPainterPath()
        path.addRoundedRect(cap, r, r)
        g = QLinearGradient(cap.topLeft(), cap.bottomLeft())
        g.setColorAt(0, QColor(38, 38, 44, 245))
        g.setColorAt(1, QColor(16, 16, 20, 245))
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 34), 1))
        p.drawPath(path)
        # a check mark in the accent colour, then the text
        cx, cy = cap.left() + 30, cap.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(96, 150, 255))                       # a filled blue disc with a white check
        p.drawEllipse(QPointF(cx, cy), 12, 12)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline([QPointF(cx - 5, cy), QPointF(cx - 1.5, cy + 3.8), QPointF(cx + 5.5, cy - 4.2)])
        p.setPen(QColor(246, 247, 250))
        p.setFont(self._font)
        p.drawText(cap.adjusted(54, 0, -24, 0), Qt.AlignVCenter | Qt.AlignLeft | Qt.TextWordWrap, self._text)
        p.end()
