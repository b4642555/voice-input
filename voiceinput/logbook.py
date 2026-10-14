"""The 辨識紀錄 tab's content (the user's pick, 2026-10-15): what was heard next to what it did on the screen, as a
diff, one row per event and one group per listening session (design B of three mockups; design A, a card per
sentence with its story in sentences, was built first and found too busy the same night). The why and the details
(seconds, the recognizer's own text, formatting steps) are a row's tooltip. Main thread only.

A session: id, listening, start, end, counts, rows. A row: time, heard (what was said, as recognized alone), change
(the document's change as HTML: new text green, removed struck through red, long unchanged stretches cut to …), tag
(a few words: 整句重聽, 指令：換行, 復原 …) and its kind (for its color), tip, copy (the sentence it finished)."""
import difflib
import html
import itertools
import time
from collections import deque

from . import rules

INS = "background-color:#DCF3E2; color:#17643A"
DEL = "background-color:#FBE1E1; color:#A23030; text-decoration:line-through"
_CONTEXT = 8   # characters kept around a change; the rest of a long sentence is "…"
_KEY_WORDS = {"Shift+Enter": "換行", "Enter": "按 Enter", "Backspace": "刪掉前一個字"}
_COMMAND_WORDS = {rules.STOP: "關掉麥克風", rules.UNDO: "復原", rules.PICK: "選字",
                  rules.DELETE_SELECTION: "刪除選取的文字", rules.DELETE_DICTATION: "刪除剛輸入的文字"}


def esc(s: str) -> str:
    return html.escape(s).replace("\n", "⏎")


def ins(s: str) -> str:
    return f"<span style='{INS}'>{esc(s)}</span>" if s else ""


def gone(s: str) -> str:
    return f"<span style='{DEL}'>{esc(s)}</span>" if s else ""


def diff_html(before: str, after: str) -> str:
    """after, with what is new marked and what went struck through; long unchanged stretches cut to "…"."""
    ops = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    out = []
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            same = after[j1:j2]
            first, last = n == 0, n == len(ops) - 1
            if first and len(same) > _CONTEXT:
                same = "…" + _keep_end(same)
            elif last and len(same) > _CONTEXT:
                same = _keep_start(same) + "…"
            elif not first and not last and len(same) > 2 * _CONTEXT + 1:
                same = _keep_start(same) + "…" + _keep_end(same)
            out.append(esc(same))
            continue
        out.append(gone(before[i1:i2]) + ins(after[j1:j2]))
    return "".join(out)


def _keep_end(s: str) -> str:
    """The last _CONTEXT characters, not cutting an English word (…S 你幫我 read as a typo)."""
    i = len(s) - _CONTEXT
    while 0 < i < len(s) and s[i - 1].isascii() and s[i - 1].isalnum() and s[i].isascii() and s[i].isalnum():
        i -= 1
    return s[i:]


def _keep_start(s: str) -> str:
    i = _CONTEXT
    while i < len(s) and s[i - 1].isascii() and s[i - 1].isalnum() and s[i].isascii() and s[i].isalnum():
        i += 1
    return s[:i]




def describe(steps: list) -> str:
    """What an operation's steps do, in a few words: 換行, 按 Enter, 關掉麥克風 …"""
    words = []
    for st in steps:
        if st.type == rules.KEYS:
            words.append(_KEY_WORDS.get(st.value, f"按 {st.value}"))
        elif st.type == rules.TEXT:
            words.append(f"輸入「{st.value}」")
        elif st.type == rules.COMMAND and st.value in _COMMAND_WORDS:
            words.append(_COMMAND_WORDS[st.value])
    return "、".join(words)


def tech_of(entry: dict | None, extra: str = "") -> str:
    """The details line: seconds, the recognizer's own text, each formatting step's change."""
    parts = []
    if entry:
        if entry.get("asr_s") is not None:
            parts.append(f"辨識 {entry['asr_s']:.2f} 秒")
        if entry.get("asr"):
            parts.append(f"模型原文：{entry['asr']}")
        for name, before, after in entry.get("steps", []):
            parts.append(f"{name}：{before}" + (f" → {after}" if after is not None else ""))
    if extra:
        parts.append(extra)
    return " · ".join(parts)


