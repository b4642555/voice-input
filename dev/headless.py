r"""Headless end-to-end test: the real App (model, selection edits, double tap) with a simulated text box in
place of the focused app, so nothing is typed into real windows. Audio comes from TTS wav files.

  .venv\Scripts\python.exe dev\headless.py <dir> step,step,...

Each step is a wav name in <dir>/wav (the hotkey held while it is spoken), sel:WORD (select the last WORD in the
box), click (the user clicks: ends what the app knows about the text), tap (one tap of the hotkey), undo / tap2
(double tap), toggle:WAV (tap, speak, tap),
hotkey:NAME (the key or button the next steps press, default mouse_middle), rules:default (the default operations), rules:norecord (every operation that records removed),
rule:JSON (add an operation, rules.Rule.from_dict), at:WORD (put the caret right after the last WORD; at: puts it at
the end), say:TEXT (dictate TEXT as if recognized), set:NAME=VALUE (change a setting in memory only; apply: as the settings window does after a change), packs:ID+ID (tick term packs, in memory only) or key:BACK (the
user presses Backspace n times: key:BACK*3), dbl:MS (a double tap, each press MS ms long), dblbusy (a double tap whose release is handled 0.5s late), pick:N / pickw:WORD (click a row of the 選字 menu), feed:WAV (while
continuous listening is on: WAV and 1s of silence reach the microphone; feed:WAV+WAV joins clips with 0.6s pauses),
logshot:NAME (the 辨識紀錄 tab saved as <dir>/NAME.png), wait:SEC (silence for SEC seconds), tap0 (a tap; the next step starts once it is known to be one,
while queued work still runs), racemove:WORD (the caret moves after the last WORD, at the end when empty, right
before the next draft update reaches the document), type:TEXT (type TEXT in the menu's input box and press Enter). Settings are never saved; hotwords and smart picks live in <dir> (hotwords.test.json,
smart-picks.test.json, emptied at the start), never in the real files.
"""
import json
import shutil
import sys
import time
import wave
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceinput import app as appmod, listen, output, rules, selection, session  # noqa: E402

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
        elif vks == (0x08,):
            self.back(1)
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


class Listener(appmod.Listener):
    """Continuous listening without the microphone: feed:WAV puts audio where the microphone would."""

    def start(self, device_name=""):
        import sherpa_onnx
        import threading
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self._vad_model
        cfg.silero_vad.min_silence_duration = listen.SILENCE
        cfg.silero_vad.max_speech_duration = listen.MAX_PIECE
        cfg.sample_rate = 16000
        import queue
        self._queue, self._discard = queue.Queue(), False
        self._thread = threading.Thread(target=self._run, daemon=True, args=(
            lambda: sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=40), cfg.silero_vad.window_size,
            self.now))
        self._thread.start()
        self._stream = True

    def stop(self, discard=False):
        self._discard = discard
        self._stream = None
        self._queue.put(None)
        self._thread.join(5)


appmod.Listener = Listener


class Probe:
    def __init__(self, *_):
        self._ctx = selection.Context(box.selection(), [], box.caret_context())

    def result(self, timeout=2.0):
        return self._ctx


appmod.ContextProbe = Probe
_Hotwords, _Memory = appmod.Hotwords, appmod.Memory
for name in ("hotwords.test.json", "smart-picks.test.json"):
    (Path(S) / name).unlink(missing_ok=True)
appmod.Hotwords = lambda: _Hotwords(Path(S) / "hotwords.test.json")
appmod.Memory = lambda: _Memory(Path(S) / "smart-picks.test.json")
appmod.recordings.DIR = Path(S) / "recordings"   # set:save_recordings=1 saves here, never in the real folder
appmod.recordings.INDEX = appmod.recordings.DIR / "index.jsonl"
qapp = QApplication(sys.argv)
qapp.setQuitOnLastWindowClosed(False)
appmod.Overlay.show = lambda self: None   # no indicator on the user's screen next to their own app's
appmod.EdgeGlow.show = lambda self, screen=None: None   # nor the glow around it
A = appmod.App(qapp)
A.tray.hide()
A.tray.showMessage = lambda title, text, *a: print(f"      [通知] {title}: {text}")


class Muter:
    """Stands in for the speaker mute (never mutes the real speakers): prints what it would do."""
    active = False

    def mute(self):
        if not self.active:
            self.active = True
            print("      [靜音]")

    def restore(self):
        if self.active:
            self.active = False
            print("      [解除靜音]")


A.muter = Muter()
A.cfg.save = lambda: None   # settings are never saved (the pack hint saves config.json)
appmod.cues.play = lambda cue, kind: print(f"      [音效] {kind}: {cue or '（無）'}")   # printed, not played


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def feed(audio):
    """What the microphone hears while listening: audio and 1s of silence."""
    audio = np.concatenate([audio, np.zeros(16000, np.float32)])
    seen = box.text
    for i in range(0, len(audio), 480):
        A.listener._queue.put(audio[i:i + 480])
        if i % 4800 == 0:
            pump(0.1)   # real time: 0.3s of audio every 0.1s
            if box.text != seen:
                seen = box.text
                print(f"      [{i / 16000:4.1f}s] {seen!r}")


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
    A.hotkeys._held.add(KEY)       # what the hook would record
    A.on_key_down(KEY, time.monotonic())


