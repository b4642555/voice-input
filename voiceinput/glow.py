"""A glow along the screen's edges while listening, like Siri's (the user's request, 2026-10-15; a style picked on
the 一般 tab, off by default). It brightens and reaches further in with the voice (the user's choice).

Four thin windows along the edges, not one over the whole screen: a translucent window is handed to Windows whole
at every frame. Each strip is drawn at a third of its size and scaled up (the glow is soft anyway): its colors
filled with one gradient, then cut by a falloff mask computed once per level when it shows. Stroking the glow as
ten wide gradient lines each frame took more than a whole CPU core (2026-10-15); now ~10-20% of one
core at 24 frames a second (12 when quiet), most of it Windows compositing the strips.
UI Automation looks through the strips (checked: the elements under the screen's edges are the same with and
without them), so terminal selections near the edges still work."""
import math
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QWidget

from . import system

DEPTH = 72          # px of the screen each strip covers, the glow's widest
_SCALE = 3          # strips are drawn at 1/_SCALE and scaled up
_LEVELS = 12        # falloff masks from quiet to loud
_FPS = 24


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    colors: tuple       # stops around the screen (conical gradient), first = last
    turn: float         # turns per second of the colors around the screen (0: still)
    pulse: float        # breathing when quiet: 0 none, 1 strong


STYLES = [
    Style("siri", "Siri 彩虹", ("#3d7bff", "#a259ff", "#ff4fa3", "#ff9f43", "#29d3ff", "#3d7bff"), 0.18, 0.2),
    Style("aurora", "極光", ("#14e8b5", "#2fa8ff", "#7b5cff", "#14e8b5", "#5cffd0", "#14e8b5"), 0.08, 0.3),
    Style("sunset", "暖陽", ("#ff7a3d", "#ff3d6e", "#ffc23d", "#ff7a3d", "#ff5c8a", "#ff7a3d"), 0.1, 0.3),
    Style("ocean", "冰藍", ("#7fd6ff", "#3a8dff", "#c8f0ff", "#7fd6ff", "#4fa8ff", "#7fd6ff"), 0.06, 0.4),
    Style("red", "錄音紅", ("#ff453a", "#ff6b5e", "#ff2d20", "#ff453a"), 0.0, 1.0),
]
BY_ID = {s.id: s for s in STYLES}


def _strength(level: int) -> float:
    """How far in and how bright the glow is at a mask level (0 quiet .. _LEVELS - 1 loud)."""
    return 0.45 + 0.55 * level / (_LEVELS - 1)


def _profile(dist: np.ndarray, strength: float) -> np.ndarray:
    """Opacity at `dist` px from an edge: bright at the edge, fading inward."""
    x = np.clip(dist / (DEPTH * strength), 0, 1)
    return (1 - x) ** 2.4 * (0.35 + 0.6 * strength)


