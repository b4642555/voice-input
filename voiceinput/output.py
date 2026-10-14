"""Insert text into the focused app: put it on the clipboard, send paste, restore clipboard. Plus Delete (deleting
a selection), Backspace (deleting what was just dictated) and key combinations (custom operations)."""
import ctypes
import ctypes.wintypes as wt
import threading
import time

from pynput import keyboard
from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtGui import QGuiApplication

from .keys import EXTENDED

_kb = keyboard.Controller()

# A paste is read by the target app some time after its Ctrl+V. The text used to be put on the clipboard by Qt, which
# hands it over lazily, through our main thread: a second paste right after the first (a draft's commit, then the
# spoken 句號, 84ms apart) replaced it before Windows Terminal had read it, and the whole draft was typed as 。
# (2026-10-14). Now the text is on the clipboard itself, and the next paste or key waits _GAP after a Ctrl+V.
_GAP = 0.15
_lock = threading.Lock()
_pasted_at = 0.0
_original = None          # the clipboard before a run of pastes, put back after the last one
_restore_timer = None

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32.OpenClipboard.argtypes = [wt.HWND]
_u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
_u32.SetClipboardData.restype = wt.HANDLE
_u32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
_u32.CreateWindowExW.restype = wt.HWND
_k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_k32.GlobalAlloc.restype = wt.HGLOBAL
_k32.GlobalLock.argtypes = [wt.HGLOBAL]
_k32.GlobalLock.restype = wt.LPVOID
_k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
_k32.GlobalFree.argtypes = [wt.HGLOBAL]
_owner = None             # a hidden window owning what we put on the clipboard (SetClipboardData needs one)


def _settle() -> None:
    """Wait until the last paste had _GAP to be read (any thread)."""
    with _lock:
        wait = _pasted_at + _GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def _set_clipboard(text: str) -> bool:
    global _owner
    if _owner is None:
        _owner = _u32.CreateWindowExW(0, "STATIC", "VoiceInput clipboard", 0, 0, 0, 0, 0, None, None, None, None)
    for _ in range(20):
        if _u32.OpenClipboard(_owner):
            break
        time.sleep(0.01)
    else:
        return False
    try:
        _u32.EmptyClipboard()
        data = ctypes.create_unicode_buffer(text)
        h = _k32.GlobalAlloc(0x0002, ctypes.sizeof(data))   # GMEM_MOVEABLE
        p = _k32.GlobalLock(h)
        ctypes.memmove(p, data, ctypes.sizeof(data))
        _k32.GlobalUnlock(h)
        if not _u32.SetClipboardData(13, h):   # CF_UNICODETEXT; the clipboard owns h once this succeeds
            _k32.GlobalFree(h)
            return False
        return True
    finally:
        _u32.CloseClipboard()


def _snapshot(cb) -> QMimeData | None:
    """A copy of every format on the clipboard, so an image or rich text copied before dictating survives
    (only the plain text used to be restored)."""
    md = cb.mimeData()
    if md is None:
        return None
    copy = QMimeData()
    for fmt in md.formats():
        copy.setData(fmt, md.data(fmt))
    if md.hasImage():
        copy.setImageData(md.imageData())
    return copy


def paste_text(text: str) -> None:
    """Must be called on the Qt main thread."""
    global _pasted_at, _original, _restore_timer
    if not text:
        return
    _settle()
    cb = QGuiApplication.clipboard()
    if _restore_timer is not None and _restore_timer.isActive():
        _restore_timer.stop()   # pasted again before the clipboard was put back: it still holds our text
    else:
        try:
            snap = _snapshot(cb)
        except Exception:
            snap = None
        _original = (snap, cb.text())
    if not _set_clipboard(text):
        cb.setText(text)
    time.sleep(0.03)  # let the clipboard settle before pasting
    with _kb.pressed(keyboard.Key.ctrl):
        _kb.press("v")
        _kb.release("v")
    with _lock:
        _pasted_at = time.monotonic()

    def restore():
        previous, previous_text = _original
        if previous is not None and previous.formats():
            cb.setMimeData(previous)
        else:
            cb.setText(previous_text)
    # Restore after the target app has had time to read the clipboard
    if _restore_timer is None:
        _restore_timer = QTimer(singleShot=True, interval=400)
    try:
        _restore_timer.timeout.disconnect()
    except (RuntimeError, TypeError):
        pass
    _restore_timer.timeout.connect(restore)
    _restore_timer.start()


def press_delete() -> None:
    """Delete the target app's current selection."""
    _settle()
    _kb.press(keyboard.Key.delete)
    _kb.release(keyboard.Key.delete)


ARROWS = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def press_key(vk: int, n: int) -> None:
    """Press a key n times. All presses go out in one SendInput call so the app receives them
    as one burst, before the paste that usually follows."""
    if n <= 0:
        return
    _settle()
    ext = 1 if vk in EXTENDED else 0   # KEYEVENTF_EXTENDEDKEY: the arrow keys, not the keypad's
    events = (_INPUT * (2 * n))()
    for i in range(2 * n):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, ext | (2 if i % 2 else 0), 0, None)   # KEYEVENTF_KEYUP on odd
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def press_chord(*vks: int) -> None:
    """Press keys together (Shift+Enter: press_chord(0x10, 0x0D)) in one SendInput call."""
    _settle()
    events = (_INPUT * (2 * len(vks)))()
    for i, (vk, up) in enumerate([(vk, 0) for vk in vks] + [(vk, 2) for vk in reversed(vks)]):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, up | (1 if vk in EXTENDED else 0), 0, None)
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def backspace(n: int, gap: float = 0.0) -> None:
    """gap: seconds between presses. Codex in a terminal dropped one of a burst of Backspaces after some
    text (abc_ascii, 2026-10-02); 20ms apart none were lost."""
    if not gap:
        press_key(0x08, n)   # VK_BACK
        return
    for _ in range(n):
        press_key(0x08, 1)
        time.sleep(gap)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MOUSEINPUT(ctypes.Structure):   # only sizes the union like the real INPUT
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _UNION)]
