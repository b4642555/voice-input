"""Global hooks for the mouse buttons and keyboard keys that custom operations use (rules.bound_keys).

Those keys are suppressed with low-level hooks (pynput) so holding CapsLock does not toggle caps and a middle
click does not reach the app. Whether a gesture is given back to the key (replay()) is up to the app: only
gestures no rule takes are. Remapped bound keys can trigger operations; our tagged replays and keystrokes pass.
"""
import time

from pynput import keyboard, mouse

from .keys import KEY_TRIGGERS
from .output import INPUT_TAG, press_key

TAP_THRESHOLD = 0.3     # seconds; shorter presses are taps
DOUBLE_TAP_GAP = 0.35   # seconds from a tap's release to the next press to count as a double tap

_WM_KEYDOWN, _WM_SYSKEYDOWN = 0x0100, 0x0104
_LLKHF_EXTENDED = 0x01
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

    def __init__(self, on_down, on_up, on_other_key=None, on_injected_key=None):
        self.on_injected_key = on_injected_key
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
        if "alt" in self._names:
            self._vks.update({0xA4: "alt_l", 0xA5: "alt_r"})
        # Keep each physical side distinct through the gesture and its eventual replay.
        self._held &= self._names | set(self._vks.values())

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
        vk = data.vkCode
        if vk == 0x12:   # VK_MENU: some remappers send a generic Alt rather than a sided code
            vk = 0xA5 if data.flags & _LLKHF_EXTENDED else 0xA4
        name = self._vks.get(vk)
        if data.flags & _LLKHF_INJECTED and (name is None or data.dwExtraInfo == INPUT_TAG):
            # unbound injected keys must not end a dictation; our keys must not retrigger. A mouse button a remapper
            # turns into Enter is injected too: the app may still take it (the 複製最後的轉錄 shortcut)
            if name is None and data.dwExtraInfo != INPUT_TAG and self.on_injected_key                     and msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN) and self.on_injected_key(vk):
                self._listener.suppress_event()
            return True
        if name is None:
            if self.on_other_key and msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                if self.on_other_key(data.vkCode):   # True: the key was taken (the 複製最後的轉錄 shortcut)
                    self._listener.suppress_event()
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
