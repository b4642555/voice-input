"""VoiceInput: single-process tray app. Hold a key or mouse button to talk, or tap it to talk until the next tap.

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

Recording is continuous listening (listen.py; on the continuous-mic branch since 2026-10-14 the one way, the
user's choice: no cutting the recording to say a command). Every pause ends a piece: said alone, an operation's
phrase or a punctuation name commits the draft and runs or types it; otherwise the piece joins the draft, which is
decoded again and typed in place (in place of the text selected when its speech began, if any: the user may select
with the mouse while listening), punctuation included. A commit (by a command, the end of listening, or every pause when
commit_at says so) decodes the draft as a whole, without sentence-final punctuation (the user says 句號). Moving the
caret leaves a draft as it is. Other gestures work while listening.

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
from .config import Config
from .hotwords import Hotwords
from .hotkey import Hotkeys
from .keys import MOUSE_TRIGGERS, combo_vks
from .listen import LEAD, Listener
from .memory import Memory, is_latin, situation
from .mute import Muter
from .output import backspace, paste_text, press_chord, press_delete
from .overlay import Overlay
from .picker import Picker
from .reading import reading_key
from .rules import DOUBLE, HOLD, TAP
from .selection import Context, ContextProbe, move_cursor_after, term_watcher
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
_WORDS = re.compile(r"[㐀-䶿一-鿿]|[A-Za-z0-9]+")   # a Chinese character or an English word counts one
_SEL_LOST = "找不到選取的文字，沒有修改"   # a terminal selection that moved or the cursor could not reach
_NO_RECORD = "目前沒有任何操作會錄音，無法語音輸入。請到「設定 → 自訂操作」加入「錄音」。"
# from the call to PlaySound until the start sound has left the speakers: measured 0.17s on the user's machine
# (Realtek speakers, Logi C270 microphone, 2026-10-13); the level bars and muting wait the sound's length plus this,
# or Kalimba's tail moved the bars and Woodblock (0.19s) was muted before it was heard
_CUE_LATENCY = 0.25
_STEP_GAP = 0.03           # seconds between the steps of an operation
_MENU_ROWS = 8
_DRAFT_MAX = 60            # seconds: a longer draft is committed at a pause (decoding it again gets slow)
_TAP_MAX = 0.6             # seconds: a press no hold operation takes, released sooner, is a tap
_SPACED = re.compile(r"[A-Za-z0-9]")


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
    decided: bool = False             # rule is known
    held: bool = False                # down for gesture_time or longer
    released: bool = False
    finished: bool = False
    deleted: object = None            # Selection a 刪除 step removed (the dictation goes there)
    listening: bool = False           # a piece of continuous listening, not a press
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
    candidates_ready = Signal(object, list)  # Selection, rows for the 選字 menu
    record = Signal(dict)         # one transcript log entry for the settings window
    status = Signal(str)

    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.cfg = Config.load()
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
        self.listener: Listener | None = None    # the microphone (listen.py)
        self._listen_owner: _Press | None = None   # the press whose operation turned listening on
        self._listen_shown = False               # it is known to listen: indicator and start sound
        self._early_pieces: list | None = None   # (Piece, probe) heard before that, handled once it is known
        self._early_lock = threading.Lock()
        self._listen_probe = None                # ContextProbe started when the latest speech began
        self._speech_sample = 0                  # where the latest speech began (with its lead-in)
        self._draft: _Draft | None = None        # text typed while listening, not committed yet
        self._draft_start = 0                    # the sample the draft's audio starts at (the last commit)
        self._ignored: set[str] = set()          # keys pressed while another was held
        self._could_record = rules.can_record(self.cfg.rules)
        self.hotwords = Hotwords()
        self._pack_stamp = None
        self._load_packs()
        self.memory = Memory()
        self._menu = None                        # ("pick", Selection) while the 選字 menu is up

        self.icon_idle = make_icon(QColor(235, 235, 235) if self._dark_taskbar() else QColor(40, 40, 40))
        self.icon_rec = make_icon(QColor(255, 69, 58))

        self.overlay = Overlay(lambda: self.listener.level if self._listening else 0.0)
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
            models.vad_path()   # continuous listening's voice activity detection, ~2MB
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
        """The key's only operations are taps that record: a press turns listening on from key-down."""
        taps = self._rules_for(key, TAP)
        return (bool(taps) and all(r.record_index() is not None for r in taps)
                and not self._rules_for(key, DOUBLE) and not self._rules_for(key, HOLD))

    def on_key_down(self, key: str, t: float):
        if self._press is not None:          # another key is held: this one is ignored
            self._ignored.add(key)
            return
        self._close_menu()
        pending, self._pending = self._pending, None
        self._pending_timer.stop()
        double = pending is not None and pending.key == key and t - pending.up_at < self.cfg.gesture_time
        if pending is not None and not double:
            self._decide(pending, TAP)       # another key came first: the tap is just a tap
        if double:
            press = _Press(key, pending.probe, t, pending.dictated, first=pending)
            if self._listen_owner is pending:
                self._listen_owner = press   # what the double tap does decides whether listening goes on
        else:
            press = _Press(key, ContextProbe(), t, self.session.deletable() is not None)
        self._press = press
        # listening starts now, unseen (no lost first syllable), whenever this press may turn it on
        if not self._listening and self._records_on(key, (DOUBLE, HOLD) if double else (TAP, HOLD)):
            self._start_listen(press)
            if not double and self._only_tap_records(key):
                self._show_listen(press)
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

    def on_key_up(self, key: str, t: float):
        if key in self._ignored:
            self._ignored.discard(key)
            return
        press = self._press
        if press is None or press.key != key:
            return
        self._press = None
        press.released, press.up_at = True, t
        log.info("press %s: %.2fs%s", key, t - press.down_at, " (second of a double tap)" if press.first else "")
        if not press.held and t - press.down_at >= self.cfg.gesture_time:   # the hold timer has not run yet
            self._hold_check(press)
        if press.first is None and not press.held:
            if self._rules_for(key, DOUBLE):     # wait to see whether a second press follows
                if not self._records_on(key, (TAP,)):
                    self._discard_listen(press)
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
        """Main thread: start the operation: its steps (those before 錄音 when it records). 錄音 turns listening on;
        while listening, a tap or double tap that records turns it off and a hold leaves it on."""
        press.rule, press.decided = rule, True
        log.info("gesture %s %s: %s", press.key, press.gesture,
                 [f"{st.type}:{st.value}" for st in rule.steps] if rule else "none (given back)")
        i = rule.record_index() if rule else None
        if i is None:
            self._discard_listen(press)
            if rule is not None:
                self._run_steps(press, rule.steps, done=press.resolved.set)
        elif self.recognizer is None:
            self._discard_listen(press)
            self._notify(self._status_text or "模型尚未就緒")
            press.finished = True
            return
        elif self._listening and self._listen_owner is not press:
            if press.gesture != HOLD:
                self._stop_listen()
        else:
            if not self._listening:
                self._start_listen(press)
            if self._listening:
                self._show_listen(press)
                self._run_steps(press, rule.steps[:i], done=press.resolved.set)
        if press.released:
            self._finish(press)

    def _finish(self, press: _Press):
        """Main thread: the press is released and its operation known."""
        if press.finished:
            return
        if press.rule is None and press.gesture == HOLD and press.up_at - press.down_at < _TAP_MAX:
            # a press a little past gesture_time that no hold operation takes is a tap: the user's taps last
            # 0.11-0.19s against their gesture_time of 0.15s, and turning listening off took three presses
            # (2026-10-14)
            press.decided = False
            self._decide(press, TAP)
            return
        press.finished = True
        if press.rule is None:
            self.hotkeys.replay(press.key, 2 if press.gesture == DOUBLE else 1)
        elif self._listen_owner is press and press.gesture == HOLD:
            self._stop_listen()   # held to listen: released, listening ends

    def _input_done(self):
        """Main thread: a piece ended in nothing, or its text was pasted. Unmute once listening is off."""
        if not self._listening:
            self.muter.restore()

    def _overlay_idle(self):
        """Main thread: a press or piece is done; while listening the indicator stays."""
        if self._listening:
            self.overlay.show_recording()
        else:
            self.overlay.hide_overlay()

    # --- listening (the one way to record since 2026-10-14, continuous-mic branch) ---
    @property
    def _listening(self) -> bool:
        return self.listener is not None and self.listener.active

    def _start_listen(self, press: _Press):
        """The microphone on for the press's operation; unseen until _show_listen (a press may still turn out to be
        another gesture), and pieces wait until then."""
        if self.recognizer is None:
            return
        try:
            if self.listener is None:
                self.listener = Listener(str(models.vad_path()), self._on_listen_speech, self._on_listen_piece)
            self.listener.start(self.cfg.mic)
        except Exception as e:
            log.exception("listen start failed")
            self._notify(f"無法開啟麥克風：{e}")
            return
        self._listen_owner, self._listen_shown = press, False
        self._draft, self._draft_start, self._listen_probe = None, 0, None
        with self._early_lock:
            self._early_pieces = []

    def _show_listen(self, press: _Press):
        """The indicator fades in and the start sound plays together, once the press is known to turn listening on
        (the user's design, 2026-10-13): a hold at gesture_time, a tap at its release or gesture_time later, at
        key-down when the key only has recording taps."""
        if not self._listening or self._listen_owner is not press or self._listen_shown:
            return
        log.info("listen: on (%s %s)", press.key, press.gesture)
        self._listen_shown = True
        self.tray.setIcon(self.icon_rec)
        self.overlay.show_recording()
        cues.play(self.cfg.start_sound, "start")
        wait = cues.duration(self.cfg.start_sound, "start")
        if wait:
            wait += _CUE_LATENCY
            self.listener.still(wait)   # the start sound is not speech; muting waits for its end
        if self.cfg.mute_while_recording:
            QTimer.singleShot(int(wait * 1000), lambda: self.muter.mute() if self._listening else None)
        with self._early_lock:
            early, self._early_pieces = self._early_pieces, None
        for piece, probe in early or []:
            self._submit_piece(piece, probe)

    def _discard_listen(self, press: _Press):
        """The press that started listening unseen does something else: the microphone off, nothing kept."""
        if self._listening and self._listen_owner is press and not self._listen_shown:
            self.listener.stop(discard=True)
            self._listen_owner = None

    def _stop_listen(self):
        owner, self._listen_owner, self._listen_shown = self._listen_owner, None, False
        self.listener.stop()   # speech still going on is handed over as a last piece
        log.info("listen: off")
        self.tray.setIcon(self.icon_idle)
        self.overlay.hide_overlay()
        done = self.cfg.stop_sound and self.cfg.stop_sound_at == "done"
        if not done:
            self.muter.restore()
            if self.cfg.stop_sound:
                cues.play(self.cfg.stop_sound, "stop")
        after = self._after_steps(owner) if owner else []
        end = self.listener.now

        def finished():   # worker thread, after the last piece: the rest of the draft is committed, then the steps
            self._on_main(lambda: None)
            d = self._draft
            if d is not None:
                self._commit_job(self._draft_start, end, end, d.ctx)

            def run():
                if done and not self._listening:
                    self.muter.restore()
                    cues.play(self.cfg.stop_sound, "stop")
                self._run_steps(owner, after)
            self.main.emit(run)
        self.worker.submit(finished)

    def _on_listen_speech(self):
        """Listener thread: speech began. What is selected now is what a new draft replaces."""
        self._listen_probe = ContextProbe()
        self._speech_sample = max(0, self.listener.now - int(LEAD * SAMPLE_RATE))

    def _on_listen_piece(self, piece):
        """Listener thread: a piece of speech ended: a command, or more of the draft."""
        probe = self._listen_probe
        with self._early_lock:
            if self._early_pieces is not None:
                self._early_pieces.append((piece, probe))   # the press is not known to listen yet
                return
        self._submit_piece(piece, probe)

    def _submit_piece(self, piece, probe):
        if self.recognizer is not None:
            self.worker.submit(self._piece_job, piece, probe)

    # --- the draft: text typed while listening, not committed yet ---
    def _new_draft(self, ctx):
        """Main thread: a draft starts where the caret is, over the selection read when its speech began."""
        self._draft = _Draft(self._draft_start, ctx, ctx.selection)
        return self._draft

    def _show_draft(self):
        """Main thread: make the document show the draft's text."""
        d = self._draft
        text = d.final
        if not d.placed:
            if not text:
                return
            if d.sel is not None:
                if d.sel.term is not None:
                    self.overlay.hide_now()
                pasted = _paste_over(d.sel, text)
                self._overlay_idle()
                if not pasted:
                    self._notify(_SEL_LOST)
                    d.sel = None
                    return
                self.session.replaced_selection(d.sel.before, text, foreground(), d.sel.before + d.sel.text)
            else:
                d.lead = " " if self.session.clean() and _space_between(self.session.buffer[-1:], text) else ""
                paste_text(d.lead + text)
                self.session.dictated(d.lead + text, None, foreground())
            d.placed, d.typed = True, text
            return
        if text == d.typed:
            return
        s = self.session
        if self._moved():
            self._freeze()   # what is typed stays
            return
        new = s.buffer[:len(s.buffer) - len(d.typed)] + text
        n, tail = s.plan_to(new)
        backspace(n)
        if tail:
            paste_text(tail)
        s.retyped(new)
        d.typed = text

    def _moved(self) -> bool:
        """Main thread: the user moved the caret away from the draft (clicked, selected, typed)."""
        d, s = self._draft, self.session
        return d is not None and d.placed and not (s.clean() and s.buffer.endswith(d.typed))

    def _freeze(self, at: int | None = None):
        """Main thread: the caret moved away from the draft. It stays as typed; speech from `at` (default: the
        speech under way) starts a new draft."""
        log.info("draft left as typed: %s", self._draft.typed if self._draft else "")
        self._draft = None
        if at is None:
            at = self._speech_sample if self._speech_sample > self._draft_start else self.listener.now
        self._draft_start = at
        self.listener.keep(at)

    def _refine(self, text: str, start: int, covered: int, ctx):
        """Main thread: the draft's audio up to `covered` decoded again (every pause): its text, punctuation
        included, is typed in place of what was there."""
        if start != self._draft_start:
            return
        d = self._draft or self._new_draft(ctx)
        d.final = text
        self._show_draft()

    def _commit(self, text: str, start: int, at: int, ctx, command=None):
        """Main thread: the draft becomes text (decoded again as a whole), then the command it ended with runs.
        Listening goes on from `at`."""
        if start == self._draft_start:
            d = self._draft or (self._new_draft(ctx) if text else None)
            if d is not None:
                d.final = text
                self._show_draft()
            self._draft = None
            self._draft_start = at
            self.listener.keep(at)
        if command is not None:
            self._run_command(command, ctx)
        self._overlay_idle()
        self._input_done()

    def _run_command(self, command, ctx):
        """Main thread: an operation spoken alone runs its steps; a punctuation name or a described character is
        typed after the text."""
        op, typed, entry = command
        self.record.emit(entry)
        if op is not None:
            log.info("spoken operation: %s", op.phrase)
            self._run_steps(_Press("listen", _Known(ctx), time.monotonic(), False), op.steps)
        else:
            log.info("typed: %s", typed)
            self._type_text(typed)

    # --- recognition (worker thread) ---
    def _decode(self, audio, ctx, commands: bool = False) -> str:
        """The recognizer's text for audio, biased by hotwords and on-screen terms. commands: a short result is
        decoded again with spoken commands boosted (they are said alone; boosting them in every sentence would
        turn 還行 into 換行)."""
        self._load_packs()
        words = self.hotwords.bias() + [(t, _SCREEN_TERM_SCORE) for t in ctx.terms]
        raw = self.recognizer.recognize(audio, _bias(words))
        names = self._command_words() if commands else []
        if names and self._short(raw, names):
            again = self.recognizer.recognize(audio, _bias([(w, _COMMAND_SCORE) for w in names] + words))
            if again != raw:
                log.info("asr with commands boosted: %s -> %s", raw, again)
            raw = again
        return raw

    @staticmethod
    def _short(raw: str, names: list) -> bool:
        """Short enough to be a command said alone, counting a Chinese character or an English word as one: by
        letters, the user's 換行 heard as "Hangan" (6) was too long to be decoded again with commands boosted, and
        typed as text (2026-10-14)."""
        return len(_WORDS.findall(raw)) <= max(map(len, names)) + _COMMAND_SLACK

    def _command(self, formatted: str, ctx, steps: list):
        """(operation, text to type) when the utterance is a command said alone: an operation's phrase, a
        punctuation name or a described character; (None, None) otherwise."""
        op = rules.pick(rules.candidates(self.cfg.rules, rules.SPEECH, formatted), ctx.selection is not None,
                        self.session.deletable() is not None)
        typed = None
        if op is None and self.cfg.punctuation_names:
            typed = edit.symbol(formatted)
            if typed:
                steps.append(("標點名稱", formatted, typed))
        if op is None and not typed and self.cfg.describe_char:
            typed = edit.described(formatted)
            if typed:
                steps.append(("指定字", formatted, typed))
        return op, typed

    def _text_of(self, raw: str, ctx, steps: list) -> str:
        """Dictated text from the recognizer's: formatted without sentence-final punctuation (the user says 句號),
        English spelled as known terms, smart picks."""
        formatted = format_text(raw, True, steps)
        known = spelling.table(self._known_terms(ctx.terms), self.hotwords.aliases())
        before = formatted
        formatted, spelled = spelling.fix(formatted, known)
        if spelled:
            log.info("spelling: %s", ", ".join(f"{a} -> {b}" for a, b in spelled))
            steps.append(("英文拼寫", before, formatted))
        prefix = ctx.selection.before if ctx.selection else (ctx.caret[0] if ctx.caret else "")
        text = self.memory.apply(formatted, prefix, self.hotwords.levels())
        if text != formatted:
            steps.append(("選字記憶", formatted, text))
        return text

    def _piece_job(self, piece, probe):
        """A piece of speech ended. Said alone, a command commits the draft before it and runs; otherwise the
        draft is decoded again up to here (committed when the setting says every pause)."""
        try:
            # what earlier pieces asked of the main thread is done; a draft the user moved away from stays as it
            # is, and this piece starts a new one (over the selection made meanwhile)
            alone, drafted = [], []

            def check():
                if self._moved():
                    self._freeze(piece.lo)
                alone.append(self._draft is None or not self._draft.final)   # nothing before it in the draft
                drafted.append(self._draft is not None)
            self._on_main(check)
            ctx = probe.result() if probe is not None else Context()
            start = self._draft_start
            audio = self.listener.audio(piece.lo, piece.hi)
            t0 = time.monotonic()
            raw = self._decode(audio, ctx, commands=True)
            t_asr = time.monotonic() - t0
            log.info("piece %.2fs: %s", t_asr, raw)
            steps = []
            formatted = format_text(raw, True, steps)
            entry = {"time": time.strftime("%H:%M:%S"), "asr": to_traditional(raw), "asr_s": t_asr, "steps": steps}
            op, typed = self._command(formatted, ctx, steps)
            names = self._command_words()
            if op is None and not typed and alone and alone[0] and names and self._short(raw, names) \
                    and piece.lo > start:
                # decoded with the draft's audio before it (as a commit would), a short piece may turn out to be a
                # command it missed alone: the user's 換行 came out as Hangan, then as 換行 in the draft, and was
                # typed as text at the next command (2026-10-14)
                whole = self.listener.audio(start, piece.hi)
                again = self._decode(whole, ctx, commands=True)
                more = format_text(again, True)
                op, typed = self._command(more, ctx, steps)
                if op is not None or typed:
                    log.info("piece with the draft before it: %s -> %s", raw, again)
                    raw, formatted, audio = again, more, whole
            if op is not None or typed:
                self._keep(audio, asr=raw, fmt=formatted, result=typed or "", op=op.phrase if op else None)
                entry.update(kind="操作", main=op.phrase) if op else entry.update(kind="輸入", main=typed, copy=typed)
                # nothing said since the last commit: the audio before the command is not decoded (a breath
                # between 句號 and 送出 was typed as "Shh", 2026-10-14)
                end = piece.start if drafted and drafted[0] else start
                self._commit_job(start, end, piece.hi, ctx, (op, typed, entry))
            elif self.cfg.commit_at == "pause" or piece.hi - start > _DRAFT_MAX * SAMPLE_RATE:
                self._commit_job(start, piece.hi, piece.hi, ctx)
            else:
                text = self._text_of(self._decode(self.listener.audio(start, piece.hi), ctx), ctx, [])
                log.info("draft: %s", text)
                self.main.emit(lambda: self._refine(text, start, piece.hi, ctx))
        except Exception:
            log.exception("piece failed")

    def _commit_job(self, start: int, end: int, at: int, ctx, command=None):
        """The draft's audio [start, end) decoded as a whole; the main thread puts the text in its place."""
        audio = self.listener.audio(start, end)
        steps, text, raw = [], "", ""
        t0 = time.monotonic()
        if audio.size >= SAMPLE_RATE * 0.2:
            raw = self._decode(audio, ctx)
            text = self._text_of(raw, ctx, steps)
        if text:
            t_asr = time.monotonic() - t0
            log.info("commit %.2fs: %s", t_asr, text)
            self._keep(audio, asr=raw, fmt=text, result=text, op=None)
            self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "輸入", "main": text, "copy": text,
                              "asr": to_traditional(raw), "asr_s": t_asr, "steps": steps})
        self.main.emit(lambda: self._commit(text, start, at, ctx, command))

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
                elif step.type == rules.COMMAND and step.value == rules.STOP:
                    self._on_main(lambda: self._stop_listen() if self._listening else None)
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
        deleted = _delete_selection(sel)
        if sel.term is not None:
            self._overlay_idle()
        if not deleted:
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
        rule = press.rule
        i = rule.record_index() if rule else None
        return rule.steps[i + 1:] if i is not None else []

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

    # --- dictation ---
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
        pasted = _paste_over(sel, sel.text[:lead] + word + sel.text[lead + len(text):])
        if sel.term is not None:
            self._overlay_idle()
        if not pasted:
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
        self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "選字", "main": f"{text} → {word}", "copy": word,
                          "steps": [("選單輸入", word, None)] if typed else []})

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
        if self._listening:
            self.listener.stop()
        self.muter.restore()
        self.hotkeys.stop()
        if self._mouse:
            self._mouse.stop()
        self.tray.hide()
        self.qapp.quit()


_CJK_CHAR = re.compile(r"[㐀-䶿一-鿿]")


@dataclass
class _Draft:
    """Text typed while listening, not committed yet (main thread)."""
    start: int                 # the sample its audio starts at
    ctx: object                # Context read when its speech began
    sel: object = None         # the Selection its text replaces
    final: str = ""            # the offline model's text of its audio up to the last pause
    typed: str = ""            # what the document shows now (after lead)
    lead: str = ""             # a space typed before it
    placed: bool = False


class _Known:
    """A ContextProbe whose result is known."""

    def __init__(self, ctx):
        self._ctx = ctx

    def result(self, timeout: float = 2.0):
        return self._ctx

    def ready(self) -> bool:
        return True


def _space_between(a: str, b: str) -> bool:
    """format_text's space between English and Chinese, or between English words."""
    if not a or not b:
        return False
    x, y = a[-1], b[0]
    return bool((_SPACED.match(x) and (_SPACED.match(y) or _CJK_CHAR.match(y))) or (_CJK_CHAR.match(x) and _SPACED.match(y)))


def _joined(a: str, b: str) -> str:
    return a + (" " if _space_between(a, b) else "") + b


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
