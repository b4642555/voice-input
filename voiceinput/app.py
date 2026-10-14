"""VoiceInput: single-process tray app. Hold a key or mouse button to talk, release to paste.

What an utterance does:
1. Text is selected (UI Automation): the utterance replaces it.
2. Otherwise it is dictated: formatted and pasted.
An utterance that is exactly a custom operation's phrase (換行, 送出 …) runs that operation instead, and a bare
punctuation name (逗號, 句號 …) is typed as the mark (edit.symbol; a setting since 2026-10-11), and 節日的節 types
節 (edit.described, a setting). Dictated text then
goes through the smart picks (memory.py, single characters only); words are left to X-ASR hotword biasing.

What the mouse buttons and keys do is set by custom operations (rules.py, 2026-10-11). The defaults: hold the
middle button to record; double click it on a selection to delete it, or right after a dictation to delete that
(while it is still the last change). When a key's double tap does not record and its hold does, holding the second
press records again: delete and say it again. A tap on a selection opens 選字. Gestures no operation takes are
given back to the key.

The one popup (picker.py) is the 選字 candidate menu; it came back 2026-10-11 after the lean branch removed every
popup, and a redesign is planned (the 加入熱詞 notice with 撤銷 lived for a few hours the same day). Its input box
takes a typed word, which replaces the selection and becomes a hotword. The second opinion (SenseVoice + suspects.py) is
commented out below.
"""
import logging
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PySide6.QtCore import QLockFile, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart, candidates, cues, edit, models, packs, paths, recordings, rules, sounds, spelling
from .asr import SAMPLE_RATE, Recognizer
from .audio import Recorder
from .config import Config
from .hotwords import Hotwords
from .hotkey import Hotkeys
from .keys import MOUSE_TRIGGERS, combo_vks
from .memory import Memory, is_latin, situation
from .mute import Muter
from .output import backspace, paste_text, press_chord, press_delete
from .overlay import Overlay
from .picker import Picker
from .reading import reading_key
from .rules import DOUBLE, HOLD, TAP
from .selection import ContextProbe, move_cursor_after, term_watcher
from .session import Session, foreground
from .settings import SettingsDialog
from .textfmt import format_text, to_traditional

log = logging.getLogger("voiceinput")

_SCREEN_TERM_SCORE = 1.5
# Spoken commands (speech operations' phrases, punctuation names) are boosted only when the utterance is short: they
# are said alone, and boosting them in every sentence would turn 還行 into 換行 (the user's choice, 2026-10-11).
# "Short" is judged by the first decode's text (at most the longest command + _COMMAND_SLACK characters), which then
# runs again with the commands: the audio's length was no use, TTS 換行 alone was already 1.7s with its silences.
# With TTS the boost fixed 據點 -> 句點 and 珊姐好 -> 刪節號; alone, 問好 / 分好 became 問號 / 分號.
_COMMAND_SCORE = 5.0
_COMMAND_SLACK = 2
_TEXT_CHARS = re.compile(r"[\s\W_]+")
_SEL_LOST = "找不到選取的文字，沒有修改"   # a terminal selection that moved or the cursor could not reach
_NO_RECORD = "目前沒有任何操作會錄音，無法語音輸入。請到「設定 → 自訂操作」加入「錄音」。"
# from the call to PlaySound until the start sound has left the speakers: measured 0.17s on the user's machine
# (Realtek speakers, Logi C270 microphone, 2026-10-13); the level bars and muting wait the sound's length plus this,
# or Kalimba's tail moved the bars and Woodblock (0.19s) was muted before it was heard
_CUE_LATENCY = 0.25
_STEP_GAP = 0.03           # seconds between the steps of an operation
_MENU_ROWS = 8


@dataclass
class _Press:
    """One press of a key or mouse button that some operation uses."""
    key: str
    probe: object                     # ContextProbe started at key-down (a double tap reuses the first tap's)
    down_at: float
    dictated: bool                    # the last dictation could still be deleted at key-down (情境「剛輸入完」)
    first: "_Press | None" = None     # the second press of a double tap: its first tap
    up_at: float = 0.0
    gesture: str = ""                 # tap / double / hold, once known
    rule: object = None               # the operation it runs (None: unbound, the gesture goes back to the key)
    redictate: object = None          # a held second press: the hold operation whose recording it does
    decided: bool = False             # rule is known
    held: bool = False                # down for gesture_time or longer
    released: bool = False
    finished: bool = False
    recording: bool = False           # the microphone is on for it
    shown: bool = False               # its operation records: the indicator showed, the start sound played
    cue: str = ""                     # the start sound played for it (taken out of the audio before recognition)
    cue_at: float = 0.0               # seconds into the recording when it played
    mic_at: float = 0.0               # monotonic time the microphone started for it
    consumed: bool = False            # it only stopped a recording a tap had started
    deleted: object = None            # Selection a 刪除 step removed (the dictation goes there)
    resolved: threading.Event = field(default_factory=threading.Event)   # steps before 錄音 are done


def make_icon(color: QColor) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(22, 6, 20, 34), 10, 10)          # mic capsule
    pen = QPen(color, 5)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(14, 16, 36, 34), 200 * 16, 140 * 16)     # holder
    p.drawLine(QPointF(32, 50), QPointF(32, 58))
    p.end()
    return QIcon(pm)


