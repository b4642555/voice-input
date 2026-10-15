r"""Headless end-to-end test: the real App (model, selection edits, double tap) with a simulated text box in
place of the focused app, so nothing is typed into real windows. Audio comes from TTS wav files.

  .venv\Scripts\python.exe dev\headless.py <dir> step,step,...

Each step is a wav name in <dir>/wav (the hotkey held while it is spoken), sel:WORD (select the last WORD in the
box), click (the user clicks: ends what the app knows about the text), tap (one tap of the hotkey), undo / tap2
(double tap), tap2:WAV (double tap, the second press held while WAV is spoken), toggle:WAV (tap, speak, tap),
hotkey:NAME (the key or button the next steps press, default mouse_middle), rules:default (the default operations),
rule:JSON (add an operation, rules.Rule.from_dict), at:WORD (put the caret right after the last WORD; at: puts it at
the end), say:TEXT (dictate TEXT as if recognized), screen:TEXT (what the foreground window shows, / for a new line), set:NAME=VALUE (change a setting in memory only) or key:BACK (the
user presses Backspace n times: key:BACK*3), pick:N / pickw:WORD (click a row of the 選字 menu), type:TEXT (type TEXT in the menu's input box and press Enter). Settings are never saved; hotwords and smart picks live in <dir> (hotwords.test.json,
smart-picks.test.json, history/, emptied at the start), never in the real files. AI 潤稿 follows the real
config (set:ai_enabled=0 turns it off for a run).
"""
import json
import shutil
import sys
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceinput import app as appmod, output, rules, screen_terms, selection, session  # noqa: E402

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
        """Shift+Enter types a newline; Enter sends the text (the box is emptied); others are printed."""
        if vks == (0x10, 0x0D):
            self.paste("\n")
        elif vks == (0x0D,):
            print(f"      [送出] {self.text!r}")
            self.text, self.caret = "", 0
        else:
            print(f"      [按鍵] {[hex(v) for v in vks]}")


box = Box()
for mod in (appmod, output):
    mod.paste_text = box.paste
appmod.backspace = box.back
appmod.press_delete = box.delete
appmod.press_chord = box.keys
appmod.press_key = box.keys
appmod.foreground = session.foreground = lambda: HWND
appmod.Hotkeys.start = lambda self: None           # no real hotkey / keyboard hook
replayed = []                                      # gestures given back to the key (key name per press)
appmod.Hotkeys.replay = lambda self, name, times=1: replayed.extend([name] * times)
appmod.App._watch_mouse = lambda self: None       # the user's real clicks must not end the simulated session
appmod.App._set_autostart = lambda self: None     # tests must not update the user's startup registration
appmod.Muter = lambda: SimpleNamespace(mute=lambda: None, restore=lambda: None)
appmod.MicGuard = lambda: SimpleNamespace(mute=lambda: None, restore=lambda: None, recover=lambda: None)
appmod.DiscordMute = lambda: SimpleNamespace(start=lambda *a: None, stop=lambda: None)   # never mute real apps
appmod.CorrectionWatcher = lambda callback: SimpleNamespace(
    prepare_before_paste=lambda: None, after_paste=lambda *args: None,
    on_key=lambda *args, **kwargs: None, on_pointer=lambda **kwargs: None,
    cancel=lambda: None, close=lambda: None)       # never read the user's live input fields


SCREEN = {"text": ""}   # screen:TEXT, what the foreground window shows (screen_terms)


class Probe:
    def __init__(self, *_):
        text = screen_terms.normalize_screen(SCREEN["text"])
        self._ctx = selection.Context(box.selection(), selection.terms_in(text), box.caret_context(),
                                      screen_terms.terms(text) + screen_terms.recent(text),
                                      screen_terms.fix_candidates(text))
        self._ctx.en_names = screen_terms.english_names(text)

    def result(self, timeout=2.0):
        return self._ctx


appmod.ContextProbe = Probe
_Hotwords, _Memory, _History = appmod.Hotwords, appmod.Memory, appmod.History
for name in ("hotwords.test.json", "smart-picks.test.json"):
    (Path(S) / name).unlink(missing_ok=True)
for p in (Path(S) / "history").glob("history-*.jsonl"):
    p.unlink()
appmod.Hotwords = lambda *_: _Hotwords(Path(S) / "hotwords.test.json")
appmod.Memory = lambda *_: _Memory(Path(S) / "smart-picks.test.json")
appmod.History = lambda *_: _History(Path(S) / "history")
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


