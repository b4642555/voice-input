"""Key names: the mouse buttons and keyboard keys a custom operation can be triggered by, and the names used in
key combinations (Ctrl+Shift+Enter) that a step presses."""

MOUSE_TRIGGERS = {"mouse_middle": "中鍵", "mouse_x2": "側鍵（上）", "mouse_x1": "側鍵（下）"}

# name -> (label, virtual-key code). Keys that are rarely typed, so taking them over costs little.
KEY_TRIGGERS = {"caps_lock": ("CapsLock", 0x14), "alt_r": ("右 Alt", 0xA5), "ctrl_r": ("右 Ctrl", 0xA3),
                "insert": ("Insert", 0x2D), "scroll_lock": ("ScrollLock", 0x91), "pause": ("Pause", 0x13)}
KEY_TRIGGERS.update({f"f{i}": (f"F{i}", 0x6F + i) for i in range(1, 25)})

MODIFIERS = {"Ctrl": 0x11, "Shift": 0x10, "Alt": 0x12, "Win": 0x5B}
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

NAMES = {"Enter": 0x0D, "Tab": 0x09, "Esc": 0x1B, "Space": 0x20, "Backspace": 0x08, "Delete": 0x2E,
         "Insert": 0x2D, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "Left": 0x25, "Up": 0x26,
         "Right": 0x27, "Down": 0x28, ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
         "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}
NAMES.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
NAMES.update({str(d): 0x30 + d for d in range(10)})
NAMES.update({f"F{i}": 0x6F + i for i in range(1, 25)})
_VK_NAMES = {vk: name for name, vk in NAMES.items()}

# Keys sent with KEYEVENTF_EXTENDEDKEY (otherwise Windows takes them for the keypad's)
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0xA3, 0xA5}


def is_modifier(vk: int) -> bool:
    return vk in _MODIFIER_VKS


def combo_text(modifiers: list[str], vk: int) -> str | None:
    """"Ctrl+Shift+Z" for modifier names and a main key, None for a key without a name."""
    name = _VK_NAMES.get(vk)
    return "+".join(modifiers + [name]) if name else None


def combo_vks(text: str) -> list[int]:
    """The virtual-key codes of a combination ("Ctrl+Shift+Z" -> [0x11, 0x10, 0x5A]); [] if it is not valid."""
    vks = []
    for part in text.split("+") if text else []:
        vk = MODIFIERS.get(part) or NAMES.get(part)
        if vk is None:
            return []
        vks.append(vk)
    return vks
