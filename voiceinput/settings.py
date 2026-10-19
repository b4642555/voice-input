"""The main window (Typeless-style, 2026-10-13; four tabs before): a sidebar and six pages.

- 首頁: what dictation has done for the user (words, time saved, speed, streak), how to start, the latest dictations.
- 歷史紀錄: every dictation kept (history.py), searchable, with copy / the raw transcript / delete; and the
  per-utterance debug log of this run (ASR, 格式, 編輯) that the 辨識紀錄 tab used to show.
- 字典: the hotwords (hotwords.py) with their weights, a box to add one, and the smart-picks file.
- AI 潤稿: the LLM that cleans up dictations (llm.py): provider, key, model, style, context, a test.
- 自訂操作: custom operations (rules.py), one card per rule.
- 設定: microphone, the small switches, autostart and the 同步 folder (sync.py).
Every change is applied and saved immediately."""
import html
import os
import re
import subprocess
import threading
import time

from pypinyin import lazy_pinyin
from PySide6.QtCore import QEasingCurve, QEvent, QRectF, QSize, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QPainter, QPainterPath, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QApplication, QButtonGroup, QComboBox, QDialog, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QPlainTextEdit, QScrollArea, QSpinBox, QStackedWidget, QTextBrowser, QVBoxLayout,
                               QWidget)

from . import audio, hotwords, llm, rules
from .history import words as count_words
from .keys import KEY_TRIGGERS, MOUSE_TRIGGERS, combo_text, is_modifier
from .rules import Rule, Step

SOURCES = [(rules.MOUSE, "滑鼠鍵"), (rules.KEY, "鍵盤鍵"), (rules.SPEECH, "說出")]
GESTURES = [(rules.TAP, "按一下"), (rules.DOUBLE, "連按兩下"), (rules.HOLD, "按住")]
SITUATIONS = [(rules.ANY, "不限"), (rules.DICTATED, "剛輸入完"), (rules.SELECTION, "有選取文字")]
STEP_TYPES = [(rules.COMMAND, "命令"), (rules.KEYS, "按鍵"), (rules.TEXT, "輸入文字"), (rules.WAIT, "等待")]
COMMANDS = [(rules.RECORD, "錄音"), (rules.PICK, "選字"),
            (rules.DELETE_SELECTION, "刪除選取的文字"),
            (rules.DELETE_DICTATION, "刪除剛輸入的文字")]
_STEP_DEFAULTS = {rules.COMMAND: rules.DELETE_SELECTION, rules.KEYS: "", rules.TEXT: "", rules.WAIT: 100}

HOTWORD_SORTS = [("weight", "權重比例（高→低）"), ("name", "名稱 A→Z"), ("name_desc", "名稱 Z→A"),
                 ("date", "日期（新→舊）"), ("date_old", "日期（舊→新）")]
_CARD_STYLE = ""      # cards are styled by the window's style sheet (QFrame#card)

# Look (2026-10-14, the user: 不要像 Typeless，要有自己的美感; 出幾種配色給我選): a top bar with pill navigation, one
# centered column, a dark home card with drifting glows and a voice ribbon. The colours come from a theme (設定 → 配色),
# switched live: every widget paints with T, the style sheet is rebuilt by _qss().
_BASE = dict(paper="#f5f2ec", surface="#ffffff", line="#e7e1d6", ink="#1f1d1a", subtle="#7a7368",
             accent="#0f7b6c", g0="#5aae9f", g1="#86b392", g2="#b7ab80",
             nav="#ebe6dc", nav_hover="#e2dccf", control="#dcd5c8", hover="#f7f3ec", row="#f0ebe2",
             chip="#f1ece2", chip_fg="#6b655b", chip_ai="#e3f1ed", primary="#1f1d1a", primary_fg="#fbf8f2",
             primary_hover="#3a3631", marker="#1f1d1a", marker_fg="#fbf8f2", scroll="#d8d0c2", browser="#fbf9f5",
             switch_off="#d9d2c5", bar="#2b2824", empty="#e4ddd0", soft="#eaf4f1", soft_line="#cfe6e0",
             card_hover="#d6cdbd", hero="#161513", glows=("#2f9e8c", "#7aa88a", "#a99a6e"))
THEMES = {
    "paper": ("紙與墨（暖米色）", {}),
    "midnight": ("午夜（深色）", dict(
        paper="#0e1014", surface="#161a21", line="#252b35", ink="#e9ecf2", subtle="#8b93a3", accent="#8b7dff",
        g0="#6fb8f0", g1="#7f9df0", g2="#9690ee", nav="#1b2029", nav_hover="#232a35", control="#2c3340",
        hover="#1d222b", row="#20262f", chip="#222833", chip_fg="#aab2c0", chip_ai="#272448", primary="#8b7dff",
        primary_fg="#0e1014", primary_hover="#a093ff", marker="#e9ecf2", marker_fg="#0e1014", scroll="#2f3744",
        browser="#12161c", switch_off="#39414e", bar="#c9cfda", empty="#262c36", soft="#1c1a33",
        soft_line="#2e2a55", card_hover="#3a4250", hero="#07080b", glows=("#3f7fd0", "#5f6fd8", "#7a68d0"))),
    "mist": ("晨霧（冷灰藍）", dict(
        paper="#eef2f7", surface="#ffffff", line="#dde4ee", ink="#142033", subtle="#66758c", accent="#2f6bff",
        g0="#5fa8ee", g1="#6d9cee", g2="#7c92ec", nav="#e1e8f2", nav_hover="#d6dfec", control="#cfd8e5",
        hover="#f3f6fb", row="#e9eef5", chip="#e8eef7", chip_fg="#4d5c73", chip_ai="#e3ebff", primary="#142033",
        primary_fg="#f5f8fc", primary_hover="#26354d", marker="#142033", marker_fg="#f5f8fc", scroll="#c7d1df",
        browser="#f7f9fc", switch_off="#cbd4e1", bar="#1f2d44", empty="#dbe3ee", soft="#e8efff",
        soft_line="#cbd9ff", card_hover="#c3cfe0", hero="#0d1626", glows=("#3d8fe0", "#4f7fe6", "#6378e0"))),
    "sakura": ("櫻花（粉橘）", dict(
        paper="#fbf3f1", surface="#ffffff", line="#f0dcd8", ink="#2b1d20", subtle="#8c7276", accent="#d6457a",
        g0="#ee8fa7", g1="#f0a09c", g2="#f0b192", nav="#f5e4e1", nav_hover="#efd6d2", control="#ebd3cf",
        hover="#fdf5f3", row="#f6e8e5", chip="#f8e6e3", chip_fg="#7d6064", chip_ai="#fbe1ea", primary="#2b1d20",
        primary_fg="#fff8f6", primary_hover="#45302f", marker="#2b1d20", marker_fg="#fff8f6", scroll="#e6cdc9",
        browser="#fdf8f7", switch_off="#e8d2ce", bar="#3a272b", empty="#f1dfdc", soft="#fdecf1",
        soft_line="#f6cfdb", card_hover="#e2c3be", hero="#1d1216", glows=("#d9708f", "#e08b86", "#c97f9f"))),
    "forest": ("森林（墨綠金）", dict(
        paper="#eef1ea", surface="#fbfcf9", line="#dbe1d4", ink="#18241c", subtle="#66735f", accent="#2f7d4f",
        g0="#78bd8c", g1="#93bf84", g2="#adbd7b", nav="#e1e7da", nav_hover="#d5ddcc", control="#cdd6c3",
        hover="#f3f6ef", row="#e6ebdf", chip="#e5ebdd", chip_fg="#55624f", chip_ai="#dcefe2", primary="#18241c",
        primary_fg="#f6f8f2", primary_hover="#2a3b2f", marker="#18241c", marker_fg="#f6f8f2", scroll="#c8d1bd",
        browser="#f6f8f3", switch_off="#cfd8c5", bar="#22332a", empty="#dde4d5", soft="#e3f1e7",
        soft_line="#c4e2cd", card_hover="#bfcab3", hero="#0e1711", glows=("#3f9f68", "#6aa86a", "#5a9a7f"))),
}
T = dict(_BASE)
DEFAULT_THEME = "mist"


def set_theme(name: str):
    T.clear()
    T.update(_BASE)
    T.update(THEMES.get(name, THEMES[DEFAULT_THEME])[1])


_SAMPLE = "嗯那個我明天下午三點，不對，四點要跟客戶開會，然後第一要準備報告，第二要訂會議室，第三記得帶筆電。"


def _qss() -> str:
    t = T
    return f"""
QDialog#main {{ background: {t['paper']}; }}
QWidget {{ font-family: "Microsoft JhengHei UI", "Segoe UI"; font-size: 13px; color: {t['ink']}; }}
QWidget#topbar {{ background: transparent; }}
QLabel#brand {{ font-size: 15px; font-weight: 700; letter-spacing: 0.3px; }}
QFrame#navpill {{ background: {t['nav']}; border-radius: 24px; }}
QPushButton#nav {{ padding: 6px 18px 6px 14px; border: 1px solid transparent; border-radius: 20px; font-size: 15px;
                  color: {t['subtle']}; background: transparent; }}
QPushButton#nav:hover {{ color: {t['ink']}; background: {t['nav_hover']}; }}
QPushButton#nav:checked {{ background: transparent; color: {t['marker_fg']}; font-weight: 600; }}
QPushButton#nav:checked:hover {{ background: transparent; }}
QPushButton#caption, QPushButton#captionClose {{ border: none; border-radius: 8px; background: transparent;
                      padding: 0; font-size: 13px; color: {t['subtle']}; }}
QPushButton#caption:hover {{ background: {t['nav_hover']}; color: {t['ink']}; }}
QPushButton#captionClose:hover {{ background: #d9534f; color: white; }}
QLabel#status {{ color: {t['subtle']}; font-size: 12px; }}
QWidget#page {{ background: transparent; }}
QLabel#title {{ font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }}
QLabel#subtitle {{ color: {t['subtle']}; font-size: 13px; }}
QLabel#section {{ font-size: 12px; font-weight: 700; color: {t['subtle']}; letter-spacing: 1px; padding-top: 10px; }}
QLabel#muted {{ color: {t['subtle']}; }}
QLabel#statValue {{ font-size: 28px; font-weight: 700; }}
QLabel#statUnit {{ font-size: 13px; color: {t['subtle']}; padding-bottom: 5px; }}
QLabel#statLabel {{ color: {t['subtle']}; font-size: 12px; }}
QFrame#hero {{ background: transparent; border: none; }}
QFrame#hero QLabel {{ color: #f6f1e7; background: transparent; }}
QLabel#heroTitle {{ font-size: 30px; font-weight: 700; }}
QLabel#heroLine {{ font-size: 14px; color: #cfc8bb; }}
QLabel#keycap {{ background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.2); border-radius: 7px;
                padding: 3px 9px; font-size: 12px; color: #f6f1e7; }}
QFrame#card {{ background: {t['surface']}; border: 1px solid {t['line']}; border-radius: 18px; }}
QFrame#card:hover {{ border-color: {t['card_hover']}; }}
QFrame#soft {{ background: {t['soft']}; border: 1px solid {t['soft_line']}; border-radius: 14px; }}
QFrame#row {{ background: transparent; border: none; border-bottom: 1px solid {t['row']}; }}
QFrame#tl {{ background: transparent; border: none; border-bottom: 1px solid {t['row']}; }}
QLabel#tlTime {{ color: {t['subtle']}; font-size: 12px; font-family: "Cascadia Mono", "Consolas"; }}
QLabel#chip {{ background: {t['chip']}; border-radius: 9px; padding: 2px 8px; font-size: 11px; color: {t['chip_fg']}; }}
QLabel#chipAi {{ background: {t['chip_ai']}; border-radius: 9px; padding: 2px 8px; font-size: 11px;
                color: {t['accent']}; }}
QPushButton {{ background: {t['surface']}; border: 1px solid {t['control']}; border-radius: 10px; padding: 6px 14px; }}
QPushButton:hover {{ background: {t['hover']}; border-color: {t['card_hover']}; }}
QPushButton:disabled {{ color: {t['subtle']}; }}
QPushButton#primary {{ background: {t['primary']}; color: {t['primary_fg']}; border: none; font-weight: 600;
                      padding: 7px 16px; }}
QPushButton#primary:hover {{ background: {t['primary_hover']}; }}
QPushButton#ghost {{ border: none; background: transparent; color: {t['subtle']}; padding: 4px 8px; }}
QPushButton#ghost:hover {{ background: {t['nav']}; color: {t['ink']}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {t['surface']}; border: 1px solid {t['control']}; border-radius: 10px;
                                  padding: 6px 10px; min-height: 18px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {t['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {t['surface']}; border: 1px solid {t['control']};
                               selection-background-color: {t['chip_ai']}; selection-color: {t['ink']}; outline: none; }}
QCheckBox {{ spacing: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QTextBrowser {{ border: 1px solid {t['line']}; border-radius: 14px; padding: 8px; background: {t['browser']}; }}
QToolTip {{ background: {t['ink']}; color: {t['paper']}; border: none; padding: 5px 8px; border-radius: 6px; }}
"""