class App(QObject):
    key_down = Signal(str, float)            # key name, time
    key_up = Signal(str, float)
    main = Signal(object)                    # run a callable on the Qt main thread (keystrokes, clipboard, UI)
    edited = Signal(object, str, object)     # Selection, replacement, _Press: spoken on a selection
    candidates_ready = Signal(object, list)  # Selection, rows for the 選字 menu
    record = Signal(dict)         # one transcript log entry for the settings window
    status = Signal(str)

    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.cfg = Config.load()
        self.recorder = Recorder()
        self.recognizer: Recognizer | None = None
        # self.second: Recognizer | None = None         # second-opinion model (SenseVoice), off for now
        self.worker = ThreadPoolExecutor(max_workers=1)   # serializes model loads and transcription
        self.steps = ThreadPoolExecutor(max_workers=1)    # runs operations' steps in order
        # self.bg = ThreadPoolExecutor(max_workers=1)     # second opinion, second model load
        self._status_text = ""
        self._records = deque(maxlen=100)       # transcript log, kept for this session only
        self.session = Session()
        self.muter = Muter()
        self._stop_cue = False                   # the end sound waits for the text to be in (stop_sound_at "done")
        self._press: _Press | None = None        # the key or button held now
        self._pending: _Press | None = None      # a tap waiting to see whether a second press follows
        self._pending_timer = QTimer(self, singleShot=True, timeout=self._pending_expired)
        self._toggle: _Press | None = None       # a recording a tap started: the next press stops it
        self._mic: _Press | None = None          # the press the microphone records for
        self._ignored: set[str] = set()          # keys pressed while another was held
        self._could_record = rules.can_record(self.cfg.rules)
        self.hotwords = Hotwords()
        self._pack_stamp = None
        self._load_packs()
        self.memory = Memory()
        self._menu = None                        # ("pick", Selection) while the 選字 menu is up

        self.icon_idle = make_icon(QColor(235, 235, 235) if self._dark_taskbar() else QColor(40, 40, 40))
        self.icon_rec = make_icon(QColor(255, 69, 58))

        self.overlay = Overlay(lambda: self.recorder.level)
        self.picker = Picker()
        self.picker.picked.connect(self.on_menu_pick)
        self.picker.typed.connect(self.on_menu_typed)
        self.picker.closed.connect(self._forget_menu)
        self._picker_hwnd = int(self.picker.winId())
        self.settings = SettingsDialog(self.cfg, self.hotwords, self.memory)
        self.settings.applied.connect(self.apply_settings)

        self.tray = QSystemTrayIcon(self.icon_idle)
        menu = QMenu()
        menu.addAction(QAction("設定…", menu, triggered=self.open_settings))
        menu.addSeparator()
        menu.addAction(QAction("結束", menu, triggered=self.quit))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.open_settings() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

        self.key_down.connect(self.on_key_down)
        self.key_up.connect(self.on_key_up)
        self.main.connect(lambda fn: fn())
        self.edited.connect(self.on_edited)
        self.candidates_ready.connect(self.on_candidates)
        self.status.connect(self.on_status)
        self.record.connect(self.on_record)

        self.hotkeys = Hotkeys(self.key_down.emit, self.key_up.emit, self._on_other_key)
        self.hotkeys.set_keys(rules.bound_keys(self.cfg.rules))
        self.hotkeys.start()
        self._mouse = self._watch_mouse()
        self.worker.submit(self._load_model_job)
        # self.bg.submit(self._load_second_job)
        candidates.preload()   # the disfluency pass uses the dictionary
        if self.cfg.autostart:
            self._set_autostart()   # re-register so the entry follows the app if its folder was moved
        if not self._could_record:
            self._notify(_NO_RECORD)

    @staticmethod
    def _dark_taskbar() -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 0
        except OSError:
            return True

    def _on_other_key(self, vk: int):
        """Keyboard hook thread: a key the user pressed (injected keys never get here). Typing in the 選字 menu's
        input box leaves the target app alone; any other key closes the menu."""
        import ctypes
        if ctypes.windll.user32.GetForegroundWindow() == self._picker_hwnd:
            return
        self.session.on_input()
        term_watcher.forget()
        self.main.emit(self.picker.dismiss)

    def _on_picker(self, pt) -> bool:
        """Mouse hook thread: the point is on the 選字 menu."""
        import ctypes.wintypes
        u32 = ctypes.windll.user32
        u32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
        hwnd = u32.WindowFromPoint(pt)
        return bool(hwnd) and u32.GetAncestor(hwnd, 2) == self._picker_hwnd   # GA_ROOT

    def _watch_mouse(self):
        """Any click may move the caret, which ends what session.py knows about the text before it. Left button
        drags may select text in a terminal (selection.TermWatcher). Our own clicks (injected) are skipped."""
        from pynput import mouse
        downs = {0x201, 0x204, 0x207, 0x20B}   # left / right / middle / x button down

        def on_event(msg, data):
            if data.flags & 1:   # LLMHF_INJECTED
                return False
            if self.hotkeys.is_bound_mouse(msg, data.mouseData):
                return False   # a button an operation uses is not a click
            if msg in downs:
                if self._on_picker(data.pt):
                    return False   # a click in the 選字 menu leaves the caret and the selection where they are
                self.main.emit(self.picker.dismiss)   # a click anywhere else closes it
                self.session.on_input()
                if msg == 0x201:
                    term_watcher.mouse_down(data.pt.x, data.pt.y)
                else:
                    term_watcher.forget()
            elif msg == 0x202:
                term_watcher.mouse_up(data.pt.x, data.pt.y)
            return False   # never passed on to pynput's (unused) callbacks: keeps the hook cheap
        listener = mouse.Listener(win32_event_filter=on_event)
        listener.daemon = True
        listener.start()
        return listener

    # --- models ---
    def _load_model_job(self):
        key = models.MAIN_MODEL
        try:
            if not models.is_installed(key):
                def progress(done, total):
                    pct = f"{done * 100 // total}%" if total else f"{done >> 20} MB"
                    self.status.emit(f"下載模型中… {pct}")
                models.download(key, progress)
            self.status.emit("載入模型中…")
            self.recognizer = Recognizer(key)
            self.status.emit("就緒")
        except Exception as e:
            log.exception("model load failed")
            self.status.emit(f"模型載入失敗：{e}")

    # def _load_second_job(self):
    #     key = models.SECOND_MODEL
    #     try:
    #         if not models.is_installed(key):
    #             models.download(key)
    #         t0 = time.monotonic()
    #         self.second = Recognizer(key)
    #         log.info("%s loaded in %.1fs", key, time.monotonic() - t0)
    #     except Exception:
    #         log.exception("%s failed to load", key)

    # --- gestures: which operation a press runs ---
    def _rules_for(self, key: str, gesture: str) -> list:
        source = rules.MOUSE if key in MOUSE_TRIGGERS else rules.KEY
        return rules.candidates(self.cfg.rules, source, key, gesture)

    def _records_on(self, key: str, gestures) -> bool:
        return any(r.record_index() is not None for g in gestures for r in self._rules_for(key, g))

    def _only_tap_records(self, key: str) -> bool:
        """The key's only operations are taps that record: a press is a recording from key-down (held past
        gesture_time it is given back, after the indicator and start sound)."""
        taps = self._rules_for(key, TAP)
        return (bool(taps) and all(r.record_index() is not None for r in taps)
                and not self._rules_for(key, DOUBLE) and not self._rules_for(key, HOLD))

    def on_key_down(self, key: str, t: float):
        if self._press is not None:          # another key is held: this one is ignored
            self._ignored.add(key)
            return
        self._close_menu()
        if self._toggle is not None:         # any press stops a recording a tap started
            toggle, self._toggle = self._toggle, None
            self._press = _Press(key, None, t, False, consumed=True)
            self._stop_recording(toggle)
            return
        pending, self._pending = self._pending, None
        self._pending_timer.stop()
        double = pending is not None and pending.key == key and t - pending.up_at < self.cfg.gesture_time
        if pending is not None and not double:
            self._discard_mic(pending)
            self._decide(pending, TAP)       # another key came first: the tap is just a tap
        if double:
            press = _Press(key, pending.probe, t, pending.dictated, first=pending)
        else:
            press = _Press(key, ContextProbe(), t, self.session.deletable() is not None)
        self._press = press
        # recording starts now (no lost first syllable) whenever this press may turn into a recording
        if self._records_on(key, (DOUBLE, HOLD) if double else (TAP, HOLD)):
            self._start_mic(press)
            if not double and self._only_tap_records(key):
                self._show_recording(press)
        if double:
            self._decide(press, DOUBLE)
        QTimer.singleShot(int(self.cfg.gesture_time * 1000), lambda: self._hold_check(press, timer=True))

    def _hold_check(self, press: _Press, timer: bool = False):
        if press.released or press.held:
            return
        if timer and not self.hotkeys.is_down(press.key):
            # released already, the main thread has not handled it yet: a double tap's delete in a terminal kept it
            # busy past 0.3s, the press counted as held, recorded (delete and say it again) and played the end sound
            # (2026-10-13). on_key_up decides from the hook's times.
            return
        press.held = True
        if press.first is None:
            self._decide(press, HOLD)
        elif press.decided:
            self._maybe_redictate(press)

    def on_key_up(self, key: str, t: float):
        if key in self._ignored:
            self._ignored.discard(key)
            return
        press = self._press
        if press is None or press.key != key:
            return
        self._press = None
        press.released, press.up_at = True, t
        log.info("press %s: %.2fs%s%s", key, t - press.down_at, " (second of a double tap)" if press.first else "",
                 ", indicator shown" if press.shown else "")
        if press.consumed:
            return
        if not press.held and t - press.down_at >= self.cfg.gesture_time:   # the hold timer has not run yet
            self._hold_check(press)
        if press.first is None and not press.held:
            if self._rules_for(key, DOUBLE):     # wait to see whether a second press follows
                if not self._records_on(key, (TAP,)):
                    self._discard_mic(press)
                self._pending = press
                self._pending_timer.start(int(self.cfg.gesture_time * 1000))
                return
            self._decide(press, TAP)
            return
        if press.decided:
            self._finish(press)

    def _pending_expired(self):
        press, self._pending = self._pending, None
        if press is not None:
            self._decide(press, TAP)

    def _decide(self, press: _Press, gesture: str):
        """Pick the operation for the gesture; waits for the selection probe only when the choice depends on it."""
        press.gesture = gesture
        cands = self._rules_for(press.key, gesture)
        if any(r.when == rules.SELECTION for r in cands):
            self._when_probed(press.probe, lambda ctx: self._apply(
                press, rules.pick(cands, ctx.selection is not None, press.dictated)))
        else:
            self._apply(press, rules.pick(cands, False, press.dictated))

    def _when_probed(self, probe, fn):
        """Run fn(Context) on the main thread once probe has read the focused app."""
        def wait():
            ctx = probe.result()
            self.main.emit(lambda: fn(ctx))
        threading.Thread(target=wait, daemon=True).start()

    def _apply(self, press: _Press, rule):
        """Main thread: start the operation: its steps (those before 錄音 when it records)."""
        press.rule, press.decided = rule, True
        log.info("gesture %s %s: %s", press.key, press.gesture,
                 [f"{st.type}:{st.value}" for st in rule.steps] if rule else "none (given back)")
        i = rule.record_index() if rule else None
        if rule is None:
            self._discard_mic(press)
        elif i is None:
            redo = press.gesture == DOUBLE and not press.released and self._records_on(press.key, (HOLD,))
            if not redo:
                self._discard_mic(press)
            self._run_steps(press, rule.steps, done=press.resolved.set)
        elif self.recognizer is None:
            self._discard_mic(press)
            self._notify(self._status_text or "模型尚未就緒")
            press.finished = True
            return
        else:
            if not press.recording:
                self._start_mic(press)
            self._show_recording(press)
            self._run_steps(press, rule.steps[:i], done=press.resolved.set)
        if press.gesture == DOUBLE and press.held:
            self._maybe_redictate(press)
        if press.released:
            self._finish(press)

    def _maybe_redictate(self, press: _Press):
        """A double tap's second press held: when the key's hold operation records, record here too, after the
        double tap's steps (a delete): delete and say it again."""
        if press.rule is None or press.rule.record_index() is not None or self.recognizer is None:
            return
        hold = rules.pick(self._rules_for(press.key, HOLD), False, False)
        if hold is not None and hold.record_index() is not None:
            press.redictate = hold
            if not press.recording:
                self._start_mic(press)
            self._show_recording(press)

    def _finish(self, press: _Press):
        """Main thread: the press is released and its operation known."""
        if press.finished:
            return
        press.finished = True
        rule = press.rule
        if press.redictate is not None:
            self._stop_recording(press)
        elif rule is None:
            self.hotkeys.replay(press.key, 2 if press.gesture == DOUBLE else 1)
        elif rule.record_index() is None:
            self._discard_mic(press)
        elif press.gesture == HOLD:
            self._stop_recording(press)
        elif press.recording:
            self._toggle = press

    # --- microphone ---
    def _start_mic(self, press: _Press):
        if self.recognizer is None:
            return
        if self._mic is not None:
            self._discard_mic(self._mic)
        try:
            self.recorder.start(self.cfg.mic)
        except Exception as e:
            log.exception("mic start failed")
            self._notify(f"無法開啟麥克風：{e}")
            return
        self._mic, press.recording, press.mic_at = press, True, time.monotonic()
        self._hwnd = foreground()
        self._stop_cue = False   # an end sound still waiting for the speakers belongs to an older recording

    def _show_recording(self, press: _Press):
        """The indicator fades in and the start sound plays together, once the press is known to record (the
        user's design, 2026-10-13): from `_apply` when the gesture is decided (a hold at gesture_time, a tap at
        its release or gesture_time later), at key-down when the key only has recording taps. Until then the
        microphone records unseen. The indicator showed at 0.1s before (0.1s after the microphone opened, so a
        double tap's ~0.12s first press rarely flashed it) and the sound with it, which beeped on double taps."""
        if press.recording and self._mic is press and not press.shown:
            press.shown = True
            self.tray.setIcon(self.icon_rec)
            self.overlay.show_recording()
            cues.play(self.cfg.start_sound, "start")
            press.cue, press.cue_at = self.cfg.start_sound, time.monotonic() - press.mic_at
            # while the start sound plays the level bars stay still and the speakers on; recording goes on (the
            # user speaks over it); muting waits for its end (the user's choices, 2026-10-13)
            wait = cues.duration(self.cfg.start_sound, "start")
            if wait:
                wait += _CUE_LATENCY
                self.recorder.still(wait)
            if self.cfg.mute_while_recording:
                QTimer.singleShot(int(wait * 1000), lambda: self._mute_for(press))

    def _mute_for(self, press: _Press):
        if press.recording and self._mic is press:
            self.muter.mute()

    def _release_mic(self, press: _Press):
        if not press.recording:
            return None
        press.recording = False
        if self._mic is press:
            self._mic = None
        self.tray.setIcon(self.icon_idle)
        return self.recorder.stop()

    def _discard_mic(self, press: _Press):
        if press.recording:
            self._release_mic(press)
            self.overlay.hide_overlay()
            self._input_done()

    def _stop_recording(self, press: _Press):
        audio = self._release_mic(press)
        if audio is None:
            self.overlay.hide_overlay()
            self._input_done()
            return
        if press.shown and audio.size and self.cfg.stop_sound:
            # the end sound, only for a recording that goes to recognition: now, or once the text is in (_input_done), the user's setting
            # (2026-10-13). Now: the microphone is off, so muted speakers come back for it.
            if self.cfg.stop_sound_at == "done":
                self._stop_cue = True
            else:
                self.muter.restore()
                cues.play(self.cfg.stop_sound, "stop")
        self.overlay.show_thinking()
        self.worker.submit(self._transcribe_job, self.recognizer, audio, press, self._hwnd)

    def _input_done(self):
        """Main thread: a recording ended in nothing, or its text was pasted. Unmute, unless another recording
        has started meanwhile."""
        if self._mic is None:
            self.muter.restore()
            if self._stop_cue:
                self._stop_cue = False
                cues.play(self.cfg.stop_sound, "stop")

    # --- steps ---
    def _run_steps(self, press: _Press, steps: list, done=None):
        """Run steps in order on the steps thread (錄音 is not a step to run: the press records)."""
        steps = [s for s in steps if not (s.type == rules.COMMAND and s.value == rules.RECORD)]
        if not steps:
            if done:
                done()
            return
        self.steps.submit(self._steps_job, press, steps, done)

    def _steps_job(self, press: _Press, steps: list, done):
        try:
            for n, step in enumerate(steps):
                if n:
                    time.sleep(_STEP_GAP)
                if step.type == rules.WAIT:
                    time.sleep(max(0, int(step.value or 0)) / 1000)
                elif step.type == rules.KEYS:
                    vks = combo_vks(step.value)
                    if vks:
                        press_chord(*vks)
                        self.session.on_input()     # the caret may have moved
                elif step.type == rules.TEXT:
                    self._on_main(lambda t=step.value: self._type_text(t))
                elif step.type == rules.COMMAND and step.value == rules.DELETE_SELECTION:
                    ctx = press.probe.result()
                    self._on_main(lambda: self._delete_selection(press, ctx))
                elif step.type == rules.COMMAND and step.value == rules.DELETE_DICTATION:
                    self._on_main(self.delete_last)
                elif step.type == rules.COMMAND and step.value == rules.PICK:
                    ctx = press.probe.result()
                    sel = ctx.selection if press.deleted is None else None
                    self._on_main(lambda: self._open_candidates(sel, ctx.terms))
        except Exception:
            log.exception("steps failed")
        finally:
            if done:
                done()

    def _on_main(self, fn):
        """Steps thread: run fn on the main thread and wait for it."""
        ev = threading.Event()

        def run():
            try:
                fn()
            finally:
                ev.set()
        self.main.emit(run)
        ev.wait(10)

    def _type_text(self, text: str):
        if text:
            paste_text(text)
            self.session.dictated(text, None, foreground())

    def _delete_selection(self, press: _Press, ctx):
        """刪除選取的文字: the text selected when the (first) press went down."""
        sel = ctx.selection if press.deleted is None else None
        if sel is None:
            log.info("delete selection: nothing selected")
            return
        log.info("delete %r on %s", sel.text, sel.app)
        if sel.term is not None:
            self.overlay.hide_now()
        if not _delete_selection(sel):
            self._notify(_SEL_LOST)
            return
        press.deleted = sel
        self.session.replaced_selection(sel.before, "", foreground())
        self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "刪除", "main": sel.text})

    def delete_last(self):
        """Main thread, 刪除剛輸入的文字: delete the last dictation (or text spoken over a selection) while it is
        still the last change before the caret. Nothing else is undone."""
        s = self.session
        step = s.deletable()
        if step is None:
            log.info("delete: nothing to delete")
            return
        n, tail = s.plan_to(step.before)
        log.info("delete last: %d backspaces + %r", n, tail)
        if self.cfg.save_recordings:
            recordings.note("delete_last")
        backspace(n)
        paste_text(tail)
        s.deleted_last(step)

    @staticmethod
    def _after_steps(press: _Press) -> list:
        """The steps after 錄音 in the press's operation: run once its text is pasted."""
        rule = press.redictate or press.rule
        i = rule.record_index() if rule else None
        return rule.steps[i + 1:] if i is not None else []

    # --- transcription ---
    def _transcribe_job(self, rec, audio, press: _Press, hwnd):
        stamp = time.strftime("%H:%M:%S")
        try:
            ctx = press.probe.result()
            press.resolved.wait(3.0)
            t0 = time.monotonic()
            heard = cues.remove(audio, press.cue, SAMPLE_RATE, press.cue_at) if press.cue else audio
            self._load_packs()
            words = self.hotwords.bias() + [(t, _SCREEN_TERM_SCORE) for t in ctx.terms]
            raw = rec.recognize(heard, _bias(words))
            commands = self._command_words()
            if commands and len(_TEXT_CHARS.sub("", raw)) <= max(map(len, commands)) + _COMMAND_SLACK:
                again = rec.recognize(heard, _bias([(w, _COMMAND_SCORE) for w in commands] + words))
                if again != raw:
                    log.info("asr with commands boosted: %s -> %s", raw, again)
                raw = again
            t_asr = time.monotonic() - t0
            log.info("asr %.2fs: %s%s", t_asr, raw, f" (terms: {len(ctx.terms)})" if ctx.terms else "")
            # no sentence-final punctuation when the caret has text on both sides: the user is inserting
            mid = ctx.caret is not None and all(s.strip() for s in ctx.caret)
            steps = []   # (name, before, after) of every step that changed the text, for the transcript
            formatted = format_text(raw, mid, steps)
            known = spelling.table(self._known_terms(ctx.terms), self.hotwords.aliases())
            before = formatted
            formatted, spelled = spelling.fix(formatted, known)
            if spelled:
                log.info("spelling: %s", ", ".join(f"{a} -> {b}" for a, b in spelled))
                steps.append(("英文拼寫", before, formatted))
            entry = {"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "steps": steps}
            selected = ctx.selection is not None and press.deleted is None and press.first is None
            op = rules.pick(rules.candidates(self.cfg.rules, rules.SPEECH, formatted), selected, press.dictated)
            if op is not None:
                log.info("spoken operation: %s", op.phrase)
                self._keep(audio, asr=raw, fmt=formatted, result="", op=op.phrase)
                self.record.emit(dict(entry, kind="操作", main=op.phrase))
                self.main.emit(lambda: self._spoken_operation(press, op))
                return
            sym = edit.symbol(formatted) if self.cfg.punctuation_names else None
            if sym:
                steps.append(("標點名稱", formatted, sym))
            described = None if sym or not self.cfg.describe_char else edit.described(formatted)
            if described:
                log.info("described character: %s", described)
                steps.append(("指定字", formatted, described))
                sym = described   # typed as it is, like a punctuation mark
            text = sym or formatted
            if not sym:   # smart picks (single characters)
                prefix = ctx.selection.before if selected else (ctx.caret[0] if ctx.caret else "")
                text = self.memory.apply(text, prefix, self.hotwords.levels())
                if text != formatted:
                    steps.append(("選字記憶", formatted, text))
            if selected and not sym:
                spelt = edit.spelled(ctx.selection.text, raw)
                if spelt:   # letters over an English word: that word, spelled, in the selection's case
                    steps.append(("拼字", text, spelt[0]))
                    text, sym = spelt[0], True
                    self.main.emit(lambda: self._learn_spelled(spelt[1], spelt[2]))
            if selected:
                self._keep(audio, asr=raw, fmt=formatted, result=text, op=None, selection=ctx.selection.text)
                self._edit_job(ctx.selection, text, bool(sym), entry, press)
                return
            replaced = press.deleted
            entry.update(kind="輸入", main=text)
            if replaced is not None:
                if not sym:
                    trimmed = edit.replacement(replaced.text, text)
                    if trimmed != text:
                        steps.append(("取代標點", text, trimmed))
                    text = trimmed
                entry.update(kind="取代", main=f"{replaced.text} → {text}")
                steps.append(("刪除後重講", replaced.text, None))
            self._keep(audio, asr=raw, fmt=formatted, result=text, op=None,
                       replaced=replaced.text if replaced is not None else None)
            self.record.emit(entry)
            self.main.emit(lambda: self._dictate(text, audio, hwnd, press))
        except Exception:
            log.exception("transcribe failed")
            self.main.emit(self.overlay.hide_overlay)
            self.main.emit(self._input_done)

    def _learn_spelled(self, old: str, new: str):
        """A word spelled over a selection counts like a 選字 pick: priority, and old as a misheard spelling."""
        log.info("spelled: %s -> %s", old, new)
        if self.cfg.save_recordings:
            recordings.note("spelled", before=old, after=new)
        self.hotwords.picked(old, new)
        self.settings.refresh_hotwords()
        self._suggest_pack(new)

    def _load_packs(self):
        """The ticked packs' words, read again when a pack file was edited (checked before each decode)."""
        stamp = packs.stamp()
        if stamp != self._pack_stamp:
            self._pack_stamp = stamp
            self.hotwords.set_packs(packs.words_for(self.cfg.packs), packs.PCT)

    def _suggest_pack(self, word: str):
        """A word picked, typed or spelled that an unticked term pack lists: a notice suggests the pack, once per
        pack (the user's request, 2026-10-13; remembered in config.pack_hints)."""
        word = word.strip()
        for pack in packs.available():
            if pack.id in self.cfg.packs or pack.id in self.cfg.pack_hints:
                continue
            if any(w == word or (is_latin(word) and w.casefold() == word.casefold()) for w in pack.words):
                log.info("pack hint: %s in %s", word, pack.id)
                self.cfg.pack_hints.append(pack.id)
                self.cfg.save()
                self.tray.showMessage("VoiceInput", f"「{word}」收錄在詞包「{pack.name}」。到設定的詞庫頁勾選它，"
                                      "同類的詞會更容易辨識。", self.icon_idle, 8000)
                return

    def _keep(self, audio, **info):
        """Development only (the user will remove it before release): save the utterance (recordings.py)."""
        if self.cfg.save_recordings and audio is not None and audio.size:
            recordings.save(audio, SAMPLE_RATE, **info)

    def _command_words(self) -> list[str]:
        """What may be said alone as a command: speech operations' phrases, and punctuation names when they are on.
        Not a word of one repeated character: boosted, 點點點 came out as 點點點點點點點點點."""
        words = [r.phrase.strip() for r in self.cfg.rules
                 if r.enabled and r.source == rules.SPEECH and not rules.problems(r, self.cfg.rules)]
        if self.cfg.punctuation_names:
            words += list(edit.SYMBOLS)
        return [w for w in words if w and len(set(w)) > 1]

    def _spoken_operation(self, press: _Press, op):
        self.overlay.hide_overlay()
        self._input_done()
        self._run_steps(press, op.steps)

    # --- dictation ---
    def _dictate(self, text: str, audio, hwnd: int, press: _Press):
        """Main thread: paste a dictation, then run the operation's steps after 錄音."""
        self.overlay.hide_overlay()
        log.info("result: %s", text)
        if not text:
            self._input_done()
            return
        paste_text(text)
        self._input_done()
        self.session.dictated(text, audio, foreground() or hwnd)
        # u = self.session.dictated(text, audio, foreground() or hwnd)
        # if audio is not None:
        #     self.bg.submit(self._suspect_job, u)
        self._run_steps(press, self._after_steps(press))

    # Second opinion, off for now: SenseVoice re-decodes each dictation and suspects.find() lists words it heard
    # as a likelier word. The menu that offered them was removed with the other popups. To turn it back on, also
    # restore `from . import suspects`, `from .textfmt import ends_with_punct`, self.second / self.bg in __init__ and
    # _load_second_job.
    # def _second_text(self, u) -> str | None:
    #     """The second model's formatted transcript of a dictation (cached on it)."""
    #     if u.second is None and self.second is not None and u.audio is not None:
    #         t0 = time.monotonic()
    #         u.second = format_text(self.second.recognize(u.audio), not ends_with_punct(u.text))
    #         log.info("second opinion %.2fs: %s", time.monotonic() - t0, u.second)
    #     return u.second
    #
    # def _suspect_job(self, u):
    #     try:
    #         found = suspects.find(u.text, self._second_text(u))
    #         if found:
    #             log.info("suspects: %s", [(f.word, f.options) for f in found])
    #     except Exception:
    #         log.exception("suspect check failed")

    def _notify(self, text: str):
        self.tray.showMessage("VoiceInput", text, self.icon_idle, 4000)

    # --- popups: 選字 and the hotword notice ---
    def _close_menu(self):
        if self.picker.isVisible():
            self.picker.hide()
        self._menu = None

    def _open_candidates(self, sel, terms=()):
        """選字: the candidate menu for the selection, under it. terms: English terms on screen."""
        if sel is None or not sel.text.strip():
            log.info("pick: nothing selected")
            return
        log.info("candidates for %r on %s", sel.text, sel.app)
        self._menu = ("pick", sel)
        self.picker.show_loading(sel.text.strip(), sel.rect)
        threading.Thread(target=lambda: self.candidates_ready.emit(sel, self._menu_words(sel, terms)), daemon=True).start()

    def _menu_words(self, sel, terms=()) -> list[str]:
        """Rows for the selection. One Chinese character: what the smart picks decide here, everything picked for it
        before, then same-sounding characters. A Chinese word: same-sounding words, the same reading first; hotwords
        first among equals, heaviest first. English: up to 4 known terms that sound like it (sounds.py: the user's
        list, ticked term packs, terms on screen), then ways of writing it, the most picked style first (the
        hotwords among them first)."""
        text = sel.text.strip()
        try:
            if is_latin(text):
                rows = self.memory.ordered_variants(text)
                known = self._known_terms(terms)
            elif len(text) == 1:
                rows = candidates.homophones(text)
            else:
                # the same reading first (the user's choice, 2026-10-11: before common words of another tone), then
                # without tones, fuzzy; within each, hotwords by weight, common words, frequency
                found = candidates.ranked(text)
                key = reading_key(text)
                for h in self.hotwords.items:
                    if h.word != text and h.word not in found and reading_key(h.word) == key:
                        found[h.word] = (0, 0)
                weights = self.hotwords.weights_for(found)
                rows = sorted(found, key=lambda w: (found[w][0], -weights.get(w, 0),
                                                    -found[w][1] < candidates.COMMON, found[w][1]))
            if len(text) == 1:
                key = reading_key(text)
                line = sel.before + sel.text + sel.after
                start = len(sel.before) + sel.text.index(text)
                left, right = situation(line, start, start + 1)
                rows = [self.memory.lookup(key, left, right, single=True)] + self.memory.values(key) + rows
            elif is_latin(text):
                weights = self.hotwords.weights_for(rows)
                rows.sort(key=lambda w: -weights.get(w, 0))   # stable: the rest keep their order
                rows = sounds.alike(text, known) + rows
        except Exception:
            log.exception("candidates failed")
            rows = []
        out = []
        for w in rows:
            if w and w != text and w not in out:
                out.append(w)
        return out[:_MENU_ROWS]

    def _known_terms(self, terms) -> list[str]:
        """English terms 選字 may offer for a misheard word: the user's list (highest 優先度 first), the ticked term
        packs, then the terms on screen."""
        listed = sorted((h for h in self.hotwords.items if is_latin(h.word)), key=lambda h: -h.pct)
        return [h.word for h in listed] + [w for w in self.hotwords.pack_pct if is_latin(w)] + list(terms)

    def on_candidates(self, sel, words: list):
        if self._menu and self._menu[0] == "pick" and self._menu[1] is sel and self.picker.isVisible():
            self.picker.show_candidates(sel.text.strip(), words)

    def _forget_menu(self):
        self._menu = None

    def on_menu_pick(self, row: int):
        menu, words = self._menu, self.picker.candidates
        self._menu = None
        if menu and menu[0] == "pick" and 0 <= row < len(words):
            self._apply_pick(menu[1], words[row])

    def on_menu_typed(self, word: str):
        """Text typed in the menu's input box: once focus is back in the app, it replaces the selection like a pick
        and becomes a hotword at the highest weight (single characters are learned as smart picks instead)."""
        menu = self._menu
        self._menu = None
        if menu and menu[0] == "pick":
            QTimer.singleShot(150, lambda: self._apply_pick(menu[1], word, typed=True))

    def _apply_pick(self, sel, word: str, typed: bool = False):
        """A pick replaces the selection. A single character is learned with its context (smart picks); a word
        gains hotword weight and the word it replaced loses some."""
        text = sel.text.strip()
        lead = sel.text.index(text)
        log.info("menu %s: %s -> %s", "typed" if typed else "pick", text, word)
        if sel.term is not None:
            self.overlay.hide_now()
        if not _paste_over(sel, sel.text[:lead] + word + sel.text[lead + len(text):]):
            self._notify(_SEL_LOST)
            return
        line = sel.before + sel.text + sel.after
        start = len(sel.before) + lead
        self.memory.record(line, start, start + len(text), word)
        if self.cfg.save_recordings:
            recordings.note("typed" if typed else "pick", before=text, after=word, line=line)
        self.hotwords.picked(text, word)
        if typed and self.hotwords.add(word):
            log.info("hotword typed: %s", word)
        self._suggest_pack(word)
        self.settings.refresh_hotwords()
        self.session.replaced_selection(sel.before, word, foreground())
        self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "選字", "main": f"{text} → {word}",
                          "steps": [("選單輸入", word, None)] if typed else []})

    # --- utterances on a selection ---
    def _edit_job(self, sel, text: str, sym: bool, entry: dict, press: _Press):
        """Worker thread: an utterance spoken on a selection replaces it."""
        if sym:
            rep = text
        elif self.cfg.period_to_comma and sel.text.strip() == "。":
            rep = edit.continue_sentence(text)
            entry["steps"].append(("選取句號", text, rep))
        else:
            rep = edit.replacement(sel.text, text)
            if rep != text:
                entry["steps"].append(("取代標點", text, rep))
        entry.update(kind="取代", main=f"{sel.text} → {rep}")
        log.info("edit on %s: %s", sel.app, entry["main"])
        self.record.emit(entry)
        self.edited.emit(sel, rep, press)

    def on_edited(self, sel, rep: str, press: _Press):
        if sel.term is not None:
            self.overlay.hide_now()
        self.overlay.hide_overlay()
        if not rep:
            self._input_done()
            return
        pasted = _paste_over(sel, rep)
        self._input_done()
        if not pasted:
            self._notify(_SEL_LOST)
            return
        # 刪除 removes the spoken text; over a 。 turned into ，…。 it puts the 。 back
        undo_to = sel.before + ("。" if rep.startswith("，") and sel.text.strip() == "。" else "")
        self.session.replaced_selection(sel.before, rep, foreground(), undo_to)
        self._run_steps(press, self._after_steps(press))

    # --- settings / status ---
    def on_status(self, text: str):
        self._status_text = text
        log.info("status: %s", text)
        self.tray.setToolTip(f"VoiceInput — {text}")
        self.settings.set_status(text)

    def on_record(self, entry: dict):
        self._records.append(entry)
        self.settings.set_log(self._records)

    def open_settings(self):
        self.settings.load_values()
        self.settings.set_log(self._records)
        self.settings.set_status(self._status_text)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def apply_settings(self):
        self.hotkeys.set_keys(rules.bound_keys(self.cfg.rules))
        can = rules.can_record(self.cfg.rules)
        if self._could_record and not can:
            self._notify(_NO_RECORD)
        self._could_record = can
        self._set_autostart()

    def _set_autostart(self):
        try:
            autostart.set_enabled(self.cfg.autostart)
        except Exception as e:
            log.exception("autostart failed")
            self.settings.set_status(f"開機啟動設定失敗：{e}")

    def quit(self):
        self.muter.restore()
        self.hotkeys.stop()
        if self._mouse:
            self._mouse.stop()
        self.tray.hide()
        self.qapp.quit()