def mask(screen: QRect, rect: QRect, strength: float) -> QImage:
    """The falloff over `rect` (screen coordinates) at 1/_SCALE: white, its alpha the glow's opacity, every edge
    of the screen counted (so a strip's corners match its neighbours')."""
    w, h = -(-rect.width() // _SCALE), -(-rect.height() // _SCALE)
    xs = rect.left() - screen.left() + (np.arange(w) + 0.5) * _SCALE
    ys = rect.top() - screen.top() + (np.arange(h) + 0.5) * _SCALE
    X, Y = np.meshgrid(xs, ys)
    keep = np.ones_like(X)
    for dist in (X, screen.width() - X, Y, screen.height() - Y):
        keep *= 1 - _profile(dist, strength)
    a = (np.clip(1 - keep, 0, 1) * 255).astype(np.uint32)
    argb = np.ascontiguousarray((a << 24) | (a << 16) | (a << 8) | a)   # premultiplied white
    return QImage(argb.tobytes(), w, h, w * 4, QImage.Format_ARGB32_Premultiplied).copy()


def frame(style: Style, t: float, level: float) -> tuple[int, float]:
    """(mask level, angle of the colors) at t seconds, level 0..1 (the voice)."""
    breathe = 0.5 + 0.5 * math.sin(t * 2.2)
    energy = max(level, style.pulse * 0.35 * breathe)   # quiet: a faint breathing glow
    return min(_LEVELS - 1, round(energy * (_LEVELS - 1))), round(-t * style.turn * 360, 1)


def render(style: Style, screen: QRect, rect: QRect, masks: list, t: float, level: float) -> QImage:
    """`rect` of the glow at t seconds, level 0..1 (the voice), at 1/_SCALE."""
    k, angle = frame(style, t, level)
    m = masks[k]
    img = QImage(m.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    cx = (screen.left() + screen.width() / 2 - rect.left()) / _SCALE
    cy = (screen.top() + screen.height() / 2 - rect.top()) / _SCALE
    grad = QConicalGradient(QPointF(cx, cy), angle)
    for i, c in enumerate(style.colors):
        grad.setColorAt(i / (len(style.colors) - 1), QColor(c))
    p.fillRect(img.rect(), grad)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawImage(0, 0, m)
    p.end()
    return img


def _click_through(widget):
    system.click_through(widget)


class _Strip(QWidget):
    """One edge's window: shows its rendered part of the glow, scaled up."""

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)   # macOS hides tool windows of an inactive app
        self.image: QImage | None = None
        self.masks: list = []

    def paintEvent(self, _):
        if self.image is None:
            return
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(self.rect(), self.image)
        p.end()


class EdgeGlow:
    def __init__(self, level_source):
        self._level_source = level_source   # callable -> float 0..1
        self._strips = [_Strip() for _ in range(4)]
        self._style: Style | None = None
        self._screen = QRect()
        self._level = 0.0
        self._t0 = time.monotonic()
        self._timer = QTimer(interval=1000 // _FPS, timeout=self._tick)
        self._fades = []
        for s in self._strips:
            fade = QPropertyAnimation(s, b"windowOpacity", duration=220)
            fade.finished.connect(lambda s=s, f=fade: s.hide() if f.endValue() == 0.0 else None)
            self._fades.append(fade)
        self._on = False
        self._shown = None   # the frame drawn last (frame())
        self._masks = {}     # (x, y, w, h) of a screen -> each strip's masks
        self._ticks = 0

    @property
    def style_id(self) -> str:
        return self._style.id if self._style else ""

    def set_style(self, style_id: str):
        self._style = BY_ID.get(style_id)
        self._shown = None
        if self._style is None:
            self.hide_now()

    def show(self, screen=None):
        if self._style is None or self._on:
            return
        self._on = True
        self._place(screen)
        self._t0 = time.monotonic()
        self._level = 0.0
        self._shown = None
        self._render()
        for s, fade in zip(self._strips, self._fades):
            s.setWindowOpacity(0.0)
            s.show()
            _click_through(s)
            fade.stop()
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.start()
        self._timer.start()

    def move(self, screen):
        """While shown: to another screen at once (the pointer went there, 2026-10-15)."""
        if self._on and screen is not None and screen.geometry() != self._screen:
            self._place(screen)
            self._shown = None
            self._render()
            for s in self._strips:
                s.update()

    def _place(self, screen):
        """The strips along `screen`'s edges, with their masks (made once per screen size and place)."""
        g = (screen or QGuiApplication.primaryScreen()).geometry()
        if g == self._screen:
            return
        self._screen = g
        d = min(DEPTH, g.height() // 4, g.width() // 4)
        rects = [QRect(g.left(), g.top(), g.width(), d), QRect(g.left(), g.bottom() - d + 1, g.width(), d),
                 QRect(g.left(), g.top() + d, d, g.height() - 2 * d),
                 QRect(g.right() - d + 1, g.top() + d, d, g.height() - 2 * d)]
        key = (g.x(), g.y(), g.width(), g.height())
        if key not in self._masks:
            self._masks[key] = [[mask(g, r, _strength(k)) for k in range(_LEVELS)] for r in rects]
        for s, r, m in zip(self._strips, rects, self._masks[key]):
            s.setGeometry(r)
            s.masks = m

    def hide(self):
        if not self._on:
            return
        self._on = False
        for s, fade in zip(self._strips, self._fades):
            fade.stop()
            fade.setStartValue(s.windowOpacity())
            fade.setEndValue(0.0)
            fade.start()
        QTimer.singleShot(260, lambda: None if self._on else self._timer.stop())

    def hide_now(self):
        """Hidden at once, with the indicator (before a terminal edit)."""
        self._on = False
        self._timer.stop()
        for s, fade in zip(self._strips, self._fades):
            fade.stop()
            s.hide()

    def _tick(self):
        target = min(1.0, max(0.0, self._level_source()) * 1.6)
        # fast up, slow down, like a level meter
        self._level += (target - self._level) * (0.5 if target > self._level else 0.08)
        self._ticks += 1
        if self._level < 0.05 and self._ticks % 2:   # quiet: the colors only drift, half the frames do
            return
        if self._render():
            for s in self._strips:
                s.update()

    def _render(self) -> bool:
        """False when the frame would look as the last one did (a still style, the same level)."""
        t = time.monotonic() - self._t0
        shown = frame(self._style, t, self._level)
        if shown == self._shown:
            return False
        self._shown = shown
        for s in self._strips:
            s.image = render(self._style, self._screen, s.geometry(), s.masks, t, self._level)
        return True
