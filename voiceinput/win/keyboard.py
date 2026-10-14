"""Windows: the clipboard (Win32, owned by a hidden window) and keystrokes (SendInput), by virtual-key code."""
import ctypes
import ctypes.wintypes as wt
import time

from pynput import keyboard

from ..keys import EXTENDED

_kb = keyboard.Controller()

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


def set_clipboard(text: str) -> bool:
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


def send_paste() -> None:
    """Ctrl+V."""
    with _kb.pressed(keyboard.Key.ctrl):
        _kb.press("v")
        _kb.release("v")


def send_keys(vk: int, n: int) -> None:
    """Press a key n times. All presses go out in one SendInput call so the app receives them
    as one burst, before the paste that usually follows."""
    ext = 1 if vk in EXTENDED else 0   # KEYEVENTF_EXTENDEDKEY: the arrow keys, not the keypad's
    events = (_INPUT * (2 * n))()
    for i in range(2 * n):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, ext | (2 if i % 2 else 0), 0, None)   # KEYEVENTF_KEYUP on odd
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def send_chord(vks) -> None:
    """Press keys together (Shift+Enter: (0x10, 0x0D)) in one SendInput call."""
    events = (_INPUT * (2 * len(vks)))()
    for i, (vk, up) in enumerate([(vk, 0) for vk in vks] + [(vk, 2) for vk in reversed(vks)]):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, up | (1 if vk in EXTENDED else 0), 0, None)
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def send_delete() -> None:
    """The Delete key."""
    _kb.press(keyboard.Key.delete)
    _kb.release(keyboard.Key.delete)


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