def up():
    A.hotkeys._held.discard(KEY)
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
        if step in ("tap", "undo", "tap2") or step.startswith("toggle:"):
            replayed.clear()
            down(), pump(0.05), up()
            if step.startswith("toggle:"):
                pump(A.cfg.gesture_time + 0.2)
                if A._listening:
                    feed(load(step[7:]))
                    pump(2.0)
                down(), pump(0.05), up()
            elif step != "tap":
                pump(0.05)
                down(), pump(0.05), up()
            pump(2.5 if step.startswith("toggle:") else 1.0)
            given = f"  (還給按鍵：{replayed})" if replayed else ""
            given += menu()
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}{given}")
            continue
        if step.startswith("wait:"):   # nothing happens for SEC seconds (the microphone hears silence)
            end = time.monotonic() + float(step[5:])
            while time.monotonic() < end:
                if A._listening:
                    A.listener._queue.put(np.zeros(4800, np.float32))
                pump(0.3)
            print(f"[{step}] 聽寫中：{A._listening}")
            continue
        if step == "tap0":   # a tap, and the next step right when it is known to be one (queued work still runs)
            down(), pump(0.05), up(), pump(A.cfg.gesture_time + 0.05)
            print(f"[tap0] 聽寫中：{A._listening}")
            continue
        if step.startswith("racemove:"):   # the caret moves (to after WORD) right before the next draft update
            w, refine = step[9:], A._refine

            def moved_first(*args, w=w, refine=refine):
                A._refine = refine
                box.sel = None
                box.caret = box.text.rfind(w) + len(w) if w else len(box.text)
                A.session.on_input()
                print(f"      [游標移到 {w!r} 後面] {box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
                refine(*args)
            A._refine = moved_first
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
        if step == "rules:norecord":   # every operation that records removed (in memory)
            A.cfg.rules = [r for r in A.cfg.rules if r.record_index() is None]
            print("[移除所有錄音操作]")
            continue
        if step.startswith("rule:"):
            A.cfg.rules.append(rules.Rule.from_dict(json.loads(step[5:])))
            A.hotkeys.set_keys(rules.bound_keys(A.cfg.rules))
            print(f"[新增操作] {A.cfg.rules[-1]}  {rules.problems(A.cfg.rules[-1], A.cfg.rules) or ''}")
            continue
        if step.startswith("set:"):
            name, value = step[4:].split("=")
            setattr(A.cfg, name, type(getattr(A.cfg, name))(int(value) if value.isdigit() else value))
            print(f"[設定 {name} = {getattr(A.cfg, name)!r}]（只改記憶體，不存檔）")
            continue
        if step == "apply":   # what the settings window does after a change
            A.apply_settings()
            pump(8.0)
            print("[套用設定]")
            continue
        if step.startswith("packs:"):
            ids = [i for i in step[6:].split("+") if i]
            A.hotwords.set_packs(appmod.packs.words_for(ids), appmod.packs.PCT)
            print(f"[詞包 {ids}]（只改記憶體，不存檔）")
            continue
        if step.startswith("say:"):
            A._type_text(step[4:])
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
        if step.startswith("feed:"):
            if not A._listening:
                print(f"[{step}]（沒有在聽寫）")
                continue
            parts = []
            for name in step[5:].split("+"):
                parts += [load(name), np.zeros(int(0.6 * 16000), np.float32)]
            feed(np.concatenate(parts[:-1]))
            pump(3.0)
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step == "click":
            A.session.on_input()
            print("[點滑鼠]")
            continue
        if step == "dblbusy":   # a double tap whose second release reaches the main thread 0.5s late (busy)
            down(), pump(0.05), up(), pump(0.05), down(), pump(0.1)
            A.hotkeys._held.discard(KEY)
            t_up = time.monotonic()
            pump(0.5)
            A.on_key_up(KEY, t_up)
            pump(1.0)
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step.startswith("press:"):   # one press lasting MS milliseconds
            down(), pump(int(step[6:]) / 1000), up(), pump(1.0)
            print(f"[{step}] 聽寫中：{A._listening} → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step.startswith("dbl:"):   # a double tap whose presses last MS milliseconds each
            ms = int(step[4:]) / 1000
            down(), pump(ms), up(), pump(0.05), down(), pump(ms), up(), pump(1.0)
            print(f"[{step}] → 文字框：{box.text[:box.caret] + '|' + box.text[box.caret:]!r}")
            continue
        if step.startswith("logshot:"):
            A.settings.set_log(A._records)
            A.settings.tabs.setCurrentIndex(1)
            A.settings.show()
            pump(0.3)
            A.settings.grab().save(f"{S}/{step[8:]}.png")
            print(f"[辨識紀錄截圖 {S}/{step[8:]}.png]")
            continue
        if step.startswith("key:BACK"):
            n = int(step.split("*")[1]) if "*" in step else 1
            box.back(n)
            for _ in range(n):
                A.session.on_input()
            print(f"[按 Backspace ×{n}]")
            continue
        down()
        pump(A.cfg.gesture_time + 0.2)
        if A._listening:
            feed(load(step))
        up()
        pump(2.5)
        shown = box.text[:box.caret] + "|" + box.text[box.caret:]
        print(f"[{step}] {A._records[-1].get('main', '') if A._records else ''!r:32} → 文字框：{shown!r}{menu()}")
finally:
    A.quit()
    shutil.rmtree(Path(S) / "__pycache__", ignore_errors=True)
