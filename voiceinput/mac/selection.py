"""macOS: the selected text, the caret's line and the visible text of the focused app through the Accessibility API
(the Windows version uses UI Automation, win/selection.py).

Chromium browsers and Electron apps build their accessibility tree only when asked: AXManualAccessibility (Electron)
and, for Chromium browsers, AXEnhancedUserInterface are set on an app the first time it is seen.

Terminals: Terminal.app and iTerm2 expose their mouse selection through AXSelectedText, but a paste goes to the
shell's cursor, not over the selection. To replace it the cursor is moved right after it with Left / Right (never
Up / Down: in a shell they recall history) while the terminal reports the cursor as an empty selected range, and
Backspace removes it; when the cursor cannot be read or is on another line nothing is deleted (the app says so).
Not tried on a real Mac yet (2026-10-15): the logs say how far it got."""
import logging
import threading
import time

from ApplicationServices import (AXUIElementCopyAttributeValue, AXUIElementCopyParameterizedAttributeValue,
                                 AXUIElementCreateApplication, AXUIElementCreateSystemWide, AXUIElementGetPid,
                                 AXUIElementSetAttributeValue, AXUIElementSetMessagingTimeout, AXValueCreate,
                                 AXValueGetValue)
from AppKit import NSRunningApplication

from ..context import Selection

log = logging.getLogger("voiceinput")

try:
    from ApplicationServices import kAXValueCFRangeType as _RANGE, kAXValueCGRectType as _RECT
except ImportError:
    from ApplicationServices import kAXValueTypeCFRange as _RANGE, kAXValueTypeCGRect as _RECT

TERMINALS = {"com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty",
             "io.alacritty", "org.alacritty", "com.github.wez.wezterm", "com.mitchellh.ghostty", "co.zeit.hyper"}
_CHROMIUM = {"com.google.Chrome", "com.microsoft.edgemac", "com.brave.Browser", "company.thebrowser.Browser",
             "com.vivaldi.Vivaldi", "com.operasoftware.Opera"}
_enabled: set[int] = set()     # pids asked to build their accessibility tree
_system = None


def _sys():
    global _system
    if _system is None:
        _system = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(_system, 1.0)   # a hung app must not hold the probe for long
    return _system


def _attr(el, name):
    err, value = AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def _param(el, name, arg):
    err, value = AXUIElementCopyParameterizedAttributeValue(el, name, arg, None)
    return value if err == 0 else None


def _range(value) -> tuple[int, int] | None:
    if value is None:
        return None
    ok, r = AXValueGetValue(value, _RANGE, None)
    if not ok:
        return None
    try:
        return int(r.location), int(r.length)
    except AttributeError:
        return int(r[0]), int(r[1])


def _ax_range(loc: int, length: int):
    return AXValueCreate(_RANGE, (loc, length))


def _rect(value) -> tuple | None:
    if value is None:
        return None
    ok, r = AXValueGetValue(value, _RECT, None)
    if not ok:
        return None
    return (r.origin.x, r.origin.y, r.size.width, r.size.height)


def _app_of(el) -> tuple[int, str]:
    err, pid = AXUIElementGetPid(el, None)
    if err != 0:
        return 0, ""
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    return pid, (app.bundleIdentifier() or "") if app is not None else ""


def _focused():
    """(focused element, its app's bundle id); (None, "") when there is none."""
    el = _attr(_sys(), "AXFocusedUIElement")
    if el is None:
        app = _attr(_sys(), "AXFocusedApplication")
        if app is not None:
            _enable(*_app_of(app))
            el = _attr(_sys(), "AXFocusedUIElement")
        if el is None:
            return None, ""
    pid, bundle = _app_of(el)
    if pid and pid not in _enabled:
        _enable(pid, bundle)
        time.sleep(0.05)
        el = _attr(_sys(), "AXFocusedUIElement") or el
    return el, bundle


def _enable(pid: int, bundle: str):
    if not pid or pid in _enabled:
        return
    _enabled.add(pid)
    app = AXUIElementCreateApplication(pid)
    AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
    if bundle in _CHROMIUM:
        AXUIElementSetAttributeValue(app, "AXEnhancedUserInterface", True)


def _line_of(el, index: int) -> tuple[int, str] | None:
    """(start index, text) of the line holding character `index`."""
    line = _param(el, "AXLineForIndex", index)
    if line is not None:
        r = _range(_param(el, "AXRangeForLine", int(line)))
        if r is not None:
            text = _param(el, "AXStringForRange", _ax_range(*r))
            if isinstance(text, str):
                return r[0], text
    value = _attr(el, "AXValue")
    if isinstance(value, str) and index <= len(value):
        start = value.rfind("\n", 0, index) + 1
        end = value.find("\n", index)
        return start, value[start:end if end >= 0 else len(value)]
    return None


