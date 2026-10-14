"""VoiceInput: single-process tray app. Tap a key or mouse button to listen until the next tap, or hold it to listen
while held.

What an utterance does:
1. Text is selected (UI Automation): the utterance replaces it.
2. Otherwise it is dictated: formatted and pasted.
An utterance that is exactly a custom operation's phrase (換行, 送出 …) runs that operation instead, and a bare
punctuation name (逗號, 句號 …) is typed as the mark (edit.symbol; a setting since 2026-10-11), and 節日的節 types
節 (edit.described, a setting). Dictated text then
goes through the smart picks (memory.py, single characters only); words are left to X-ASR hotword biasing.

What the mouse buttons and keys do is set by custom operations (rules.py, 2026-10-11). The defaults
(rules.defaults, the user's setup): tap the middle button to listen; double click it to take back VoiceInput's last
change (復原), on a selection to delete it; hold it on a selection for 選字; spoken 換行, 送出, 復原 / 撤銷, 刪除
(Backspace) and a few more. Gestures no operation takes are given back to the key.

Recording is continuous listening (listen.py; on the continuous-mic branch since 2026-10-14 the one way, the
user's choice: no cutting the recording to say a command). Every pause ends a piece: said alone, an operation's
phrase or a punctuation name commits the draft and runs or types it; otherwise the piece joins the draft, which is
decoded again and typed in place (in place of the text selected when its speech began, if any: the user may select
with the mouse while listening), punctuation included; only its open sentence is decoded again (text up to a mark
the recognizer put in a pause is settled). A commit (by a command, the end of listening, or every pause when
commit_at says so) decodes the open sentence once more; by an operation or the end of listening it keeps the
recognizer's 。？！ or gets 。 (a spoken mark is typed instead). Moving the caret leaves a draft as typed, and speech
not typed yet is decoded again at the new caret. Other gestures work while listening.

The one popup (picker.py) is the 選字 candidate menu; it came back 2026-10-11 after the lean branch removed every
popup, and a redesign is planned (the 加入熱詞 notice with 撤銷 lived for a few hours the same day). Its input box
takes a typed word, which replaces the selection and becomes a hotword. The second opinion (SenseVoice + suspects.py) is
commented out below.
"""
import logging
import os
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np
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
        self._listen_typed = False               # something was typed or run while listening (send_on_stop)
        self._listen_hwnd = 0                    # the window it was typed in
        self._quitting = False
        self._marked = None                      # the buffer when the last commit added its sentence-final mark
        self._spans: list = []                   # worker thread: the pieces of the draft's open sentence
        self._ignored: set[str] = set()          # keys pressed while another was held
        self._could_record = rules.can_record(self.cfg.rules)
        self.hotwords = Hotwords()
        self._pack_stamp = None
        self._load_packs()
        self.memory = Memory()
        self._menu = None                        # ("pick", Selection) while the 選字 menu is up

        self.icon_idle = make_icon(QColor(235, 235, 235) if self._dark_taskbar() else QColor(40, 40, 40))
        self.icon_rec = make_icon(QColor(255, 69, 58))

        self._busy, self._busy_lock = 0, threading.Lock()   # decoding jobs queued or running
        self.overlay = Overlay(lambda: self.listener.level if self._listening else 0.0,
                               lambda: self.cfg.spin_while_decoding and self._busy > 0)
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
                self._run_steps(press, rule.steps)
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
                self._run_steps(press, rule.steps[:i])
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
        self._listen_probe = None
        begin = self.listener.now

        def fresh():   # worker thread, after the last session's pieces and its end (they still need its draft)
            self._spans = []

            def reset():
                self._draft, self._draft_start = None, begin
                self._listen_typed, self._listen_hwnd = False, 0
                self.listener.keep(begin)
            self._on_main(reset)
        self.worker.submit(fresh)
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

    def _stop_listen(self, send: bool = True):
        """Listening off. send: by the key (not 送出 / 結束錄音): Enter once the text is in when the setting says so
        and something was typed while listening."""
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
            spans, self._spans = self._spans, []
            if d is not None:
                spans = [p for p in spans if p.start >= d.anchor]
                self._commit_job(self._draft_start, d.anchor, end, end, d.ctx, final=True, spans=spans)

            def run():
                if done and not self._listening:
                    self.muter.restore()
                    cues.play(self.cfg.stop_sound, "stop")
                steps = list(after)
                if send and self.cfg.send_on_stop and self._listen_typed and self.session.buffer \
                        and foreground() == self._listen_hwnd:   # not into another window (2026-10-15)
                    log.info("listen: sent on stop")
                    steps.append(rules.Step(rules.KEYS, "Enter"))
                if owner:
                    self._run_steps(owner, steps)
            self.main.emit(run)
        self._decoding(finished)

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
            self._decoding(self._piece_job, piece, probe)

    def _decoding(self, fn, *args):
        """fn on the worker thread, counted as decoding while queued or running (the indicator's spinner)."""
        with self._busy_lock:
            self._busy += 1

        def run():
            try:
                fn(*args)
            finally:
                with self._busy_lock:
                    self._busy -= 1
        self.worker.submit(run)

    # --- the draft: text typed while listening, not committed yet ---
    def _new_draft(self, ctx):
        """Main thread: a draft starts where the caret is, over the selection read when its speech began."""
        self._draft = _Draft(self._draft_start, ctx, ctx.selection, anchor=self._draft_start,
                             shown=self._draft_start)
        return self._draft

    def _show_draft(self, step: bool = False):
        """Main thread: make the document show the draft's text. step: a pause's update, which 復原 takes back on
        its own."""
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
        s.retyped(new, step)
        d.typed = text

    def _moved(self) -> bool:
        """Main thread: the user moved the caret away from the draft (clicked, selected, typed)."""
        d, s = self._draft, self.session
        return d is not None and d.placed and not (s.clean() and s.buffer.endswith(d.typed))

    def _freeze(self, at: int | None = None):
        """Main thread: the caret moved away from the draft. It stays as typed; speech from `at` (default: the
        speech under way) starts a new draft."""
        d = self._draft
        log.info("draft left as typed: %s", d.typed if d else "")
        if d is not None and d.placed and d.typed:   # never committed: logged as it stands
            self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "輸入", "main": d.typed, "copy": d.typed,
                              "steps": [("草稿", "游標移開了，照畫面上的文字定下來，沒有再重新辨識", None)]})
        self._draft = None
        if at is None:
            at = self._speech_sample if self._speech_sample > self._draft_start else self.listener.now
        self._draft_start = at
        self.listener.keep(at)

    def _refine(self, start: int, anchor: int, settled: str, live: str, after: int, upto: int, ctx):
        """Main thread: a pause. The draft's open sentence (its audio from `anchor` to `upto`) was decoded again:
        `settled` is the part up to a ， or 。 that fell in a pause, fixed from now on (the open sentence starts at
        `after`), `live` the rest. Typed in place of what was there."""
        d = self._draft
        if start != self._draft_start or (d is not None and d.anchor != anchor):
            return
        if d is None and not settled and not live:   # a noise: no draft, so the next speech reads its own selection
            return
        if self._moved():
            # the caret moved before this update was typed: what is typed stays, and the speech after it is decoded
            # again where the caret is now (it was lost until 2026-10-15)
            at = d.shown
            self._freeze(at)
            self._decoding(self._redo_job, at)
            return
        if d is None:
            d = self._new_draft(ctx)
        elif not d.placed:   # nothing typed yet: the latest speech's caret and selection
            d.ctx, d.sel = ctx, ctx.selection
        d.settled = _joined(d.settled, settled)
        d.anchor = after
        d.final = _TRAILING.sub("", _joined(d.settled, live))
        # the recognizer's 。？！ at the end is shown; the next pause turns it into ， when the sentence goes on (the
        # user's request, 2026-10-15; until then drafts had none while speaking)
        end = _SENTENCE_END.search(_joined(d.settled, live))
        if end and d.final and not _inside(d.ctx):
            d.final += end.group()
        self.listener.keep(after)
        self._show_draft(step=True)
        if d.placed and d.typed == d.final:
            d.shown = upto

    def _commit(self, text: str, start: int, anchor: int, at: int, ctx, command=None, final: bool = False):
        """Main thread: the draft's open sentence decoded again joins its settled text and the draft is done; then
        the command it ended with runs. Listening goes on from `at`. final (an operation or the end of listening):
        the recognizer's 。？！ at the end is kept, or 。 added when it gave none (the user's choice, 2026-10-14/15:
        not while speaking, where a pause is not a sentence end, nor before a spoken punctuation mark), unless the
        caret had text on both sides."""
        d = self._draft
        marked = False
        if start == self._draft_start and (d is None or d.anchor == anchor):
            if d is None and text:
                d = self._new_draft(ctx)
            elif d is not None and not d.placed:
                d.ctx, d.sel = ctx, ctx.selection
            if d is not None:
                d.final = _TRAILING.sub("", _joined(d.settled, text))
                end = _SENTENCE_END.search(_joined(d.settled, text))
                if final and d.final and not _inside(d.ctx):
                    # none from the recognizer (a short sentence often has none): 。 (the user's choice, 2026-10-15),
                    # ？ after 嗎 (the user's request the same day)
                    d.final += end.group() if end else "？" if d.final.endswith("嗎") \
                        else "。" if _CJK_CHAR.search(d.final) else "."
                    marked = True
                self._show_draft()
                if d.final and d.typed == d.final:   # else left as typed (logged by _freeze)
                    self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "輸入", "main": d.final,
                                      "copy": d.final})
            self._draft = None
            self._draft_start = at
            self.listener.keep(at)
            if d is not None:
                self._marked = self.session.buffer if marked else None
        if command is not None or d is not None and d.final:
            self._listen_typed, self._listen_hwnd = True, foreground()
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
            s = self.session
            if typed in edit.SYMBOLS.values() and self._marked is not None and s.clean() \
                    and s.buffer == self._marked:
                # the mark a commit added (after 換行 or the like) gives way to the one said now: not 。。
                s.changed(s.buffer[:-1] + typed)
                backspace(1)
                paste_text(typed)
            else:
                self._type_text(typed)
            self._marked = None

    # --- recognition (worker thread) ---
    def _decode(self, audio, ctx, commands: bool = False) -> str:
        """The recognizer's text for audio, biased by hotwords and on-screen terms. commands: a short result is
        decoded again with spoken commands boosted (they are said alone; boosting them in every sentence would
        turn 還行 into 換行)."""
        words = self._bias_words(ctx)
        raw = self.recognizer.recognize(audio, words)
        names = self._command_words() if commands else []
        if names and self._short(raw, names):
            again = self.recognizer.recognize(audio, _bias([(w, _COMMAND_SCORE) for w in names] + words))
            if again != raw:
                log.info("asr with commands boosted: %s -> %s", raw, again)
            raw = again
        return raw

    def _bias_words(self, ctx) -> list:
        """Hotwords and on-screen terms, as the recognizer takes them."""
        self._load_packs()
        return _bias(self.hotwords.bias() + [(t, _SCREEN_TERM_SCORE) for t in ctx.terms])

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

    def _text_of(self, raw: str, ctx, steps: list, keep_end: bool = False) -> str:
        """Dictated text from the recognizer's: formatted without sentence-final punctuation (the user says 句號;
        keep_end: a sentence settled inside the draft keeps its ， or 。), English spelled as known terms, smart
        picks."""
        formatted = format_text(raw, not keep_end, steps)
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
        draft's open sentence is decoded again up to here (committed when the setting says every pause)."""
        try:
            # what earlier pieces asked of the main thread is done; a draft the user moved away from stays as it
            # is, and this piece starts a new one (over the selection made meanwhile)
            state = {}

            def check():
                if self._moved():
                    self._freeze(piece.lo)
                d = self._draft
                state.update(drafted=d is not None, alone=d is None or not d.final, start=self._draft_start,
                             anchor=d.anchor if d is not None else self._draft_start)
            self._on_main(check)
            if "anchor" not in state:   # the main thread did not answer in time
                log.warning("piece dropped: the main thread is busy")
                return
            ctx = probe.result() if probe is not None else Context()
            start, anchor = state["start"], state["anchor"]
            self._spans = [p for p in self._spans if p.start >= anchor] + [piece]
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
            if op is None and not typed and state["alone"] and names and self._short(raw, names) \
                    and piece.lo > anchor:
                # decoded with the draft's audio before it (as a commit would), a short piece may turn out to be a
                # command it missed alone: the user's 換行 came out as Hangan, then as 換行 in the draft, and was
                # typed as text at the next command (2026-10-14)
                whole, _ = self._speech(anchor, piece.hi, self._spans)
                again = self._decode(whole, ctx, commands=True)
                more = format_text(again, True)
                op, typed = self._command(more, ctx, steps)
                if op is not None or typed:
                    log.info("piece with the draft before it: %s -> %s", raw, again)
                    raw, formatted, audio = again, more, whole
            if op is not None or typed:
                spans, self._spans = [p for p in self._spans if p is not piece], []
                self._keep(audio, asr=raw, fmt=formatted, result=typed or "",
                           op=op.phrase if op else None)
                entry.update(kind="操作", main=op.phrase) if op else entry.update(kind="輸入", main=typed, copy=typed)
                # nothing said since the last commit: the audio before the command is not decoded (a breath
                # between 句號 and 送出 was typed as "Shh", 2026-10-14)
                end = piece.start if state["drafted"] else anchor
                self._commit_job(start, anchor, end, piece.hi, ctx, (op, typed, entry), final=not typed,
                                 spans=spans)
            elif self.cfg.commit_at == "pause" or piece.hi - anchor > _DRAFT_MAX * SAMPLE_RATE:
                spans, self._spans = self._spans, []
                self._commit_job(start, anchor, piece.hi, piece.hi, ctx, spans=spans)
            else:
                self._refine_job(start, anchor, piece, ctx, short=bool(names) and self._short(raw, names))
        except Exception:
            log.exception("piece failed")

    def _refine_job(self, start: int, anchor: int, piece, ctx, short: bool = False):
        """The draft's open sentence (audio from `anchor`) decoded again up to this pause. Text up to the last ，
        or 。 the recognizer put in an earlier pause is settled: it is not decoded again, so a pause costs one
        sentence, not the whole draft (the user's request, 2026-10-14: a commit of a long draft took 3.25s).
        short: the piece is short enough to be a command."""
        t0 = time.monotonic()
        audio, index = self._speech(anchor, piece.hi, self._spans)
        words = self._bias_words(ctx)
        names = self._command_words() if short else []
        # a short piece is decoded with the speech before it and commands boosted first: plain, the user's 換行 at
        # the end was often left out, then turned up inside the sentence at the next pause (2026-10-15)
        for bias in ([_bias([(w, _COMMAND_SCORE) for w in names] + words)] if names else []) + [words]:
            text, tokens, times = self.recognizer.recognize_timed(audio, bias)
            if self._ran_tail(text, tokens, times, index, piece, ctx, start, anchor, t0):
                return
        settled, live, after = "", text, anchor
        # a blank token (between English letters) is no mark: "" in _MARKS is True, and counting it settled a whole
        # sentence that was then typed again (2026-10-15)
        marks = [i for i, tok in enumerate(tokens[:-1]) if tok.strip() and tok.strip() in _MARKS]
        for k in range(len(marks) - 1, -1, -1):
            at = _sample_at(index, int(times[marks[k]] * SAMPLE_RATE))
            gap = next((nxt for prev, nxt in zip(self._spans, self._spans[1:])
                        if prev.end - _MARK_SLACK <= at <= nxt.start + _MARK_SLACK), None)
            spots = [i for i, ch in enumerate(text) if ch in _MARKS]
            if gap is not None and k < len(spots):
                cut = spots[k] + 1
                settled, live, after = text[:cut], text[cut:], gap.lo
                self._spans = [p for p in self._spans if p.start >= after]
                break
        settled = self._text_of(settled, ctx, [], keep_end=True) if settled else ""
        live = self._text_of(live, ctx, [], keep_end=True)
        log.info("draft %.2fs: %s | %s", time.monotonic() - t0, settled, live)
        self.main.emit(lambda: self._refine(start, anchor, settled, live, after, piece.hi, ctx))

    def _ran_tail(self, text, tokens, times, index, piece, ctx, start, anchor, t0) -> bool:
        """The last piece, decoded after the speech before it, may be a command it was not alone: the user's 換行
        came out as "Hang Hung," alone and 送出 as "Contral.", as themselves after it, and were typed as text
        (2026-10-15). Then the speech before it is committed and the command runs (True)."""
        head, tail = _split_at(text, tokens, times, index, piece.start - _MARK_SLACK)
        if not tail:
            return False
        steps = []
        said = format_text(tail, True, steps)
        op, typed = self._command(said, ctx, steps)
        if op is not None or typed:
            log.info("piece with the speech before it: %s | %s", head, said)
            entry = {"time": time.strftime("%H:%M:%S"), "asr": to_traditional(tail),
                     "asr_s": time.monotonic() - t0, "steps": steps}
            entry.update(kind="操作", main=op.phrase) if op else entry.update(kind="輸入", main=typed, copy=typed)
            self._spans = []
            head = self._text_of(head, ctx, []) if head.strip() else ""
            command = (op, typed, entry)
            self.main.emit(lambda: self._commit(head, start, anchor, piece.hi, ctx, command, not typed))
            return True
        return False

    def _redo_job(self, at: int):
        """A pause's update came after the caret moved: the speech since sample `at` (after what is typed) is
        decoded again as a new draft at the caret and selection now."""
        state = {}
        self._on_main(lambda: state.update(go=self._draft is None and self._draft_start == at))
        spans = [p for p in self._spans if p.hi > at]
        if not state.get("go") or not spans:   # a later piece got there first
            return
        self._spans = spans
        self._refine_job(at, at, spans[-1], ContextProbe().result())

    def _speech(self, anchor: int, end: int, spans: list):
        """The open sentence's audio [anchor, end) with the silence between its pieces (spans) cut down to their own
        lead-in and tail, and an index of (offset in it, sample) for each stretch kept. The user thinks between
        sentences: with 15s pauses kept a draft passed the 30s beyond which the recognizer gives no timestamps,
        so nothing was settled and each pause decoded it all again, 2-3s (2026-10-15)."""
        stretches = []
        for p in spans:
            lo, hi = max(p.lo, anchor), min(p.hi, end)
            if hi <= lo:
                continue
            if stretches and lo <= stretches[-1][1]:
                stretches[-1][1] = max(stretches[-1][1], hi)
            else:
                stretches.append([lo, hi])
        if not stretches:
            return self.listener.audio(anchor, end), [(0, anchor)]
        parts, index, n = [], [], 0
        for lo, hi in stretches:
            a = self.listener.audio(lo, hi)
            parts.append(a)
            index.append((n, lo))
            n += a.size
        return np.concatenate(parts), index

    def _commit_job(self, start: int, anchor: int, end: int, at: int, ctx, command=None, final: bool = False,
                    spans: list = ()):
        """The draft's open sentence, audio [anchor, end), decoded again; the main thread puts the draft's text in
        its place (final: with its sentence-final mark)."""
        audio, _ = self._speech(anchor, end, spans)
        steps, text, raw = [], "", ""
        t0 = time.monotonic()
        if audio.size >= SAMPLE_RATE * 0.2:
            raw = self._decode(audio, ctx)
            text = self._text_of(raw, ctx, steps, keep_end=final)
        if text:
            log.info("commit %.2fs: %s", time.monotonic() - t0, text)
            self._keep(audio, asr=raw, fmt=text, result=text, op=None)
        self.main.emit(lambda: self._commit(text, start, anchor, at, ctx, command, final))

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
                        if step.value == "Shift+Enter":   # a new line, which 復原 can take back
                            self._on_main(lambda: self.session.dictated("\n", None, foreground()))
                        elif step.value == "Backspace":   # one character less, which 復原 can type again
                            self._on_main(self._backspaced)
                        elif step.value == "Enter":   # sent: nothing left for the end of listening to send
                            self.session.on_input()
                            self._listen_typed = False
                        else:
                            self.session.on_input()     # the caret may have moved
                elif step.type == rules.TEXT:
                    self._on_main(lambda t=step.value: self._type_text(t))
                elif step.type == rules.COMMAND and step.value == rules.DELETE_SELECTION:
                    ctx = press.probe.result()
                    self._on_main(lambda: self._delete_selection(press, ctx))
                elif step.type == rules.COMMAND and step.value in (rules.UNDO, rules.DELETE_DICTATION):
                    self._on_main(self.undo)
                elif step.type == rules.COMMAND and step.value == rules.STOP:
                    self._on_main(lambda: self._stop_listen(send=False) if self._listening else None)
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
        for _ in range(100):   # 10s; not when quitting: the main loop is gone and Python waits for this thread
            if ev.wait(0.1) or self._quitting:
                return

    def _backspaced(self):
        """Main thread: a Backspace step deleted the character before the caret."""
        s = self.session
        if s.clean():
            s.changed(s.buffer[:-1])
        else:
            s.on_input()

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
        self.session.replaced_selection(sel.before, "", foreground(), sel.before + sel.text)
        self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "刪除", "main": sel.text})

    def undo(self):
        """Main thread, 復原 (and 刪除剛輸入的文字): take back VoiceInput's last change while it is still right before
        the caret: up to session.UNDO_DEPTH of them, one each time; a draft's are its pauses' updates. After one,
        the draft is left as it now is (not decoded again: the audio taken back would come back) and the next speech
        starts a new one."""
        s = self.session
        step = s.deletable()
        if step is None:
            log.info("undo: nothing to take back")
            return
        n, tail = s.plan_to(step.before)
        log.info("undo: %d backspaces + %r", n, tail)
        if self.cfg.save_recordings:
            recordings.note("delete_last")
        backspace(n)
        if tail:
            paste_text(tail)
        s.deleted_last(step)
        d = self._draft
        if d is not None and d.placed and step.after.endswith(d.typed):   # the draft's change was taken back
            self._draft = None
            self._draft_start = self.listener.now if self._listening else 0
            if self._listening:
                self.listener.keep(self._draft_start)
        p = len(os.path.commonprefix([step.before, step.after]))
        gone, back = step.after[p:], step.before[p:]
        self.record.emit({"time": time.strftime("%H:%M:%S"), "kind": "復原",
                          "main": f"{gone or '（無）'}" + (f" → {back}" if back else "")})

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
        if not can and self._listening:   # nothing could turn it off any more
            self._stop_listen(send=False)
        self._could_record = can
        self._set_autostart()

    def _set_autostart(self):
        try:
            autostart.set_enabled(self.cfg.autostart)
        except Exception as e:
            log.exception("autostart failed")
            self.settings.set_status(f"開機啟動設定失敗：{e}")

    def quit(self):
        self._quitting = True
        if self._listening:
            self.listener.stop(discard=True)
        self.worker.shutdown(wait=False, cancel_futures=True)
        self.steps.shutdown(wait=False, cancel_futures=True)
        self.muter.restore()
        self.hotkeys.stop()
        if self._mouse:
            self._mouse.stop()
        self.tray.hide()
        self.qapp.quit()


