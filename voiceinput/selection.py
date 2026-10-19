"""Read the selected text and its surrounding line in the focused app via Windows UI Automation.

Tested (2026-09-27): Chrome/Edge inputs, Win11 Notepad and LINE expose a TextPattern with the selection and
its line; the first read in LINE takes ~1.2s, later reads 5-50ms.

Terminals (2026-09-30, keyboard only since 2026-10-02): a TUI such as herdr / Claude Code / Codex / agy draws its
own mouse selection, so Windows Terminal reports none, and a paste goes to the app's cursor. A mouse drag there is
watched instead (TermWatcher): UIA RangeFromPoint maps the press and release points to the characters under them,
and the text between them is read from the screen (no clipboard: herdr's copy_on_select may be off), so the
selection's exact place is known (a repeated word included). It is used only on one line of the input box the
terminal cursor is in. To edit it, arrow keys move the app's cursor right after it, re-reading the cursor after
each burst (move_cursor_after), and Backspace removes it. Windows Terminal reports its cursor as an empty UIA
selection.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import queue
import re
import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from . import screen_terms

log = logging.getLogger("voiceinput")

_TERMINALS = {"windowsterminal.exe", "conhost.exe", "openconsole.exe", "wezterm-gui.exe", "alacritty.exe",
              "mintty.exe", "tabby.exe", "warp.exe"}
_CHAR, _LINE, _START, _END = 0, 3, 0, 1
_local = threading.local()


@dataclass
class Selection:
    text: str
    before: str       # rest of the line before the selection
    after: str        # rest of the line after the selection
    app: str          # process name, e.g. chrome.exe
    rect: tuple | None = None   # selection bounds (x, y, w, h) in physical screen pixels (where menus open)
    term: tuple | None = None   # terminal: screen point of the selection's last character


def _proc_name(pid: int) -> str:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(260)
        size = wt.DWORD(260)
        k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return buf.value.rsplit("\\", 1)[-1].lower()
    finally:
        k32.CloseHandle(h)


def _uia():
    """One IUIAutomation per thread (COM objects are apartment-bound)."""
    if not hasattr(_local, "uia"):
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U
        _local.U = U
        _local.uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
    return _local.uia, _local.U


def read_selection() -> Selection | None:
    """The focused app's non-empty selection, or None (nothing selected, unsupported app, terminal)."""
    try:
        uia, U = _uia()
        el = uia.GetFocusedElement()
        app = _proc_name(el.CurrentProcessId)
        if app in _TERMINALS or el.CurrentClassName == "TermControl":
            return term_watcher.selection()
        pattern = el.GetCurrentPattern(U.UIA_TextPatternId)
        if not pattern:
            return None
        pattern = pattern.QueryInterface(U.IUIAutomationTextPattern)
        ranges = pattern.GetSelection()
        if ranges.Length == 0:
            return None
        sel = ranges.GetElement(0)
        text = sel.GetText(-1)
        if not text.strip():
            return None
        line = sel.Clone()
        line.ExpandToEnclosingUnit(_LINE)
        before, after = line.Clone(), line.Clone()
        before.MoveEndpointByRange(_END, sel, _START)
        after.MoveEndpointByRange(_START, sel, _END)
        try:
            rects = sel.GetBoundingRectangles()
            rect = tuple(rects[:4]) if rects and len(rects) >= 4 else None
        except Exception:
            rect = None
        return Selection(text, before.GetText(-1), after.GetText(-1).rstrip("\r\n"), app, rect)
    except Exception:
        log.exception("reading the selection failed")
        return None