def _split(el, loc: int, length: int) -> tuple[str, str]:
    """The text of the line before character `loc` and after `loc + length` (on the same line)."""
    line = _line_of(el, loc)
    if line is None:
        return "", ""
    start, text = line
    end = loc + length - start
    return text[:loc - start], (text[end:] if end <= len(text) else "").rstrip("\r\n")


def read_selection() -> Selection | None:
    """The focused app's non-empty selection, or None."""
    try:
        el, app = _focused()
        if el is None:
            return None
        if app in TERMINALS and not term_watcher.fresh:
            return None
        text = _attr(el, "AXSelectedText")
        if not isinstance(text, str) or not text.strip():
            return None
        rng = _range(_attr(el, "AXSelectedTextRange"))
        before = after = ""
        rect = None
        if rng is not None:
            before, after = _split(el, *rng)
            rect = _rect(_param(el, "AXBoundsForRange", _ax_range(*rng)))
        term = (rng[0] + rng[1],) if app in TERMINALS and rng is not None else None
        return Selection(str(text), before, after, app, rect, term)
    except Exception:
        log.exception("reading the selection failed")
        return None


def read_caret() -> tuple[str, str] | None:
    """The text of the caret's line before and after it, or None when unknown (terminals: their cursor is the
    shell's)."""
    try:
        el, app = _focused()
        if el is None or app in TERMINALS:
            return None
        rng = _range(_attr(el, "AXSelectedTextRange"))
        if rng is None:
            return None
        return _split(el, *rng)
    except Exception:
        log.exception("reading the caret failed")
        return None


def visible_text() -> str:
    """The text visible in the focused control, "" if it has none."""
    try:
        el, _ = _focused()
        if el is None:
            return ""
        vr = _range(_attr(el, "AXVisibleCharacterRange"))
        if vr is not None and vr[1] > 0:
            text = _param(el, "AXStringForRange", _ax_range(vr[0], min(vr[1], 20000)))
            if isinstance(text, str):
                return text
        value = _attr(el, "AXValue")
        return value[-20000:] if isinstance(value, str) else ""
    except Exception:
        log.exception("reading the visible text failed")
        return ""


class _TermWatcher:
    """macOS terminals report their selection, but it stays highlighted after a copy or while typing: like on
    Windows, it counts only when the left button was released (a drag, a double click) since the last key or
    click."""

    def __init__(self):
        self.fresh = False

    def forget(self):
        self.fresh = False

    def mouse_down(self, x, y):
        self.fresh = False

    def mouse_up(self, x, y):
        self.fresh = True

    def selection(self):
        return None


term_watcher = _TermWatcher()


def move_cursor_after(sel: Selection) -> bool:
    """Terminal: move the shell's cursor right after sel with Left / Right. False (nothing deleted) when the cursor
    cannot be read or is not on the selection's line."""
    out = []
    t = threading.Thread(target=lambda: out.append(_move_after(sel)), daemon=True)
    t.start()
    t.join(4.0)
    return bool(out and out[0])


def _cursor(el) -> int | None:
    """The terminal's cursor: an empty selected range (a selection hides it)."""
    r = _range(_attr(el, "AXSelectedTextRange"))
    return r[0] if r is not None and r[1] == 0 else None


def _move_after(sel: Selection) -> bool:
    from ..output import ARROWS, press_key
    try:
        el, app = _focused()
        if el is None or app not in TERMINALS or not sel.term:
            return False
        target = sel.term[0]
        if _param(el, "AXStringForRange", _ax_range(target - len(sel.text), len(sel.text))) != sel.text:
            log.info("terminal selection %r is no longer there", sel.text)
            return False
        target_line = _param(el, "AXLineForIndex", target - 1)
        end = time.monotonic() + 3.0
        cur = _cursor(el)
        if cur is None:
            # the terminal still shows the selection: a Right key goes to the shell (the cursor moves at most one
            # character, at the end of the line it does not move) and may clear it
            press_key(ARROWS["right"], 1)
            time.sleep(0.15)
            cur = _cursor(el)
        for _ in range(12):
            if cur is None or time.monotonic() > end:
                break
            line = _param(el, "AXLineForIndex", cur)
            log.info("terminal cursor at %d, line %s (target %d, line %s)", cur, line, target, target_line)
            if line is None or target_line is None or int(line) != int(target_line):
                break   # Up / Down would recall shell history
            if cur == target:
                return True
            press_key(ARROWS["right" if target > cur else "left"], abs(target - cur))
            time.sleep(0.15)
            cur = _cursor(el)
        log.info("could not move the terminal cursor after %r", sel.text)
    except Exception:
        log.exception("moving the terminal cursor failed")
    return False