_CJK_CHAR = re.compile(r"[㐀-䶿一-鿿]")


def _bias(words: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Biasing words without a Chinese word another one starts with: with 剪貼 and 剪貼簿 both listed, X-ASR wrote 剪貼布
    (the short one is matched first and takes the boost; dev experiment 2026-10-11). English keeps both: dropping
    Gemini for Gemini CLI left Gemini unboosted (the user's recordings, 2026-10-13: 14/14 at 3 with both or alone,
    10/14 with Gemini CLI alone). The first of duplicates wins.
    A lowered word (negative score) sharing a start with another word is left out, so it never costs that one its
    boost or its place (城市 lowered next to 城市規劃)."""
    keys = [w.casefold() for w, _ in words]
    words = [(w, s) for (w, s), k in zip(words, keys)
             if s >= 0 or not any(o != k and (o.startswith(k) or k.startswith(o)) for o in keys)]
    seen = {w.casefold() for w, _ in words}
    out, kept = [], set()
    for w, score in words:
        k = w.casefold()
        if k in kept or (_CJK_CHAR.search(k) and any(o != k and o.startswith(k) for o in seen)):
            continue
        kept.add(k)
        out.append((w, score))
    return out


def _delete_selection(sel) -> bool:
    """Delete the selected text. In a terminal arrow keys move the cursor right after it first
    (selection.move_cursor_after), then Backspace removes it. False when that failed and nothing changed."""
    if sel.term is None:
        press_delete()
        return True
    term_watcher.forget()
    if not move_cursor_after(sel):
        return False
    backspace(len(sel.text), gap=0.02)
    return True


def _paste_over(sel, text: str) -> bool:
    """Replace the selected text: a paste replaces a selection, except in a terminal."""
    if sel.term is not None and not _delete_selection(sel):
        return False
    paste_text(text)
    return True


def main():
    paths.migrate_legacy()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(paths.LOG_PATH, encoding="utf-8"),
                  logging.StreamHandler()],
    )
    packs.install()
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(paths.LOCK_PATH))
    if not lock.tryLock(100):
        log.info("already running")
        return 0

    app = App(qapp)  # noqa: F841 (keep reference alive)
    return qapp.exec()
