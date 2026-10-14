"""The 辨識紀錄 tab's content: one card per sentence (a draft, from its first words to its commit), holding what
happened to it as steps in plain words (the user's pick, 2026-10-15, design A of three mockups: the old list showed
each result alone, and how a pause decoded the sentence again, which part settled, why a 。 was added or why a
command was taken for text could not be seen). Commands, 選字 and the like said or done on their own are cards of
one step. Main thread only.

A card: id, time (of its last step), kind (輸入 / 操作 / 刪除 / 選字 / 取代 / 復原), title (the text it typed now),
copy (what 複製 copies), steps, counts. A step: time, kind (input / fix / lock / op / end / undo / note), html (the
document's change, with <ins>/<del> marks as styled spans), why (a sentence for anyone), tech (seconds, the
recognizer's own text and the formatting steps; shown when the user asks for details)."""
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


def quote(s: str) -> str:
    return f"「{esc(s)}」"


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
    def __init__(self, on_change, size: int = 100):
        self.cards = deque(maxlen=size)
        self._on_change = on_change   # called after every change, to render
        self._ids = itertools.count()
        self.card = None              # the sentence being written (a draft)
        self.last = None              # the last sentence card, which 復原 may bring back
        self._session = 0
        self._spoken_undo = None      # the 復原 just said, for the undo step that follows

    # --- cards and steps ---
    def _new(self, kind: str, title: str = "") -> dict:
        card = {"id": next(self._ids), "kind": kind, "title": title, "copy": "", "time": _now(), "steps": [],
                "counts": {"pieces": 0, "redo": 0, "undo": 0}, "session": self._session, "end": ""}
        self.cards.append(card)
        return card

    def _step(self, card: dict, kind: str, body: str, why: str = "", tech: str = ""):
        card["steps"].append({"time": _now(), "kind": kind, "html": body, "why": why, "tech": tech})
        card["time"] = _now()

    def _sentence(self) -> dict:
        if self.card is None:
            self.card = self._new("輸入")
            self.last = self.card
        return self.card

    def _done(self):
        self._on_change()

    def new_session(self):
        """Listening turned on."""
        self._session += 1
        self.card = None

    # --- a draft's events ---
    def heard(self, info: dict | None, before: str, after: str, locked: str = ""):
        """A pause: the draft shows `after` where it showed `before`; `locked`, the part settled now."""
        if before == after and not locked:
            return
        c = self._sentence()
        info = info or {}
        said = info.get("heard", "")
        c["counts"]["pieces"] += 1 if said else 0
        if not before and info.get("redo"):
            self._step(c, "input", "游標移開前還沒打出來的話，改打在新的游標位置：" + ins(after),
                       "辨識結果還沒打出來前游標就移開了")
        elif not before:
            if said and _bare(said) == _bare(after):   # typed as heard
                body = f"聽到「{ins(after)}」，先打出來"
            else:
                body = (f"聽到{quote(said)}，先打出來：" if said else "打出來：") + ins(after)
            self._step(c, "input", body, "", tech_of(info.get("entry")))
        elif info.get("redo"):
            self._step(c, "fix", "游標移開後講的話，在新位置重新辨識：" + diff_html(before, after),
                       "辨識結果還沒打出來前游標就移開了，所以在你點的新位置打", tech_of(None, info.get("tech", "")))
        else:
            c["counts"]["redo"] += 1
            head = f"聽到{quote(said)}，整句重聽一次：" if said else "整句重聽一次："
            why = "" if c["counts"]["redo"] > 1 else "停頓之後才知道前面該斷在哪、怎麼寫，所以每次停頓都把還沒定下來的句子重聽"
            self._step(c, "fix", head + diff_html(before, after), why,
                       tech_of(info.get("entry"), f"整句重聽 {info['redo_s']:.2f} 秒" if "redo_s" in info else ""))
        if locked:
            self._step(c, "lock", f"{quote(_tail(locked))}以前<b>定下來</b>，之後不再重聽",
                       "標點落在你停頓的地方，前面就固定了；之後停頓只重聽後面的句子，比較快")
        c["title"] = c["copy"] = after
        self._done()

    def command(self, entry: dict, op, typed: str | None, before: str, after: str, marked: bool,
                drafted: bool, over: str | None = None):
        """A command said alone: the draft committed (`before` -> `after`), then the operation runs or the mark
        is typed (over the selection `over`)."""
        if op is not None and [(s.type, s.value) for s in op.steps] == [(rules.COMMAND, rules.UNDO)]:
            self._spoken_undo = (op.phrase, entry)   # told with what it took back
            if drafted and before != after:
                self._step(self._sentence(), "fix", "整句定稿：" + diff_html(before, after))
            return
        alone = entry.get("alone")
        heard = (f"單獨聽是{quote(alone)}，接在句子後面聽才認出是指令" if alone else "")
        if drafted:
            c = self._sentence()
            name = op.phrase if op else entry.get("asr", "")
            end = after if op else after + typed   # a mark said is typed after the sentence
            body = f"說「<b>{esc(name)}</b>」：整句定稿" + (("：" + diff_html(before, end)) if before != end else "")
            what = describe(op.steps) if op else ""
            if not op and before == end:
                body += f"，句尾用「{esc(typed)}」"
            body += f"，再{esc(what)}" if what else ""
            why = ("指令會先把前面的草稿定下來" + ("；句尾沒有標點就補上" if marked else "")) if op else \
                "說的是標點名稱，就把它打在句子後面"
            self._step(c, "op", body, "；".join(w for w in (why, heard) if w), tech_of(entry))
            c["end"] = op.phrase if op else typed
            c["title"] = c["copy"] = after + ("" if op else typed)
            self.card = None
        else:
            if op is not None:
                c = self._new("操作", op.phrase)
                what = describe(op.steps)
                self._step(c, "op", f"說「<b>{esc(op.phrase)}</b>」" + (f"：{esc(what)}" if what else ""),
                           "整句只有這個詞，所以當指令執行" + (f"；{heard}" if heard else ""), tech_of(entry))
            else:
                c = self._new("輸入", typed)
                c["copy"] = typed
                body = f"打上「{esc(typed)}」" + (f"，取代選取的{quote(over)}" if over else "")
                why = "說的是標點名稱或「某個詞的某個字」，只打那個符號或字"
                self._step(c, "op", body, why, tech_of(entry))
            self.card = None
            self.last = None
        self._done()

    def committed(self, before: str, after: str, marked: bool, by_stop: bool):
        """The draft committed without a command: listening turned off, or a pause when the setting says so."""
        c = self._sentence()
        body = ("麥克風關掉，整句定稿" if by_stop else "停頓，整句定稿") + \
            (("：" + diff_html(before, after)) if before != after else "")
        why = ("句尾沒有標點就補上「。」" if marked else "") if by_stop else "設定是每次停頓就定稿（或草稿超過 60 秒）"
        self._step(c, "end", body, why)
        c["end"] = "關麥克風" if by_stop else "停頓"
        c["title"] = c["copy"] = after
        self.card = None
        self._done()

    def frozen(self, typed: str):
        """The caret moved away from the draft: it stays as typed."""
        c = self.card
        if c is None:
            return
        self._step(c, "note", "游標移開了，照畫面上的字定下來", "之後講的話會從新的游標位置開始")
        c["end"] = "游標移開"
        self.card = None
        self._done()

    def undo(self, took: str, back: str, revived: str):
        """復原 took back `took` (and typed `back`); `revived`: the text left a draft again."""
        spoken, entry = self._spoken_undo or (None, None)
        self._spoken_undo = None
        c = self.card or (self.last if self.last is not None and self.last["session"] == self._session else None)
        if c is None:
            c = self._new("復原", took or back)
        who = f"你說「{esc(spoken)}」：" if spoken else "復原："
        body = who + "收回 " + (gone(took) if took else "（無）") + (" 換回 " + ins(back) if back else "")
        why = ("前面的字回到草稿：之後講的話接在後面，說指令或關麥克風時照樣補句尾標點"
               if revived and not c["counts"]["undo"] else "")
        self._step(c, "undo", body, why, tech_of(entry))
        c["counts"]["undo"] += 1
        if revived and c["kind"] == "輸入":
            self.card = c
            c["end"] = ""
            c["title"] = c["copy"] = revived
        self._done()

    def single(self, kind: str, title: str, body: str, why: str = "", entry: dict | None = None, copy: str = ""):
        """Something done on its own: a 選字 pick, a selection deleted …"""
        c = self._new(kind, title)
        c["copy"] = copy
        tech = tech_of(entry)
        if body != esc(title) or why or tech:   # a step that only repeats the title is left out
            self._step(c, "op", body, why, tech)
        self.card = None if self.card is not None and self.card is not c else self.card
        self._done()


def _bare(s: str) -> str:
    return s.rstrip("，。？！、；：,.?!;: ")


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _tail(text: str, n: int = 12) -> str:
    return text if len(text) <= n else "…" + text[-n:]
