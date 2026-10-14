"""Insert text into the focused app: put it on the clipboard, send paste, restore clipboard. Plus Delete (deleting
a selection), Backspace (deleting what was just dictated) and key combinations (custom operations). The clipboard
and keystrokes themselves are win/keyboard.py or mac/keyboard.py; keys are named by Windows virtual-key codes on
both (keys.py)."""
import sys
import threading
import time

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtGui import QGuiApplication

if sys.platform == "darwin":
    from .mac import keyboard as _native
else:
    from .win import keyboard as _native

# A paste is read by the target app some time after its Ctrl+V. The text used to be put on the clipboard by Qt, which
# hands it over lazily, through our main thread: a second paste right after the first (a draft's commit, then the
# spoken 句號, 84ms apart) replaced it before Windows Terminal had read it, and the whole draft was typed as 。
# (2026-10-14). Now the text is on the clipboard itself, and the next paste or key waits _GAP after a Ctrl+V.
_GAP = 0.15
_lock = threading.Lock()
_pasted_at = 0.0
_original = None          # the clipboard before a run of pastes, put back after the last one
_restore_timer = None


def _settle() -> None:
    """Wait until the last paste had _GAP to be read (any thread)."""
    with _lock:
        wait = _pasted_at + _GAP - time.monotonic()
    if wait > 0:
        time.sleep(wait)


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
    if not _native.set_clipboard(text):
        cb.setText(text)
    time.sleep(0.03)  # let the clipboard settle before pasting
    _native.send_paste()
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
    _native.send_delete()


ARROWS = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}


def press_key(vk: int, n: int) -> None:
    """Press a key n times, as one burst, before the paste that usually follows."""
    if n <= 0:
        return
    _settle()
    _native.send_keys(vk, n)


def press_chord(*vks: int) -> None:
    """Press keys together (Shift+Enter: press_chord(0x10, 0x0D))."""
    _settle()
    _native.send_chord(vks)


def backspace(n: int, gap: float = 0.0) -> None:
    """gap: seconds between presses. Codex in a terminal dropped one of a burst of Backspaces after some
    text (abc_ascii, 2026-10-02); 20ms apart none were lost."""
    if not gap:
        press_key(0x08, n)   # VK_BACK
        return
    for _ in range(n):
        press_key(0x08, 1)
        time.sleep(gap)
