"""Observe a verified dictation field and learn short replacements after submission.

No keys are intercepted or replayed. UIA runs on one daemon worker; a slow provider
can disable learning, but cannot hold up dictation beyond the bounded pre-paste wait.
"""
from dataclasses import dataclass, field
import difflib
import logging
import queue
import re
import threading
import time

log = logging.getLogger("voiceinput")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:['._+#-][A-Za-z0-9]+)*|[㐀-䶿一-鿿]")
_TERM = re.compile(r"(?:[㐀-䶿一-鿿]{1,4}|[A-Za-z0-9][A-Za-z0-9'._+#-]*(?: [A-Za-z0-9'._+#-]+)?)$")
_COMPOSING = re.compile(r"[㄀-ㄯㆠ-ㆿˊˇˋ˙]")   # 注音 still being typed
_MODIFIERS = {0x10, 0x11, 0xA0, 0xA1, 0xA2, 0xA3}  # Shift/Ctrl do not edit text alone
_CANCEL_KEYS = {0x09, 0x1B, 0x12, 0xA4, 0xA5, 0x5B, 0x5C}  # Tab/Esc/Alt/Windows


@dataclass(frozen=True)
class InputSnapshot:
    hwnd: int
    pid: int
    identity: tuple[int, ...]
    text: str
    start: int | None = None
    end: int | None = None

    @property
    def scope(self):
        return self.hwnd, self.pid, self.identity


@dataclass(frozen=True)
class Correction:
    text: str                # original field, for Memory.record's bounded context
    start: int
    end: int
    new: str

    @property
    def old(self) -> str:
        return self.text[self.start:self.end]


@dataclass(frozen=True)
class ReadTicket:
    generation: int
    revision: int
    started: float


def _adjacent(tokens) -> bool:
    """The tokens touch each other in the text (no space or punctuation between): one run, one word."""
    return all(a.end() == b.start() for a, b in zip(tokens, tokens[1:]))


def _replacements(original: str, corrected: str):
    """Offsets of short lexical replacements; case is significant, insertions are skipped."""
    old, new = list(_TOKEN.finditer(original)), list(_TOKEN.finditer(corrected))
    if not old or not new:
        return []
    # Trim unchanged ends in linear time before SequenceMatcher. Repeated long
    # dictations otherwise have quadratic matching cost inside the key hook.
    prefix = 0
    while prefix < min(len(old), len(new)) and old[prefix].group() == new[prefix].group():
        prefix += 1
    suffix = 0
    while suffix < min(len(old), len(new)) - prefix and old[-suffix - 1].group() == new[-suffix - 1].group():
        suffix += 1
    old_middle = old[prefix:len(old) - suffix]
    new_middle = new[prefix:len(new) - suffix]
    if max(len(old_middle), len(new_middle)) > 32:
        return []
    matcher = difflib.SequenceMatcher(a=[m.group() for m in old_middle],
                                     b=[m.group() for m in new_middle], autojunk=False)
    ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
    if not ops or len(ops) > 4:
        return []
    # words added or removed are not mistakes the recognizer made: skip them, keep learning the replacements
    # (幫你發娛樂城更正宇澄醫生 -> 包你發娛樂城跟吳宇澄 dropped 醫生 and was not learned at all, 2026-10-14)
    ops = [op for op in ops if op[0] == "replace"]
    if not ops:
        return []
    # edits a character or two apart inside one Chinese run are one word: 醉夜狂喜 -> 罪夜狂襲 is learned as the
    # word 罪夜狂襲, not as 醉 -> 罪 and 喜 -> 襲 (2026-10-14)
    merged = [ops[0]]
    for op in ops[1:]:
        _, a0, b0, c0, d0 = merged[-1]
        _, a1, b1, c1, d1 = op
        gap = old_middle[b0:a1]
        if a1 - b0 <= 2 and c1 - d0 == a1 - b0 \
                and all(re.fullmatch(r"[㐀-䶿一-鿿]", m.group()) for m in old_middle[a0:b1]) \
                and _adjacent(old_middle[a0:b1]):
            merged[-1] = ("replace", a0, b1, c0, d1)
        else:
            merged.append(op)
    ops = merged
    matching = prefix + suffix + sum(block.size for block in matcher.get_matching_blocks())
    if 2 * matching / (len(old) + len(new)) < 0.5 and not (
            _TERM.fullmatch(original.strip()) and _TERM.fullmatch(corrected.strip())):
        return []
    changes = []
    for _, a, b, c, d in ops:
        a, b, c, d = a + prefix, b + prefix, c + prefix, d + prefix
        start, end = old[a].start(), old[b - 1].end()
        value = corrected[new[c].start():new[d - 1].end()]
        previous = original[start:end]
        if max(len(previous), len(value)) > 20 or "\n" in previous + value:
            return []
        changes.append((start, end, value))
    return changes


class CorrectionMonitor:
    """Pure state machine. The watcher serializes calls; unit tests need no OS access."""
    QUIET = 0.25
    SAMPLE_GAP = 0.10
    CACHE_AGE = 0.60
    SUBMIT_TIMEOUT = 0.75
    LIFETIME = 120.0
    UNREADABLE_GRACE = 5.0   # seconds without a read of the field before the watch is given up
    WHOLE_FIELD = 1000    # a field this short (a chat box) is compared whole; a longer one only at the dictation
    LATEST_AFTER = 0.10   # a read begun this long after the last key may stand in for a settled one on Enter
    LATEST_AGE = 1.00     # (LINE reads slowly: the send came before two settled reads, 2026-10-14)
    LAST_GOOD_AGE = 3.0   # the last read after typing, when 注音's Enter came between it and the send

    def __init__(self):
        self.generation = 0
        self.cancel()

    @property
    def watching(self) -> bool:
        return self._scope is not None

    def _stop(self, reason: str):
        log.info("correction: stopped watching (%s)", reason)    # why nothing is learned, never the text
        self.cancel()

    def cancel(self):
        self.generation += 1
        self.revision = 0
        self._scope = None
        self._sample = None
        self._cache = None
        self._pending = None
        self._latest = None
        self._last_good = None
        self._last_typing = 0.0
        self._has_input = False

    def arm(self, before: InputSnapshot | None, after: InputSnapshot | None, text: str, *, now: float) -> bool:
        self.cancel()
        if before is None or after is None or not text or before.scope != after.scope:
            return False
        if not before.hwnd or not before.pid or not before.identity:
            return False
        if before.start is not None and before.end is not None:
            a, b = before.start, before.end
            if not 0 <= a <= b <= len(before.text):
                return False
            if before.text[:a] + text + before.text[b:] != after.text:
                return False
            if after.start is not None and (after.start != after.end or after.start != a + len(text)):
                return False
        else:
            # ValuePattern has no caret. Require exactly one possible inserted/replaced span.
            positions, at = [], 0
            while True:
                at = after.text.find(text, at)
                if at < 0:
                    break
                prefix, suffix = after.text[:at], after.text[at + len(text):]
                if before.text.startswith(prefix) and before.text.endswith(suffix) \
                        and len(before.text) >= len(prefix) + len(suffix):
                    positions.append(at)
                at += 1
            if len(positions) != 1:
                return False
            a = positions[0]
        self._scope = after.scope
        self._base = after.text
        # the whole field is compared, not only the dictation: the user also edits an earlier dictation still in
        # the box (貓米發娛樂城 before 我說包你發娛樂城), which stopped the watch as "text around changed" (2026-10-14)
        # A long document keeps the old rule: only the dictation, the text around it must stay.
        if len(after.text) <= self.WHOLE_FIELD:
            self._original = after.text
            self._prefix = self._suffix = ""
        else:
            self._original = text
            self._prefix, self._suffix = after.text[:a], after.text[a + len(text):]
        self._armed_at = self._last_input = self._last_ok = now
        return True

    def ticket(self, *, now: float) -> ReadTicket | None:
        return ReadTicket(self.generation, self.revision, now) if self.watching else None

    def _note(self, text: str):
        """Log a reason once per distinct text (why a watch goes quiet; never the field's text)."""
        if getattr(self, "_last_note", "") != text:
            self._last_note = text
            log.info("correction: %s", text)

    def _input(self, now, typing: bool = True):
        self.revision += 1
        self._last_input = now
        if typing:                   # a key that types or a click; not the Enter that confirms 注音
            self._last_typing = now
            self._last_good = None
        if self._pending is not None:
            self._note("pending dropped: a key or click after Enter")
        self._sample = self._cache = self._pending = self._latest = None
        self._has_input = True

    def on_pointer(self, *, hwnd: int | None = None, now: float):
        if not self.watching:
            return
        if hwnd != self._scope[0]:
            self._stop("a click in another window")
        else:
            self._input(now)

    def on_key(self, vk: int, *, ctrl=False, shift=False, alt=False, hwnd: int | None = None, now: float) -> bool:
        if not self.watching:
            return False
        if hwnd != self._scope[0] or vk in _CANCEL_KEYS or alt:
            self._stop("a key in another window" if hwnd != self._scope[0] else f"key {vk:#x} (Tab/Esc/Alt/Win)")
            return False
        if vk in _MODIFIERS:
            return False
        if vk == 0x0D and not shift:
            if self._has_input and self._cache is not None:
                revision, value, at = self._cache
                if revision == self.revision and now - at <= self.CACHE_AGE \
                        and now - self._last_input >= self.QUIET and _replacements(self._original, value):
                    self._pending = (value, now, revision)
                    log.info("correction: Enter after an edit, waiting for the field to clear")
                    return True
            if self._has_input and self._latest is not None:
                # no settled read yet (LINE answers slowly): the latest read begun after the last keystroke
                revision, value, at = self._latest
                if revision == self.revision and now - at <= self.LATEST_AGE and _replacements(self._original, value):
                    self._pending = (value, now, revision)
                    log.info("correction: Enter after an edit (latest read), waiting for the field to clear")
                    return True
            if self._has_input and self._last_good is not None:
                # LINE with 注音: the IME's own Enter, then the send, too fast for a read in between, and reads
                # fail while the candidate window has the focus. The last read begun after the last typing key or
                # click, with no 注音 still composing, at most LAST_GOOD_AGE old.
                value, at = self._last_good
                if now - at <= self.LAST_GOOD_AGE and not _COMPOSING.search(value)                         and _replacements(self._original, value):
                    self._pending = (value, now, self.revision)
                    log.info("correction: Enter after an edit (last good read), waiting for the field to clear")
                    return True
            # why nothing is learned, without the text (2026-10-14: 64 captures, never a lesson, no way to tell)
            reason = ("no edit" if not self._has_input else "field not read yet" if self._cache is None
                      else "edited too recently" if now - self._last_input < self.QUIET
                      else "stale read" if self._cache[0] != self.revision or now - self._cache[2] > self.CACHE_AGE
                      else "not a short word replacement")
            log.info("correction: Enter, nothing to learn (%s)", reason)
            # This may be IME confirmation; require fresh reads before a later Enter.
            self._input(now, typing=False)
            return False
        self._input(now)
        return False

    def observe(self, snapshot: InputSnapshot | None, ticket: ReadTicket | None, *, now: float) -> list[Correction]:
        if not self.watching or ticket is None or ticket.generation != self.generation \
                or ticket.revision != self.revision:
            if self.watching and ticket is not None and self._pending is not None:
                self._note("pending: a read from before the last key or click was dropped")
            return []
        if snapshot is None and now - self._last_ok <= self.UNREADABLE_GRACE and now - self._armed_at <= self.LIFETIME:
            return []         # the focus is somewhere else for a moment (注音 composing in Claude's box, 2026-10-14)
        if snapshot is None or snapshot.scope != self._scope or now - self._armed_at > self.LIFETIME:
            self._stop("the field could not be read" if snapshot is None else "another field"
                       if snapshot.scope != self._scope else "two minutes passed")
            return []
        self._last_ok = now
        if self._pending is not None:
            value, sent_at, revision = self._pending
            if ticket.started < sent_at:
                self._note("pending: waiting for a read begun after Enter")
                return []  # a read begun before Enter cannot confirm that Enter cleared the field
            if now - sent_at > self.SUBMIT_TIMEOUT:
                log.info("correction: the field did not clear after Enter (注音's Enter?), still watching")
                self._input(now, typing=False)
                return []
            self._note(f"pending: a read after Enter, the field has {len(snapshot.text)} characters")
            if not snapshot.text.strip() and revision == self.revision:
                offset = len(self._prefix)
                changes = [Correction(self._base, offset + a, offset + b, new)
                           for a, b, new in _replacements(self._original, value)]
                self.cancel()
                return changes
            current = self._prefix + value + self._suffix
            if snapshot.text != current:
                self._input(now)  # IME may have committed a different value; the cached value cannot be learned
            return []
        if len(snapshot.text) < len(self._prefix) + len(self._suffix) \
                or not snapshot.text.startswith(self._prefix) or not snapshot.text.endswith(self._suffix):
            self._stop("the text around the dictation changed")
            return []
        end = len(snapshot.text) - len(self._suffix) if self._suffix else len(snapshot.text)
        value = snapshot.text[len(self._prefix):end]
        if not value:
            return []      # deleted to be typed again (醉夜狂喜 -> 罪夜狂襲, 2026-10-14): keep watching
        if value != self._original and ticket.started >= self._last_typing + self.LATEST_AFTER:
            self._last_good = value, ticket.started
        if ticket.started >= self._last_input + self.LATEST_AFTER:
            self._latest = self.revision, value, ticket.started
        if ticket.started < self._last_input + self.QUIET:
            return []
        if self._sample is None or self._sample[0] != value:
            self._sample = value, ticket.started
            self._cache = None
        elif ticket.started - self._sample[1] >= self.SAMPLE_GAP:
            self._cache = self.revision, value, ticket.started
        return []


@dataclass(frozen=True)
class PasteToken:
    generation: int
    input_sequence: int
    snapshot: InputSnapshot


@dataclass
class _BeforeRequest:
    generation: int
    input_sequence: int
    expires: float
    done: threading.Event = field(default_factory=threading.Event)
    snapshot: InputSnapshot | None = None


class CorrectionWatcher:
    """Main/hook-thread API around a single daemon UIA worker.

    on_corrections runs on the worker: marshal any UI updates to the main thread.
    Tests/headless callers inject a reader and warmup so they never inspect the desktop.
    """
    POLL_INTERVAL = 0.05

    def __init__(self, on_corrections, *, reader=None, warmup=None):
        if reader is None:
            from .selection import read_input_snapshot, initialize_input_snapshot_reader
            reader = read_input_snapshot
            warmup = warmup or initialize_input_snapshot_reader
        self._reader, self._warmup = reader, warmup
        self._on_corrections = on_corrections
        self._lock = threading.RLock()
        self._monitor = CorrectionMonitor()
        self._generation = self._input_sequence = 0
        self._tasks = queue.Queue()
        self._closed = threading.Event()
        threading.Thread(target=self._work, daemon=True, name="VoiceInput corrections").start()

    @property
    def watching(self):
        with self._lock:
            return self._monitor.watching

    def cancel(self):
        with self._lock:
            self._generation += 1
            self._monitor.cancel()

    def close(self):
        self._closed.set()
        self.cancel()  # never join a thread which may be waiting inside another app's UIA provider

    def prepare_before_paste(self, timeout=0.12) -> PasteToken | None:
        timeout = max(0, min(float(timeout), 0.5))
        with self._lock:
            if self._closed.is_set():
                return None
            self.cancel()
            request = _BeforeRequest(self._generation, self._input_sequence, time.monotonic() + timeout)
        self._tasks.put(("before", request))
        request.done.wait(timeout)
        with self._lock:
            if request.snapshot is None or request.generation != self._generation \
                    or request.input_sequence != self._input_sequence:
                log.info("correction capture skipped: before snapshot unavailable or timed out")
                return None
            return PasteToken(request.generation, request.input_sequence, request.snapshot)

    def after_paste(self, token: PasteToken | None, text: str):
        if token is not None and text and not self._closed.is_set():
            self._tasks.put(("after", (token, text, time.monotonic() + 0.6)))

    def on_key(self, vk, *, ctrl=False, shift=False, alt=False, hwnd=None):
        with self._lock:
            if vk not in _MODIFIERS:
                self._input_sequence += 1
            if vk in _CANCEL_KEYS or alt:
                if self._monitor.watching:
                    log.info("correction: stopped watching (key %#x with Tab/Esc/Alt/Win)", vk)
                self.cancel()
                return False
            return self._monitor.on_key(vk, ctrl=ctrl, shift=shift, alt=alt, hwnd=hwnd, now=time.monotonic())

    def on_pointer(self, *, hwnd=None):
        with self._lock:
            self._input_sequence += 1
            self._monitor.on_pointer(hwnd=hwnd, now=time.monotonic())

    def _read(self):
        try:
            result = self._reader()
            return result if isinstance(result, InputSnapshot) else None
        except Exception:
            log.debug("correction snapshot unavailable", exc_info=True)
            return None

    def _after(self, token, text, expires):
        while not self._closed.is_set() and time.monotonic() <= expires:
            with self._lock:
                if token.generation != self._generation or token.input_sequence != self._input_sequence:
                    return
            snapshot = self._read()  # a fresh call after paste; never reuse the before read
            with self._lock:
                if token.generation != self._generation or token.input_sequence != self._input_sequence \
                        or time.monotonic() > expires:
                    return
                if self._monitor.arm(token.snapshot, snapshot, text, now=time.monotonic()):
                    log.info("correction capture armed: hwnd=%s pid=%s chars=%d", snapshot.hwnd, snapshot.pid, len(text))
                    return
                if snapshot is None or snapshot.scope != token.snapshot.scope or snapshot.text != token.snapshot.text:
                    return
            self._closed.wait(0.05)  # the app may not have applied its clipboard paste yet

    def _work(self):
        if self._warmup is not None:
            try:
                self._warmup()  # initialize COM/type library, without reading a field
            except Exception:
                log.debug("correction UIA initialization unavailable", exc_info=True)
        while not self._closed.is_set():
            try:
                # Keep this below prepare_before_paste's 120 ms budget so a
                # request queued just after a poll cannot routinely expire first.
                kind, request = self._tasks.get(timeout=self.POLL_INTERVAL)
            except queue.Empty:
                with self._lock:
                    ticket = self._monitor.ticket(now=time.monotonic())
                if ticket is not None:
                    snapshot = self._read()
                    if snapshot is None:
                        import sys as _sys
                        problem = getattr(_sys.modules.get("voiceinput.selection"), "snapshot_problem", "")
                        log.info("correction: field unreadable (%s)", problem or "unknown")
                    with self._lock:
                        changes = self._monitor.observe(snapshot, ticket, now=time.monotonic())
                    if changes:
                        try:
                            self._on_corrections(changes)
                        except Exception:
                            log.exception("saving manual corrections failed")
                continue
            if kind == "before":
                with self._lock:
                    valid = request.generation == self._generation and time.monotonic() <= request.expires
                snapshot = self._read() if valid else None
                with self._lock:
                    if request.generation == self._generation and request.input_sequence == self._input_sequence \
                            and time.monotonic() <= request.expires:
                        request.snapshot = snapshot
                request.done.set()
            else:
                self._after(*request)
