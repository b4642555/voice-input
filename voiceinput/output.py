"""Insert text into the focused app: put it on the clipboard, send paste, restore clipboard. Plus Delete (deleting
a selection), Backspace (deleting what was just dictated) and key combinations (custom operations)."""
import ctypes
import ctypes.wintypes as wt
import time

from pynput import keyboard
from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtGui import QGuiApplication

from .keys import EXTENDED

# Identify our SendInput events so hotkeys can accept other apps' remapped keys without feedback.
INPUT_TAG = 0x56494E50

_kb = keyboard.Controller()


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
    if not text:
        return
    cb = QGuiApplication.clipboard()
    try:
        previous = _snapshot(cb)
    except Exception:
        previous = None
    previous_text = cb.text()
    cb.setText(text)
    time.sleep(0.03)  # let the clipboard owner settle before pasting
    with _kb.pressed(keyboard.Key.ctrl):
        _kb.press("v")
        _kb.release("v")

    def restore():
        if previous is not None and previous.formats():
            cb.setMimeData(previous)
        else:
            cb.setText(previous_text)
    # Restore after the target app has had time to read the clipboard
    QTimer.singleShot(400, restore)


def press_delete() -> None:
    """Delete the target app's current selection."""
    _kb.press(keyboard.Key.delete)
    _kb.release(keyboard.Key.delete)


ARROWS = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def press_key(vk: int, n: int) -> None:
    """Press a key n times. All presses go out in one SendInput call so the app receives them
    as one burst, before the paste that usually follows."""
    if n <= 0:
        return
    ext = 1 if vk in EXTENDED else 0   # KEYEVENTF_EXTENDEDKEY: the arrow keys, not the keypad's
    events = (_INPUT * (2 * n))()
    for i in range(2 * n):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, ext | (2 if i % 2 else 0), 0, INPUT_TAG)   # KEYEVENTF_KEYUP on odd
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def press_chord(*vks: int) -> None:
    """Press keys together (Shift+Enter: press_chord(0x10, 0x0D)) in one SendInput call."""
    events = (_INPUT * (2 * len(vks)))()
    for i, (vk, up) in enumerate([(vk, 0) for vk in vks] + [(vk, 2) for vk in reversed(vks)]):
        events[i].type = 1   # INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(vk, 0, up | (1 if vk in EXTENDED else 0), 0, INPUT_TAG)
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
                ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSEINPUT(ctypes.Structure):   # only sizes the union like the real INPUT
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _UNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _UNION)]


def key_down(*vks: int) -> None:
    """Hold keys down (released by key_up): Discord's 按住靜音 is held for the whole recording."""
    events = (_INPUT * len(vks))()
    for i, vk in enumerate(vks):
        events[i].type = 1
        events[i].ki = _KEYBDINPUT(vk, 0, 1 if vk in EXTENDED else 0, 0, INPUT_TAG)
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))


def key_up(*vks: int) -> None:
    events = (_INPUT * len(vks))()
    for i, vk in enumerate(reversed(vks)):
        events[i].type = 1
        events[i].ki = _KEYBDINPUT(vk, 0, 2 | (1 if vk in EXTENDED else 0), 0, INPUT_TAG)
    ctypes.windll.user32.SendInput(len(events), events, ctypes.sizeof(_INPUT))
