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
from types import SimpleNamespace
import numpy as np
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from PySide6.QtCore import QLockFile, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart, candidates, edit, laugh, llm, models, paths, rules, screen_terms, sync
from .asr import SAMPLE_RATE, Recognizer
from .audio import Recorder
from .config import Config
from .correction import CorrectionWatcher
from .history import Entry, History
from .hotwords import Hotwords, eligible as hotword_eligible
from .hotkey import DOUBLE_TAP_GAP, TAP_THRESHOLD, Hotkeys
from .keys import MOUSE_TRIGGERS, combo_vks
from .memory import Memory, is_latin, situation
from .discord import DiscordMute
from .micguard import MicGuard
from .mute import Muter
from .output import backspace, paste_text, press_chord, press_delete
from .copycard import CopyCard
from .overlay import Overlay, Toast
from .picker import Picker
from .reading import reading_key
from .rules import DOUBLE, HOLD, TAP
from .selection import ContextProbe, no_input_focused, move_cursor_after, term_watcher
from .session import Session, foreground
from .settings import SettingsDialog
from .textfmt import drop_casual_period, format_text, to_traditional

log = logging.getLogger("voiceinput")

_SCREEN_TERM_SCORE = 1.5
_SCREEN_ZH_SCORE = 3.0     # Chinese names / topic words on screen (screen_terms); hotwords go to 5, these are guesses
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
_INDICATOR_DELAY = 0.1     # seconds held before the recording indicator shows (a tap is < TAP_THRESHOLD)
_STEP_GAP = 0.03           # seconds between the steps of an operation
_MENU_ROWS = 8
_VOICE_TICK = 500          # ms between looks at the end of the recording for a spoken 送出
_VOICE_WINDOW = 3.0        # seconds of the end that are listened to
_VOICE_QUIET = 0.4         # the last seconds must be quiet: the speaker has just stopped
_VOICE_QUIET_RMS, _VOICE_SPEECH_RMS = 0.012, 0.02
_AFTER_PASTE = 0.35        # seconds before a command said after the text runs (the app has pasted by then)
_TEXT_STRIP = "，,。.！!？?、；;： \n　"
_MODIFIER_KEYS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


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
    trailing: object = None           # steps of a spoken command said at the end (…。送出。): run after the paste
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
        self.sense: Recognizer | None = None             # SenseVoice, loaded only for the laughter setting (laugh.py)
        self._sense_loading = False
        # self.second: Recognizer | None = None         # second-opinion model (SenseVoice), off for now
        self.worker = ThreadPoolExecutor(max_workers=1)   # serializes model loads and transcription
        self.steps = ThreadPoolExecutor(max_workers=1)    # runs operations' steps in order
        # self.bg = ThreadPoolExecutor(max_workers=1)     # second opinion, second model load
        self._status_text = ""
        self._records = deque(maxlen=100)       # transcript log, kept for this session only
        self.session = Session()
        self.muter = Muter()
        self.micguard = MicGuard()               # other apps get no microphone while recording
        self.discord = DiscordMute()
        self.micguard.recover()                  # our own session unmuted, a crashed run's mutes undone
        self._press: _Press | None = None        # the key or button held now
        self._pending: _Press | None = None      # a tap waiting to see whether a second press follows
        self._pending_timer = QTimer(self, singleShot=True, timeout=self._pending_expired)
        self._toggle: _Press | None = None       # a recording a tap started: the next press stops it
        self._voice_timer = QTimer(self, interval=_VOICE_TICK, timeout=self._voice_tick)
        self._voice_busy, self._voice_seen = False, 0   # a look at the end is running / samples at the last look
        self._mic: _Press | None = None          # the press the microphone records for
        self._ignored: set[str] = set()          # keys pressed while another was held
        self._could_record = rules.can_record(self.cfg.rules)
        self.shared = sync.folder(self.cfg.sync_dir)     # 同步: where hotwords, smart picks and history live
        self.hotwords = Hotwords(self.shared / sync.HOTWORDS)
        self.memory = Memory(self.shared / sync.PICKS)
        self.history = History(self.shared)
        self._ai_warned = False
        self.corrections = CorrectionWatcher(
            lambda changes: self.main.emit(lambda: self._learn_corrections(changes)))
        self._menu = None                        # ("pick", Selection) while the 選字 menu is up

        self.icon_idle = make_icon(QColor(235, 235, 235) if self._dark_taskbar() else QColor(40, 40, 40))
        self.icon_rec = make_icon(QColor(255, 69, 58))

        self.overlay = Overlay(lambda: self.recorder.level)
        self.toast = Toast()                     # 「程式」已加入字典 after a manual correction is learned
        self.copycard = CopyCard()               # 複製最後的轉錄, when there is no input field to paste into
        self.copycard.copy_requested.connect(self._copy_last)
        self._last_text = ""
        self._swallow_up = False                 # a click on the recording pill's buttons: its release is swallowed too
        self.picker = Picker()
        self.picker.picked.connect(self.on_menu_pick)
        self.picker.typed.connect(self.on_menu_typed)
        self.picker.closed.connect(self._forget_menu)
        self._picker_hwnd = int(self.picker.winId())
        self.settings = SettingsDialog(self.cfg, self.hotwords, self.memory.path, history=self.history,
                                       memory=self.memory, hotword_validator=self._hotword_error)
        self.settings.applied.connect(self.apply_settings)
        self.settings.sync_changed.connect(self.change_sync_folder)

        self.tray = QSystemTrayIcon(self.icon_idle)
        menu = QMenu()
        menu.addAction(QAction("設定…", menu, triggered=self.open_settings))
        self.ai_action = QAction("AI 潤稿（會用掉 AI 額度）", menu, checkable=True)
        self.ai_action.setChecked(self.cfg.ai_enabled)
        self.ai_action.toggled.connect(self._toggle_ai)
        menu.addAction(self.ai_action)
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

        self._focus_rect = "unknown"             # the focused input box (screen rectangle), kept by _focus_loop
        self.hotkeys = Hotkeys(self.key_down.emit, self.key_up.emit, self._on_other_key, self._on_injected_key,
                               mouse_gate=self._mouse_gate)
        threading.Thread(target=self._focus_loop, daemon=True, name="VoiceInput focus").start()
        self.hotkeys.set_keys(rules.bound_keys(self.cfg.rules))
        self.hotkeys.start()
        self._mouse = self._watch_mouse()
        self.worker.submit(self._load_model_job)
        # self.bg.submit(self._load_second_job)
        candidates.preload()   # the disfluency pass uses the dictionary
        self._warm_ai()
        self._sync_laugh()
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
        if self.copycard.active:
            if self._is_copy_key(vk):
                self.main.emit(self._copy_last)
                return True          # taken: Enter must not also open a selected file
            if vk not in _MODIFIER_KEYS:
                self.main.emit(self.copycard.dismiss)   # any other key: the user has moved on
        if ctypes.windll.user32.GetForegroundWindow() == self._picker_hwnd:
            return
        self.corrections.on_key(vk,
                                ctrl=bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000),
                                shift=bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000),
                                alt=bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000),
                                hwnd=foreground())
        self.session.on_input()
        term_watcher.forget()
        self.main.emit(self.picker.dismiss)

    def _on_injected_key(self, vk: int) -> bool:
        """Keyboard hook thread: a key another program sent (a mouse button remapped to Enter by its driver). Only the
        複製最後的轉錄 shortcut is taken from those; they never end a dictation otherwise."""
        if self.copycard.active and self._is_copy_key(vk):
            self.main.emit(self._copy_last)
            return True
        return False

    def _is_copy_key(self, vk: int) -> bool:
        """Keyboard hook thread: the key (with exactly the modifiers) set for copying the last dictation."""
        import ctypes
        vks = combo_vks(self.cfg.copy_key)
        if not vks or vk != vks[-1]:
            return False
        down = lambda k: bool(ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000)
        return all(down(m) == (m in vks[:-1]) for m in (0x11, 0x10, 0x12))

    def _copy_last(self):
        """Main thread: the 複製最後的轉錄 card's button or shortcut."""
        if self._last_text and self.copycard.active:
            self.qapp.clipboard().setText(self._last_text)
            log.info("last transcription copied")
            self.copycard.copied()

    def _send_phrases(self) -> list[str]:
        """Spoken operations that send (press a plain Enter): saying one ends the recording."""
        out = []
        for r in self.cfg.rules:
            if r.enabled and r.source == rules.SPEECH and r.phrase.strip() and not rules.problems(r, self.cfg.rules):
                if any(s.type == rules.KEYS and combo_vks(s.value) == [0x0D] for s in r.steps):
                    out.append(r.phrase.strip())
        return out

    def _voice_tick(self):
        """Main thread, every _VOICE_TICK ms while recording: when the speaker has just stopped, the last seconds are
        recognized on the worker; if they end with 送出 the recording ends as if ✓ was pressed, and the text is typed
        and sent (the user, 2026-10-16: 聽到送出，幫我結束錄音並傳送出去)."""
        press = self._mic
        if press is None or not press.recording or self._voice_busy or self.recognizer is None:
            return
        phrases = self._send_phrases()
        if not phrases:
            return
        audio, total = self.recorder.tail(_VOICE_WINDOW)
        if total < SAMPLE_RATE * 1.0 or total - self._voice_seen < SAMPLE_RATE * 0.5:
            return
        n = int(SAMPLE_RATE * _VOICE_QUIET)
        if audio.size < n or float(np.sqrt(np.mean(audio[-n:] ** 2))) > _VOICE_QUIET_RMS                 or float(np.max(np.abs(audio))) < _VOICE_SPEECH_RMS:
            return                      # still talking, or nothing was said
        self._voice_seen, self._voice_busy = total, True
        rec = self.recognizer
        self.worker.submit(self._voice_job, rec, audio, press, phrases)

    def _voice_job(self, rec, audio, press, phrases):
        try:
            raw = rec.recognize(audio, _bias([(w, _COMMAND_SCORE) for w in phrases]))
            body, said = rules.split_trailing(format_text(raw, False), phrases)
            if said:
                self.main.emit(lambda: self._voice_stop(press, said))
        except Exception:
            log.exception("listening for 送出 failed")
        finally:
            self._voice_busy = False

    def _voice_stop(self, press: _Press, said: list):
        if self._mic is press and press.recording:
            log.info("heard %s: the recording ends", "、".join(said))
            self._overlay_button(True)

    def _overlay_button(self, ok: bool):
        """Main thread: the ✓ (stop and recognize) or ✕ (throw the recording away) on the recording pill."""
        press = self._mic
        if press is None or not press.recording:
            return
        if self._toggle is press:
            self._toggle = None
        press.consumed = press.finished = True    # the key's release, if it is still down, changes nothing
        log.info("recording %s from the pill", "finished" if ok else "cancelled")
        if ok:
            self._stop_recording(press)
        else:
            self._discard_mic(press)

    def _on_picker(self, pt) -> bool:
        """Mouse hook thread: the point is on the 選字 menu."""
        import ctypes.wintypes
        u32 = ctypes.windll.user32
        u32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
        hwnd = u32.WindowFromPoint(pt)
        return bool(hwnd) and u32.GetAncestor(hwnd, 2) == self._picker_hwnd   # GA_ROOT

    def _focus_loop(self):
        """Every 0.25s: where the focused input box is (the mouse buttons the shortcuts use act only there)."""
        from .selection import text_focus_rect
        while True:
            time.sleep(0.25)
            try:
                if self.cfg.mouse_text_only:
                    self._focus_rect = text_focus_rect()
            except Exception:
                self._focus_rect = "unknown"

    def _mouse_gate(self, name: str, x: int, y: int) -> bool:
        """Mouse hook thread: may this press be a shortcut? Only on the focused input box; anywhere else the
        button is left alone (a middle click opening a link, closing a tab, scrolling). Unknown: yes."""
        if not self.cfg.mouse_text_only:
            return True
        rect = self._focus_rect
        if rect == "unknown":
            return True
        if rect is None:
            return False
        left, top, right, bottom = rect
        pad = 12
        return left - pad <= x <= right + pad and top - pad <= y <= bottom + pad

    def _watch_mouse(self):
        """Any click may move the caret, which ends what session.py knows about the text before it. Left button
        drags may select text in a terminal (selection.TermWatcher). Our own clicks (injected) are skipped."""
        from pynput import mouse
        downs = {0x201, 0x204, 0x207, 0x20B}   # left / right / middle / x button down

        def on_event(msg, data):
            if data.flags & 1:   # LLMHF_INJECTED
                return False
            if self.hotkeys.is_bound_mouse(msg, data.mouseData, data.pt):
                return False   # a button an operation uses is not a click
            if msg == 0x201:
                button = self.overlay.button_at(data.pt.x, data.pt.y)
                if button:
                    self._swallow_up = True
                    self.main.emit(lambda ok=button == "ok": self._overlay_button(ok))
                    listener.suppress_event()    # the click belongs to the pill, not to the app under it
            elif msg == 0x202 and self._swallow_up:
                self._swallow_up = False
                listener.suppress_event()
            if msg in downs and self.copycard.isVisible():
                if self.copycard.contains_screen_point(data.pt.x, data.pt.y):
                    return False      # a click on the card is not a click in the app
                self.main.emit(self.copycard.dismiss)
            if msg in downs:
                if self._on_picker(data.pt):
                    return False   # a click in the 選字 menu leaves the caret and the selection where they are
                self.main.emit(self.picker.dismiss)   # a click anywhere else closes it
                self.corrections.on_pointer(hwnd=foreground())
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
        self._close_menu()
        self.reload_shared()
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
            # A double tap replaces the pending tap. Its speculative microphone belongs to the first press,
            # even when the second press is a delete (and never starts a microphone of its own).
            self._discard_mic(pending)
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
        press.up_at = t
        if press.consumed:
            press.released = True
            return
        if not press.held and t - press.down_at >= TAP_THRESHOLD:   # the hold timer has not run yet
            self._hold_check(press)
        press.released = True
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
            if self.cfg.mic_exclusive and False:      # disabled: the mute is shared with our own capture
                self.micguard.mute()
            # Windows can keep a microphone session's mute for a program: ours was muted once (flat waveform,
            # words guessed, 2026-10-15), so our own session is unmuted at every recording
            self.micguard.recover()
            if self.cfg.discord_mute:
                self.discord.start(self.cfg.discord_key)
            if self.cfg.voice_stop:
                self._voice_seen = 0
                self._voice_timer.start()

    def _release_mic(self, press: _Press):
        if not press.recording:
            return None
        press.recording = False
        if self._mic is press:
            self._mic = None
        self.micguard.restore()          # the other apps get the microphone back as soon as we stop listening
        self.discord.stop()
        self._voice_timer.stop()
        self.tray.setIcon(self.icon_idle)
        return self.recorder.stop()

    def _discard_mic(self, press: _Press):
        if press.recording:
            audio = self._release_mic(press)
            log.info("recording discarded: key=%s gesture=%s seconds=%.2f", press.key, press.gesture or "pending",
                     audio.size / 16000 if audio is not None else 0)
            self.overlay.hide_overlay()
            self._input_done()

    def _stop_recording(self, press: _Press):
        audio = self._release_mic(press)
        if audio is None:
            self.overlay.hide_overlay()
            self._input_done()
            return
        self.overlay.show_thinking()
        log.info("recording stopped: key=%s gesture=%s seconds=%.2f", press.key, press.gesture, audio.size / 16000)
        self.worker.submit(self._transcribe_job, self.recognizer, audio, press, self._hwnd)

    def _input_done(self):
        """Main thread: a recording ended in nothing, or its text was pasted. Unmute, unless another recording
        has started meanwhile."""
        if self._mic is None:
            self.muter.restore()

    # --- steps ---
    def _run_steps(self, press: _Press, steps: list, done=None, delay: float = 0.0):
        """Run steps in order on the steps thread (錄音 is not a step to run: the press records). delay: seconds to
        wait first (after a paste: a web or Electron app reads the clipboard a moment later, and an Enter that comes
        before it sends nothing and the text only appears, 2026-10-16)."""
        steps = [s for s in steps if not (s.type == rules.COMMAND and s.value in rules.RECORDS)]
        if not steps:
            if done:
                done()
            return
        self.steps.submit(self._steps_job, press, steps, done, delay)

    def _steps_job(self, press: _Press, steps: list, done, delay: float = 0.0):
        try:
            if delay:
                time.sleep(delay)
            for n, step in enumerate(steps):
                if n:
                    time.sleep(_STEP_GAP)
                if step.type == rules.WAIT:
                    time.sleep(max(0, int(step.value or 0)) / 1000)
                elif step.type == rules.KEYS:
                    vks = combo_vks(step.value)
                    if vks:
                        if 0x0D in vks:
                            self.corrections.on_key(0x0D,
                                                    ctrl=bool({0x11, 0xA2, 0xA3}.intersection(vks)),
                                                    shift=bool({0x10, 0xA0, 0xA1}.intersection(vks)),
                                                    alt=bool({0x12, 0xA4, 0xA5}.intersection(vks)),
                                                    hwnd=foreground())
                        else:
                            self.corrections.cancel()
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
                    sel = press.probe.result().selection if press.deleted is None else None
                    self._on_main(lambda: self._open_candidates(sel))
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
            self.corrections.cancel()
            paste_text(text)
            self.session.dictated(text, None, foreground())

    def _delete_selection(self, press: _Press, ctx):
        """刪除選取的文字: the text selected when the (first) press went down."""
        sel = ctx.selection if press.deleted is None else None
        if sel is None:
            log.info("delete selection: nothing selected")
            return
        self.corrections.cancel()
        log.info("delete %r on %s", sel.text, sel.app)
        if sel.term is not None:
            self.overlay.hide_now()
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
        self.corrections.cancel()
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
        return (rule.steps[i + 1:] if i is not None else []) + list(press.trailing or [])

    # --- transcription ---
    def _transcribe_job(self, rec, audio, press: _Press, hwnd):
        stamp = time.strftime("%H:%M:%S")
        t0, raw = time.monotonic(), ""
        try:
            ctx = press.probe.result()
            zh = getattr(ctx, "zh_terms", [])   # Chinese names / topic words on screen
            press.resolved.wait(3.0)
            t0 = time.monotonic()
            words = (self.hotwords.bias() + [(t, _SCREEN_TERM_SCORE) for t in ctx.terms]
                     + [(t, _SCREEN_ZH_SCORE) for t in zh])
            raw = rec.recognize(audio, _bias(words))
            commands = self._command_words()
            if commands and len(_TEXT_CHARS.sub("", raw)) <= max(map(len, commands)) + _COMMAND_SLACK:
                again = rec.recognize(audio, _bias([(w, _COMMAND_SCORE) for w in commands] + words))
                if again != raw:
                    log.info("asr with commands boosted: %s -> %s", raw, again)
                raw = again
            t_asr = time.monotonic() - t0
            log.info("asr %.2fs: %s (screen terms: %d en, %d zh)", t_asr, raw, len(ctx.terms), len(zh))
            laughing = False
            if self.cfg.laugh_enabled and self.sense is not None and audio is not None:
                laughing = laugh.heard(self.sense, audio)
                if laughing:
                    log.info("laughter heard")
            # no sentence-final punctuation when the caret has text on both sides: the user is inserting
            mid = ctx.caret is not None and all(s.strip() for s in ctx.caret)
            formatted = format_text(raw, mid)
            # a heard common word gives way to a screen name only without the AI: with it, the model reads the whole
            # sentence (在輕井澤鍋物開會 became 輕井澤鍋物凱蕙 by the rule, 2026-10-14)
            ai_on = self.cfg.ai_enabled and self._llm_settings().ready()
            formatted, fixed = screen_terms.fix(formatted, zh, getattr(ctx, "zh_fix", []), common_wins=not ai_on)
            formatted, fixed_en = screen_terms.fix_english(formatted, getattr(ctx, "en_names", []))
            fixed += fixed_en
            if fixed:
                log.info("screen terms fixed %d span(s)", fixed)   # never the words: screen text is not logged
            selected = ctx.selection is not None and press.deleted is None and press.first is None
            op = rules.pick(rules.candidates(self.cfg.rules, rules.SPEECH, formatted), selected, press.dictated)
            if op is not None:
                log.info("spoken operation: %s", op.phrase)
                self.record.emit({"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "fmt": "",
                                  "edit": f"操作：「{op.phrase}」"})
                self.main.emit(lambda: self._spoken_operation(press, op))
                return
            # a command said at the end after a pause (…你檢查一下。送出。): the text is typed, then the command runs
            phrases = [r.phrase for r in self.cfg.rules if r.enabled and r.source == rules.SPEECH
                       and not rules.problems(r, self.cfg.rules)]
            body, said = rules.split_trailing(formatted, phrases)
            tail_ops = [rules.pick(rules.candidates(self.cfg.rules, rules.SPEECH, ph), selected, press.dictated)
                        for ph in said]
            if said and all(op is not None for op in tail_ops):
                log.info("spoken operations at the end: %s", "、".join(said))
                steps = [st for op in tail_ops for st in op.steps]
                if not body.strip(_TEXT_STRIP):          # only commands (said more than once, or several)
                    self.record.emit({"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "fmt": "",
                                      "edit": "操作：" + "、".join(f"「{ph}」" for ph in said)})
                    self.main.emit(lambda: self._spoken_operation(press, SimpleNamespace(steps=steps)))
                    return
                press.trailing = steps
                formatted = body
            sym = edit.symbol(formatted) if self.cfg.punctuation_names else None
            described = None if sym or not self.cfg.describe_char else edit.described(formatted)
            if described:
                log.info("described character: %s", described)
                sym = described   # typed as it is, like a punctuation mark
            text = sym or formatted
            polished = False
            if not sym:   # smart picks (single characters)
                prefix = ctx.selection.before if selected else (ctx.caret[0] if ctx.caret else "")
                text = self.memory.apply(text, prefix)
                # the screen wins over smart picks: 成 -> 宸 learned for 林又宸 turned 吳宇澄 (fixed from the
                # screen) into 吳宇宸 (2026-10-14)
                text, _ = screen_terms.fix(text, zh, getattr(ctx, "zh_fix", []), common_wins=not ai_on)
                text, polished = self._polish(text, ctx, selected, mid)
                if self.cfg.casual_no_period:
                    text = drop_casual_period(text)
                if self.cfg.laugh_enabled:
                    text = laugh.apply(text, self.cfg.laugh_text, laughing)
            entry = {"time": stamp, "asr": to_traditional(raw), "asr_s": t_asr, "fmt": text,
                     "ai": polished,
                     # what was taken from the screen, for the 除錯紀錄 page only: in memory for this run, never
                     # in the log file (the user's condition) -- so the user can see what the program saw
                     "screen": "、".join(list(zh[:25]) + list(getattr(ctx, "en_names", [])[:15])) or "（沒有）"}
            if text.strip():
                self.history.add(Entry(time.time(), text, to_traditional(raw), ctx.app,
                                       round(len(audio) / SAMPLE_RATE, 2) if audio is not None else 0.0,
                                       polished))
            if described:
                entry["edit"] = f"指定字：{formatted.strip()} → {described}"
            if selected:
                self._edit_job(ctx.selection, text, bool(sym), entry, press)
                return
            replaced = press.deleted
            if replaced is not None:
                if not sym:
                    text = edit.replacement(replaced.text, text)
                entry["edit"] = f"{replaced.text} → {text}（刪除後重講）"
            self.record.emit(entry)
            nowhere = bool(text.strip()) and no_input_focused()
            self.main.emit(lambda: self._dictate(text, audio, hwnd, press, nowhere))
        except Exception as exc:
            log.exception("transcribe failed")
            detail = f"{type(exc).__name__}: {exc}"
            self.record.emit({"time": stamp, "asr": to_traditional(raw), "asr_s": time.monotonic() - t0,
                              "fmt": "", "audio_s": audio.size / 16000, "error": detail})
            self.main.emit(lambda: self._notify("辨識失敗，已留下紀錄。請到「設定 → 辨識紀錄」查看原因。"))
            self.main.emit(self.overlay.hide_overlay)
            self.main.emit(self._input_done)
        finally:
            _forget_screen(press)

    def _polish(self, text: str, ctx, selected: bool, mid: bool) -> tuple[str, bool]:
        """Worker thread: AI 潤稿 (llm.py) when it is on, or the text as it was. Never loses the dictation: any
        failure pastes the formatted transcript."""
        s = self._llm_settings()
        known = [w for w, _ in self.hotwords.bias()] + list(getattr(ctx, "zh_terms", [])) + list(getattr(ctx, "en_names", []))
        if not self.cfg.ai_enabled or not s.ready() or not llm.worth_polishing(text, known, self.cfg.ai_scope == "saver"):
            return text, False
        self.main.emit(self.overlay.show_polishing)
        c = llm.Context()
        if self.cfg.ai_context:
            c.app, c.title = ctx.app, ctx.title
            c.screen = getattr(ctx, "screen", "")
            if selected:
                c.before, c.after, c.selected = ctx.selection.before, ctx.selection.after, ctx.selection.text
            elif ctx.caret:
                c.before, c.after = ctx.caret
        c.inserting = mid
        glossary = [w for w, _ in self.hotwords.bias()]
        if self.cfg.ai_context:   # screen terms are screen content: sent only with the context switch on
            glossary += (list(getattr(ctx, "en_names", [])[:20]) + list(ctx.terms[:20])
                         + list(getattr(ctx, "zh_terms", [])[:40]))
        try:
            result = llm.polish(s, text, c, glossary)
            if s.fallback_reason:
                log.info("AI provider: %s", llm.result_label(s))
            return result, True
        except llm.LLMError as e:
            log.warning("AI 潤稿 failed, pasting the transcript: %s", e)
            if not self._ai_warned:
                self._ai_warned = True
                self._notify(f"AI 潤稿失敗，已改貼原始辨識結果：{str(e)[:120]}")
            return text, False

    def _toggle_ai(self, on: bool):
        """The tray switch for the AI (the user: 要有關掉 AI 額度的選項): saved at once, the settings page follows."""
        if on == self.cfg.ai_enabled:
            return
        self.cfg.ai_enabled = on
        self.cfg.save()
        if on:
            self._warm_ai()
        self.settings.load_values()
        self.toast.show_text("AI 潤稿已開啟" if on else "AI 潤稿已關閉，不會用掉額度", 2.5)

    def _warm_ai(self):
        if self.cfg.ai_enabled:
            llm.warm_up(self._llm_settings())

    def _llm_settings(self) -> llm.Settings:
        return llm.settings_from(self.cfg)

    def reload_shared(self):
        """Another computer saved the shared files (同步): read them again. Called at key-down."""
        self.hotwords.reload_if_changed()
        self.memory.reload_if_changed()
        if self.history.reload() and self.settings.isVisible():
            self.settings.refresh_history()

    def change_sync_folder(self):
        new = sync.folder(self.cfg.sync_dir)
        copied = sync.move(self.shared, new)
        log.info("sync folder %s -> %s, copied %s", self.shared, new, copied)
        self.shared = new
        self.hotwords = Hotwords(new / sync.HOTWORDS)
        self.memory = Memory(new / sync.PICKS)
        self.history.set_folder(new)
        self.settings.hotwords, self.settings.memory = self.hotwords, self.memory
        self.settings.memory_path = self.memory.path
        self.settings.refresh_hotwords()
        self.settings.refresh_history()

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
    def _dictate(self, text: str, audio, hwnd: int, press: _Press, nowhere: bool = False):
        """Main thread: paste a dictation, then run the operation's steps after 錄音. With no input field in focus
        (nowhere) it is shown on the 複製最後的轉錄 card instead."""
        self.overlay.hide_overlay()
        log.info("result: %s", text)
        if not text:
            self._input_done()
            return
        self._last_text = text
        if not nowhere:
            self.copycard.dismiss()
        if nowhere:
            log.info("no input field in focus: offering to copy")
            self._input_done()
            self.copycard.show_text(text, self.cfg.copy_key)
            return
        token = None
        try:
            token = self.corrections.prepare_before_paste()
        except Exception:
            log.exception("correction snapshot failed; continuing dictation")
        paste_text(text)
        try:
            self.corrections.after_paste(token, text)
        except Exception:
            log.exception("correction tracking failed; dictation was pasted")
        self._input_done()
        self.session.dictated(text, audio, foreground() or hwnd)
        # u = self.session.dictated(text, audio, foreground() or hwnd)
        # if audio is not None:
        #     self.bg.submit(self._suspect_job, u)
        self._run_steps(press, self._after_steps(press), delay=_AFTER_PASTE)

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

    def _hotword_error(self, word: str) -> str:
        if self.recognizer is None:
            return "模型載入中，請稍後再加入熱詞。"
        if not self.recognizer.hotword_spec([(word, 1)]):
            return "目前模型無法辨識此詞的部分字元，請移除符號或調整文字。"
        return ""

    def _learn_corrections(self, changes):
        """Verified edits from one submitted dictation; UI and stores update on the main thread."""
        learned = []
        for change in changes:
            saved = False
            # what is learned is exactly what the user changed, no more (the user, 2026-10-19: 改「置中」就只記「置中」，
            # not 正下方置中): a replaced span becomes the word, a single changed character is no word (it is a smart
            # pick below when it replaced one character)
            word = change.new.strip()
            named = False
            if word[:1] in "跟和與及的了把在是":
                word = ""        # it starts with a connecting character (跟吳 from 跟吳宇澄): not a word to learn
            # a single character is a smart pick only when it is not part of a name: the name is learned instead
            # (成 -> 宸 learned for 林又宸 later turned 吳宇澄 into 吳宇宸)
            if not named and ((len(change.old) == len(change.new) == 1
                                        and re.fullmatch(r"[㐀-䶿一-鿿]", change.old)
                                        and re.fullmatch(r"[㐀-䶿一-鿿]", change.new))
                                       or (is_latin(change.old) and is_latin(change.new))):
                saved = self.memory.record(change.text, change.start, change.end, change.new)
            if word and hotword_eligible(word) and not self._hotword_error(word):
                added = self.hotwords.add(word)
                log.info("manual correction: hotword add %s, the list has %d words", "ok" if added else "refused",
                         len(self.hotwords.items))
                saved = added or saved
            elif word:
                log.info("manual correction: hotword not added (%s)",
                         "not eligible" if not hotword_eligible(word) else self._hotword_error(word))
            if saved:
                learned.append(word.strip())
                log.info("manual correction learned: %r -> %r", change.old, change.new)
                self.record.emit({"time": time.strftime("%H:%M:%S"), "asr": "", "asr_s": None,
                                  "fmt": "", "edit": f"手動修正學習：{change.old} → {change.new}"})
        if learned:   # Typeless-style notice (the user's wording, 2026-10-14)
            self.toast.show_text("、".join(f"「{w}」" for w in learned[:3]) + " 已加入字典")
        self.settings.refresh_hotwords()

    # --- popups: 選字 and the hotword notice ---
    def _close_menu(self):
        if self.picker.isVisible():
            self.picker.hide()
        self._menu = None

    def _open_candidates(self, sel):
        """選字: the candidate menu for the selection, under it."""
        if sel is None or not sel.text.strip():
            log.info("pick: nothing selected")
            return
        log.info("candidates for %r on %s", sel.text, sel.app)
        self._menu = ("pick", sel)
        self.picker.show_loading(sel.text.strip(), sel.rect)
        threading.Thread(target=lambda: self.candidates_ready.emit(sel, self._menu_words(sel)), daemon=True).start()

    def _menu_words(self, sel) -> list[str]:
        """Rows for the selection. One Chinese character: what the smart picks decide here, everything picked for it
        before, then same-sounding characters. A word: same-sounding words, the same reading first (Chinese), or ways
        of writing it, the most picked style first (English); hotwords first among equals, heaviest first."""
        text = sel.text.strip()
        try:
            if is_latin(text):
                rows = self.memory.ordered_variants(text)
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
        except Exception:
            log.exception("candidates failed")
            rows = []
        out = []
        for w in rows:
            if w and w != text and w not in out:
                out.append(w)
        return out[:_MENU_ROWS]

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
        self.corrections.cancel()
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
        self.hotwords.picked(text, word)
        if typed and self.hotwords.add(word):
            log.info("hotword typed: %s", word)
        self.settings.refresh_hotwords()
        self.session.replaced_selection(sel.before, word, foreground())
        self.record.emit({"time": time.strftime("%H:%M:%S"), "asr": "", "asr_s": None, "fmt": "",
                          "edit": f"{'輸入' if typed else '選字'}：{text} → {word}"})

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
        self.corrections.cancel()
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
        self._run_steps(press, self._after_steps(press), delay=_AFTER_PASTE)

    # --- settings / status ---
    def on_status(self, text: str):
        self._status_text = text
        log.info("status: %s", text)
        self.tray.setToolTip(f"VoiceInput — {text}")
        self.settings.set_status(text)

    def on_record(self, entry: dict):
        self._records.append(entry)
        self.settings.set_log(self._records)
        if self.settings.isVisible():
            self.settings.refresh_history()

    def open_settings(self):
        self.settings.load_values()
        self.settings.set_log(self._records)
        self.settings.set_status(self._status_text)
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()

    def _sync_laugh(self):
        """SenseVoice (the laughter listener) is in memory only while the setting is on."""
        if not self.cfg.laugh_enabled:
            self.sense = None
        elif self.sense is None and not self._sense_loading:
            self._sense_loading = True
            self.worker.submit(self._load_sense_job)

    def _load_sense_job(self):
        try:
            if not models.is_installed(models.SECOND_MODEL):
                models.download(models.SECOND_MODEL)
            t0 = time.monotonic()
            self.sense = Recognizer(models.SECOND_MODEL)
            log.info("laughter listener loaded in %.1fs", time.monotonic() - t0)
        except Exception:
            log.exception("the laughter listener failed to load")
        finally:
            self._sense_loading = False

    def apply_settings(self):
        self._sync_laugh()
        self.hotkeys.set_keys(rules.bound_keys(self.cfg.rules))
        can = rules.can_record(self.cfg.rules)
        if self._could_record and not can:
            self._notify(_NO_RECORD)
        self._could_record = can
        self._set_autostart()
        if self.ai_action.isChecked() != self.cfg.ai_enabled:
            self.ai_action.blockSignals(True)       # the settings page changed it: only the menu follows
            self.ai_action.setChecked(self.cfg.ai_enabled)
            self.ai_action.blockSignals(False)
        self._warm_ai()

    def _set_autostart(self):
        try:
            autostart.set_enabled(self.cfg.autostart)
        except Exception as e:
            log.exception("autostart failed")
            self.settings.set_status(f"開機啟動設定失敗：{e}")

    def quit(self):
        self.corrections.close()
        self.muter.restore()
        self.micguard.restore()
        self.discord.stop()
        self.hotkeys.stop()
        if self._mouse:
            self._mouse.stop()
        self.tray.hide()
        llm.codex_cli.shutdown()
        llm.antigravity_cli.shutdown()
        self.qapp.quit()


def _forget_screen(press):
    """The terms read from the screen are dropped once their dictation is done (the user's condition)."""
    try:
        ctx = press.probe.result()      # already read: the dictation used it
        ctx.terms, ctx.zh_terms, ctx.zh_fix, ctx.en_names = [], [], [], []
        ctx.screen = ""
    except Exception:
        pass


def _bias(words: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Biasing words without a word another one starts with: with 剪貼 and 剪貼簿 both listed, X-ASR wrote 剪貼布
    (the short one is matched first and takes the boost; dev experiment 2026-10-11). The first of duplicates wins."""
    seen = {w.casefold() for w, _ in words}
    out, kept = [], set()
    for w, score in words:
        k = w.casefold()
        if k in kept or any(o != k and o.startswith(k) for o in seen):
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
    qapp = QApplication(sys.argv)
    qapp.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(paths.LOCK_PATH))
    if not lock.tryLock(100):
        log.info("already running")
        return 0

    app = App(qapp)  # noqa: F841 (keep reference alive)
    return qapp.exec()
