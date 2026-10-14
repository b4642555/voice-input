"""macOS: one Quartz event tap for the keys and mouse buttons that custom operations use, the user's clicks and their
other keys (the Windows version uses low-level hooks, win/hotkey.py; the same Hotkeys interface).

The tap is an active one at the session level, so a bound key or button is swallowed (returning None) and a middle
click does not reach the app. It needs the Accessibility permission (and Input Monitoring for keys): without it
CGEventTapCreate fails, and it is tried again every 2 seconds until the user has allowed it (mac/system.py asks).
Events we post ourselves carry keyboard.MARK and pass untouched.

CapsLock: macOS reports it as a toggle (flagsChanged), not as down / up, so a hold cannot be seen. While it is bound
it is remapped to F18 with hidutil (no admin rights, every keyboard, lost at reboot) and F18 is watched; quitting
removes the mapping, and a mapping left by a crash is removed at the next launch when CapsLock is not bound. A tap
given back cannot toggle caps lock (docs/macos-port.md)."""
import logging
import subprocess
import threading
import time

import Quartz as Q

from ..keys import KEY_TRIGGERS
from .keyboard import MARK, VK_TO_MAC, post_key

log = logging.getLogger("voiceinput")

_F18 = 79
# trigger name -> macOS key code; right-hand modifiers come as flagsChanged with their device mask
_KEYS = {"caps_lock": _F18, "alt_r": 61, "cmd_r": 54, "ctrl_r": 62}
_KEYS.update({name: VK_TO_MAC[vk] for name, (_, vk) in KEY_TRIGGERS.items() if name.startswith("f") and vk in VK_TO_MAC})
_DEVICE_MASKS = {61: 0x40, 54: 0x10, 62: 0x2000}   # NX_DEVICERALTKEYMASK, NX_DEVICERCMDKEYMASK, NX_DEVICERCTLKEYMASK
# mouse trigger -> button number (0 left, 1 right, 2 middle, 3 back, 4 forward)
_BUTTONS = {"mouse_middle": 2, "mouse_x1": 3, "mouse_x2": 4}
_CAPS_TO_F18 = '{"UserKeyMapping":[{"HIDKeyboardModifierMappingSrc":0x700000039,' \
               '"HIDKeyboardModifierMappingDst":0x70000006D}]}'
_NO_MAPPING = '{"UserKeyMapping":[]}'


def _hidutil(mapping: str) -> None:
    try:
        subprocess.run(["hidutil", "property", "--set", mapping], capture_output=True, timeout=5)
    except Exception:
        log.exception("hidutil failed")


def caps_remapped() -> bool:
    try:
        out = subprocess.run(["hidutil", "property", "--get", "UserKeyMapping"], capture_output=True, text=True,
                             timeout=5).stdout
    except Exception:
        return False
    return "30064771129" in out and "30064771181" in out   # 0x700000039 -> 0x70000006D