_CJK_CHAR = re.compile(r"[㐀-䶿一-鿿]")
_TRAILING = re.compile(r"[\s，。、！？；：,.!?;:…]+$")


def _inside(ctx) -> bool:
    """The text goes between text on its line: the caret's, or a selection's (that one got a 。 until 2026-10-15)."""
    sel = ctx.selection
    sides = (sel.before, sel.after) if sel is not None else ctx.caret
    return sides is not None and all(t.strip() for t in sides)


def _split_at(text: str, tokens: list, times: list, index: list, at: int) -> tuple[str, str]:
    """The recognizer's text split before the first token emitted at sample `at` or later (times in App._speech's
    audio, index its map): (before, from there on); ("", "") when no token is that late."""
    i = next((k for k, t in enumerate(times) if _sample_at(index, int(t * SAMPLE_RATE)) >= at), None)
    if i is None:
        return "", ""
    n = sum(len(re.sub(r"[\s▁]", "", t)) for t in tokens[i:])
    cut = len(text)
    while cut > 0 and n > 0:
        cut -= 1
        n -= not text[cut].isspace()
    while cut < len(text) and (text[cut].isspace() or text[cut] in _MARKS):   # the sentence's own mark
        cut += 1
    return text[:cut], text[cut:]


def _sample_at(index: list, offset: int) -> int:
    """The sample an offset into App._speech's audio was recorded at."""
    for n, lo in reversed(index):
        if offset >= n:
            return lo + offset - n
    return index[0][1]


_SENTENCE_END = re.compile(r"[。？！.?!]$")
_MARKS = "，。？！；"          # punctuation that may close a settled part of a draft
_MARK_SLACK = int(0.2 * SAMPLE_RATE)   # samples a mark may lie outside the pause it closes


@dataclass
class _Draft:
    """Text typed while listening, not committed yet (main thread)."""
    start: int                 # the sample its audio starts at
    ctx: object                # Context read when its speech began
    sel: object = None         # the Selection its text replaces
    anchor: int = 0            # the sample its open sentence starts at (audio before it is settled)
    settled: str = ""          # text up to a ， or 。 the recognizer put in a pause: not decoded again
    final: str = ""            # what it shows: settled + the open sentence decoded up to the last pause
    typed: str = ""            # what the document shows now (after lead)
    lead: str = ""             # a space typed before it
    placed: bool = False
    shown: int = 0             # the sample its typed text was decoded up to


class _Known:
    """A ContextProbe whose result is known."""

    def __init__(self, ctx):
        self._ctx = ctx

    def result(self, timeout: float = 2.0):
        return self._ctx


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
