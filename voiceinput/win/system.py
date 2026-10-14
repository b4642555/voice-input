"""Windows: windows, focus, sounds and the like (system.py picks this module or mac/system.py)."""
import ctypes
import ctypes.wintypes as wt
import winsound

from PySide6.QtCore import QPoint
from PySide6.QtGui import QGuiApplication

_GWL_EXSTYLE, _WS_EX_NOACTIVATE, _WS_EX_TRANSPARENT, _WS_EX_TOPMOST = -20, 0x08000000, 0x20, 0x8
_u32 = ctypes.windll.user32
_u32.WindowFromPoint.argtypes = [wt.POINT]

SOUND_FLAGS = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT


def setup(qapp) -> None:
    """Once the QApplication exists."""


def permissions(notify) -> None:
    """Nothing to ask for on Windows."""


def notify(text: str) -> bool:
    """False: the tray icon shows notices on Windows."""
    return False


def foreground() -> int:
    """The window in front."""
    return _u32.GetForegroundWindow()


def window_handle(widget) -> int:
    return int(widget.winId())


def foreground_is(handle: int) -> bool:
    """The window `handle` (window_handle) is in front: keys typed now are meant for it."""
    return _u32.GetForegroundWindow() == handle


def point_on(handle: int, x: int, y: int) -> bool:
    """The screen point (physical pixels, as the hooks report it) is on the window `handle` (hook threads)."""
    hwnd = _u32.WindowFromPoint(wt.POINT(x, y))
    return bool(hwnd) and _u32.GetAncestor(hwnd, 2) == handle   # GA_ROOT


def click_through(widget) -> None:
    """The indicator and the glow: never activated, clicks pass through, above other windows."""
    hwnd = int(widget.winId())
    style = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TRANSPARENT | _WS_EX_TOPMOST)


def no_activate(widget) -> None:
    """The 選字 menu: clicks do not take focus from the app it was opened in."""
    hwnd = int(widget.winId())
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
                        | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST)


def take_focus(widget):
    """The 選字 menu's input box was clicked: the menu takes focus so an input method works. Returns what to give
    focus back to (give_focus)."""
    hwnd = int(widget.winId())
    prev = _u32.GetForegroundWindow()
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & ~_WS_EX_NOACTIVATE)
    _u32.SetForegroundWindow(hwnd)
    return prev


def give_focus(prev) -> None:
    if prev:
        _u32.SetForegroundWindow(prev)


def bring_to_front(widget) -> None:
    """The settings window, opened from the tray."""
    widget.raise_()
    widget.activateWindow()


def to_logical(x: int, y: int) -> QPoint:
    """UI Automation's physical pixels as Qt's logical ones. Qt keeps each screen's origin in device pixels and
    scales only within the screen."""
    for s in QGuiApplication.screens():
        g, r = s.geometry(), s.devicePixelRatio()
        if g.x() <= x < g.x() + g.width() * r and g.y() <= y < g.y() + g.height() * r:
            return QPoint(g.x() + int((x - g.x()) / r), g.y() + int((y - g.y()) / r))
    return QPoint(int(x), int(y))


def dark_menu_bar() -> bool:
    """The taskbar is dark (the tray icon is drawn light)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 0
    except OSError:
        return True


def play_sound(path: str) -> None:
    """Asynchronous; a new sound cuts the one playing. From the file: winsound cannot play asynchronously from
    memory (RuntimeError)."""
    winsound.PlaySound(path, SOUND_FLAGS)


def key_of(event) -> int:
    """A Qt key event's key as a Windows virtual-key code (keys.py)."""
    return event.nativeVirtualKey()
