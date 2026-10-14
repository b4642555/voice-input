"""macOS checks for CI (.github/workflows/mac.yml), run with the installed Python from the installed source:
the platform modules import and their key tables are complete, the Accessibility and event calls do not crash
without permissions, and the recognizer turns a test clip into the expected text.

  venv/bin/python3 dev/mac_smoke.py
"""
import sys
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from voiceinput import keys, models, rules, selection, system  # noqa: E402
from voiceinput.asr import Recognizer  # noqa: E402
from voiceinput.mac import autostart, hotkey, keyboard, mute  # noqa: E402
from voiceinput.textfmt import format_text  # noqa: E402

failures = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


# key tables: every key a rule can name has a macOS key code, every trigger a code to watch
missing = [n for n, vk in keys.NAMES.items() if vk not in keyboard.VK_TO_MAC]
check(not missing, f"every key name maps to a macOS key code {missing or ''}")
missing = [n for n in keys.KEY_TRIGGERS if n not in hotkey._KEYS]
check(not missing, f"every trigger key is watched {missing or ''}")
check(keys.combo_vks("Cmd+V") == [0x5B, 0x56], "Cmd+V parses")
check(any(s.value == "Cmd+V" for r in rules.defaults() for s in r.steps), "貼上 presses Cmd+V by default")

# Accessibility without permission: None, no exception
t = time.monotonic()
ctx = selection.read_context()
check(isinstance(ctx, selection.Context), f"read_context returns ({time.monotonic() - t:.2f}s)")
check(isinstance(system.foreground(), int), "foreground is a pid")
check(isinstance(hotkey.caps_remapped(), bool), "hidutil readable")
mute.Muter()
check(autostart._program()[0], "autostart has a program")

# recognition
rec = Recognizer(models.MAIN_MODEL)
for name, expect in (("a", "今天天氣很好"), ("b", "我們下午去公園散步")):
    w = wave.open(str(ROOT / "dev" / "testdata" / "wav" / f"{name}.wav"))
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    t = time.monotonic()
    text = format_text(rec.recognize(audio), True)
    check(expect in text, f"recognized {name}.wav as {text!r} in {time.monotonic() - t:.2f}s")

print("\nFAILED: " + ", ".join(failures) if failures else "\nall passed")
sys.exit(1 if failures else 0)