class LogBook:
    """Sessions (a listening session, or what was done while not listening) of rows, newest session last."""

    def __init__(self, on_change, size: int = 50):
        self.sessions = deque(maxlen=size)
        self._on_change = on_change   # called after every change, to render
        self._ids = itertools.count()
        self._listening = False
        self._spoken_undo = None      # the 復原 just said, for the undo row that follows
        self._redos = 0               # pauses decoded again this session (the why is told once)

    # --- sessions and rows ---
    def new_session(self):
        """Listening turned on."""
        self._listening = True
        self._redos = 0
        self.sessions.append(self._group(True))
        self._on_change()

    def end_session(self):
        """Listening turned off (its last commit may still come)."""
        self._listening = False

    def _group(self, listening: bool) -> dict:
        return {"id": next(self._ids), "listening": listening, "start": _now(), "end": _now(), "rows": [],
                "counts": {"pieces": 0, "commands": 0, "undo": 0}}

    def _session(self, draft: bool = True) -> dict:
        """The session a row goes to: the last listening one for a draft's events (its last commit comes after
        listening stops), else one for what is done while not listening."""
        last = self.sessions[-1] if self.sessions else None
        if last is not None and (draft and last["listening"] or last["listening"] == self._listening):
            return last
        g = self._group(self._listening)
        self.sessions.append(g)
        return g

    def _row(self, heard: str, change: str, tag: str = "", kind: str = "", tip: str = "", copy: str = "",
             draft: bool = True):
        g = self._session(draft)
        g["rows"].append({"id": next(self._ids), "time": _now(), "heard": heard, "change": change, "tag": tag,
                          "kind": kind, "tip": tip, "copy": copy})
        del g["rows"][:-300]
        g["end"] = _now()
        self._on_change()
        return g

    # --- a draft's events ---
    def heard(self, info: dict | None, before: str, after: str, locked: str = ""):
        """A pause: the draft shows `after` where it showed `before`; `locked`, the part settled now."""
        if before == after and not locked:
            return
        info = info or {}
        said = info.get("heard", "")
        why, tag, kind = [], "", ""
        if info.get("redo"):
            said, tag, kind = "（游標移開前講的）", "改打在新的游標位置", "fix"
            why.append("辨識結果還沒打出來前游標就移開了，所以打在你點的新位置")
        elif before and not after.startswith(before):
            tag, kind = "整句重聽", "fix"
            self._redos += 1
            if self._redos == 1:
                why.append("停頓之後才知道前面該斷在哪、怎麼寫，所以每次停頓都把還沒定下來的句子重聽一次")
        if locked:
            tag = "；".join(t for t in (tag, f"「{_tail(locked)}」以前定下來") if t)
            kind = kind or "lock"
            why.append("標點落在你停頓的地方，前面就固定了；之後停頓只重聽後面的句子，比較快")
        tech = tech_of(info.get("entry"), f"整句重聽 {info['redo_s']:.2f} 秒" if "redo_s" in info else "")
        change = diff_html(before, after) if before else ins(after)
        g = self._row(said, change, tag, kind, _tip(why, tech))
        if said and not info.get("redo"):
            g["counts"]["pieces"] += 1

    def command(self, entry: dict, op, typed: str | None, before: str, after: str, marked: bool,
                drafted: bool, over: str | None = None):
        """A command said alone: the draft committed (`before` -> `after`), then the operation runs or the mark
        is typed (over the selection `over`)."""
        if op is not None and [(s.type, s.value) for s in op.steps] == [(rules.COMMAND, rules.UNDO)]:
            self._spoken_undo = (op.phrase, entry, before if drafted else "", after if drafted else "")
            return   # told with what it took back
        alone = entry.get("alone")
        # what was said: 句號, not the 。 it typed
        said = op.phrase if op else ((entry.get("asr") or "").strip().rstrip("，。？！、,.?!") or typed)
        heard = f"{alone} → {said}" if alone else said
        why = []
        if alone:
            why.append(f"單獨聽是「{alone}」，接在前面的句子後面聽才認出是「{said}」")
        if op is not None:
            what = describe(op.steps)
            change = (diff_html(before, after) + " " if drafted and before != after else "") + \
                (f"<span style='color:{_GRAY}'>→ {esc(what)}</span>" if what else "")
            tag = f"指令：{op.phrase}"
            why.append("整句只有這個詞，所以當指令執行" if not drafted else
                       "指令會先把前面的草稿定下來" + ("，句尾沒有標點就補上" if marked else ""))
            copy = after if drafted else ""
        else:
            change = (diff_html(before, after + typed) if drafted and before != after + typed else ins(typed))
            if over:
                change = gone(over) + ins(typed)
            tag = "標點" if typed in "，。？！、；：…" else "指定字"
            why.append("說的是標點名稱或「某個詞的某個字」，只打那個符號或字"
                       + ("，取代你選取的字" if over else ""))
            copy = after + typed if drafted else typed
        g = self._row(heard, change or "（無）", tag, "op", _tip(why, tech_of(entry)), copy)
        g["counts"]["commands"] += 1

    def committed(self, before: str, after: str, marked: bool, by_stop: bool):
        """The draft committed without a command: listening turned off, or a pause when the setting says so."""
        heard = "（關掉麥克風）" if by_stop else "（停頓）"
        why = ("關麥克風時整句定稿" + ("，句尾沒有標點就補上「。」" if marked else "")) if by_stop else \
            "設定是每次停頓就定稿（或草稿超過 60 秒）"
        change = diff_html(before, after) if before != after else f"<span style='color:{_GRAY}'>（不變）</span>"
        self._row(heard, change, "定稿", "end", _tip([why], ""), after)

    def frozen(self, typed: str):
        """The caret moved away from the draft: it stays as typed, shown as its start … its end (the user's
        design, 2026-10-15), copied whole."""
        short = typed if len(typed) <= 2 * _CONTEXT + 1 else _keep_start(typed) + "…" + _keep_end(typed)
        self._row("（游標移到別處）", esc(short), "照畫面上的字定下來", "note",
                  _tip(["你點了別的地方、選取或打了字，草稿就照畫面上的樣子定下來；之後講的話從新的游標位置開始"], ""),
                  typed)

    def undo(self, took: str, back: str, revived: str):
        """復原 took back `took` (and typed `back`); `revived`: the text left a draft again."""
        spoken, entry, before, after = self._spoken_undo or (None, None, "", "")
        self._spoken_undo = None
        why = []
        if revived:
            why.append("前面的字回到草稿：之後講的話接在後面，說指令或關麥克風時照樣補句尾標點")
        change = (gone(took) if took else "") + ins(back) or f"<span style='color:{_GRAY}'>（無）</span>"
        g = self._row(spoken or "（連按兩下）", change, "復原", "undo", _tip(why, tech_of(entry)),
                      draft=self._listening)
        g["counts"]["undo"] += 1

    def single(self, kind: str, title: str, body: str, why: str = "", entry: dict | None = None, copy: str = ""):
        """Something done on its own: a 選字 pick, a selection deleted …"""
        if kind == "選字" and " → " in title:
            old, new = title.split(" → ", 1)
            body = gone(old) + ins(new)
        elif kind == "刪除":
            body = gone(title)
        self._row(f"（{kind}）", body, kind, "op", _tip([why] if why else [], tech_of(entry)), copy, draft=False)


_GRAY = "#6B7280"


def _tip(why: list, tech: str) -> str:
    return "\n".join([w for w in why if w] + ([tech] if tech else []))




def _now() -> str:
    return time.strftime("%H:%M:%S")


def _tail(text: str, n: int = 12) -> str:
    return text if len(text) <= n else "…" + text[-n:]
