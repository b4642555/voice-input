"""VoiceInput: single-process tray app. Hold a key or mouse button to talk, release to paste.

What an utterance does:
1. Text is selected (UI Automation): the utterance replaces it.
2. Otherwise it is dictated: formatted and pasted.
An utterance that is exactly a custom operation's phrase (換行, 送出 …) runs that operation instead, and a bare
punctuation name (逗號, 句號 …) is typed as the mark (edit.symbol).

What the mouse buttons and keys do is set by custom operations (rules.py, 2026-10-11). The defaults: hold the
middle button to record; double click it on a selection to delete it, or right after a dictation to delete that
(while it is still the last change). When a key's double tap does not record and its hold does, holding the second
press records again: delete and say it again. Gestures no operation takes are given back to the key.

All popup menus (選字, suspected mishearings, the hotword ✓ box) and hotwords were removed on the lean branch
(2026-10-10); popups are to be redesigned. The second opinion (SenseVoice + suspects.py) is commented out below.
"""
import logging
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PySide6.QtCore import QLockFile, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart, candidates, edit, models, paths, rules
from .asr import Recognizer
from .audio import Recorder
from .config import Config
from .hotkey import DOUBLE_TAP_GAP, TAP_THRESHOLD, Hotkeys
from .keys import MOUSE_TRIGGERS, combo_vks
from .mute import Muter
from .output import backspace, paste_text, press_chord, press_delete
from .overlay import Overlay
from .rules import DOUBLE, HOLD, TAP
from .selection import ContextProbe, move_cursor_after, term_watcher
from .session import Session, foreground
from .settings import SettingsDialog
from .textfmt import format_text, to_traditional

log = logging.getLogger("voiceinput")

