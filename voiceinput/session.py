"""What VoiceInput knows about the text right before the caret.

Every dictation is pasted at the caret. As long as the user has not pressed a key or clicked since, and the same
window is in front, the text before the caret ends with exactly what we pasted (the "buffer"). Deleting the last
dictation is done by pressing Backspace up to the edit point and pasting the new tail. This works in any app,
terminals included (Claude Code), because it needs neither UI Automation nor a selection. Any key press or mouse
click from the user ends the buffer: the caret may have moved.

復原 (a double tap or saying it) takes back the last change VoiceInput made, up to UNDO_DEPTH of them, while they
are still right before the caret (the user's request, 2026-10-14; one, the last dictation, from 2026-10-04): typed
text, a punctuation mark, a new line, text spoken over a selection (the selection comes back), a deleted
selection.
"""
import threading
import time
from dataclasses import dataclass, field

UNDO_DEPTH = 3


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
        self.history: list[Step] = []          # the last changes, oldest first, while they can be taken back

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
                self.buffer, self.utterances, self.history = "", [], []
            before, prior = self.buffer, list(self.utterances)
            start = len(self.buffer)
            self.buffer += text
            u = Utterance(start, len(self.buffer), text, audio)
            self.utterances.append(u)
            self._push(Step(before, self.buffer, prior))
            self.hwnd, self.dirty = hwnd, False
            return u

    def _push(self, step: Step):
        self.history = (self.history + [step])[-UNDO_DEPTH:]

    def replaced_selection(self, before: str, new: str, hwnd: int, undo_to: str | None = None):
        """Record an edit of a selection: the caret now follows new, with before (rest of the line) ahead.
        undo_to: what 復原 turns before + new back into (before + the selected text), None when it cannot be."""
        with self._lock:
            self.buffer, self.utterances = before + new, []
            self.history = [Step(undo_to, self.buffer, [])] if undo_to is not None else []
            self.hwnd, self.dirty = hwnd, False

    def retyped(self, new: str):
        """The caller turned the buffer into new (Backspace + paste): a draft shown while listening changed. The
        last change, when it was the draft, now ends with it."""
        with self._lock:
            if self.history and self.history[-1].after == self.buffer:
                self.history[-1].after = new
            self.buffer = new
            self.dirty = False

    def changed(self, new: str):
        """The caller turned the buffer into new (「X改成Y」): a change 復原 can take back."""
        with self._lock:
            self._push(Step(self.buffer, new, list(self.utterances)))
            self.buffer, self.utterances, self.dirty = new, [], False

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
            if self.dirty or self.hwnd != foreground() or not self.history or self.history[-1].after != self.buffer:
                return None
            return self.history[-1]

    def deleted_last(self, step: Step):
        with self._lock:
            if self.history and self.history[-1] is step:
                self.history.pop()
            self.buffer = step.before
            self.utterances = step.utterances
            self.dirty = False