def _voice_gradient(x0: float, x1: float):
    from PySide6.QtGui import QLinearGradient
    g = QLinearGradient(x0, 0, x1, 0)
    g.setColorAt(0, QColor(T["g0"]))
    g.setColorAt(0.55, QColor(T["g1"]))
    g.setColorAt(1, QColor(T["g2"]))
    return g


class BrandMark(QWidget):
    """The app's mark: five rounded bars of a voice level, teal to amber."""
    HEIGHTS = (0.35, 0.75, 1.0, 0.6, 0.3)

    def __init__(self, size: int = 22):
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(_voice_gradient(0, self.width()))
        n, w, h = len(self.HEIGHTS), self.width(), self.height()
        bw = w / (n * 1.6)
        for i, f in enumerate(self.HEIGHTS):
            bh = max(bw, h * f)
            x = i * bw * 1.6 + bw * 0.3
            p.drawRoundedRect(QRectF(x, (h - bh) / 2, bw, bh), bw / 2, bw / 2)


class VoiceRibbon(QWidget):
    """The home card's moving voice: a row of rounded bars breathing in two slow waves (only while visible)."""
    BARS = 34

    def __init__(self):
        super().__init__()
        self.setMinimumSize(220, 96)
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _tick(self):
        self._t += 0.05
        self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        step = w / self.BARS
        bw = step * 0.46
        grad = _voice_gradient(0, w)
        p.save()                                                     # a faint haze behind the whole voice
        r = w * 0.5                                                  # fits the widget: fades to 0 before any edge
        p.translate(w / 2, h / 2)
        p.scale(1.0, (h * 0.5) / r)
        haze = QRadialGradient(0, 0, r)
        c = QColor(T["g1"])
        for stop, alpha in ((0, 55), (0.45, 24), (0.8, 6), (1, 0)):
            c.setAlpha(alpha)
            haze.setColorAt(stop, c)
        p.setBrush(haze)
        p.drawEllipse(QRectF(-r, -r, 2 * r, 2 * r))
        p.restore()
        for i in range(self.BARS):
            x = i / (self.BARS - 1)
            env = math.sin(math.pi * x) ** 1.4                       # quiet at both ends
            wave = 0.55 + 0.45 * math.sin(self._t * 2.1 + x * 9.0) * math.sin(self._t * 0.9 + x * 4.0 + 1.3)
            bh = max(bw, h * 0.92 * env * (0.25 + 0.75 * abs(wave)))
            bx = i * step + (step - bw) / 2
            p.setBrush(grad)
            p.drawRoundedRect(QRectF(bx, (h - bh) / 2, bw, bh), bw / 2, bw / 2)


def _nav_icon(key: str):
    """A line icon for a page (drawn here: no image files), grey when off, light on the dark marker when on."""
    from PySide6.QtGui import QIcon, QPen, QPixmap
    icon = QIcon()
    for state, colour in ((QIcon.Off, T["subtle"]), (QIcon.On, T["marker_fg"])):
        for scale in (1, 2):
            pm = QPixmap(18 * scale, 18 * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(colour), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            path = QPainterPath()
            if key == "home":
                path.moveTo(3, 8.5); path.lineTo(9, 3.5); path.lineTo(15, 8.5)
                path.moveTo(4.5, 7.5); path.lineTo(4.5, 15); path.lineTo(13.5, 15); path.lineTo(13.5, 7.5)
                path.moveTo(7.5, 15); path.lineTo(7.5, 11); path.lineTo(10.5, 11); path.lineTo(10.5, 15)
            elif key == "history":
                path.addEllipse(QRectF(2.5, 2.5, 13, 13))
                path.moveTo(9, 5.5); path.lineTo(9, 9); path.lineTo(11.5, 10.5)
            elif key == "dictionary":
                path.addRoundedRect(QRectF(3.5, 2.5, 11, 13), 1.5, 1.5)
                path.moveTo(6.5, 6.5); path.lineTo(11.5, 6.5)
                path.moveTo(6.5, 9.5); path.lineTo(10, 9.5)
            elif key == "ai":
                path.moveTo(8, 2.5); path.quadTo(8.6, 7.4, 13.5, 8); path.quadTo(8.6, 8.6, 8, 13.5)
                path.quadTo(7.4, 8.6, 2.5, 8); path.quadTo(7.4, 7.4, 8, 2.5)
                path.moveTo(13.5, 12); path.lineTo(13.5, 15.5); path.moveTo(11.75, 13.75); path.lineTo(15.25, 13.75)
            elif key == "rules":
                for y, x in ((5, 11.5), (9, 6.5), (13, 10)):
                    path.moveTo(3, y); path.lineTo(15, y)
                    path.addEllipse(QRectF(x - 1.6, y - 1.6, 3.2, 3.2))
            else:   # settings: a gear
                import math
                path.addEllipse(QRectF(6.5, 6.5, 5, 5))
                for k in range(8):
                    a = k * math.pi / 4
                    path.moveTo(9 + 4.6 * math.cos(a), 9 + 4.6 * math.sin(a))
                    path.lineTo(9 + 6.6 * math.cos(a), 9 + 6.6 * math.sin(a))
                path.addEllipse(QRectF(4.4, 4.4, 9.2, 9.2))
            p.drawPath(path)
            p.end()
            icon.addPixmap(pm, QIcon.Normal, state)
    return icon


class AuroraCard(QFrame):
    """The home card: near-black with three slow glows (teal, amber, violet) drifting under its content."""
    GLOWS = ((0.22, 0.30, 0.55, 0.0), (0.78, 0.75, 0.45, 2.1), (0.55, 0.15, 0.40, 4.2))   # x, y, size, phase

    def __init__(self):
        super().__init__(objectName="hero")
        self._t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, e):
        super().showEvent(e)
        self._timer.start()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._timer.stop()

    def _tick(self):
        self._t += 0.02
        self.update()

    def paintEvent(self, _):
        import math
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), 24, 24)
        p.setClipPath(path)
        p.fillRect(self.rect(), QColor(T["hero"]))
        p.setPen(Qt.NoPen)
        for color, (x, y, size, phase) in zip(T["glows"], self.GLOWS):
            cx = w * (x + 0.10 * math.sin(self._t * 1.3 + phase))
            cy = h * (y + 0.18 * math.cos(self._t + phase))
            r = max(w, h) * size * 1.25
            g = QRadialGradient(cx, cy, r)
            c = QColor(color)
            for stop, alpha in ((0, 130), (0.3, 90), (0.6, 40), (1, 0)):   # a long, even fade: no visible edge
                c.setAlpha(alpha)
                g.setColorAt(stop, c)
            p.setBrush(g)
            p.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))


class NavPill(QFrame):
    """The page buttons on one pill; the dark marker under the current one slides to the next (240ms)."""

    def __init__(self):
        super().__init__(objectName="navpill")
        self._rect = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._moved)
        self.current = None

    def _moved(self, r):
        self._rect = r
        self.update()

    def point_at(self, button, animate=True):
        self.current = button
        target = QRectF(button.geometry())
        if not animate or self._rect.isNull():
            self._anim.stop()
            self._moved(target)
            return
        self._anim.stop()
        self._anim.setStartValue(self._rect)
        self._anim.setEndValue(target)
        self._anim.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.current is not None:
            QTimer.singleShot(0, lambda: self.point_at(self.current, False))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._rect.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["marker"]))
        r = self._rect
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)


def _count_up(label: QLabel, value: float, fmt, ms: int = 700):
    """Show value on label by counting up to it (ease-out), formatted by fmt(number)."""
    old = getattr(label, "_counter", None)
    if old is not None:
        old.stop()
    a = QVariantAnimation(label)
    a.setDuration(ms)
    a.setEasingCurve(QEasingCurve.OutCubic)
    a.setStartValue(0.0)
    a.setEndValue(float(value))
    a.valueChanged.connect(lambda v: label.setText(fmt(v)))
    label._counter = a
    a.start()


class DayBars(QWidget):
    """Words per day for the last 14 days as slim bars (today on the right, in the voice colours)."""
    DAYS = 14

    def __init__(self):
        super().__init__()
        self.values: list = [0] * self.DAYS
        self.setFixedHeight(46)
        self.setMinimumWidth(140)

    def set_values(self, values: list):
        self.values = values
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h = self.width(), self.height()
        step = w / self.DAYS
        bw = min(10.0, step * 0.55)
        top = max(self.values) or 1
        for i, v in enumerate(self.values):
            bh = max(4.0, (h - 2) * v / top) if v else 4.0
            x = i * step + (step - bw) / 2
            if v:
                p.setBrush(_voice_gradient(x, x + bw) if i == self.DAYS - 1 else QColor(T["bar"]))
            else:
                p.setBrush(QColor(T["empty"]))
            p.drawRoundedRect(QRectF(x, h - bh, bw, bh), bw / 2, bw / 2)


class WeekDots(QWidget):
    """The last seven days as dots, filled where something was dictated (today last, ringed)."""

    def __init__(self):
        super().__init__()
        self.active: list = [False] * 7
        self.setFixedHeight(46)
        self.setMinimumWidth(140)

    def set_active(self, active: list):
        self.active = active
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        step = w / 7
        r = min(9.0, step * 0.32)
        for i, on in enumerate(self.active):
            c = QRectF(i * step + step / 2 - r, h / 2 - r, 2 * r, 2 * r)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(T["accent"]) if on else QColor(T["empty"]))
            p.drawEllipse(c)
            if i == 6:
                from PySide6.QtGui import QPen
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(T["g2"]), 2))
                p.drawEllipse(c.adjusted(-4, -4, 4, 4))


class Switch(QAbstractButton):
    """An iOS-style switch with the same API as a QCheckBox (isChecked, setChecked, toggled)."""

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 22)

    def sizeHint(self):
        return QSize(40, 22)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["accent"]) if on else QColor(T["switch_off"]))
        if not self.isEnabled():
            p.setOpacity(0.5)
        p.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(20 if on else 2, 2, 18, 18))
        p.end()


def _label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if name:
        lab.setObjectName(name)
    lab.setWordWrap(wrap)
    return lab


def _setting_row(title: str, desc: str, control: QWidget) -> QFrame:
    """One line of a settings card: a title with a short description on the left, the control on the right."""
    row = QFrame(objectName="row")
    h = QHBoxLayout(row)
    h.setContentsMargins(16, 12, 16, 12)
    v = QVBoxLayout()
    v.setSpacing(2)
    v.addWidget(_label(title))
    if desc:
        d = _label(desc, "muted", wrap=True)
        d.setStyleSheet("font-size: 12px")
        v.addWidget(d)
    h.addLayout(v, 1)
    h.addSpacing(12)
    h.addWidget(control, 0, Qt.AlignVCenter)
    return row


def _card(*rows: QWidget) -> QFrame:
    card = QFrame(objectName="card")
    v = QVBoxLayout(card)
    v.setContentsMargins(0, 4, 0, 4)
    v.setSpacing(0)
    for r in rows:
        v.addWidget(r)
    if rows and isinstance(rows[-1], QFrame) and rows[-1].objectName() == "row":
        rows[-1].setStyleSheet("QFrame#row { border-bottom: none; }")
    return card


def _scroll_instead(widget, e):
    """Give a wheel event to the scroll area around widget: on the list of operations the wheel scrolls and never
    changes a value (the user kept changing them by accident); only a click opens a drop-down."""
    area = widget.parentWidget()
    while area is not None and not isinstance(area, QScrollArea):
        area = area.parentWidget()
    if area is not None:
        QApplication.sendEvent(area.viewport(), e)


class _NoWheelCombo(QComboBox):
    def wheelEvent(self, e):
        _scroll_instead(self, e)


class _NoWheelSpin(QSpinBox):
    def wheelEvent(self, e):
        _scroll_instead(self, e)


def _combo(items, value, tip="") -> QComboBox:
    c = _NoWheelCombo()
    for key, label in items:
        c.addItem(label, key)
    c.setCurrentIndex(max(0, c.findData(value)))
    if tip:
        c.setToolTip(tip)
    return c