class TermWatcher:
    """Selections made by dragging the mouse in a terminal (see the module docstring). The mouse hook reports
    left button presses and releases; a drag is read on a worker thread and forgotten on the next key press or
    click, or when another window comes to the front."""
    # an input box's prompt (Claude Code ❯, Codex ›, agy >) at the pane's left edge
    _PROMPT = re.compile(r" {0,2}([❯›>])(?=[ \xa0])")

    def __init__(self):
        self._lock = threading.Lock()
        self._gen = 0
        self._sel: Selection | None = None
        self._hwnd = 0
        self._ready = threading.Event()
        self._ready.set()
        self._down = None
        self._clicks = (0, 0.0, None)   # (count, time, point) of the last run of clicks in one place
        self._jobs: queue.Queue | None = None

    def forget(self):
        with self._lock:
            self._gen += 1
            self._sel = None
            self._ready.set()

    def mouse_down(self, x: int, y: int):
        self.forget()
        self._down = (x, y)

    def mouse_up(self, x: int, y: int):
        down, self._down = self._down, None
        if down is None:
            return
        if abs(down[0] - x) + abs(down[1] - y) <= 4:
            # a double click (Claude Code, agy) or triple click (Codex) selects a line up to punctuation or a space
            n, t, pt = self._clicks
            now = time.monotonic()
            near = pt is not None and abs(pt[0] - x) + abs(pt[1] - y) <= 4
            n = n + 1 if near and now - t <= ctypes.windll.user32.GetDoubleClickTime() / 1000 else 1
            self._clicks = (n, now, (x, y))
            if n < 2:
                return
            down = None
        else:
            self._clicks = (0, 0.0, None)
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if _proc_name(_window_pid(hwnd)) not in _TERMINALS:
            return
        with self._lock:
            gen = self._gen
            self._ready.clear()
        if self._jobs is None:
            self._jobs = queue.Queue()
            threading.Thread(target=self._run, daemon=True).start()
        self._jobs.put((gen, hwnd, down, (x, y)))

    def selection(self) -> Selection | None:
        """The current terminal selection; waits for a drag that is still being read."""
        self._ready.wait(1.0)
        with self._lock:
            if self._sel is not None and self._hwnd == ctypes.windll.user32.GetForegroundWindow():
                return self._sel
        return None

    def _run(self):
        while True:
            gen, hwnd, down, up = self._jobs.get()
            sel = None
            try:
                sel = self._read(down, up) if down else self._read_click(up)
            except Exception:
                log.exception("reading the terminal selection failed")
            with self._lock:
                if gen == self._gen:
                    self._sel, self._hwnd = sel, hwnd
                    self._ready.set()

    def _read(self, down, up) -> Selection | None:
        uia, U = _uia()
        ends = []
        for x, y in (down, up):
            pattern = _term_pattern(x, y)
            if pattern is None:
                return None
            ch = pattern.RangeFromPoint(wt.POINT(x, y))
            ch.ExpandToEnclosingUnit(_CHAR)
            ends.append(_pos(ch))
        (line, c1), (line2, c2) = ends
        if not line.Compare(line2):
            return None           # one line only (decided 2026-09-30)
        row = line.GetText(-1).rstrip("\r\n")
        a, b = _highlighted(line, min(c1, c2), max(c1, c2) + 1, len(row))
        return self._make(line, row, a, b, pattern, "drag " + ("right" if c2 >= c1 else "left"))

    def _read_click(self, pt) -> Selection | None:
        """After a double or triple click: the highlighted run of cells around the click, if any."""
        pattern = _term_pattern(*pt)
        if pattern is None:
            return None
        ch = pattern.RangeFromPoint(wt.POINT(*pt))
        ch.ExpandToEnclosingUnit(_CHAR)
        line, c = _pos(ch)
        row = line.GetText(-1).rstrip("\r\n")
        for _ in range(3):        # the TUI may not have drawn it yet
            run = _highlight_run(line, c, len(row))
            if run:
                return self._make(line, row, *run, pattern, f"{self._clicks[0]} clicks")
            time.sleep(0.1)
        return None

    def _make(self, line, row, a, b, pattern, how) -> Selection | None:
        text = row[a:b]
        a += len(text) - len(text.lstrip())   # a drag past the text selects blanks
        text = text.strip()
        if not text:
            return None
        b = a + len(text)
        p = self._input_start(row, line, a, _term_cursor(pattern))
        if p is None:
            log.info("terminal selection %r is not in the input box", text)
            return None
        first, last = _char_rect(line, a), _char_rect(line, b - 1)
        rect = (first[0], first[1], last[0] + last[2] - first[0], first[3])
        point = (last[0] + last[2] // 2, last[1] + last[3] // 2)
        log.info("terminal selection %r after %r (%s)", text, row[p:a], how)
        return Selection(text, row[p:a], row[b:].rstrip(), "terminal", rect, point)

    def _input_start(self, row: str, line, a: int, cursor) -> int | None:
        """Where the text of the input box starts on this row (after the prompt or the continuation indent), or
        None when the row is outside the input box. The box is a prompt row at the pane's left edge and the
        rows below it that are blank under the prompt; the terminal cursor must be in it (a past prompt in the
        transcript also starts with the prompt, but the cursor is not there), and a blank row may not separate a
        selection below the cursor from it."""
        if cursor is None:
            return None
        left = max(row.rfind("│", 0, a), row.rfind("┃", 0, a)) + 1   # herdr's sidebar or a pane on the left
        right = min((i for i in (row.find("│", a), row.find("┃", a)) if i >= 0), default=len(row))
        rows = [row[:a]] + _lines(line, -1, 30)
        i = next((k for k, r in enumerate(rows) if self._PROMPT.match(r, left)), None)
        if i is None:
            return None
        col = self._PROMPT.match(rows[i], left).start(1)

        def inside(r):         # blank rows too: Shift+Enter leaves empty lines in the box (2026-10-04)
            return r[left:col + 2].strip() == ""
        if i and (rows[0][left:col + 2].strip() or not all(inside(r) for r in rows[1:i])):
            return None
        dr = _rows_between(line, cursor[0])
        if dr is None or dr < -i or (dr > 0 and not all(inside(r) for r in _lines(line, 1, dr))):
            return None
        if dr < 0 and any(not r[left:right].strip() for r in rows[1:-dr]):
            return None        # below a blank row under the cursor: Codex's status line, not the box
        return col + 2 if a >= col + 2 else None


term_watcher = TermWatcher()


def move_cursor_after(sel: Selection) -> bool:
    """Terminal: move the app's cursor right after sel with arrow keys. Up/Down first, then Left/Right, reading
    the cursor again after each burst, so soft-wrapped rows and the cursor's starting place do not matter.
    False (nothing deleted yet) when the text is no longer there or the cursor does not get there."""
    out = []
    t = threading.Thread(target=lambda: out.append(_move_after(sel)), daemon=True)
    t.start()
    t.join(4.0)
    return bool(out and out[0])


def _move_after(sel: Selection) -> bool:
    from .output import ARROWS, press_key
    try:
        pattern = _term_pattern(*sel.term)
        if pattern is None:
            return False
        ch = pattern.RangeFromPoint(wt.POINT(*sel.term))
        ch.ExpandToEnclosingUnit(_CHAR)
        line, col = _pos(ch)
        col += 1
        if line.GetText(-1)[col - len(sel.text):col] != sel.text:
            log.info("terminal selection %r is no longer on screen", sel.text)
            return False
        end = time.monotonic() + 3.0
        cur = _term_cursor(pattern)
        if cur is not None and cur[1] == col and _rows_between(cur[0], line) == 0:
            # A drag ending here left the cursor here, and Codex keeps that selection: a Backspace would delete
            # all of it, the next one a character more. An arrow key clears it; the loop moves back.
            press_key(ARROWS["left"], 1)
            cur = _cursor_moved(pattern, cur, time.monotonic() + 0.5)
        for _ in range(12):
            if cur is None or time.monotonic() > end:
                break
            dr = _rows_between(cur[0], line)
            log.info("terminal cursor: %s rows, column %d (target %d)", dr, cur[1], col)
            if dr is None:
                break
            if dr == 0 and cur[1] == col:
                return True
            if dr:
                press_key(ARROWS["down" if dr > 0 else "up"], abs(dr))
            else:
                press_key(ARROWS["right" if col > cur[1] else "left"], abs(col - cur[1]))
            cur = _cursor_moved(pattern, cur, end)
        log.info("could not move the terminal cursor after %r", sel.text)
    except Exception:
        log.exception("moving the terminal cursor failed")
    return False


def _term_pattern(x: int, y: int):
    """The TextPattern of the terminal under a screen point, or None."""
    uia, U = _uia()
    el = uia.ElementFromPoint(wt.POINT(x, y))
    if el.CurrentClassName != "TermControl":
        return None
    return el.GetCurrentPattern(U.UIA_TextPatternId).QueryInterface(U.IUIAutomationTextPattern)


def _term_cursor(pattern):
    """(line range, column) of the terminal's cursor, or None. Windows Terminal reports it as an empty
    selection. (While its window is in the background it was reported one character to the left.)"""
    ranges = pattern.GetSelection()
    if ranges.Length != 1:
        return None
    r = ranges.GetElement(0)
    if r.GetText(-1):
        return None               # the terminal's own selection
    return _pos(r)


def _cursor_moved(pattern, old, end: float):
    """The cursor once it has moved from old and stayed put for a moment (the keys may arrive in parts)."""
    def same(a, b):
        return a is not None and b is not None and a[1] == b[1] and a[0].Compare(b[0])
    last, still = old, 0.0
    while time.monotonic() < end:
        time.sleep(0.02)
        cur = _term_cursor(pattern)
        if same(cur, last):
            if not same(cur, old):
                still += 0.02
                if still >= 0.06:
                    return cur
        else:
            last, still = cur, 0.0
    return last


def _pos(rng):
    """(line range, column) of a range's start."""
    line = rng.Clone()
    line.ExpandToEnclosingUnit(_LINE)
    before = line.Clone()
    before.MoveEndpointByRange(_END, rng, _START)
    return line, len(before.GetText(-1))


def _rows_between(a, b) -> int | None:
    """Signed number of rows from line range a down to line range b (None when more than 60 apart)."""
    if a.Compare(b):
        return 0
    step = 1 if a.CompareEndpoints(_START, b, _START) < 0 else -1
    r = a.Clone()
    for n in range(1, 61):
        if r.Move(_LINE, step) == 0:
            return None
        r.ExpandToEnclosingUnit(_LINE)
        if r.Compare(b):
            return n * step
    return None


def _window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _lines(line, step: int, n: int) -> list[str]:
    """Up to n rows above (step -1) or below (step 1) a line range, nearest first."""
    r, out = line.Clone(), []
    for _ in range(n):
        if r.Move(_LINE, step) == 0:
            break
        r.ExpandToEnclosingUnit(_LINE)
        out.append(r.GetText(-1).rstrip("\r\n"))
    return out


def _char(line, i: int):
    """Range of character i of a line range."""
    r = line.Clone()
    r.MoveEndpointByUnit(_START, _CHAR, i)
    r.MoveEndpointByRange(_END, r, _START)
    r.MoveEndpointByUnit(_END, _CHAR, 1)
    return r


def _char_rect(line, i: int) -> tuple:
    """Screen rect (x, y, w, h) of character i of a line range."""
    rect = _char(line, i).GetBoundingRectangles()
    return tuple(int(v) for v in rect[:4])


def _style(line, i: int) -> tuple:
    """(background, foreground) of character i of a line range."""
    _, U = _uia()
    r = _char(line, i)
    return (r.GetAttributeValue(U.UIA_BackgroundColorAttributeId),
            r.GetAttributeValue(U.UIA_ForegroundColorAttributeId))


def _highlight_run(line, c: int, n: int) -> tuple[int, int] | None:
    """The run of cells colored like character c, when it is a highlight: its background differs from the
    cells on both sides. (Plain text has the background of the blanks around it and runs on to a border.)"""
    if not 0 <= c < n:
        return None
    s = _style(line, c)
    a, b = c, c + 1
    while a > 0 and _style(line, a - 1) == s:
        a -= 1
    while b < n and _style(line, b) == s:
        b += 1
    sides = [_style(line, i) for i in (a - 1, b) if 0 <= i < n]
    if not sides or any(o[0] == s[0] for o in sides):
        return None
    return a, b


def _highlighted(line, a: int, b: int, n: int) -> tuple[int, int]:
    """Narrow characters a..b-1 (those under the press and release points and between them) to what the TUI
    highlighted. herdr selects up to the cell edge nearest the pointer, so the character under an end point is
    often not selected; it inverts the colors of selected cells, and Windows Terminal reports cell colors via
    UIA. Ends colored like the characters just outside the drag are dropped (2026-10-03)."""
    def style(i):
        return _style(line, i)
    try:
        styles = [style(i) for i in range(a, b)]
        if len(set(styles)) < 2:
            return a, b           # nothing to tell apart
        plain = {style(i) for i in (a - 1, b) if 0 <= i < n}
        while a < b and styles[0] in plain:
            a, styles = a + 1, styles[1:]
        while a < b and styles[-1] in plain:
            b, styles = b - 1, styles[:-1]
    except Exception:
        log.exception("reading the terminal highlight failed")
    return a, b


# English terms on screen bias the recognizer toward them (worktree, LLM, sherpa-onnx in a terminal).
# Common words are left out: boosting them only risks inserting them.
_TERM = re.compile(r"(?<![A-Za-z0-9_.])[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9_]|\.[A-Za-z])")
_COMMON = set("""the and for you that this with from have are was were will would could should there their
what when where which while about after before again because being between both each into just like make
more most much must only other over same some such than then them they these those through under until very
your yours also been does done doing here how its itself our out own down off once all any can did don few
has had her him his not now nor too yes who whom why let may might new old use used using file files line
lines code text true false null none return print import class function def else elif self error value
name type data list open close save edit view help tools window select click press enter""".split())
_MAX_TERMS = 40


def terms_in(text: str) -> list[str]:
    """Distinctive English terms in text, most frequent first: acronyms, CamelCase and words with digits
    always; other words of 5+ letters only when they appear at least twice. Dotted names (ranges.GetText) are
    skipped: nobody says them aloud."""
    counts = Counter(m.group(0) for m in _TERM.finditer(text))
    out = []
    for w, n in counts.most_common():
        if len(w) < 3 or w.casefold() in _COMMON:
            continue
        strong = any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w)
        if strong or (len(w) >= 5 and n >= 2):
            out.append(w)
        if len(out) == _MAX_TERMS:
            break
    return out