def settle():
    """Wait for the serial recognizer and deliver its Qt signals, including long recordings."""
    barrier = A.worker.submit(lambda: None)
    end = time.monotonic() + 120
    while not barrier.done() and time.monotonic() < end:
        pump(0.05)
    if not barrier.done():
        raise TimeoutError("transcription did not finish in 120 seconds")
    pump(0.2)


def load(name):
    w = wave.open(f"{S}/wav/{name}.wav")
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


KEY = "mouse_middle"


def _steps(arg: str) -> list[str]:
    """Split on commas outside the braces of rule:JSON."""
    out, depth, cur = [], 0, ""
    for c in arg:
        depth += (c in "{[") - (c in "}]")
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
    return out + [cur]


def menu() -> str:
    if not A.picker.isVisible():
        return ""
    return f"   氣泡：{A.picker.candidates}"


def down():
    A.on_key_down(KEY, time.monotonic())


def up():
    A.on_key_up(KEY, time.monotonic())


t0 = time.monotonic()
while time.monotonic() - t0 < 120 and not A.recognizer:
    pump(0.3)
pump(2)
try:
    for step in _steps(sys.argv[2]):
        if step.startswith("sel:"):
            w = step[4:]
            i = box.text.rfind(w)
            box.sel, box.caret = (i, i + len(w)), i + len(w)
            A.session.on_input()
            print(f"[選取 {w}]")
            continue
        if step in ("tap", "undo", "tap2") or step.startswith(("tap2:", "toggle:")):
            replayed.clear()
            wav = step.split(":", 1)[1] if ":" in step else ""
            A.recorder.audio = load(wav) if step.startswith("toggle:") else None
            down(), pump(0.05), up()
            if step.startswith("toggle:"):
                pump(1.0)       # speaking
                down(), pump(0.05), up()
            elif step != "tap":
                pump(0.05)
                A.recorder.audio = load(wav) if wav else None
                down()
                pump(0.6 if wav else 0.05)
                up()
            pump(2.5 if wav else 1.0)
            settle()
            given = f"  (還給按鍵：{replayed})" if replayed else ""
            given += menu()
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}{given}")
            continue
        if step.startswith("hotkey:"):
            KEY = step[7:]
            print(f"[按鍵 {KEY}]")
            continue
        if step == "rules:default":
            A.cfg.rules = rules.defaults()
            A.hotkeys.set_keys(rules.bound_keys(A.cfg.rules))
            print("[預設操作]")
            continue
        if step.startswith("rule:"):
            A.cfg.rules.append(rules.Rule.from_dict(json.loads(step[5:])))
            A.hotkeys.set_keys(rules.bound_keys(A.cfg.rules))
            print(f"[新增操作] {A.cfg.rules[-1]}  {rules.problems(A.cfg.rules[-1], A.cfg.rules) or ''}")
            continue
        if step.startswith("screen:"):
            SCREEN["text"] = step[7:].replace("/", "\n")
            print(f"[畫面] 專有名詞：{screen_terms.terms(SCREEN['text'])}")
            continue
        if step.startswith("set:"):
            name, value = step[4:].split("=")
            setattr(A.cfg, name, type(getattr(A.cfg, name))(int(value) if value.isdigit() else value))
            print(f"[設定 {name} = {getattr(A.cfg, name)!r}]（只改記憶體，不存檔）")
            continue
        if step.startswith("say:"):
            A._dictate(step[4:], None, HWND, appmod._Press("", Probe(), 0, False))
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
        if step.startswith(("pick:", "pickw:")):
            shown = A.picker.isVisible()
            if shown:
                rows = A.picker.candidates
                i = int(step[5:]) - 1 if step.startswith("pick:") else rows.index(step[6:]) if step[6:] in rows else -1
                if 0 <= i < len(rows):
                    A.picker._pick(i)
            pump(0.5)
            print(f"[{step}] {'' if shown else '（沒有氣泡）'} → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}"
                  f"  熱詞：{A.hotwords.bias()}")
            continue
        if step.startswith("type:"):
            shown = A.picker.isVisible()
            if shown:
                A.picker.input.setText(step[5:])
                A.picker._submit()
            pump(0.6)
            print(f"[{step}] {'' if shown else '（沒有氣泡）'} → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}"
                  f"  熱詞：{A.hotwords.bias()}")
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
        down()
        pump(0.6)
        up()
        pump(2.5)
        settle()
        shown = box.text[:box.caret] + "|" + box.text[box.caret:]
        print(f"[{step}] {A._records[-1]['fmt'] if A._records else ''!r:32} → 文字框：{shown!r}{menu()}")
finally:
    A.quit()
    shutil.rmtree(Path(S) / "__pycache__", ignore_errors=True)
