"""macOS: the clipboard (NSPasteboard) and keystrokes (CGEventPost). Keys come as Windows virtual-key codes (keys.py)
and are translated here. Every event we post carries MARK in kCGEventSourceUserData, so our own event tap
(mac/hotkey.py) lets it through and does not take it for the user's."""
import Quartz as Q
from AppKit import NSPasteboard, NSPasteboardTypeString

MARK = 0x56494E50   # "VINP"

# Windows virtual-key code -> macOS key code (kVK_*)
VK_TO_MAC = {
    0x08: 51, 0x09: 48, 0x0D: 36, 0x10: 56, 0x11: 59, 0x12: 58, 0x14: 57, 0x1B: 53, 0x20: 49,
    0x21: 116, 0x22: 121, 0x23: 119, 0x24: 115, 0x25: 123, 0x26: 126, 0x27: 124, 0x28: 125,
    0x2D: 114, 0x2E: 117, 0x5B: 55, 0x5C: 54, 0xA0: 56, 0xA1: 60, 0xA2: 59, 0xA3: 62, 0xA4: 58, 0xA5: 61,
    0xBA: 41, 0xBB: 24, 0xBC: 43, 0xBD: 27, 0xBE: 47, 0xBF: 44, 0xC0: 50, 0xDB: 33, 0xDC: 42, 0xDD: 30, 0xDE: 39,
}
VK_TO_MAC.update(dict(zip(range(0x30, 0x3A), (29, 18, 19, 20, 21, 23, 22, 26, 28, 25))))   # 0-9
VK_TO_MAC.update(dict(zip(range(0x41, 0x5B), (0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46, 45, 31, 35, 12, 15,
                                               1, 17, 32, 9, 13, 7, 16, 6))))           # A-Z
_F_KEYS = (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80, 90)
VK_TO_MAC.update({0x70 + i: code for i, code in enumerate(_F_KEYS)})                    # F1-F20
MAC_TO_VK = {code: vk for vk, code in VK_TO_MAC.items() if vk not in (0xA0, 0xA2, 0xA4, 0x14)}

_MODIFIER_FLAGS = {0x10: Q.kCGEventFlagMaskShift, 0xA0: Q.kCGEventFlagMaskShift, 0xA1: Q.kCGEventFlagMaskShift,
                   0x11: Q.kCGEventFlagMaskControl, 0xA2: Q.kCGEventFlagMaskControl,
                   0xA3: Q.kCGEventFlagMaskControl,
                   0x12: Q.kCGEventFlagMaskAlternate, 0xA4: Q.kCGEventFlagMaskAlternate,
                   0xA5: Q.kCGEventFlagMaskAlternate,
                   0x5B: Q.kCGEventFlagMaskCommand, 0x5C: Q.kCGEventFlagMaskCommand}
# arrows and the navigation keys are function-row / keypad keys on a Mac keyboard: real presses carry these flags
_NAV = {123: Q.kCGEventFlagMaskNumericPad | Q.kCGEventFlagMaskSecondaryFn,
        124: Q.kCGEventFlagMaskNumericPad | Q.kCGEventFlagMaskSecondaryFn,
        125: Q.kCGEventFlagMaskNumericPad | Q.kCGEventFlagMaskSecondaryFn,
        126: Q.kCGEventFlagMaskNumericPad | Q.kCGEventFlagMaskSecondaryFn,
        115: Q.kCGEventFlagMaskSecondaryFn, 116: Q.kCGEventFlagMaskSecondaryFn, 117: Q.kCGEventFlagMaskSecondaryFn,
        119: Q.kCGEventFlagMaskSecondaryFn, 121: Q.kCGEventFlagMaskSecondaryFn}
_source = None


def _src():
    """A private event source: the modifiers the user holds (a held trigger key) do not leak into our keys."""
    global _source
    if _source is None:
        _source = Q.CGEventSourceCreate(Q.kCGEventSourceStatePrivate)
    return _source


def post_key(code: int, down: bool, flags: int = 0) -> None:
    e = Q.CGEventCreateKeyboardEvent(_src(), code, down)
    Q.CGEventSetFlags(e, flags | _NAV.get(code, 0))
    Q.CGEventSetIntegerValueField(e, Q.kCGEventSourceUserData, MARK)
    Q.CGEventPost(Q.kCGHIDEventTap, e)


def set_clipboard(text: str) -> bool:
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    return bool(pb.setString_forType_(text, NSPasteboardTypeString))


def send_paste() -> None:
    """Cmd+V."""
    send_chord((0x5B, 0x56))


def send_keys(vk: int, n: int) -> None:
    code = VK_TO_MAC.get(vk)
    if code is None:
        return
    for _ in range(n):
        post_key(code, True)
        post_key(code, False)


def send_chord(vks) -> None:
    """Modifiers become the flags of the other keys (Shift+Enter: Enter with the Shift flag)."""
    flags = 0
    for vk in vks:
        flags |= _MODIFIER_FLAGS.get(vk, 0)
    keys = [VK_TO_MAC[vk] for vk in vks if vk not in _MODIFIER_FLAGS and vk in VK_TO_MAC]
    for code in keys:
        post_key(code, True, flags)
    for code in reversed(keys):
        post_key(code, False, flags)


def send_delete() -> None:
    """Forward delete: deletes the selection."""
    send_keys(0x2E, 1)
