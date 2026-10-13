"""Global hooks for the mouse buttons and keyboard keys that custom operations use (rules.bound_keys).

Those keys are suppressed with low-level hooks (pynput) so holding CapsLock does not toggle caps and a middle
click does not reach the app. Whether a gesture is given back to the key (replay()) is up to the app: only
gestures no rule takes are. Injected events (our own replays and keystrokes) always pass.
"""
import time

from pynput import keyboard, mouse

from .keys import KEY_TRIGGERS
from .output import press_key

TAP_THRESHOLD = 0.3     # seconds; shorter presses are taps
DOUBLE_TAP_GAP = 0.3    # seconds from a tap's release to the next press to count as a double tap (0.35 until
                        # 2026-10-13, the same as a hold at the user's request; their gaps: median 0.09s)

_WM_KEYDOWN, _WM_SYSKEYDOWN = 0x0100, 0x0104
_LLKHF_INJECTED = 0x10
_LLMHF_INJECTED = 1
# name -> (button down message, button up message, XBUTTON number in the high word of MSLLHOOKSTRUCT.mouseData)
_MOUSE = {"mouse_x1": (0x20B, 0x20C, 1), "mouse_x2": (0x20B, 0x20C, 2), "mouse_middle": (0x207, 0x208, None)}
_BUTTONS = {"mouse_x1": mouse.Button.x1, "mouse_x2": mouse.Button.x2, "mouse_middle": mouse.Button.middle}


def _mouse_key(names, msg: int, mouse_data: int) -> tuple[str, bool] | None:
    """(name, is down) when a low-level mouse event is a press or release of one of these buttons."""
    for name in names:
        spec = _MOUSE.get(name)
        if spec and msg in spec[:2] and (spec[2] is None or mouse_data >> 16 == spec[2]):
            return name, msg == spec[0]
    return None


class Hotkeys:
    """Calls on_down(name, time) / on_up(name, time) for the watched keys, and on_other_key(vk) for any other key
    the user presses."""

    def __init__(self, on_down, on_up, on_other_key=None):
        self.on_down = on_down
        self.on_up = on_up
        self.on_other_key = on_other_key
        self._names: frozenset = frozenset()
        self._vks: dict[int, str] = {}
        self._held: set[str] = set()
        self._listener = None
        self._mouse_listener = None
        self._mouse = mouse.Controller()

    def set_keys(self, names) -> None:
        self._names = frozenset(names)
        self._vks = {KEY_TRIGGERS[n][1]: n for n in self._names if n in KEY_TRIGGERS}
        self._held &= self._names

    def is_down(self, name: str) -> bool:
        """The key is down now, as the hook saw it (the main thread may not have handled its release yet)."""
        return name in self._held

    def is_bound_mouse(self, msg: int, mouse_data: int) -> bool:
        return _mouse_key(self._names, msg, mouse_data) is not None

    def start(self) -> None:
        self._listener = keyboard.Listener(win32_event_filter=self._win32_filter)
        self._listener.daemon = True
        self._listener.start()
        self._mouse_listener = mouse.Listener(win32_event_filter=self._win32_mouse_filter)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

    def stop(self) -> None:
        if self._listener:
            self._listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()

    # Everything is handled inside the hooks so the key can be suppressed
    def _win32_filter(self, msg, data):
        if data.flags & _LLKHF_INJECTED:
            return True
        name = self._vks.get(data.vkCode)
        if name is None:
            if self.on_other_key and msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                self.on_other_key(data.vkCode)
            return True
        self._event(name, msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN))
        self._listener.suppress_event()

    def _win32_mouse_filter(self, msg, data):
        if data.flags & _LLMHF_INJECTED:
            return False
        hit = _mouse_key(self._names, msg, data.mouseData)
        if hit is None:
            return False   # never passed on to pynput's (unused) callbacks
        self._event(*hit)
        self._mouse_listener.suppress_event()

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
        """Give a gesture back: press the key, or click the mouse button (injected, so the hooks let it through)."""
        if name in KEY_TRIGGERS:
            press_key(KEY_TRIGGERS[name][1], times)
        elif name in _BUTTONS:
            self._mouse.click(_BUTTONS[name], times)