_SCREEN_TERM_SCORE = 1.5
_SEL_LOST = "找不到選取的文字，沒有修改"   # a terminal selection that moved or the cursor could not reach
_NO_RECORD = "目前沒有任何操作會錄音，無法語音輸入。請到「設定 → 自訂操作」加入「錄音」。"
_INDICATOR_DELAY = 0.1     # seconds held before the recording indicator shows (a tap is < TAP_THRESHOLD)
_STEP_GAP = 0.03           # seconds between the steps of an operation


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
    held: bool = False                # down for TAP_THRESHOLD or longer
    released: bool = False
    finished: bool = False
    recording: bool = False           # the microphone is on for it
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
        self._press: _Press | None = None        # the key or button held now
        self._pending: _Press | None = None      # a tap waiting to see whether a second press follows
        self._pending_timer = QTimer(self, singleShot=True, timeout=self._pending_expired)
        self._toggle: _Press | None = None       # a recording a tap started: the next press stops it
        self._mic: _Press | None = None          # the press the microphone records for
        self._ignored: set[str] = set()          # keys pressed while another was held
        self._could_record = rules.can_record(self.cfg.rules)

        self.icon_idle = make_icon(QColor(235, 235, 235) if self._dark_taskbar() else QColor(40, 40, 40))
        self.icon_rec = make_icon(QColor(255, 69, 58))

        self.overlay = Overlay(lambda: self.recorder.level)
        self.settings = SettingsDialog(self.cfg)
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
        """Keyboard hook thread: a key the user pressed (injected keys never get here)."""
        self.session.on_input()
        term_watcher.forget()

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

    def on_key_down(self, key: str, t: float):
        if self._press is not None:          # another key is held: this one is ignored
            self._ignored.add(key)
            return
        if self._toggle is not None:         # any press stops a recording a tap started
            toggle, self._toggle = self._toggle, None
            self._press = _Press(key, None, t, False, consumed=True)
            self._stop_recording(toggle)
            return
        pending, self._pending = self._pending, None
        self._pending_timer.stop()
        double = pending is not None and pending.key == key and t - pending.up_at < DOUBLE_TAP_GAP
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
        if double:
            self._decide(press, DOUBLE)
        QTimer.singleShot(int(TAP_THRESHOLD * 1000), lambda: self._hold_check(press))

    def _hold_check(self, press: _Press):
        if press.released or press.held:
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
        if press.consumed:
            return
        if not press.held and t - press.down_at >= TAP_THRESHOLD:   # the hold timer has not run yet
            self._hold_check(press)
        if press.first is None and not press.held:
            if self._rules_for(key, DOUBLE):     # wait to see whether a second press follows
                if not self._records_on(key, (TAP,)):
                    self._discard_mic(press)
                self._pending = press
                self._pending_timer.start(int(DOUBLE_TAP_GAP * 1000))
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
        self._mic, press.recording = press, True
        self._hwnd = foreground()
        # the indicator waits a little so taps and double taps do not flash it (0.1s, not TAP_THRESHOLD: the user
        # found 0.3s and 0.2s too slow; a slow tap may flash it)
        QTimer.singleShot(int(_INDICATOR_DELAY * 1000), lambda: self._show_recording(press))

    def _show_recording(self, press: _Press):
        if press.recording and self._mic is press:
            self.tray.setIcon(self.icon_rec)
            self.overlay.show_recording()
            if self.cfg.mute_while_recording:
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
        self.overlay.show_thinking()
        self.worker.submit(self._transcribe_job, self.recognizer, audio, press, self._hwnd)

    def _input_done(self):
        """Main thread: a recording ended in nothing, or its text was pasted. Unmute, unless another recording
        has started meanwhile."""
        if self._mic is None:
            self.muter.restore()

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
        if not _delete_selection(sel):
            self._notify(_SEL_LOST)
            return
        press.deleted = sel
        self.session.replaced_selection(sel.before, "", foreground())
        self.record.emit({"time": time.strftime("%H:%M:%S"), "asr": "", "asr_s": None, "fmt": "",
                          "edit": f"刪除「{sel.text}」"})

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
            raw = rec.recognize(audio, [(t, _SCREEN_TERM_SCORE) for t in ctx.terms])
            t_asr = time.monotonic() - t0
            log.info("asr %.2fs: %s%s", t_asr, raw, f" (terms: {len(ctx.terms)})" if ctx.terms else "")
            # no sentence-final punctuation when the caret has text on both sides: the user is inserting
            mid = ctx.caret is not None and all(s.strip() for s in ctx.caret)
            formatted = format_text(raw, mid)
            selected = ctx.selection is not None and press.deleted is None and press.first is None
            op = rules.pick(rules.candidates(self.cfg.rules, rules.SPEECH, formatted), selected, press.dictated)
            if op is not None:
                log.info("spoken operation: %s", op.phrase)
                self.record.emit({"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "fmt": "",
                                  "edit": f"操作：「{op.phrase}」"})
                self.main.emit(lambda: self._spoken_operation(press, op))
                return
            sym = edit.symbol(formatted)
            text = sym or formatted
            entry = {"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "fmt": text}
            if selected:
                self._edit_job(ctx.selection, text, bool(sym), entry, press)
                return
            replaced = press.deleted
            if replaced is not None:
                if not sym:
                    text = edit.replacement(replaced.text, text)
                entry["edit"] = f"{replaced.text} → {text}（刪除後重講）"
            self.record.emit(entry)
            self.main.emit(lambda: self._dictate(text, audio, hwnd, press))
        except Exception:
            log.exception("transcribe failed")
            self.main.emit(self.overlay.hide_overlay)
            self.main.emit(self._input_done)

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

    # --- utterances on a selection ---
    def _edit_job(self, sel, text: str, sym: bool, entry: dict, press: _Press):
        """Worker thread: an utterance spoken on a selection replaces it."""
        if sym:
            rep = text
        elif self.cfg.period_to_comma and sel.text.strip() == "。":
            rep = edit.continue_sentence(text)
        else:
            rep = edit.replacement(sel.text, text)
        entry["edit"] = f"{sel.text} → {rep}"
        log.info("edit on %s: %s", sel.app, entry["edit"])
        self.record.emit(entry)
        self.edited.emit(sel, rep, press)

    def on_edited(self, sel, rep: str, press: _Press):
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
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(paths.LOCK_PATH))
    if not lock.tryLock(100):
        log.info("already running")
        return 0

    app = App(qapp)  # noqa: F841 (keep reference alive)
    return qapp.exec()
