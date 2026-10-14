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

# windows through pyobjc: the indicator ignores clicks and floats, the 選字 menu is a nonactivating panel
from AppKit import NSPasteboard, NSPasteboardTypeString  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from voiceinput import cues, output  # noqa: E402
from voiceinput.overlay import Overlay  # noqa: E402
from voiceinput.picker import Picker  # noqa: E402

qapp = QApplication(sys.argv)
system.setup(qapp)
ov = Overlay(lambda: 0.0)
ov.show_recording()
qapp.processEvents()
win = system._window(ov)
check(win is not None and win.ignoresMouseEvents() and not win.hidesOnDeactivate(),
      "indicator: click-through, stays when the app is inactive")
pk = Picker()
pk.show_loading("測試", (100, 100, 40, 20))
pk.show_candidates("測試", ["測試", "側試"])
qapp.processEvents()
pw = system._window(pk)
check(pw is not None and bool(pw.styleMask() & (1 << 7)), "選字 menu: nonactivating panel")
handle = system.window_handle(pk)
g = pk.frameGeometry()
check(system.point_on(handle, g.center().x(), g.center().y()), "a point on the menu is on it")
check(system.to_logical(10, 20).x() == 10, "Accessibility points are Qt's")
pk.hide()
ov.hide_now()

# clipboard and keys: our text goes on the pasteboard; posting keys does not raise (dropped without permission)
check(keyboard.set_clipboard("語音輸入 test"), "clipboard set")
check(NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString) == "語音輸入 test", "clipboard read back")
keyboard.send_chord((0x10, 0x0D))
output.press_key(0x25, 2)
cues.play(cues.DEFAULT, "start")
check(cues.duration(cues.DEFAULT, "start") > 0, "start sound found")
m = mute.Muter()
m.mute()
m.restore()
mute._runner.submit(lambda: None).result(10)
check(not m.active, "mute and restore")

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