@dataclass
class Context:
    selection: Selection | None = None
    terms: list[str] = field(default_factory=list)
    caret: tuple[str, str] | None = None   # without a selection: the line's text before and after the caret
    zh_terms: list[str] = field(default_factory=list)   # Chinese names / topic words on screen (screen_terms)
    zh_fix: list[str] = field(default_factory=list)     # every name-like span on screen, for same-sound fixes only
    en_names: list[str] = field(default_factory=list)   # English names on screen (screen_en), for fixes
    app: str = ""        # foreground process name (AI 潤稿 picks its tone by it)
    title: str = ""      # foreground window title
    screen: str = ""     # the window's text for the AI (memory only; emptied by _forget_screen)


def read_context(ctx: Context | None = None) -> Context:
    """The focused app's selection (see read_selection), else the text around its caret, and the terms on screen:
    the focused control's visible text and the whole foreground window (UI Automation), or OCR when the window
    shows UI Automation (almost) nothing, as LINE's messages do. ctx is filled in place, selection first, so a
    caller that stops waiting still gets what was read."""
    ctx = ctx if ctx is not None else Context()
    ctx.app, ctx.title = foreground_app()
    ctx.selection = read_selection()
    if ctx.selection is None:
        ctx.caret = read_caret()
    window = _with_timeout(window_text, _WINDOW_BUDGET)    # the whole window: chat history, page, mail
    focus = visible_text()
    # a small focused field is the message being written: what the user dictated there, maybe misheard, is not
    # screen evidence (宋家 in LINE's input box protected itself from 宋嘉 on screen, 2026-10-14)
    draft = focus if len(focus) < _DRAFT_MAX else ""
    text = ("" if draft else focus) + "\n" + window
    if len(text.strip()) < _OCR_BELOW:
        from . import ocr          # imported on first need: the OCR runtime costs ~25MB once loaded
        text += "\n" + ocr.window_text()
    text = screen_terms.without_draft(text, draft)
    text = screen_terms.normalize_screen(text)   # a simplified page, 臺 -> 台
    ctx.screen = _for_ai(text)     # memory only, handed to the AI as the topic (the user's request); cleared after use
    ctx.terms = terms_in(text)
    ctx.en_names = screen_terms.english_names(text)
    ctx.zh_fix = screen_terms.fix_candidates(text)
    strong = screen_terms.terms(text, ctx.title)
    ctx.zh_terms = strong + [w for w in screen_terms.recent(text) if w not in strong]
    del text, window          # never kept: only the terms leave this function (the user's condition)
    return ctx