class Hotkeys:
    """Calls on_down(name, time) / on_up(name, time) for the watched keys, and on_other_key(code) for any other
    key the user presses."""

    def __init__(self, on_down, on_up, on_other_key=None):
        self.on_down = on_down
        self.on_up = on_up
        self.on_other_key = on_other_key
        self._names: frozenset = frozenset()
        self._codes: dict[int, str] = {}
        self._buttons: dict[int, str] = {}
        self._held: set[str] = set()
        self._on_press = self._on_release = None   # watch_clicks
        self._tap = None
        self._loop = None
        self._caps = False
        self._caps_checked = False                 # a mapping a crash left behind was looked for
        self._cb = self._callback                  # kept referenced while the tap lives
        self.ready = threading.Event()             # the tap is installed (the permission was given)

    def set_keys(self, names) -> None:
        self._names = frozenset(names)
        self._codes = {_KEYS[n]: n for n in self._names if n in _KEYS}
        self._buttons = {_BUTTONS[n]: n for n in self._names if n in _BUTTONS}
        self._held &= self._names
        caps = "caps_lock" in self._names
        stale = not caps and not self._caps_checked and caps_remapped()
        self._caps_checked = True
        if caps != self._caps or stale:
            _hidutil(_CAPS_TO_F18 if caps else _NO_MAPPING)
            self._caps = caps

    def is_down(self, name: str) -> bool:
        return name in self._held

    def watch_clicks(self, on_press, on_release):
        """on_press(x, y, left) when a mouse button other than a bound one goes down, on_release(x, y) when the
        left one comes up (screen points, Qt's logical coordinates; on the tap's thread)."""
        self._on_press, self._on_release = on_press, on_release
        return self

    def start(self) -> None:
        threading.Thread(target=self._run, name="event tap", daemon=True).start()

    def stop(self) -> None:
        if self._caps:
            _hidutil(_NO_MAPPING)
            self._caps = False
        if self._tap is not None:
            Q.CGEventTapEnable(self._tap, False)
        if self._loop is not None:
            Q.CFRunLoopStop(self._loop)

    def _run(self):
        mask = 0
        for t in (Q.kCGEventKeyDown, Q.kCGEventKeyUp, Q.kCGEventFlagsChanged, Q.kCGEventLeftMouseDown,
                  Q.kCGEventLeftMouseUp, Q.kCGEventRightMouseDown, Q.kCGEventOtherMouseDown,
                  Q.kCGEventOtherMouseUp):
            mask |= Q.CGEventMaskBit(t)
        warned = False
        while True:
            self._tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap,
                                           Q.kCGEventTapOptionDefault, mask, self._cb, None)
            if self._tap is not None:
                break
            if not warned:
                log.warning("event tap refused: Accessibility / Input Monitoring not allowed yet")
                warned = True
            time.sleep(2)
        log.info("event tap installed")
        source = Q.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = Q.CFRunLoopGetCurrent()
        Q.CFRunLoopAddSource(self._loop, source, Q.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(self._tap, True)
        self.ready.set()
        Q.CFRunLoopRun()

    def _callback(self, proxy, etype, event, refcon):
        try:
            return self._handle(etype, event)
        except Exception:
            log.exception("event tap callback failed")
            return event

    def _handle(self, etype, event):
        if etype in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
            Q.CGEventTapEnable(self._tap, True)   # macOS turns a slow tap off
            return event
        if Q.CGEventGetIntegerValueField(event, Q.kCGEventSourceUserData) == MARK:
            return event
        if etype in (Q.kCGEventKeyDown, Q.kCGEventKeyUp):
            code = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
            name = self._codes.get(code)
            if name is None:
                if etype == Q.kCGEventKeyDown and self.on_other_key:
                    self.on_other_key(code)
                return event
            self._event(name, etype == Q.kCGEventKeyDown)
            return None
        if etype == Q.kCGEventFlagsChanged:
            code = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
            name = self._codes.get(code)
            if name is None or code not in _DEVICE_MASKS:
                return event
            self._event(name, bool(Q.CGEventGetFlags(event) & _DEVICE_MASKS[code]))
            return None
        if etype in (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp):
            name = self._buttons.get(Q.CGEventGetIntegerValueField(event, Q.kCGMouseEventButtonNumber))
            if name is not None:
                self._event(name, etype == Q.kCGEventOtherMouseDown)
                return None
        if etype in (Q.kCGEventLeftMouseDown, Q.kCGEventRightMouseDown, Q.kCGEventOtherMouseDown):
            if self._on_press:
                pt = Q.CGEventGetLocation(event)
                self._on_press(int(pt.x), int(pt.y), etype == Q.kCGEventLeftMouseDown)
        elif etype == Q.kCGEventLeftMouseUp and self._on_release:
            pt = Q.CGEventGetLocation(event)
            self._on_release(int(pt.x), int(pt.y))
        return event

    def _event(self, name: str, down: bool):
        now = time.monotonic()
        if down:
            if name not in self._held:   # not auto-repeat
                self._held.add(name)
                self.on_down(name, now)
        elif name in self._held:
            self._held.discard(name)
            self.on_up(name, now)

    def replay(self, name: str, times: int = 1) -> None:
        """Give a gesture back: press the key, or click the mouse button (marked, so the tap lets it through)."""
        if name in _BUTTONS:
            pt = Q.CGEventGetLocation(Q.CGEventCreate(None))
            for _ in range(times):
                for etype in (Q.kCGEventOtherMouseDown, Q.kCGEventOtherMouseUp):
                    e = Q.CGEventCreateMouseEvent(None, etype, pt, _BUTTONS[name])
                    Q.CGEventSetIntegerValueField(e, Q.kCGMouseEventButtonNumber, _BUTTONS[name])
                    Q.CGEventSetIntegerValueField(e, Q.kCGEventSourceUserData, MARK)
                    Q.CGEventPost(Q.kCGHIDEventTap, e)
        elif name == "caps_lock":
            log.info("a CapsLock tap is not given back on macOS (it is remapped to F18)")
        elif name in _KEYS and _KEYS[name] not in _DEVICE_MASKS:
            for _ in range(times):
                post_key(_KEYS[name], True)
                post_key(_KEYS[name], False)