class KeyCapture(QLineEdit):
    """Click, then press a key combination: shows and stores it as text (Ctrl+Shift+Z)."""
    captured = Signal(str)

    def __init__(self, text: str):
        super().__init__(text, placeholderText="點這裡後按下組合鍵")
        self.setReadOnly(True)

    _MOD_NAMES = {0x10: "Shift", 0xA0: "Shift", 0xA1: "Shift", 0x11: "Ctrl", 0xA2: "Ctrl", 0xA3: "Ctrl",
                  0x12: "Alt", 0xA4: "Alt", 0xA5: "Alt", 0x5B: "Win", 0x5C: "Win"}
    _MOD_ORDER = ("Ctrl", "Shift", "Alt", "Win")

    def event(self, e):
        t = e.type()
        if t == QEvent.ShortcutOverride:   # Alt+letter would otherwise be taken as a mnemonic
            e.accept()
            return True
        if t == QEvent.KeyPress:   # here, not keyPressEvent: Tab would move the focus first
            self._key(e)
            return True
        if t == QEvent.KeyRelease:
            self._key_up(e)
            return True
        return super().event(e)

    def _key(self, e):
        vk = e.nativeVirtualKey()
        if is_modifier(vk):
            if not getattr(self, "_held", None):
                self._held, self._used = set(), False
            self._held.add(self._MOD_NAMES.get(vk))
            return
        self._used = True
        mods = e.modifiers()
        names = [n for n, flag in (("Ctrl", Qt.ControlModifier), ("Shift", Qt.ShiftModifier),
                                   ("Alt", Qt.AltModifier), ("Win", Qt.MetaModifier)) if mods & flag]
        text = combo_text(names, vk)
        if text:
            self.setText(text)
            self.captured.emit(text)

    def _key_up(self, e):
        """Modifiers pressed and released with no other key: the combination is the modifiers alone (Alt)."""
        if e.isAutoRepeat() or not is_modifier(e.nativeVirtualKey()):
            return
        held = getattr(self, "_held", None)
        if held and not self._used:
            text = "+".join(n for n in self._MOD_ORDER if n in held)
            self.setText(text)
            self.captured.emit(text)
        self._held = None


