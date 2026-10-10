"""What VoiceInput knows about the text right before the caret.

Every dictation is pasted at the caret. As long as the user has not pressed a key or clicked since, and the same
window is in front, the text before the caret ends with exactly what we pasted (the "buffer"). Deleting the last
dictation is done by pressing Backspace up to the edit point and pasting the new tail. This works in any app,
terminals included (Claude Code), because it needs neither UI Automation nor a selection. Any key press or mouse
click from the user ends the buffer: the caret may have moved.

A double tap of the hotkey deletes the last dictation, only while it is the last change and only that one
(decided 2026-10-04: no multi-step undo). Text spoken over a selection counts as a dictation too (2026-10-07): a
double tap deletes it, leaving the selected text gone as typing over it would.
"""
import threading
import time
from dataclasses import dataclass, field


def foreground() -> int:
    import ctypes
    return ctypes.windll.user32.GetForegroundWindow()


@dataclass
class Utterance:
    start: int              # span of the pasted text in Session.buffer
    end: int
    text: str               # what was pasted
    audio: object           # numpy audio, for the second-opinion model (off for now)
    at: float = field(default_factory=time.monotonic)
    second: str | None = None   # second model's formatted text, filled in the background


@dataclass
class Step:
    before: str             # the buffer before the change
    after: str              # the buffer right after it
    utterances: list        # Session.utterances before the change, restored when it is deleted


class Session:
    def __init__(self):
        self._lock = threading.RLock()
        self.buffer = ""
        self.hwnd = 0
        self.dirty = True
        self.utterances: list[Utterance] = []
        self.step: Step | None = None          # the last change, while it can still be deleted

    def on_input(self):
        """The user pressed a key or clicked (hook threads): the caret may have moved."""
        self.dirty = True

    def clean(self) -> bool:
        """True if the text before the caret is known to end with self.buffer."""
        return not self.dirty and bool(self.buffer) and self.hwnd == foreground()

    # --- changes (the caller performs the keystrokes) ---
    def dictated(self, text: str, audio, hwnd: int) -> Utterance:
        """Record a paste of text at the caret."""
        with self._lock:
            if self.dirty or hwnd != self.hwnd:
                self.buffer, self.utterances = "", []
            before, prior = self.buffer, list(self.utterances)
            start = len(self.buffer)
            self.buffer += text
            u = Utterance(start, len(self.buffer), text, audio)
            self.utterances.append(u)
            self.step = Step(before, self.buffer, prior)
            self.hwnd, self.dirty = hwnd, False
            return u

    def replaced_selection(self, before: str, new: str, hwnd: int, undo_to: str | None = None):
        """Record an edit of a selection: the caret now follows new, with before (rest of the line) ahead.
        undo_to: what a double tap turns before + new back into (spoken text), None when it cannot be deleted."""
        with self._lock:
            self.buffer, self.utterances = before + new, []
            self.step = Step(undo_to, self.buffer, []) if undo_to is not None else None
            self.hwnd, self.dirty = hwnd, False

    def replace_tail(self, old: str, new: str) -> tuple[int, str] | None:
        """The text right before the caret ends with old (just typed, such as a spelled character): plan turning it
        into new, recorded as if new had been typed in its place. None when the caret may have moved."""
        with self._lock:
            if not self.clean() or not old or not self.buffer.endswith(old):
                return None
            target = self.buffer[:-len(old)] + new
            plan = self.plan_to(target)
            u = self.utterances[-1] if self.utterances else None
            if u is not None and u.end == len(self.buffer) and u.text.endswith(old):
                u.text, u.end = u.text[:-len(old)] + new, len(target)
            if self.step is not None and self.step.after == self.buffer:
                self.step.after = target
            self.buffer = target
            return plan

    def plan_to(self, after: str) -> tuple[int, str]:
        """Keystrokes to turn the buffer into after: (backspaces, text to paste)."""
        p = 0
        n = min(len(self.buffer), len(after))
        while p < n and self.buffer[p] == after[p]:
            p += 1
        return len(self.buffer) - p, after[p:]

    def deletable(self) -> Step | None:
        """The last change if it is still right before the caret."""
        with self._lock:
            if not self.clean() or self.step is None or self.step.after != self.buffer:
                return None
            return self.step

    def deleted_last(self, step: Step):
        with self._lock:
            self.step = None
            self.buffer = step.before
            self.utterances = step.utterances
            self.dirty = False
