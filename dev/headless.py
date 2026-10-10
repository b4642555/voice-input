r"""Headless end-to-end test: the real App (model, selection edits, double tap) with a simulated text box in
place of the focused app, so nothing is typed into real windows. Audio comes from TTS wav files.

  .venv\Scripts\python.exe dev\headless.py <dir> step,step,...

Each step is a wav name in <dir>/wav (the hotkey held while it is spoken), sel:WORD (select the last WORD in the
box), click (the user clicks: ends what the app knows about the text), tap (one tap of the hotkey), undo / tap2
(double tap), tap2:WAV (double tap, the second press held while WAV is spoken), at:WORD (put the caret right after
the last WORD; at: puts it at the end), say:TEXT (dictate TEXT as if recognized), set:NAME=VALUE (change a setting
in memory only) or key:BACK (the user presses Backspace n times: key:BACK*3).
"""
import shutil
import sys
import time
import wave
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceinput import app as appmod, output, selection, session  # noqa: E402

S = sys.argv[1]
HWND = 4242


class Box:
    """A text box with a caret and an optional selection."""

    def __init__(self):
        self.text, self.sel, self.caret = "", None, 0      # sel: (start, end)

    def paste(self, s):
        if self.sel:
            self.delete()
        self.text = self.text[:self.caret] + s + self.text[self.caret:]
        self.caret += len(s)

    def back(self, n):
        n = min(n, self.caret)
        self.text = self.text[:self.caret - n] + self.text[self.caret:]
        self.caret -= n

    def delete(self):
        if self.sel:
            a, b = self.sel
            self.text, self.sel, self.caret = self.text[:a] + self.text[b:], None, a

    def selection(self):
        if not self.sel:
            return None
        a, b = self.sel
        line_start = self.text.rfind("\n", 0, a) + 1
        line_end = self.text.find("\n", b)
        line_end = len(self.text) if line_end < 0 else line_end
        return selection.Selection(self.text[a:b], self.text[line_start:a], self.text[b:line_end], "fake.exe")

    def caret_context(self):
        if self.sel:
            return None
        start = self.text.rfind("\n", 0, self.caret) + 1
        end = self.text.find("\n", self.caret)
        return self.text[start:self.caret], self.text[self.caret:len(self.text) if end < 0 else end]

    def keys(self, *vks):
        """Shift+Enter types a newline; Enter sends the text (the box is emptied)."""
        if vks == (0x10, 0x0D):
            self.paste("\n")
        elif vks == (0x0D, 1):
            print(f"      [送出] {self.text!r}")
            self.text, self.caret = "", 0


box = Box()
for mod in (appmod, output):
    mod.paste_text = box.paste
appmod.backspace = box.back
appmod.press_delete = box.delete
appmod.press_chord = box.keys
appmod.press_key = box.keys
appmod.foreground = session.foreground = lambda: HWND
appmod.PushToTalk.start = lambda self: None        # no real hotkey / keyboard hook
replayed = []                                      # hotkey taps passed on to the app (CapsLock toggles)
appmod.PushToTalk.tap = lambda self: replayed.append(1)
appmod.App._watch_mouse = lambda self: None       # the user's real clicks must not end the simulated session


class Probe:
    def __init__(self, *_):
        self._ctx = selection.Context(box.selection(), [], box.caret_context())

    def result(self, timeout=2.0):
        return self._ctx


appmod.ContextProbe = Probe
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
A = appmod.App(qapp)
A.tray.showMessage = lambda title, text, *a: print(f"      [通知] {title}: {text}")


class Recorder:
    """Stands in for the microphone: stop() returns the wav queued for the current press."""
    level = 0.0
    audio = None

    def start(self, mic):
        pass

    def stop(self):
        return self.audio


A.recorder = Recorder()


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def load(name):
    w = wave.open(f"{S}/wav/{name}.wav")
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


t0 = time.monotonic()
while time.monotonic() - t0 < 120 and not A.recognizer:
    pump(0.3)
pump(2)
try:
    for step in sys.argv[2].split(","):
        if step.startswith("sel:"):
            w = step[4:]
            i = box.text.rfind(w)
            box.sel, box.caret = (i, i + len(w)), i + len(w)
            A.session.on_input()
            print(f"[選取 {w}]")
            continue
        if step in ("tap", "undo", "tap2") or step.startswith("tap2:"):
            replayed.clear()
            A.recorder.audio = None
            A.on_press(False)
            A.on_release(0.1, False)
            if step != "tap":
                pump(0.05)
                wav = step[5:] if step.startswith("tap2:") else ""
                A.recorder.audio = load(wav) if wav else None
                A.on_press(True)
                A.on_release(1.0 if wav else 0.1, True)
            pump(2.5 if step.startswith("tap2:") else 1.0)
            caps = "CapsLock 切換" if len(replayed) % 2 else "CapsLock 不變"
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}  ({caps})")
            continue
        if step.startswith("set:"):
            name, value = step[4:].split("=")
            setattr(A.cfg, name, type(getattr(A.cfg, name))(int(value) if value.isdigit() else value))
            print(f"[設定 {name} = {getattr(A.cfg, name)!r}]（只改記憶體，不存檔）")
            continue
        if step.startswith("say:"):
            A._dictate(step[4:], None, HWND)
            pump(0.5)
            print(f"[說 {step[4:]}（跳過辨識）] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step.startswith("at:"):
            w = step[3:]
            box.sel = None
            box.caret = box.text.rfind(w) + len(w) if w else len(box.text)
            A.session.on_input()
            print(f"[游標移到] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step == "click":
            A.session.on_input()
            print("[點滑鼠]")
            continue
        if step.startswith("key:BACK"):
            n = int(step.split("*")[1]) if "*" in step else 1
            box.back(n)
            for _ in range(n):
                A.session.on_input()
            print(f"[按 Backspace ×{n}]")
            continue
        A.recorder.audio = load(step)
        A.on_press(False)
        pump(0.5)
        A.on_release(1.0, False)
        pump(2.5)
        shown = box.text[:box.caret] + "|" + box.text[box.caret:]
        print(f"[{step}] {A._records[-1]['fmt'] if A._records else ''!r:32} → 文字框：{shown!r}")
finally:
    A.quit()
    shutil.rmtree(Path(S) / "__pycache__", ignore_errors=True)