class _CenteredPage(QWidget):
    """A page whose content is one column of at most MAX pixels, centered (wide windows get margins, not lines a
    metre long)."""
    MAX, SIDE = 880, 32

    def __init__(self):
        super().__init__(objectName="page")
        self.setAttribute(Qt.WA_StyledBackground)
        v = QVBoxLayout(self)
        v.setContentsMargins(self.SIDE, 6, self.SIDE, 18)
        v.setSpacing(4)

    def resizeEvent(self, e):
        side = max(self.SIDE, (self.width() - self.MAX) // 2)
        self.layout().setContentsMargins(side, 6, side, 18)
        super().resizeEvent(e)


class SettingsDialog(QDialog):
    applied = Signal()
    sync_changed = Signal()
    _ai_result = Signal(str, bool)      # AI test / model list result from a thread: text, ok
    _models_ready = Signal(list, str)
    _catalog_ready = Signal(str, list)        # a service's model list from a thread: "claude" / "gpt" / "flash", [(label, value)]
    _codex_state_ready = Signal(str, str)
    _cli_state_ready = Signal(str, str)      # Claude Code check from a thread: missing / out / ok, the account

    NAV = [("home", "首頁", "⌂"), ("history", "歷史紀錄", "☰"), ("dictionary", "字典", "Aa"),
           ("ai", "AI 潤稿", "✦"), ("rules", "自訂操作", "⌘"), ("settings", "設定", "◎")]

    def __init__(self, cfg, hotwords, memory_path, history=None, memory=None, hotword_validator=None):
        # no system title bar (2026-10-14): our own top bar and window buttons; drag and resize through
        # WM_NCHITTEST (nativeEvent below)
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinMaxButtonsHint)
        self.cfg = cfg
        self.hotwords = hotwords
        self.memory_path = memory_path
        self.history = history
        self.memory = memory
        self.hotword_validator = hotword_validator
        self._status_text = ""
        self._loading = False
        self._history_limit = 60
        self.setObjectName("main")
        self.setWindowTitle("VoiceInput")
        set_theme(getattr(cfg, "theme", DEFAULT_THEME))
        self.setStyleSheet(_qss())
        geo = QGuiApplication.primaryScreen().availableGeometry()          # a roomy first size, never bigger
        self.resize(min(1260, int(geo.width() * 0.8)), min(860, int(geo.height() * 0.85)))    # than the screen
        self.setMinimumSize(640, 480)

        self.stack = QStackedWidget()
        self.pages = {}
        builders = {"home": self._home_page, "history": self._history_page, "dictionary": self._hotwords_page,
                    "ai": self._ai_page, "rules": self._rules_page, "settings": self._general_page}
        for key, _, _ in self.NAV:
            self.pages[key] = builders[key]()
            self.stack.addWidget(self.pages[key])

        # the top bar: the mark and name, the pages as one pill of buttons, the status, the window buttons
        self.topbar = QWidget(objectName="topbar")
        self.topbar.setFixedHeight(64)
        tb = QHBoxLayout(self.topbar)
        tb.setContentsMargins(22, 0, 10, 0)
        tb.setSpacing(10)
        tb.addWidget(BrandMark(20))
        self._brand = _label("VoiceInput", "brand")
        tb.addWidget(self._brand)
        tb.addStretch(1)
        pill = self._pill = NavPill()
        pill.setFixedHeight(50)
        ph = QHBoxLayout(pill)
        ph.setContentsMargins(3, 3, 3, 3)
        ph.setSpacing(2)
        self.nav = QButtonGroup(self)
        self._nav_buttons = {}
        for i, (key, text, _icon) in enumerate(self.NAV):
            b = QPushButton(text, objectName="nav", checkable=True)
            b.setFixedHeight(42)
            b.setIcon(_nav_icon(key))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            self.nav.addButton(b, i)
            self._nav_buttons[key] = b
            ph.addWidget(b)
        self.nav.idClicked.connect(self._go)
        tb.addWidget(pill, 0, Qt.AlignVCenter)
        tb.addStretch(1)
        self.status = _label("", "status")       # shown only when there is something to say (not 就緒)
        self.save_status = _label("", "status")  # shown only when saving failed
        self.status.hide()
        self.save_status.hide()
        tb.addWidget(self.status)
        tb.addWidget(self.save_status)
        tb.addSpacing(6)
        self._caption = QWidget()
        ch = QHBoxLayout(self._caption)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(2)
        for text, name, fn in (("—", "caption", self.showMinimized), ("□", "caption", self._toggle_max),
                               ("✕", "captionClose", self.close)):
            b = QPushButton(text, objectName=name)
            b.setFixedSize(38, 32)
            b.setCursor(Qt.ArrowCursor)
            b.clicked.connect(fn)
            ch.addWidget(b)
        tb.addWidget(self._caption)
        self._nav_buttons["home"].setChecked(True)
        QTimer.singleShot(0, lambda: self._pill.point_at(self._nav_buttons["home"], False))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.topbar)
        layout.addWidget(self.stack, 1)

        self._ai_result.connect(self._show_ai_result)
        self._models_ready.connect(self._fill_models)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._adapt()

    def _toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def showEvent(self, e):
        super().showEvent(e)
        try:                       # Windows 11: rounded corners and the drop shadow of a normal window
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(ctypes.c_int(2)), 4)
            margins = (ctypes.c_int * 4)(0, 0, 0, 1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
        except Exception:
            pass

    def nativeEvent(self, event_type, message):
        """The frameless window still moves (drag the top strip), resizes (the edges, 8px) and maximizes (double
        click). Worked out in physical pixels from the window's own rectangle (any scale, any monitor)."""
        if event_type == b"windows_generic_MSG":
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x84:                                    # WM_NCHITTEST
                rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(wintypes.HWND(int(self.winId())), ctypes.byref(rect)):
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value - rect.left
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value - rect.top
                    w, h = rect.right - rect.left, rect.bottom - rect.top
                    ratio = self.devicePixelRatioF()
                    b = max(6, round(8 * ratio))
                    if not self.isMaximized():
                        left, right, top, bottom = x < b, x >= w - b, y < b, y >= h - b
                        code = {(1, 0, 1, 0): 13, (0, 1, 1, 0): 14, (1, 0, 0, 1): 16, (0, 1, 0, 1): 17}.get(
                            (left, right, top, bottom))
                        if code is None:
                            code = 10 if left else 11 if right else 12 if top else 15 if bottom else None
                        if code:
                            return True, code
                    if y < self.topbar.height() * ratio:
                        from PySide6.QtCore import QPoint
                        child = self.childAt(QPoint(round(x / ratio), round(y / ratio)))
                        if child is None or not isinstance(child, (QAbstractButton, QLineEdit, QComboBox, QSpinBox)):
                            return True, 2                             # HTCAPTION
        return super().nativeEvent(event_type, message)

    def _adapt(self):
        """The top bar fits its width (nothing is cut off): below ~1080px the page buttons keep only their icons,
        below ~760px the name goes, and the status line shows only when there is room and something to say."""
        w = self.width()
        compact = w < 1080
        for key, b in self._nav_buttons.items():
            label = next(t for k, t, _ in self.NAV if k == key)
            b.setText("" if compact else label)
            b.setToolTip(label if compact else "")
            b.setStyleSheet("QPushButton#nav { padding: 6px 14px; }" if compact else "")
        self._brand.setVisible(w >= 760)
        self.status.setVisible(w >= 900 and bool(self.status.text()))
        self.save_status.setVisible(bool(self.save_status.text()))
        if self._pill.current is not None:
            QTimer.singleShot(0, lambda: self._pill.point_at(self._pill.current, False))

    def _dev_to_logical(self, x: int, y: int):
        from PySide6.QtCore import QPoint
        r = self.devicePixelRatioF()
        return QPoint(round(x / r), round(y / r))

    def show_page(self, key: str):
        self._nav_buttons[key].setChecked(True)
        self._go(list(self.pages).index(key))

    def _go(self, i: int):
        self.stack.setCurrentIndex(i)
        key = list(self.pages)[i]
        self._pill.point_at(self._nav_buttons[key])
        if key == "home":
            self._count_next = True        # the numbers count up each time the home page is opened
        if key in ("home", "history"):
            self.refresh_history()

    def _fade_in(self, page):
        """A page comes in by fading (200ms); the effect is removed afterwards (it slows painting)."""
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(eff)
        a = QVariantAnimation(page)
        a.setDuration(220)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.valueChanged.connect(eff.setOpacity)
        a.finished.connect(lambda: page.setGraphicsEffect(None))
        a.start()
        page._fade = a

    @staticmethod
    def _page(title: str, subtitle: str = "", scroll: bool = True):
        """A page: a title, a subtitle and a content column (inside a scroll area unless scroll is False)."""
        page = _CenteredPage()
        outer = page.layout()
        if title:
            outer.addSpacing(10)
            outer.addWidget(_label(title, "title"))
        if subtitle:
            outer.addWidget(_label(subtitle, "subtitle", wrap=True))
        outer.addSpacing(14)
        if not scroll:
            return page, outer
        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 6, 8, 0)
        col.setSpacing(12)
        area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        area.setWidget(inner)
        outer.addWidget(area, 1)
        return page, col

    # --- 首頁 ---
    def _home_page(self) -> QWidget:
        page, col = self._page("")
        col.setSpacing(14)

        # the dark card on top: a greeting, today in one sentence, how to start, and the voice moving
        hero = AuroraCard()
        hero.setMinimumHeight(190)
        hh = QHBoxLayout(hero)
        hh.setContentsMargins(30, 26, 26, 26)
        hh.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(8)
        self._greeting = _label("", "heroTitle")
        left.addWidget(self._greeting)
        self._today_line = _label("", "heroLine", wrap=True)
        left.addWidget(self._today_line)
        left.addStretch(1)
        self._how = QVBoxLayout()           # how to record, from the custom operations (refilled by _refresh_how)
        self._how.setSpacing(6)
        left.addLayout(self._how)
        hh.addLayout(left, 5)
        self.ribbon = VoiceRibbon()
        hh.addWidget(self.ribbon, 4)
        col.addWidget(hero)

        # three numbers, each with a small picture of where it comes from
        self._stats = {}
        row = QHBoxLayout()
        row.setSpacing(14)

        def metric(key, caption, picture):
            card = QFrame(objectName="card")
            v = QVBoxLayout(card)
            v.setContentsMargins(20, 16, 20, 16)
            v.setSpacing(2)
            v.addWidget(_label(caption, "statLabel"))
            line = QHBoxLayout()
            line.setSpacing(6)
            value, unit = _label("0", "statValue"), _label("", "statUnit")
            line.addWidget(value, 0, Qt.AlignBottom)
            line.addWidget(unit, 0, Qt.AlignBottom)
            line.addStretch(1)
            v.addLayout(line)
            v.addSpacing(6)
            v.addWidget(picture)
            note = _label("", "muted")
            note.setStyleSheet("font-size: 12px")
            v.addWidget(note)
            self._stats[key] = (value, unit, note)
            row.addWidget(card, 1)

        self.day_bars = DayBars()
        self.week_dots = WeekDots()
        speed = QWidget()
        speed.setFixedHeight(46)
        metric("words", "說了多少", self.day_bars)
        metric("saved", "省下的時間", speed)
        metric("streak", "連續使用", self.week_dots)
        self._speed_box = QHBoxLayout(speed)
        self._speed_box.setContentsMargins(0, 0, 0, 0)
        self._speed_label = _label("", "muted", wrap=True)
        self._speed_box.addWidget(self._speed_label)
        col.addLayout(row)

        self._ai_banner = QFrame(objectName="soft")
        bh = QHBoxLayout(self._ai_banner)
        bh.setContentsMargins(18, 12, 12, 12)
        self._ai_banner_text = _label("", wrap=True)
        bh.addWidget(self._ai_banner_text, 1)
        go = QPushButton("開啟 AI 潤稿", objectName="primary", clicked=lambda: self.show_page("ai"))
        bh.addWidget(go)
        col.addWidget(self._ai_banner)

        head = QHBoxLayout()
        head.addWidget(_label("最近說的話", "section"))
        head.addStretch(1)
        head.addWidget(QPushButton("全部紀錄 →", objectName="ghost", clicked=lambda: self.show_page("history")))
        col.addLayout(head)
        self._recent_card = QFrame(objectName="card")
        self._recent = QVBoxLayout(self._recent_card)
        self._recent.setContentsMargins(6, 4, 6, 4)
        self._recent.setSpacing(0)
        col.addWidget(self._recent_card)

        col.addWidget(_label("小技巧", "section"))
        tips = QFrame(objectName="card")
        tg = QGridLayout(tips)
        tg.setContentsMargins(20, 14, 20, 14)
        tg.setHorizontalSpacing(24)
        tg.setVerticalSpacing(10)
        for i, (k, t) in enumerate((("換行 / 送出", "只說「換行」或「送出」就換行、按 Enter"),
                                    ("改口", "「三點，不對，四點」只會留下四點"),
                                    ("選字", "選取一個詞，按一下中鍵換成同音字"),
                                    ("取代", "選取文字後按住說話，直接改寫它"))):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(_label(k, "statLabel"))
            box.addWidget(_label(t, wrap=True))
            tg.addLayout(box, i // 2, i % 2)
        col.addWidget(tips)
        col.addStretch(1)
        return page

    def _refresh_home(self):
        import datetime
        hour = time.localtime().tm_hour
        self._greeting.setText("早安" if 5 <= hour < 12 else "午安" if 12 <= hour < 18 else "晚安")
        if self.history is not None:
            st = self.history.stats()
            days = self.history.per_day()
            today = datetime.date.today()
            last14 = [days.get((today - datetime.timedelta(days=13 - i)).isoformat(), 0) for i in range(14)]
            today_words = last14[-1]
            self._today_line.setText(f"今天說了 {today_words:,} 字。" if today_words else
                                     "今天還沒開口。想到什麼，直接說出來就好。")
            count = getattr(self, "_count_next", True)
            self._count_next = False

            def show(label, value, fmt):
                if count:
                    _count_up(label, value, fmt)
                else:
                    label.setText(fmt(value))

            v, u, n = self._stats["words"]
            show(v, st["words"], lambda x: f"{round(x):,}")
            u.setText("字")
            n.setText(f"近兩週 {sum(last14):,} 字")
            self.day_bars.set_values(last14)

            v, u, n = self._stats["saved"]
            m = st["saved_min"]
            if m >= 60:
                show(v, m / 60, lambda x: f"{x:.1f}")
                u.setText("小時")
            else:
                show(v, m, lambda x: f"{x:.0f}")
                u.setText("分鐘")
            wpm = st["wpm"]
            self._speed_label.setText(f"你說話每分鐘約 {wpm:.0f} 字，是打字的 {wpm / 40:.1f} 倍" if wpm else
                                      "多說幾句，這裡會算出你說話的速度")
            n.setText(f"總共說了 {st['spoken_min']:.0f} 分鐘")

            v, u, n = self._stats["streak"]
            show(v, st["streak"], lambda x: f"{round(x)}")
            u.setText("天")
            n.setText(f"最長 {st['longest_streak']} 天 · 共用了 {st['active_days']} 天")
            self.week_dots.set_active([bool(x) for x in last14[-7:]])
        on = self.cfg.ai_enabled and self._llm_settings().ready()
        self._refresh_how()
        self._ai_banner.setVisible(not on)
        self._ai_banner_text.setText("開啟 AI 潤稿：自動去掉贅字、修正改口，整理成好讀的文字。")
        self._fill_timeline(self._latest(6))

    def _refresh_how(self):
        """The home card says how to record with the keys the user has set (自訂操作): one line per way."""
        while self._how.count():
            item = self._how.takeAt(0)
            lay, w = item.layout(), item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif lay is not None:
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.setParent(None)
                        sub.deleteLater()
        lines = []
        for r in self.cfg.rules:
            if not r.enabled or r.source == rules.SPEECH or r.record_index() is None                     or rules.problems(r, self.cfg.rules):
                continue
            name = MOUSE_TRIGGERS.get(r.key) or KEY_TRIGGERS.get(r.key, (r.key,))[0]
            if r.source == rules.MOUSE and not name.endswith("鍵"):
                name = "滑鼠" + name
            if r.gesture == rules.HOLD:
                line = ("按住", name, "說話，放開就打好字")
            elif r.gesture == rules.TAP:
                line = ("按一下", name, "開始說話，再按一下結束")
            else:
                line = ("連按兩下", name, "開始說話，再按一下結束")
            if line not in lines:
                lines.append(line)
        if not lines:
            row = QHBoxLayout()
            row.addWidget(_label("還沒有錄音的按鍵：到「自訂操作」新增一個。", "heroLine"))
            row.addStretch(1)
            self._how.addLayout(row)
            return
        for before, key, after in lines[:2]:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(_label(before, "heroLine"))
            row.addWidget(_label(key, "keycap"))
            row.addWidget(_label(after, "heroLine"))
            row.addStretch(1)
            self._how.addLayout(row)

    def _fill_timeline(self, entries):
        lay = self._recent
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not entries:
            empty = _label("還沒有紀錄。按住滑鼠中鍵說一句話試試看。", "muted")
            empty.setContentsMargins(16, 16, 16, 16)
            lay.addWidget(empty)
            return
        today = time.strftime("%Y-%m-%d")
        for i, e in enumerate(entries):
            row = QFrame(objectName="tl")
            if i == len(entries) - 1:
                row.setStyleSheet("QFrame#tl { border-bottom: none; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(14, 12, 8, 12)
            h.setSpacing(14)
            when = time.strftime("%H:%M", time.localtime(e.t))
            if e.day() != today:
                when = e.day()[5:].replace("-", "/") + "\n" + when
            t = _label(when, "tlTime")
            t.setFixedWidth(44)
            h.addWidget(t, 0, Qt.AlignTop)
            body = QVBoxLayout()
            body.setSpacing(6)
            text = _label(e.text if len(e.text) < 140 else e.text[:140] + "…", wrap=True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text.setStyleSheet("font-size: 14px")
            body.addWidget(text)
            chips = QHBoxLayout()
            chips.setSpacing(6)
            if e.app:
                chips.addWidget(_label(e.app.removesuffix(".exe"), "chip"))
            if e.polished:
                chips.addWidget(_label("AI 整理", "chipAi"))
            chips.addStretch(1)
            body.addLayout(chips)
            h.addLayout(body, 1)
            h.addWidget(QPushButton("複製", objectName="ghost", clicked=lambda _=False, s=e.text: self._copy(s)),
                        0, Qt.AlignTop)
            lay.addWidget(row)

    # --- 歷史紀錄 ---
    def _history_page(self) -> QWidget:
        page, outer = self._page("歷史紀錄", "每次口述的結果都留在這裡（設定同步資料夾後，各台電腦會共用）。",
                                 scroll=False)
        top = QHBoxLayout()
        self.h_search = QLineEdit(placeholderText="搜尋…")
        self.h_search.setClearButtonEnabled(True)
        self.h_search.textChanged.connect(lambda _: self.refresh_history())
        top.addWidget(self.h_search, 1)
        self.h_debug = QPushButton("除錯紀錄", checkable=True)
        self.h_debug.setToolTip("這次執行期間每句話的 ASR、格式化和編輯細節")
        self.h_debug.toggled.connect(lambda on: self.h_stack.setCurrentIndex(1 if on else 0))
        top.addWidget(self.h_debug)
        clear = QPushButton("清除", objectName="ghost", clicked=self._clear_history)
        top.addWidget(clear)
        outer.addLayout(top)
        outer.addSpacing(8)

        inner = QWidget()
        col = QVBoxLayout(inner)
        col.setContentsMargins(0, 6, 8, 0)
        col.setSpacing(8)
        self.h_list = QVBoxLayout()
        self.h_list.setSpacing(8)
        col.addLayout(self.h_list)
        self.h_more = QPushButton("顯示更多", objectName="ghost", clicked=self._more_history)
        col.addWidget(self.h_more, 0, Qt.AlignHCenter)
        col.addStretch(1)
        area = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        area.setWidget(inner)

        self.log = QTextBrowser()
        self.log.setPlaceholderText("每次說話的辨識結果、格式化結果與操作會顯示在這裡（只保留這次執行期間）")
        self.h_stack = QStackedWidget()
        self.h_stack.addWidget(area)
        self.h_stack.addWidget(self.log)
        outer.addWidget(self.h_stack, 1)
        return page

    def _latest(self, n: int, query: str = "") -> list:
        if self.history is None:
            return []
        out = []
        for e in reversed(self.history.entries):
            if query and query.casefold() not in (e.text + e.raw + e.app).casefold():
                continue
            out.append(e)
            if len(out) >= n:
                break
        return out

    def refresh_history(self):
        if self.history is None:
            return
        self._refresh_home()
        q = self.h_search.text().strip()
        shown = self._latest(self._history_limit + 1, q)
        self.h_more.setVisible(len(shown) > self._history_limit)
        self._fill_entries(self.h_list, shown[:self._history_limit], compact=False,
                           empty="還沒有紀錄。按住滑鼠中鍵說話試試看。" if not q else "找不到符合的紀錄。")

    def _more_history(self):
        self._history_limit += 100
        self.refresh_history()

    def _fill_entries(self, layout, entries, compact: bool, empty: str = "還沒有紀錄。按住滑鼠中鍵說話試試看。"):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        if not entries:
            layout.addWidget(_label(empty, "muted"))
            return
        today = time.strftime("%Y-%m-%d")
        last_day = None
        for e in entries:
            day = e.day()
            if not compact and day != last_day:
                last_day = day
                layout.addWidget(_label("今天" if day == today else day, "section"))
            layout.addWidget(self._entry_card(e, compact))

    def _entry_card(self, e, compact: bool) -> QFrame:
        card = QFrame(objectName="card")
        v = QVBoxLayout(card)
        v.setContentsMargins(16, 10, 10, 10)
        v.setSpacing(4)
        head = QHBoxLayout()
        meta = time.strftime("%H:%M", time.localtime(e.t))
        if compact and e.day() != time.strftime("%Y-%m-%d"):
            meta = e.day()[5:] + " " + meta
        app = e.app.removesuffix(".exe") if e.app else ""
        head.setSpacing(6)
        head.addWidget(_label(meta, "tlTime"))
        if app:
            head.addWidget(_label(app, "chip"))
        if e.polished:
            head.addWidget(_label("AI 整理", "chipAi"))
        head.addWidget(_label(f"{count_words(e.text)} 字", "muted"))
        head.addStretch(1)
        copy = QPushButton("複製", objectName="ghost", clicked=lambda: self._copy(e.text))
        head.addWidget(copy)
        if not compact:
            if e.raw and e.raw != e.text:
                raw = QPushButton("原文", objectName="ghost", checkable=True)
                head.addWidget(raw)
            delete = QPushButton("刪除", objectName="ghost", clicked=lambda: self._delete_entry(e))
            head.addWidget(delete)
        v.addLayout(head)
        body = _label(e.text if not compact or len(e.text) < 160 else e.text[:160] + "…", wrap=True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.setStyleSheet("font-size: 14px")
        v.addWidget(body)
        if not compact and e.raw and e.raw != e.text:
            raw_text = _label("原始辨識：" + e.raw, "muted", wrap=True)
            raw_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            raw_text.hide()
            raw.toggled.connect(raw_text.setVisible)
            v.addWidget(raw_text)
        return card

    def _copy(self, text: str):
        QGuiApplication.clipboard().setText(text)
        self.set_status("已複製")

    def _delete_entry(self, e):
        self.history.delete(e)
        self.refresh_history()

    def _clear_history(self):
        if QMessageBox.question(self, "清除歷史紀錄", "刪除這台電腦的所有歷史紀錄？無法復原。") == QMessageBox.Yes:
            self.history.clear()
            self.refresh_history()

    def set_log(self, entries):
        """Render entries newest first, in pipeline order. Each entry: time, asr, asr_s, fmt, and edit (an
        operation or edit description, optional)."""
        def row(label, secs, text, color=""):
            t = f"{secs:.2f}s" if secs is not None else ""
            style = f" style='color:{color}'" if color else ""
            return (f"<tr><td width=40><b>{label}</b></td><td width=46 style='color:gray'>{t}</td>"
                    f"<td{style}>{html.escape(text)}</td></tr>")

        def block(e):
            return (f"<div style='color:gray'>{e['time']}</div><table cellspacing=0 cellpadding=2>"
                    + row("ASR", e["asr_s"], e["asr"])
                    + row("AI" if e.get("ai") else "格式", None, e["fmt"])
                    + (row("編輯", None, e["edit"]) if e.get("edit") else "")
                    + (row("畫面", None, e["screen"], "#8a8a92") if e.get("screen") else "")
                    + (row("失敗", e.get("audio_s"), e["error"], "#d32f2f") if e.get("error") else "")
                    + "</table>")

        self.log.setHtml("<br>".join(block(e) for e in reversed(entries)))
        self.log.verticalScrollBar().setValue(0)
        self._refresh_data_errors()

    # --- 字典 (hotwords) ---
    def _hotwords_page(self) -> QWidget:
        page, outer = self._page("字典", "常用的人名、專有名詞和英文寫法。字典裡的詞辨識時會優先，AI 潤稿也會照這裡的寫法。",
                                 scroll=False)
        add_row = QHBoxLayout()
        self.hw_input = QLineEdit(placeholderText="新增一個詞，例如：Claude Code、張雨薇")
        self.hw_input.returnPressed.connect(self._add_hotword)
        add_row.addWidget(self.hw_input, 1)
        add_row.addWidget(QPushButton("新增", objectName="primary", clicked=self._add_hotword))
        add_row.addSpacing(12)
        add_row.addWidget(_label("排序", "muted"))
        self.hw_sort = _combo(HOTWORD_SORTS, self.cfg.hotword_sort)
        self.hw_sort.currentIndexChanged.connect(self._sort_hotwords)
        add_row.addWidget(self.hw_sort)
        outer.addLayout(add_row)
        self.hw_feedback = _label("", "muted", wrap=True)
        self.hw_feedback.setVisible(False)
        outer.addWidget(self.hw_feedback)
        self.hw_error = _label("", wrap=True)
        self.hw_error.setStyleSheet("color: #d32f2f")
        self.hw_error.setVisible(False)
        outer.addWidget(self.hw_error)
        learning = _label("語音輸入後手動改字，稍停再按 Enter／Ctrl+Enter 送出；可讀取的輸入框清空後，會將確認的修正加入"
                          "字典或智慧選字（終端機、滑鼠送出與讀不到文字的欄位不支援）。", "muted", wrap=True)
        learning.setStyleSheet("font-size: 12px")
        outer.addWidget(learning)
        outer.addSpacing(8)

        self.hw_list = QVBoxLayout()
        self.hw_list.setSpacing(6)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 8, 0)
        self.hw_empty = _label("還沒有詞。在上面輸入，或選字時選了詞語、在選字選單的輸入框輸入文字，就會加到這裡。",
                               "muted", wrap=True)
        v.addWidget(self.hw_empty)
        v.addLayout(self.hw_list)
        v.addStretch(1)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.addWidget(_label("智慧選字會記住你選過的單字和前後文。", "muted"))
        foot.addStretch()
        foot.addWidget(QPushButton("開啟智慧選字紀錄", objectName="ghost", clicked=self._open_memory))
        outer.addLayout(foot)
        return page

    def _word_error(self, word: str) -> str:
        if not word:
            return "請先輸入詞語。"
        if not hotwords.eligible(word):
            return "單一中文字由智慧選字依前後文學習；字典請輸入兩個字以上的詞語。"
        return self.hotword_validator(word) if self.hotword_validator else ""

    def _feedback(self, text: str, error: bool = False):
        self.hw_feedback.setStyleSheet("color: #d32f2f" if error else "")
        self.hw_feedback.setText(text)
        self.hw_feedback.setVisible(bool(text))

    def _add_hotword(self):
        word = self.hw_input.text().strip()
        error = self._word_error(word)
        if not error and not self.hotwords.add(word):
            error = getattr(self.hotwords, "error", "") or "儲存失敗，請確認文字與檔案狀態。"
        self.refresh_hotwords()
        self._feedback(error or f"已加入「{word}」，下一次辨識就會用到。", bool(error))
        if not error:
            self.hw_input.clear()

    def _edit_hotword(self, old: str):
        new, accepted = QInputDialog.getText(self, "編輯詞語", "詞語：", text=old)
        if not accepted:
            return
        new = new.strip()
        error = self._word_error(new)
        if not error and not self.hotwords.rename(old, new):
            error = getattr(self.hotwords, "error", "") or "修改失敗，原詞已保留。"
        self.refresh_hotwords()
        self._feedback(error or f"已修改為「{new}」，下一次辨識就會用到。", bool(error))

    def _refresh_data_errors(self):
        stores = [self.hotwords] + ([self.memory] if self.memory is not None else [])
        for store in stores:
            store.reload_if_changed()
        errors = [store.error for store in stores if getattr(store, "error", "")]
        shown = [t for t in [self._status_text] + errors if t and t != "就緒"]
        self.status.setText("  ".join(shown))
        self._adapt()
        self.hw_error.setText("\n".join(errors))
        self.hw_error.setVisible(bool(errors))

    def refresh_hotwords(self):
        self._refresh_data_errors()
        while self.hw_list.count():
            w = self.hw_list.takeAt(0).widget()
            w.setParent(None)
            w.deleteLater()
        for h in self._sorted_hotwords():
            card = QFrame(objectName="card")
            row = QHBoxLayout(card)
            row.setContentsMargins(14, 6, 6, 6)
            word = _label(h.word)
            word.setStyleSheet("font-size: 14px; font-weight: 600")
            row.addWidget(word, 1)
            row.addWidget(_label("手動" if h.source == hotwords.MANUAL else "選字", "muted"))
            row.addSpacing(10)
            added = _label(h.added, "muted")
            added.setToolTip("加入的日期")
            row.addWidget(added)
            row.addSpacing(10)
            pct = self.hotwords.percent(h)
            bar = QFrame()
            bar.setFixedSize(60, 6)
            bar.setStyleSheet(f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {T['accent']}, "
                              f"stop:{max(0.01, min(pct, 100)) / 100:.2f} {T['accent']}, "
                              f"stop:{min(1, max(0.01, min(pct, 100)) / 100 + 0.01):.2f} {T['empty']}, "
                              f"stop:1 {T['empty']});"
                              "border-radius: 3px;")
            bar.setToolTip(f"權重 {h.weight:g} / 上限 {hotwords.cap(h.word):g}（{pct:.0f}%）")
            row.addWidget(bar)
            percent = _label(f"{pct:.0f}%", "muted")
            percent.setMinimumWidth(40)
            percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(percent)
            row.addWidget(QPushButton("編輯", objectName="ghost",
                                      clicked=lambda _=False, w=h.word: self._edit_hotword(w)))
            remove = QPushButton("✕", objectName="ghost", toolTip="刪除這個詞",
                                 clicked=lambda _=False, w=h.word: self._remove_hotword(w))
            row.addWidget(remove)
            self.hw_list.addWidget(card)
        self.hw_empty.setVisible(not self.hotwords.items)

    def _sorted_hotwords(self) -> list:
        """The list in the chosen order; ties by weight (as a share of the cap: Chinese and English caps differ),
        then name. Names sort by their pinyin, so Chinese words sit among the English ones by sound (程式 near
        Claude)."""
        def name(h):
            return "".join(lazy_pinyin(h.word)).casefold()
        items = sorted(self.hotwords.items, key=lambda h: (-self.hotwords.percent(h), name(h)))
        order = self.cfg.hotword_sort
        if order in ("name", "name_desc"):
            items.sort(key=name, reverse=order == "name_desc")
        elif order in ("date", "date_old"):
            items.sort(key=lambda h: h.added, reverse=order == "date")
        return items

    def _sort_hotwords(self):
        self.cfg.hotword_sort = self.hw_sort.currentData()
        self._save()
        self.refresh_hotwords()

    def _remove_hotword(self, word: str):
        self.hotwords.remove(word)
        self.refresh_hotwords()

    def _open_memory(self):
        try:
            if not self.memory_path.exists():
                if self.memory is not None:
                    if not self.memory.save():
                        QMessageBox.warning(self, "無法儲存紀錄檔", self.memory.error)
                        return
                else:
                    self.memory_path.parent.mkdir(parents=True, exist_ok=True)
                    self.memory_path.write_text('{\n"examples": [\n],\n"styles": {}\n}\n', encoding="utf-8")
            editor = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
            subprocess.Popen([editor, str(self.memory_path)])
        except OSError as exc:
            QMessageBox.warning(self, "無法開啟紀錄檔", f"記事本啟動失敗：{exc}\n檔案：{self.memory_path}")

    # --- AI 潤稿 ---
    def _ai_page(self) -> QWidget:
        page, col = self._page("AI 潤稿", "說完話後由 AI 去掉贅字、修正改口、自動分段和條列，並依你正在用的 App 調整語氣。"
                                         "可使用已登入的訂閱額度，或自行設定其他服務。")
        self.ai_on = Switch()
        self.ai_provider = QComboBox()
        for key, (label, *_rest) in llm.PROVIDERS.items():
            if key != "claude_cli":          # Claude is part of the one subscription entry (codex_cli)
                self.ai_provider.addItem("訂閱帳號（Claude → Flash → GPT，免金鑰）" if key == "codex_cli" else label, key)
        self.ai_key = QLineEdit(echoMode=QLineEdit.Password, placeholderText="貼上 API 金鑰")
        self.ai_key.setMinimumWidth(260)
        self.ai_get_key = QPushButton("取得免費金鑰", objectName="ghost", clicked=self._open_key_page)
        key_box = QWidget()
        kh = QHBoxLayout(key_box)
        kh.setContentsMargins(0, 0, 0, 0)
        kh.addWidget(self.ai_key)
        kh.addWidget(self.ai_get_key)
        self.ai_model = QComboBox(editable=True)
        self.ai_model.setMinimumWidth(260)
        self.ai_model.lineEdit().setPlaceholderText("模型名稱")
        self.ai_models_btn = QPushButton("取得清單", objectName="ghost", clicked=self._list_models)
        model_box = QWidget()
        mh = QHBoxLayout(model_box)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.ai_model)
        mh.addWidget(self.ai_models_btn)
        self.ai_url = QLineEdit(placeholderText="https://…/v1")
        self.ai_url.setMinimumWidth(320)
        self.ai_style = QComboBox()
        for key, label in llm.STYLES.items():
            self.ai_style.addItem(label, key)
        self.ai_context = Switch()
        self.ai_scope = QComboBox()
        self.ai_scope.addItem("每句都檢查（最準）", "all")
        self.ai_scope.addItem("只檢查可疑或較長的句子（省額度）", "saver")
        self._url_row = _setting_row("API 網址", "OpenAI 相容的 /v1 端點", self.ai_url)
        self._key_row = _setting_row("登入並取得金鑰", "按右邊的按鈕：在網頁登入帳號，按「建立 API 金鑰」並複製，程式會自動填入"
                                     "（金鑰只存在這台電腦，並用 Windows 帳號加密）", key_box)
        self._model_row = _setting_row("模型", "留空使用預設；訂閱自動接續時，此欄為 GPT 模型。", model_box)
        # the three subscriptions: fixed order Claude -> Flash -> GPT, each with a model menu read from the service
        self.ai_claude_model = self._model_combo("Haiku 5.5，最快最省")
        self.ai_flash_model = self._model_combo("Flash Low")
        self.ai_gpt_model = self._model_combo("Luna")
        self.ai_flash_enabled = Switch()
        self._catalog_loaded = False
        self.ai_reload_models = QPushButton("重新讀取模型清單", objectName="ghost", clicked=lambda: self._load_catalog(True))
        self._order_row = _setting_row("使用順序", "固定為 Claude → Flash → GPT：前一個無法使用時，自動換下一個。"
                                       "GPT 固定使用 Fast（比較快，較耗訂閱額度）。模型清單直接向各服務讀取。",
                                       self.ai_reload_models)
        self._claude_model_row = _setting_row("Claude 模型", "", self.ai_claude_model)
        self._flash_row = _setting_row("Antigravity Flash（選用）", "使用 Antigravity 已登入的帳號。開啟代表同意它保存潤稿對話與傳入的畫面文字。",
                                       self.ai_flash_enabled)
        self._flash_model_row = _setting_row("Flash 模型", "", self.ai_flash_model)
        self._gpt_model_row = _setting_row("GPT 模型", "", self.ai_gpt_model)
        self.ai_cli_state = _label("", wrap=True)
        self.ai_cli_btn = QPushButton("", objectName="primary", clicked=self._cli_action)
        self.ai_cli_recheck = QPushButton("重新檢查", objectName="ghost", clicked=self._check_cli)
        cli_box = QWidget()
        ch = QHBoxLayout(cli_box)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.addWidget(self.ai_cli_state, 1)
        ch.addWidget(self.ai_cli_btn)
        ch.addWidget(self.ai_cli_recheck)
        self._cli_stage = "missing"
        self._cli_state_ready.connect(self._show_cli_state)
        self._cli_row = _setting_row("Claude Code", "用你自己的 Claude 訂閱，不用金鑰。裝好並登入就能用，之後每句約 1～3 秒", cli_box)

        self.ai_codex_state = _label("", wrap=True)
        self.ai_codex_state.setTextFormat(Qt.TextFormat.PlainText)
        self.ai_codex_btn = QPushButton("", objectName="primary", clicked=self._codex_action)
        self.ai_codex_recheck = QPushButton("重新檢查", objectName="ghost", clicked=self._check_codex)
        gpt_box = QWidget()
        gh = QHBoxLayout(gpt_box)
        gh.setContentsMargins(0, 0, 0, 0)
        gh.addWidget(self.ai_codex_state, 1)
        gh.addWidget(self.ai_codex_btn)
        gh.addWidget(self.ai_codex_recheck)
        self._codex_stage = "missing"
        self._codex_state_ready.connect(self._show_codex_state)
        self._codex_row = _setting_row("GPT", "使用 ChatGPT 訂閱。", gpt_box)

        self.ai_usage = _label("", "muted")
        row = _setting_row("開啟 AI 潤稿", "關閉時完全不會用到 AI 額度，直接貼上辨識結果。"
                           "選擇省額度時，短句會交給程式規則。也可以在工具列圖示按右鍵開關", self.ai_on)
        row.layout().itemAt(0).layout().addWidget(self.ai_usage)
        col.addWidget(_card(row))
        col.addWidget(_label("模型", "section"))
        col.addWidget(_card(
            _setting_row("服務", "訂閱方式不需 API 金鑰；Claude、Flash、GPT 共用下方潤稿規則。", self.ai_provider),
            self._order_row,
            self._cli_row,
            self._claude_model_row,
            self._flash_row,
            self._flash_model_row,
            self._codex_row,
            self._gpt_model_row,
            self._url_row,
            self._key_row,
            self._model_row))
        col.addWidget(_label("風格", "section"))
        col.addWidget(_card(
            _setting_row("整理程度", "", self.ai_style),
            _setting_row("檢查範圍", "AI 會判斷整句語意，把辨識錯的字（隻友→只有）改回來。選省額度時，短又沒問題的句子不送出",
                         self.ai_scope),
            _setting_row("參考畫面上下文", "傳送 App 名稱、視窗標題、游標附近與畫面文字，協助理解語意與專有名詞。VoiceInput 不儲存畫面文字；啟用 Antigravity 時，它會保存對話。",
                         self.ai_context)))

        col.addWidget(_label("提示詞（進階）", "section"))
        self.ai_prompt = QPlainTextEdit()
        self.ai_prompt.setMinimumHeight(260)
        self.ai_prompt.setPlaceholderText("給 AI 的整理規則")
        self._prompt_timer = QTimer(self, singleShot=True, interval=700, timeout=self._save_prompt)
        self.ai_prompt.textChanged.connect(lambda: None if self._loading else self._prompt_timer.start())
        reset = QPushButton("還原預設", objectName="ghost", clicked=self._reset_prompt)
        head = QHBoxLayout()
        head.addWidget(_label("這是每句話交給 AI 的整理規則，可以直接改。{format_rule} 會換成上面選的整理程度，"
                              "{tone_rule} 會換成依 App 調整的語氣；刪掉就不會加入。改完 0.7 秒後自動儲存，下一句就套用。",
                              "muted", wrap=True), 1)
        head.addWidget(reset, 0, Qt.AlignTop)
        box = QFrame(objectName="card")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(16, 12, 16, 12)
        bv.addLayout(head)
        bv.addWidget(self.ai_prompt)
        col.addWidget(box)

        col.addWidget(_label("試試看", "section"))
        test = QFrame(objectName="card")
        tv = QVBoxLayout(test)
        tv.setContentsMargins(16, 12, 16, 12)
        self.ai_sample = QLineEdit(_SAMPLE)
        tv.addWidget(self.ai_sample)
        th = QHBoxLayout()
        self.ai_test_btn = QPushButton("測試", objectName="primary", clicked=self._test_ai)
        th.addWidget(self.ai_test_btn)
        th.addStretch()
        tv.addLayout(th)
        self.ai_out = _label("", wrap=True)
        self.ai_out.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tv.addWidget(self.ai_out)
        col.addWidget(test)
        col.addStretch(1)

        self.ai_on.toggled.connect(self._apply_ai)
        self.ai_context.toggled.connect(self._apply_ai)
        self.ai_provider.currentIndexChanged.connect(self._provider_changed)
        self.ai_key.editingFinished.connect(self._apply_ai)
        self.ai_url.editingFinished.connect(self._apply_ai)
        self.ai_model.currentTextChanged.connect(lambda _: self._apply_ai())
        self.ai_style.currentIndexChanged.connect(self._apply_ai)
        self.ai_scope.currentIndexChanged.connect(self._apply_ai)
        for combo in (self.ai_claude_model, self.ai_flash_model, self.ai_gpt_model):
            combo.currentIndexChanged.connect(self._apply_ai)
        self.ai_flash_enabled.toggled.connect(self._apply_ai)
        self._catalog_ready.connect(self._fill_catalog)
        return page

    def _llm_settings(self) -> llm.Settings:
        return llm.settings_from(self.cfg)

    def _provider_changed(self):
        if self._loading:
            return
        self.cfg.ai_provider = self.ai_provider.currentData()
        if self.cfg.ai_provider != "codex_cli":
            self.cfg.ai_model = ""
        self.cfg.ai_base_url = ""
        self._load_ai()
        self._save()

    @staticmethod
    def _model_combo(default_name: str) -> QComboBox:
        c = _NoWheelCombo()
        c.setMinimumWidth(300)
        c.addItem(f"預設（{default_name}）", "")
        return c

    def _set_models(self, combo: QComboBox, current: str, items=None):
        """Fill a model menu: the default, what the service lists, and the saved model when the list lacks it."""
        was = combo.blockSignals(True)
        first = combo.itemText(0)
        known = items if items is not None else [(combo.itemText(k), combo.itemData(k)) for k in range(1, combo.count())]
        combo.clear()
        combo.addItem(first, "")
        for label, value in known:
            if value and combo.findData(value) < 0:
                combo.addItem(label, value)
        if current and combo.findData(current) < 0:
            combo.addItem(current, current)
        combo.setCurrentIndex(max(0, combo.findData(current or "")))
        combo.blockSignals(was)

    def _load_catalog(self, force: bool = False):
        """Read the model menus of the three services in the background (once per window, or when asked)."""
        if self._catalog_loaded and not force:
            return
        self._catalog_loaded = True
        self.ai_reload_models.setText("讀取中…")
        self.ai_reload_models.setEnabled(False)

        def job(name, read):
            try:
                items = read()
            except Exception:
                items = []
            self._catalog_ready.emit(name, items)

        def claude():
            return [(m.get("description") or m.get("displayName") or m["value"], m["value"])
                    for m in llm.claude_models() if m.get("value") and m["value"] != "default"]

        for name, read in (("claude", claude),
                           ("gpt", lambda: [(m, m) for m in llm.codex_cli.list_models()]),
                           ("flash", lambda: [(m, m) for m in llm.antigravity_cli.list_models()])):
            threading.Thread(target=job, args=(name, read), daemon=True).start()
        self._catalog_pending = 3

    def _fill_catalog(self, name: str, items: list):
        c = self.cfg
        combo, current = {"claude": (self.ai_claude_model, c.ai_claude_model),
                          "gpt": (self.ai_gpt_model, c.ai_model),
                          "flash": (self.ai_flash_model, c.ai_flash_model)}[name]
        self._set_models(combo, current, items)
        self._catalog_pending -= 1
        if self._catalog_pending <= 0:
            self.ai_reload_models.setText("重新讀取模型清單")
            self.ai_reload_models.setEnabled(True)

    def _load_ai(self):
        was, self._loading = self._loading, True
        c = self.cfg
        p = llm.PROVIDERS.get(c.ai_provider, llm.PROVIDERS["custom"])
        self.ai_on.setChecked(c.ai_enabled)
        n = self.history.ai_today() if self.history is not None else 0
        self.ai_usage.setText(f"今日潤稿 {n} 次（不含測試）" if c.ai_enabled else "目前關閉")
        self.ai_provider.setCurrentIndex(max(0, self.ai_provider.findData(c.ai_provider)))
        self.ai_key.setText(c.ai_key)
        self.ai_key.setPlaceholderText("本機不需要金鑰（可留空）" if c.ai_provider == "ollama" else "貼上 API 金鑰")
        self.ai_get_key.setText("下載 Ollama" if c.ai_provider == "ollama" else
                                "登入 Google 並取得金鑰" if c.ai_provider == "gemini" else "登入並取得金鑰")
        self.ai_get_key.setVisible(bool(p[3]))
        keyed = c.ai_provider in llm.KEY_PATTERNS
        self.ai_get_key.setObjectName("primary" if keyed else "ghost")     # the one thing to press for Google / Groq
        self.ai_get_key.setStyle(self.ai_get_key.style())                  # re-read the style sheet
        self.ai_url.setText(c.ai_base_url or p[1])
        sub = c.ai_provider == "codex_cli"
        for row in (self._order_row, self._cli_row, self._claude_model_row, self._flash_row,
                    self._codex_row, self._gpt_model_row):
            row.setVisible(sub)
        self._flash_model_row.setVisible(sub and c.ai_flash_enabled)
        self._url_row.setVisible(c.ai_provider in ("custom", "ollama"))
        self._key_row.setVisible(not sub and c.ai_provider != "ollama")
        self._model_row.setVisible(not sub)
        if sub:
            self._check_cli()
            self._check_codex()
            self.ai_flash_enabled.setChecked(c.ai_flash_enabled)
            self._set_models(self.ai_claude_model, c.ai_claude_model)
            self._set_models(self.ai_gpt_model, c.ai_model)
            self._set_models(self.ai_flash_model, c.ai_flash_model)
            self._load_catalog()
        self.ai_model.clear()
        if p[2]:
            self.ai_model.addItem(p[2])
        self.ai_model.setEditText(c.ai_model or p[2])
        self.ai_style.setCurrentIndex(max(0, self.ai_style.findData(c.ai_style)))
        self.ai_context.setChecked(c.ai_context)
        self.ai_scope.setCurrentIndex(max(0, self.ai_scope.findData(c.ai_scope)))
        self.ai_prompt.setPlainText(c.ai_prompt or llm.default_prompt())
        self._loading = was

    def _save_prompt(self):
        """The rules the user edited are saved at once; text equal to the built-in rules is stored as "" so that later
        improvements of the built-in rules reach a user who never changed them."""
        text = self.ai_prompt.toPlainText().strip()
        self.cfg.ai_prompt = "" if text == llm.default_prompt().strip() or not text else text
        self._save()
        self._warm_after_prompt()

    def _reset_prompt(self):
        self.ai_prompt.setPlainText(llm.default_prompt())
        self._save_prompt()

    def _warm_after_prompt(self):
        if self.cfg.ai_enabled:
            llm.warm_up(self._llm_settings())

    def _check_codex(self):
        if not self.ai_codex_recheck.isEnabled():
            return
        self.ai_codex_state.setText("檢查中…")
        self.ai_codex_btn.hide()
        self.ai_codex_recheck.setEnabled(False)
        threading.Thread(target=lambda: self._codex_state_ready.emit(*llm.codex_cli.login_status()), daemon=True).start()

    def _show_codex_state(self, stage: str, account: str = ""):
        self._codex_stage = stage
        self.ai_codex_recheck.setEnabled(True)
        text, button = {"ok": ("✓ 已登入：" + account if account else "✓ 已登入 ChatGPT（未提供帳號名稱）", ""),
                        "unknown": ("暫時無法確認 ChatGPT 帳號，請重新檢查", ""),
                        "out": ("尚未登入 ChatGPT，目前會使用 Claude", "登入 ChatGPT"),
                        "missing": ("找不到 Codex，目前會使用 Claude", "安裝 Codex")}[stage]
        self.ai_codex_state.setText(text)
        self.ai_codex_btn.setText(button)
        self.ai_codex_btn.setVisible(bool(button))
        if stage == "ok" and self.cfg.ai_enabled and self.cfg.ai_provider == "codex_cli":
            llm.codex_cli.retry_now()

    def _codex_action(self):
        if self._codex_stage == "missing":
            QDesktopServices.openUrl(QUrl(llm.codex_cli.INSTALL_URL))
        else:
            try:
                llm.codex_cli.login()
                self.ai_codex_state.setText("請在瀏覽器完成登入，再按「重新檢查」")
            except (llm.codex_cli.CodexError, OSError) as e:
                self.ai_codex_state.setText(str(e))

    def _check_cli(self):
        self.ai_cli_state.setText("檢查中…")
        self.ai_cli_state.setStyleSheet("")
        self.ai_cli_btn.hide()
        threading.Thread(target=lambda: self._cli_state_ready.emit(*llm.claude_status()), daemon=True).start()

    def _show_cli_state(self, stage: str, account: str = ""):
        self._cli_stage = stage
        text, btn = {"ok": ("✓ 已登入" + (f"：{account}" if account else "（已安裝）"), ""),
                     "out": ("已安裝，但還沒登入", "登入 Claude"),
                     "missing": ("還沒安裝 Claude Code", "安裝 Claude Code")}[stage]
        self.ai_cli_state.setText(text)
        self.ai_cli_state.setStyleSheet("" if stage == "ok" else "color: #c0392b")
        self.ai_cli_btn.setText(btn)
        self.ai_cli_btn.setVisible(bool(btn))

    def _cli_action(self):
        if self._cli_stage == "missing":
            QDesktopServices.openUrl(QUrl(llm.CLAUDE_INSTALL_URL))
            self.ai_cli_state.setText("安裝完成後按「重新檢查」")
        else:
            try:
                llm.claude_login()
                self.ai_cli_state.setText("請在瀏覽器完成登入，再按「重新檢查」")
            except llm.LLMError as e:
                self.ai_cli_state.setText(str(e))

    def _apply_ai(self):
        if self._loading:
            return
        c = self.cfg
        p = llm.PROVIDERS.get(c.ai_provider, llm.PROVIDERS["custom"])
        c.ai_enabled = self.ai_on.isChecked()
        c.ai_key = self.ai_key.text().strip()
        guess = llm.provider_for_key(c.ai_key)
        if guess and guess != c.ai_provider and c.ai_provider in ("groq", "gemini", "openrouter"):
            # a Gemini key pasted while Groq was selected (it happened): follow the key
            c.ai_provider, c.ai_model, c.ai_base_url = guess, "", ""
            self._load_ai()
            self.ai_out.setText(f"已依金鑰切換到 {llm.PROVIDERS[guess][0]}")
            self._save()
            return
        url = self.ai_url.text().strip()
        c.ai_base_url = "" if url == p[1] else url
        if c.ai_provider == "codex_cli":
            c.ai_model = self.ai_gpt_model.currentData() or ""
            c.ai_claude_model = self.ai_claude_model.currentData() or ""
            c.ai_flash_model = self.ai_flash_model.currentData() or ""
            c.ai_flash_enabled = self.ai_flash_enabled.isChecked()
            self._flash_model_row.setVisible(c.ai_flash_enabled)      # the model menu only matters when Flash is on
        else:
            model = self.ai_model.currentText().strip()
            c.ai_model = "" if model == p[2] else model
        c.ai_style = self.ai_style.currentData()
        c.ai_context = self.ai_context.isChecked()
        c.ai_scope = self.ai_scope.currentData()
        if c.ai_enabled and not self._llm_settings().ready():
            self.ai_out.setText("⚠ 請先安裝並登入 Claude Code、Antigravity 或 Codex 其中一個。" if c.ai_provider == "codex_cli"
                                else "⚠ 還需要 API 金鑰（或網址 / 模型）才能使用。")
        self._save()

    def _open_key_page(self):
        """Open the service's key page in the browser (sign in there, with Chrome and the Google account) and watch the
        clipboard for the key that is copied (2026-10-14, the user: 按一個鍵、登入 Google 帳號就能用)."""
        url = llm.PROVIDERS.get(self.cfg.ai_provider, llm.PROVIDERS["custom"])[3]
        if url:
            QDesktopServices.openUrl(QUrl(url))
        self._watch_clipboard()

    def _watch_clipboard(self):
        pattern = llm.KEY_PATTERNS.get(self.cfg.ai_provider)
        if not pattern:
            return
        self._clip_pattern = re.compile(pattern)
        self._clip_start = QGuiApplication.clipboard().text() or ""     # only a NEW copy counts
        self._clip_until = time.monotonic() + 300
        self.ai_out.setStyleSheet("")
        self.ai_out.setText("已打開登入頁。登入後按「建立 API 金鑰」（Google 請選「在新專案中建立」），再按複製，"
                            "程式會自動接手設定。")
        if not hasattr(self, "_clip_timer"):
            self._clip_timer = QTimer(self, interval=600, timeout=self._poll_clipboard)
        self._clip_timer.start()

    def _poll_clipboard(self):
        if time.monotonic() > self._clip_until:
            self._clip_timer.stop()
            return
        text = QGuiApplication.clipboard().text() or ""
        m = self._clip_pattern.search(text) if text != self._clip_start else None
        if m is None:
            return
        self._clip_timer.stop()
        self.ai_key.setText(m.group(0))
        self.ai_on.setChecked(True)
        self._apply_ai()
        self.ai_out.setText("已收到金鑰，測試中…")
        self._test_ai()

    def _test_ai(self):
        self._apply_ai()
        s = self._llm_settings()
        if not s.ready():
            self.ai_out.setText("⚠ 請先填入 API 金鑰（或網址 / 模型）。")
            return
        self.ai_test_btn.setEnabled(False)
        self.ai_out.setText("整理中…")
        text = self.ai_sample.text().strip() or _SAMPLE

        def run():
            t0 = time.monotonic()
            try:
                out = llm.polish(s, text, llm.Context(), [w for w, _ in self.hotwords.bias()])
                self._ai_result.emit(f"{out}\n\n（{llm.result_label(s)} · {time.monotonic() - t0:.1f} 秒）", True)
            except llm.LLMError as e:
                self._ai_result.emit(f"失敗：{e}", False)
        threading.Thread(target=run, daemon=True).start()

    def _show_ai_result(self, text: str, ok: bool):
        self.ai_test_btn.setEnabled(True)
        self.ai_out.setStyleSheet("" if ok else "color: #c0392b")
        self.ai_out.setText(text)

    def _list_models(self):
        self._apply_ai()
        s = self._llm_settings()
        self.ai_models_btn.setEnabled(False)

        def run():
            try:
                self._models_ready.emit(llm.list_models(s), "")
            except llm.LLMError as e:
                self._models_ready.emit([], str(e))
        threading.Thread(target=run, daemon=True).start()

    def _fill_models(self, models: list, error: str):
        self.ai_models_btn.setEnabled(True)
        if error:
            self.ai_out.setStyleSheet("color: #c0392b")
            self.ai_out.setText(f"取得模型清單失敗：{error}")
            return
        current = self.ai_model.currentText()
        was, self._loading = self._loading, True
        self.ai_model.clear()
        self.ai_model.addItems(models)
        self.ai_model.setEditText(current)
        self._loading = was
        self.ai_model.showPopup()

    # --- 設定 ---
    def _general_page(self) -> QWidget:
        page, col = self._page("設定")
        self.mic = QComboBox()
        self.mic.setMinimumWidth(240)
        self.mic.addItem("系統預設", "")
        for name in audio.list_input_devices():
            self.mic.addItem(name, name)
        self.punct = Switch()
        self.period_comma = Switch()
        self.autostart = Switch()
        self.mute = Switch()
        self.mic_exclusive = Switch()
        self.discord_on = Switch()
        self.voice_stop = Switch()
        self.mouse_text_only = Switch()
        self.discord_key = KeyCapture(self.cfg.discord_key)
        self.discord_key.setMinimumWidth(150)
        self.discord_key.captured.connect(self._apply)
        self.discord_f24 = QPushButton("5 秒後送出 F24", clicked=self._send_f24)
        self.discord_f24.setToolTip("鍵盤沒有 F24：按這裡後 5 秒內切到 Discord 的快捷鍵錄製欄位，VoiceInput 會替你按 F24")
        discord_box = QWidget()
        dh = QHBoxLayout(discord_box)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.addWidget(self.discord_key)
        dh.addWidget(self.discord_f24)
        self.describe = Switch()
        self.casual = Switch()
        self.laugh_on = Switch()
        self.laugh_text = QLineEdit(placeholderText="例如 哈哈、XD、😂")
        self.laugh_text.setFixedWidth(160)
        self.copy_key = KeyCapture(self.cfg.copy_key)
        self.copy_key.setMinimumWidth(150)
        self.copy_key.captured.connect(self._apply)

        col.addWidget(_label("聲音", "section"))
        col.addWidget(_card(
            _setting_row("麥克風", "", self.mic),
            _setting_row("滑鼠按鍵只在輸入框上有效", "只有游標在打字的輸入框上按滑鼠快捷鍵（例如中鍵）才會錄音；"
                         "在網頁、檔案、桌面按中鍵，照原本的功能運作，不受影響", self.mouse_text_only),
            _setting_row("說「送出」自動結束錄音", "錄音時說完話、停頓一下再說「送出」，會自動結束錄音、打出文字並送出，"
                         "不用再按鍵（錄音時多一點點耗電）", self.voice_stop),
            _setting_row("錄音時靜音電腦", "錄音期間把喇叭靜音，文字輸入完成後恢復；原本就靜音的話不會動", self.mute),
            _setting_row("錄音時讓 Discord 收不到", "開始錄音時按一次 Discord 的快捷鍵，結束時再按一次。"
                         "Discord 要設成全域快捷鍵（使用者設定 → 快捷鍵）", self.discord_on),
            _setting_row("Discord 的快捷鍵", "建議用 F24（鍵盤上沒有，其他程式都不會有反應）：在 Discord 的「使用者設定 → "
                         "快捷鍵 → 新增快捷鍵」選「切換靜音」，按右邊的按鈕，5 秒內點 Discord 的錄製欄位", discord_box)))
        col.addWidget(_label("輸入", "section"))
        col.addWidget(_card(
            _setting_row("說標點符號名稱時輸入符號", "整句只說「逗號、句號、問號、驚嘆號、頓號、分號、冒號、點點點…」時輸入該符號",
                         self.punct),
            _setting_row("選取句號後講話改成逗號接續", "今天很好。明天見 → 選取「。」講「我們去公園」→ 今天很好，我們去公園。明天見",
                         self.period_comma),
            _setting_row("指定字", "說「節日的節」時只輸入「節」", self.describe),
            _setting_row("聊天短句不加句號", "中文逗號少於 5 個、英文逗號 2 個以下時，句尾不加句號（問號、驚嘆號照留）",
                         self.casual),
            _setting_row("複製最後的轉錄的快捷鍵", "游標不在輸入框時，聽寫結果不會貼上，而是顯示在卡片上；按這個鍵或「複製」按鈕就複製。"
                         "點一下欄位再按新的組合鍵可以更改", self.copy_key)))
        col.addWidget(_label("實驗功能", "section"))
        col.addWidget(_card(
            _setting_row("聽到笑聲時自動加字", "用 SenseVoice 分辨笑聲（開啟時多用約 150MB 記憶體，準確度還在測試）。"
                         "只笑一聲就輸入下面的字，說完話帶著笑就加在句尾", self.laugh_on),
            _setting_row("笑聲要輸入的字", "留空就不加", self.laugh_text)))

        col.addWidget(_label("同步", "section"))
        self.sync_path = _label("", "muted", wrap=True)
        pick = QPushButton("選擇資料夾", clicked=self._pick_sync)
        self.sync_reset = QPushButton("改回本機", objectName="ghost", clicked=lambda: self._set_sync(""))
        sync_box = QWidget()
        sh = QHBoxLayout(sync_box)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.addWidget(self.sync_reset)
        sh.addWidget(pick)
        sync_row = _setting_row("同步資料夾", "選 OneDrive / Google 雲端硬碟 / Dropbox 裡的資料夾，字典、智慧選字和歷史紀錄"
                                "會在各台電腦間共用", sync_box)
        sync_row.layout().itemAt(0).layout().addWidget(self.sync_path)
        col.addWidget(_card(sync_row))

        col.addWidget(_label("外觀", "section"))
        self.theme = QComboBox()
        self.theme.setMinimumWidth(200)
        for key, (label, _) in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.currentIndexChanged.connect(lambda _: self._apply_theme())
        col.addWidget(_card(_setting_row("配色", "主視窗的顏色，選了馬上套用", self.theme)))

        col.addWidget(_label("系統", "section"))
        col.addWidget(_card(_setting_row("開機時自動啟動", "", self.autostart)))
        col.addStretch(1)

        self.mic.currentIndexChanged.connect(self._apply)
        for box in (self.punct, self.period_comma, self.describe, self.casual, self.autostart, self.mute, self.mic_exclusive, self.discord_on, self.voice_stop, self.mouse_text_only,
                    self.laugh_on):
            box.toggled.connect(self._apply)
        self.laugh_text.editingFinished.connect(self._apply)
        return page

    def _apply_theme(self):
        if self._loading:
            return
        self.cfg.theme = self.theme.currentData()
        set_theme(self.cfg.theme)
        self.setStyleSheet(_qss())
        for key, b in self._nav_buttons.items():
            b.setIcon(_nav_icon(key))
        self.refresh_hotwords()          # its bars carry colours of their own
        for w in self.findChildren(QWidget):
            w.update()
        self._save()

    def _send_f24(self):
        """Discord records a shortcut from a key press, and no keyboard has F24: count down, then press it."""
        self._f24_left = 5

        def tick():
            if self._f24_left > 0:
                self.discord_f24.setText(f"{self._f24_left} 秒後送出 F24…")
                self.discord_f24.setEnabled(False)
                self._f24_left -= 1
                QTimer.singleShot(1000, tick)
                return
            from .output import press_chord
            press_chord(0x87)                       # VK_F24
            self.discord_f24.setText("已送出 F24")
            self.discord_f24.setEnabled(True)
            QTimer.singleShot(2500, lambda: self.discord_f24.setText("5 秒後送出 F24"))
            self.discord_key.setText("F24")         # VoiceInput presses the same key from now on
            self._apply()
        tick()

    def _pick_sync(self):
        start = self.cfg.sync_dir or os.environ.get("OneDrive") or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self, "選擇同步資料夾", start)
        if d:
            self._set_sync(os.path.join(os.path.normpath(d), "VoiceInput")
                           if os.path.basename(os.path.normpath(d)).lower() != "voiceinput" else os.path.normpath(d))

    def _set_sync(self, path: str):
        if path == self.cfg.sync_dir:
            return
        self.cfg.sync_dir = path
        self.cfg.save()
        self._show_sync()
        self.sync_changed.emit()

    def _show_sync(self):
        self.sync_path.setText(f"目前：{self.cfg.sync_dir}" if self.cfg.sync_dir else "目前：只存在這台電腦")
        self.sync_reset.setVisible(bool(self.cfg.sync_dir))

    def load_values(self):
        self._loading = True
        self.mic.setCurrentIndex(max(0, self.mic.findData(self.cfg.mic)))
        self.punct.setChecked(self.cfg.punctuation_names)
        self.period_comma.setChecked(self.cfg.period_to_comma)
        self.autostart.setChecked(self.cfg.autostart)
        self.mute.setChecked(self.cfg.mute_while_recording)
        self.mic_exclusive.setChecked(self.cfg.mic_exclusive)
        self.discord_on.setChecked(self.cfg.discord_mute)
        self.voice_stop.setChecked(self.cfg.voice_stop)
        self.mouse_text_only.setChecked(self.cfg.mouse_text_only)
        self.discord_key.setText(self.cfg.discord_key)
        self.describe.setChecked(self.cfg.describe_char)
        self.casual.setChecked(self.cfg.casual_no_period)
        self.laugh_on.setChecked(self.cfg.laugh_enabled)
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.cfg.theme)))
        self.laugh_text.setText(self.cfg.laugh_text)
        self.copy_key.setText(self.cfg.copy_key)
        self._load_ai()
        self._show_sync()
        self._loading = False
        self.refresh_rules()
        self.refresh_hotwords()
        self.refresh_history()

    def set_status(self, text: str):
        self._status_text = text
        self._refresh_data_errors()

    def _apply(self):
        if self._loading:
            return
        self.cfg.mic = self.mic.currentData()
        self.cfg.punctuation_names = self.punct.isChecked()
        self.cfg.period_to_comma = self.period_comma.isChecked()
        self.cfg.autostart = self.autostart.isChecked()
        self.cfg.mute_while_recording = self.mute.isChecked()
        self.cfg.mic_exclusive = self.mic_exclusive.isChecked()
        self.cfg.discord_mute = self.discord_on.isChecked()
        self.cfg.voice_stop = self.voice_stop.isChecked()
        self.cfg.mouse_text_only = self.mouse_text_only.isChecked()
        self.cfg.discord_key = self.discord_key.text() or "Ctrl+Shift+D"
        self.cfg.describe_char = self.describe.isChecked()
        self.cfg.casual_no_period = self.casual.isChecked()
        self.cfg.laugh_enabled = self.laugh_on.isChecked()
        self.cfg.laugh_text = self.laugh_text.text().strip()
        self.cfg.copy_key = self.copy_key.text() or "Enter"
        self._save()

    def _save(self) -> bool:
        try:
            self.cfg.save()
        except Exception as exc:
            self.save_status.setText(f"設定未能儲存：{exc}")
            self.save_status.setStyleSheet("color: #b00020")
            self._adapt()
            return False
        self.save_status.setText("")
        self._adapt()
        self.applied.emit()
        return True

    # --- 自訂操作: one card per rule ---
    def _rules_page(self) -> QWidget:
        add = QPushButton("＋ 新增操作", objectName="primary", clicked=self._add_rule)
        reset = QPushButton("還原預設", clicked=self._reset_rules)
        top = QHBoxLayout()
        top.addWidget(add)
        top.addStretch()
        top.addWidget(reset)

        self.cards = QVBoxLayout()
        self.cards.setSpacing(6)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.cards)
        v.addStretch(1)
        self.rules_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.rules_scroll.setWidget(inner)

        page, layout = self._page("自訂操作", "設定滑鼠鍵、鍵盤鍵或說出的話要做什麼：錄音、選字、刪除、按鍵、輸入文字。",
                                  scroll=False)
        layout.addLayout(top)
        layout.addSpacing(8)
        layout.addWidget(self.rules_scroll, 1)
        return page

    def refresh_rules(self):
        bar = self.rules_scroll.verticalScrollBar()
        pos = bar.value()
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            w.setParent(None)
            w.deleteLater()
        self._problem_labels = []
        for rule in self.cfg.rules:
            self.cards.addWidget(self._rule_card(rule))
        self._show_problems()
        bar.setValue(pos)

    def _changed(self, rebuild: bool = False):
        """A rule was edited: save, and redraw the cards when their layout changes."""
        self._save()
        if rebuild:
            self.refresh_rules()
        else:
            self._show_problems()

    def _show_problems(self):
        for rule, label in self._problem_labels:
            text = "、".join(rules.problems(rule, self.cfg.rules))
            label.setText(f"⚠ {text}" if text else "")
            label.setVisible(bool(text))

    def _rule_card(self, rule: Rule) -> QFrame:
        card = QFrame(objectName="card")
        card.setStyleSheet(_CARD_STYLE)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 10, 10)
        v.setSpacing(4)

        enabled = Switch(rule.enabled)
        enabled.setToolTip("啟用")
        enabled.toggled.connect(lambda on: (setattr(rule, "enabled", on), self._changed()))
        source = _combo(SOURCES, rule.source, "觸發方式")
        source.currentIndexChanged.connect(lambda _: self._set_source(rule, source.currentData()))
        head = QHBoxLayout()
        head.addWidget(enabled)
        head.addWidget(source)
        if rule.source == rules.SPEECH:
            phrase = QLineEdit(rule.phrase, placeholderText="完整說出的話，例如：送出")
            phrase.setToolTip("整句只說這些話時才執行（標點、空白不計）")
            phrase.textEdited.connect(lambda t: (setattr(rule, "phrase", t.strip()), self._changed()))
            head.addWidget(phrase, 1)
        else:
            names = MOUSE_TRIGGERS.items() if rule.source == rules.MOUSE else (
                (k, label) for k, (label, _) in KEY_TRIGGERS.items())
            key = _combo(names, rule.key)
            key.currentIndexChanged.connect(lambda _: (setattr(rule, "key", key.currentData()), self._changed()))
            gesture = _combo(GESTURES, rule.gesture, "按法")
            gesture.currentIndexChanged.connect(
                lambda _: (setattr(rule, "gesture", gesture.currentData()), self._changed()))
            head.addWidget(key, 1)
            head.addWidget(gesture)
        when = _combo(SITUATIONS, rule.when, "情境：什麼時候才執行")
        when.currentIndexChanged.connect(lambda _: (setattr(rule, "when", when.currentData()), self._changed()))
        head.addWidget(when)
        remove = QPushButton("✕", objectName="ghost", toolTip="刪除這條操作", clicked=lambda: self._remove_rule(rule))
        head.addWidget(remove)
        v.addLayout(head)

        for i, step in enumerate(rule.steps):
            v.addLayout(self._step_row(rule, i, step))
        add = QPushButton("＋ 新增步驟", objectName="ghost", clicked=lambda: self._add_step(rule))
        add_row = QHBoxLayout()
        add_row.addSpacing(24)
        add_row.addWidget(add)
        add_row.addStretch()
        v.addLayout(add_row)

        problem = QLabel()
        problem.setStyleSheet("color: #d9534f")
        problem.setWordWrap(True)
        v.addWidget(problem)
        self._problem_labels.append((rule, problem))
        return card

    def _step_row(self, rule: Rule, i: int, step: Step) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QLabel(f"{i + 1}."))
        kind = _combo(STEP_TYPES, step.type)
        kind.currentIndexChanged.connect(lambda _: self._set_step_type(step, kind.currentData()))
        row.addWidget(kind)
        if step.type == rules.COMMAND:
            value = _combo(COMMANDS, step.value)
            value.currentIndexChanged.connect(lambda _: self._set_value(step, value.currentData()))
        elif step.type == rules.KEYS:
            value = KeyCapture(step.value)
            value.captured.connect(lambda t: self._set_value(step, t))
        elif step.type == rules.TEXT:
            value = QLineEdit(step.value, placeholderText="要輸入的文字")
            value.textEdited.connect(lambda t: self._set_value(step, t))
        else:
            value = _NoWheelSpin(minimum=0, maximum=10000, singleStep=50, suffix=" 毫秒", value=int(step.value or 0))
            value.valueChanged.connect(lambda n: self._set_value(step, n))
        row.addWidget(value, 1)
        for text, tip, delta in (("↑", "上移", -1), ("↓", "下移", 1)):
            b = QPushButton(text, objectName="ghost", toolTip=tip,
                            clicked=lambda _=False, d=delta: self._move_step(rule, i, d))
            b.setEnabled(0 <= i + delta < len(rule.steps))
            row.addWidget(b)
        remove = QPushButton("✕", objectName="ghost", toolTip="刪除這個步驟", clicked=lambda: self._remove_step(rule, i))
        row.addWidget(remove)
        return row

    def _set_source(self, rule: Rule, source: str):
        rule.source = source
        if source == rules.MOUSE and rule.key not in MOUSE_TRIGGERS:
            rule.key = next(iter(MOUSE_TRIGGERS))
        elif source == rules.KEY and rule.key not in KEY_TRIGGERS:
            rule.key = next(iter(KEY_TRIGGERS))
        self._changed(rebuild=True)

    def _set_step_type(self, step: Step, kind: str):
        step.type, step.value = kind, _STEP_DEFAULTS[kind]
        self._changed(rebuild=True)

    def _set_value(self, step: Step, value):
        step.value = value
        self._changed()

    def _move_step(self, rule: Rule, i: int, delta: int):
        j = i + delta
        rule.steps[i], rule.steps[j] = rule.steps[j], rule.steps[i]
        self._changed(rebuild=True)

    def _remove_step(self, rule: Rule, i: int):
        del rule.steps[i]
        self._changed(rebuild=True)

    def _add_step(self, rule: Rule):
        rule.steps.append(Step(rules.KEYS, ""))
        self._changed(rebuild=True)

    def _add_rule(self):
        self.cfg.rules.append(Rule(rules.MOUSE, "mouse_x2", gesture=rules.TAP, steps=[Step(rules.KEYS, "")]))
        self._changed(rebuild=True)
        bar = self.rules_scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _remove_rule(self, rule: Rule):
        self.cfg.rules.remove(rule)
        self._changed(rebuild=True)

    def _reset_rules(self):
        if QMessageBox.question(self, "還原預設", "把自訂操作還原成預設？目前的設定會被取代。") == QMessageBox.Yes:
            self.cfg.rules = rules.defaults()
            self._changed(rebuild=True)