_SCREEN_AI_MAX = 1500     # characters of screen text sent to the AI: the tail (newest lines), blank lines dropped


def _for_ai(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    out, seen = [], set()
    for ln in lines:
        if len(ln) < 2 or ln in seen:
            continue
        seen.add(ln)
        out.append(ln)
    return "\n".join(out)[-_SCREEN_AI_MAX:]


_OCR_BELOW = 300         # characters of readable text under which the window is read by OCR
_DRAFT_MAX = 1000         # a focused field shorter than this is an input box (its text is the draft)
_WINDOW_BUDGET = 1.2      # seconds; a window that takes longer to read is skipped (the dictation must not wait)
_WINDOW_ELEMENTS = 4000


def _with_timeout(fn, seconds: float) -> str:
    box = [""]

    def run():
        try:
            box[0] = fn()
        except Exception:
            log.exception("reading the window failed")
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if not t.is_alive() else ""


def window_text() -> str:
    """The names of the foreground window's elements (UI Automation, one cached FindAll): the text a user sees in a
    chat history, a web page or a mail. Password fields expose no value, only their label."""
    uia, U = _uia()
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return ""
    root = uia.ElementFromHandle(hwnd)
    req = uia.CreateCacheRequest()
    req.AddProperty(U.UIA_NamePropertyId)
    found = root.FindAllBuildCache(U.TreeScope_Descendants, uia.CreateTrueCondition(), req)
    names = []
    for i in range(min(found.Length, _WINDOW_ELEMENTS)):
        name = found.GetElement(i).CachedName
        if name:
            names.append(name)
    return "\n".join(names)


def foreground_app() -> tuple[str, str]:
    """The foreground window's process name and title."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        return _proc_name(_window_pid(hwnd)), buf.value
    except Exception:
        log.exception("reading the foreground window failed")
        return "", ""


def read_caret() -> tuple[str, str] | None:
    """The text of the caret's line before and after it, or None when unknown. In a terminal: the input box row
    the cursor is on, between the prompt (or continuation indent) and a right border."""
    try:
        uia, U = _uia()
        el = uia.GetFocusedElement()
        pattern = el.GetCurrentPattern(U.UIA_TextPatternId)
        if not pattern:
            return None
        pattern = pattern.QueryInterface(U.IUIAutomationTextPattern)
        if _proc_name(el.CurrentProcessId) in _TERMINALS or el.CurrentClassName == "TermControl":
            cursor = _term_cursor(pattern)
            if cursor is None:
                return None
            line, col = cursor
            row = line.GetText(-1).rstrip("\r\n")
            p = term_watcher._input_start(row, line, col, cursor)
            if p is None:
                return None
            right = min((i for i in (row.find("│", col), row.find("┃", col)) if i >= 0), default=len(row))
            return row[p:col], row[col:right]
        ranges = pattern.GetSelection()
        if ranges.Length == 0:
            return None
        caret = ranges.GetElement(0)
        line = caret.Clone()
        line.ExpandToEnclosingUnit(_LINE)
        before, after = line.Clone(), line.Clone()
        before.MoveEndpointByRange(_END, caret, _START)
        after.MoveEndpointByRange(_START, caret, _END)
        return before.GetText(-1), after.GetText(-1).rstrip("\r\n")
    except Exception:
        log.exception("reading the caret failed")
        return None


def visible_text() -> str:
    """The text visible in the focused control (UI Automation TextPattern), "" if it has none."""
    try:
        uia, U = _uia()
        pattern = uia.GetFocusedElement().GetCurrentPattern(U.UIA_TextPatternId)
        if not pattern:
            return ""
        ranges = pattern.QueryInterface(U.IUIAutomationTextPattern).GetVisibleRanges()
        return "\n".join(ranges.GetElement(i).GetText(20000) for i in range(min(ranges.Length, 50)))
    except Exception:
        log.exception("reading the visible text failed")
        return ""


class ContextProbe:
    """Reads the selection and on-screen terms on a background thread so a slow app does not delay the
    recording start."""

    def __init__(self):
        self._result = Context()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        read_context(self._result)

    def result(self, timeout: float = 2.0) -> Context:
        self._thread.join(timeout)
        return self._result


def initialize_input_snapshot_reader():
    """Warm this worker's COM/type library without inspecting any field."""
    _uia()


def no_input_focused(timeout: float = 0.6) -> bool:
    """True only when the focus is surely not a place text can go (2026-10-14): a desktop, a file list, a button, a
    web page without a field. A terminal, an editable field, a control UI Automation tells nothing about (Electron,
    a game) or a slow answer all count as "maybe": the dictation is pasted as before."""
    box = [False]

    def run():
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return
            uia, U = _uia()
            el = uia.GetFocusedElement()
            if _proc_name(el.CurrentProcessId) in _TERMINALS or el.CurrentClassName == "TermControl"                     or el.CurrentIsPassword:
                return
            kind = el.CurrentControlType

            def pattern(pid, interface):
                try:
                    value = el.GetCurrentPattern(pid)
                    return value.QueryInterface(interface) if value else None
                except Exception:
                    return None

            value = pattern(U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
            text = pattern(U.UIA_TextPatternId, U.IUIAutomationTextPattern)
            read_only = []
            if value is not None:
                read_only.append(bool(value.CurrentIsReadOnly))
            if text is not None:
                flag = text.DocumentRange.GetAttributeValue(U.UIA_IsReadOnlyAttributeId)
                read_only.append(bool(flag) if isinstance(flag, (bool, int)) else None)
            verdict = "maybe"
            if kind in (U.UIA_EditControlTypeId, U.UIA_ComboBoxControlTypeId):
                pass                        # a field, writable or not known to be read-only
            elif read_only and (False in read_only or None in read_only):
                pass                        # something writable, or a flag it did not answer
            elif kind in _NOT_INPUT_TYPES(U):
                verdict = "none"
            log.info("focus check: %s (control type %s, class %s, read-only %s)", verdict, kind,
                     el.CurrentClassName, read_only)
            box[0] = verdict == "none"
        except Exception:
            log.debug("no_input_focused failed", exc_info=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] and not t.is_alive()


def _NOT_INPUT_TYPES(U):
    return {U.UIA_DocumentControlTypeId, U.UIA_ListControlTypeId, U.UIA_ListItemControlTypeId,
            U.UIA_ButtonControlTypeId, U.UIA_CheckBoxControlTypeId, U.UIA_HyperlinkControlTypeId,
            U.UIA_ImageControlTypeId, U.UIA_TextControlTypeId, U.UIA_TabItemControlTypeId,
            U.UIA_TreeControlTypeId, U.UIA_TreeItemControlTypeId, U.UIA_MenuItemControlTypeId,
            U.UIA_DataItemControlTypeId, U.UIA_TitleBarControlTypeId, U.UIA_ToolBarControlTypeId}


snapshot_problem = ""      # why the last read_input_snapshot returned None (logged by correction.py, never the text)


def _no_snapshot(reason: str):
    global snapshot_problem
    snapshot_problem = reason
    return None


def read_input_snapshot():
    """The focused editable control, for scoped correction learning, or None.

    Called only by CorrectionWatcher's daemon worker. TextPattern supplies selection
    offsets; ValuePattern can supply a value whose insertion must later be unique.
    Terminal screen buffers, passwords and unknown/read-only controls are excluded.
    """
    from .correction import InputSnapshot

    maximum = 20000
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return _no_snapshot("no foreground window")
        uia, U = _uia()
        el = uia.GetFocusedElement()
        pid, identity = el.CurrentProcessId, tuple(el.GetRuntimeId())
        if not pid or not identity or el.CurrentIsPassword or not el.CurrentIsEnabled:
            return _no_snapshot("focused control has no id, is a password or disabled")
        if _proc_name(pid) in _TERMINALS or el.CurrentClassName == "TermControl":
            return _no_snapshot("terminal")
        if el.CurrentControlType not in (U.UIA_EditControlTypeId, U.UIA_DocumentControlTypeId):
            return _no_snapshot(f"focus is not a text field (control type {el.CurrentControlType})")

        def pattern(kind, interface):
            try:
                value = el.GetCurrentPattern(kind)
                return value.QueryInterface(interface) if value else None
            except Exception:
                return None

        value = pattern(U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
        text_pattern = pattern(U.UIA_TextPatternId, U.IUIAutomationTextPattern)
        value_readonly = value.CurrentIsReadOnly if value is not None else None
        document = text_pattern.DocumentRange if text_pattern is not None else None
        text_readonly = document.GetAttributeValue(U.UIA_IsReadOnlyAttributeId) if document is not None else None

        def flag(raw):
            return bool(raw) if isinstance(raw, (bool, int)) else None

        writable_flags = flag(value_readonly), flag(text_readonly)
        if True in writable_flags or False not in writable_flags:
            return _no_snapshot(f"field not known to be writable {writable_flags}")
        start = end = None
        if document is not None:
            text = document.GetText(maximum + 1)
            if len(text) > maximum:
                return _no_snapshot("field too long")
            ranges = text_pattern.GetSelection()
            if ranges.Length == 1:
                selected = ranges.GetElement(0)
                prefix = document.Clone()
                prefix.MoveEndpointByRange(_END, selected, _START)
                left, span = prefix.GetText(maximum + 1), selected.GetText(maximum + 1)
                start, end = len(left), len(left) + len(span)
                if end > len(text) or text[:start] != left or text[start:end] != span:
                    # an input method composing: the caret is unknown, the text is still what the field holds
                    # (LINE with 注音: every read was thrown away, nothing was ever learned, 2026-10-14)
                    start = end = None
        elif value is not None:
            text = value.CurrentValue
            if not isinstance(text, str) or len(text) > maximum:
                return _no_snapshot("value unreadable or too long")
        else:
            return _no_snapshot("no text or value pattern")
        # Reject a focus/window change while the provider was responding.
        current = uia.GetFocusedElement()
        if ctypes.windll.user32.GetForegroundWindow() != hwnd or current.CurrentProcessId != pid \
                or tuple(current.GetRuntimeId()) != identity:
            return _no_snapshot("focus moved while reading")
        return InputSnapshot(hwnd, pid, identity, text, start, end)
    except Exception as e:
        log.debug("editable input snapshot unavailable", exc_info=True)
        return _no_snapshot(f"UI Automation error {type(e).__name__}")


def text_focus_rect(timeout: float = 0.5):
    """The screen rectangle (left, top, right, bottom, physical pixels) of the focused control when it may take text
    (an edit box, a document that is writable or unknown, a terminal), None when it surely cannot (a desktop, a file
    list, a button, a page without a field), "unknown" when UI Automation did not answer. For the mouse buttons that
    act only on an input box (2026-10-19, the user: 中鍵只在對話框有效，其他時候不要影響中鍵)."""
    box = ["unknown"]

    def run():
        try:
            if not ctypes.windll.user32.GetForegroundWindow():
                box[0] = None
                return
            uia, U = _uia()
            el = uia.GetFocusedElement()
            r = el.CurrentBoundingRectangle
            rect = (r.left, r.top, r.right, r.bottom)
            if _proc_name(el.CurrentProcessId) in _TERMINALS or el.CurrentClassName == "TermControl" \
                    or el.CurrentIsPassword:
                box[0] = rect
                return
            kind = el.CurrentControlType

            def pattern(pid, interface):
                try:
                    value = el.GetCurrentPattern(pid)
                    return value.QueryInterface(interface) if value else None
                except Exception:
                    return None

            value = pattern(U.UIA_ValuePatternId, U.IUIAutomationValuePattern)
            text = pattern(U.UIA_TextPatternId, U.IUIAutomationTextPattern)
            read_only = []
            if value is not None:
                read_only.append(bool(value.CurrentIsReadOnly))
            if text is not None:
                flag = text.DocumentRange.GetAttributeValue(U.UIA_IsReadOnlyAttributeId)
                read_only.append(bool(flag) if isinstance(flag, (bool, int)) else None)
            if kind in (U.UIA_EditControlTypeId, U.UIA_ComboBoxControlTypeId):
                box[0] = rect
            elif read_only and (False in read_only or None in read_only):
                box[0] = rect
            elif kind in _NOT_INPUT_TYPES(U):
                box[0] = None
            else:
                box[0] = rect                 # a control UI Automation tells nothing about (Electron, a game): maybe
        except Exception:
            log.debug("text_focus_rect failed", exc_info=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return "unknown" if t.is_alive() else box[0]
