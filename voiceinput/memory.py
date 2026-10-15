"""Smart picks (智慧選字, 2026-10-11): single characters picked in the candidate menu, learned like an input method.

Only single Chinese characters (在 / 再) since the same day (the user's choice): a word of two or more characters
picked in 選字 raises its X-ASR hotword weight instead (hotwords.py), and nothing is replaced after recognition.

Modelled on art-shuangpin's core/user_preferences (its docs/spec.md §7). Every pick is one EXAMPLE: "the span that
sounds like <key>, between <left> and <right>, was picked as <value>". key is the span's reading (toned pinyin per
character, casefolded English words: "cheng2 shi4", "voice input"); left is up to two characters in front, nearest
last, `^` for "nothing usable in front" (the start, punctuation, English); right is up to two toneless readings after
it, nearest first. Readings on the right because what follows may be misheard too. (The rules below were written
for phrases too; with single characters only, the phrase cases no longer arise.)

After formatting, every span whose reading has examples is matched: walking outward from the span on each side,
agreeing tokens count until the first disagreement. Most agreement wins, then more on the right, then the boundary,
then the newest. A single Chinese character needs at least one agreeing token and is left alone when the best
examples disagree (so 在 / 再 do not flip each other); a phrase applies anywhere, even with nothing agreeing (the
user's choice: picked once, used everywhere until picked back in another context). A pick deletes older examples
with another value whose context does not conflict with it; examples in conflicting contexts stay, which is how one
reading learns two answers.

Also learned, in the same file so the user can read everything (DATA_DIR/smart-picks.json, opened from the 一般
tab): styles, how often each way of writing English was picked (voiceInput, Voice Input …), counted apart for one
word and for several, to order those candidates. (Picks raising a word's X-ASR bias was planned and dropped the same
day: X-ASR barely follows Chinese biasing, and the user chose not to keep it for English alone.)
"""
import copy
import json
import logging
import re
import threading

from .paths import DATA_DIR
from .reading import Token, reading_key, tokens
from .word_storage import JsonFile

log = logging.getLogger("voiceinput")

PATH = DATA_DIR / "smart-picks.json"
BOUNDARY = "^"
_SIDE = 2
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_LATIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]*$")
_STYLES_MANY = ["camel", "pascal", "title", "lower", "upper", "snake", "kebab", "joined"]


def situation(text: str, start: int, end: int) -> tuple[list[str], list[str]]:
    """(left, right) around text[start:end]: left characters nearest last (with `^` when they stop before two),
    right toneless readings nearest first."""
    toks = tokens(text)
    before = [t for t in toks if t.end <= start]
    after = [t for t in toks if t.start >= end]
    left, pos = [], start
    for t in reversed(before):
        if len(left) == _SIDE or t.kind != "cjk" or t.end != pos:
            break
        left.insert(0, t.text)
        pos = t.start
    if len(left) < _SIDE:
        left.insert(0, BOUNDARY)
    right, pos = [], end
    for t in after:
        if len(right) == _SIDE or t.kind != "cjk" or t.start != pos:
            break
        right.append(t.toneless)
        pos = t.end
    return left, right


def _agreement(ex_left, ex_right, left, right) -> tuple[int, int, bool]:
    lm, boundary = 0, False
    for k in range(1, min(len(ex_left), len(left)) + 1):
        if ex_left[-k] != left[-k]:
            break
        if ex_left[-k] == BOUNDARY:
            boundary = True
            break
        lm += 1
    rm = 0
    for a, b in zip(ex_right, right):
        if a != b:
            break
        rm += 1
    return lm, rm, boundary


def _conflict(a: dict, b: dict) -> bool:
    """Some position both windows have disagrees."""
    la, lb = a["left"], b["left"]
    if any(la[-k] != lb[-k] for k in range(1, min(len(la), len(lb)) + 1)):
        return True
    return any(x != y for x, y in zip(a["right"], b["right"]))


def split_words(text: str) -> list[str]:
    """English words in text, camelCase and snake_case split: voiceInput -> voice, Input."""
    return [w for part in re.split(r"[\s_\-]+", text) for w in _CAMEL.findall(part)]


def variants(text: str) -> list[tuple[str, str]]:
    """(style, form) ways of writing English text, in default order: Claude / claude / CLAUDE for one word;
    voiceInput / VoiceInput / Voice Input / voice input / VOICE INPUT / voice_input / voice-input / voiceinput."""
    words = [w.lower() for w in split_words(text)]
    if not words:
        return []
    if len(words) == 1:
        w = words[0]
        return [("1:capital", w.capitalize()), ("1:lower", w), ("1:upper", w.upper())]
    forms = {
        "camel": words[0] + "".join(w.capitalize() for w in words[1:]),
        "pascal": "".join(w.capitalize() for w in words),
        "title": " ".join(w.capitalize() for w in words),
        "lower": " ".join(words),
        "upper": " ".join(w.upper() for w in words),
        "snake": "_".join(words),
        "kebab": "-".join(words),
        "joined": "".join(words),
    }
    return [(f"n:{s}", forms[s]) for s in _STYLES_MANY]


def is_latin(text: str) -> bool:
    return bool(_LATIN.match(text.strip()))


def _count(value, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("選字次數與序號須為整數")
    number = int(value)
    if number < minimum:
        raise ValueError("選字次數與序號不可小於下限")
    return number


def _parse(data):
    if not isinstance(data, dict):
        raise ValueError("智慧選字檔須為物件")
    rows, styles = data.get("examples", []), data.get("styles", {})
    if not isinstance(rows, list) or not isinstance(styles, dict):
        raise ValueError("examples 須為清單，styles 須為物件")
    examples, serial = {}, 1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("value"), str):
            raise ValueError("選字例子須有文字 value 欄位")
        if not _single(row["value"]):
            continue  # phrases and English were learned before the change to single characters
        key, left, right = row.get("key"), row.get("left"), row.get("right")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("選字例子須有讀音 key")
        if any(not isinstance(side, list) or len(side) > _SIDE
               or any(not isinstance(token, str) or not token for token in side)
               for side in (left, right)):
            raise ValueError("選字前後文須為最多兩個文字項目的清單")
        number = _count(row.get("serial"), 1)
        examples.setdefault(key, []).append({"value": row["value"], "left": list(left),
                                              "right": list(right), "serial": number})
        serial = max(serial, number + 1)
    for exs in examples.values():
        exs.sort(key=lambda e: -e["serial"])
    counts = {}
    for key, value in styles.items():
        if not isinstance(key, str) or not key:
            raise ValueError("英文寫法偏好須有文字 key")
        counts[key] = _count(value, 0)
    return examples, counts, serial


class Memory:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.RLock()
        self.examples: dict[str, list[dict]] = {}   # key -> examples, newest first
        self.styles: dict[str, int] = {}
        self._serial = 1
        self._file = JsonFile(path, _parse, lambda: ({}, {}, 1))
        self.reload_if_changed()

    @property
    def error(self) -> str | None:
        return self._file.error

    def reload_if_changed(self) -> bool:
        with self._lock:
            valid, state = self._file.load()
            if state is not None:
                self.examples, self.styles, self._serial = state
            return valid

    def save(self) -> bool:
        with self._lock:
            return self.reload_if_changed() and self._save()

    def _save(self) -> bool:
        rows = [{"key": k, "value": e["value"], "left": e["left"], "right": e["right"], "serial": e["serial"]}
                for k, exs in sorted(self.examples.items()) for e in exs]
        # one example per line, so the file reads as a list
        lines = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
        text = ('{\n"examples": [\n' + lines + '\n],\n"styles": '
                + json.dumps(self.styles, ensure_ascii=False) + "\n}\n")
        return self._file.save(text)

    # --- examples ---
    def lookup(self, key: str, left, right, single: bool) -> str | None:
        with self._lock:
            self.reload_if_changed()
            return self._lookup(key, left, right, single)

    def _lookup(self, key: str, left, right, single: bool) -> str | None:
        exs = self.examples.get(key)
        if not exs:
            return None
        scored = []
        for e in exs:
            lm, rm, b = _agreement(e["left"], e["right"], left, right)
            scored.append(((lm + rm, rm, b), e))
        best = max(rank for rank, _ in scored)
        top = [e for rank, e in scored if rank == best]
        if single:
            total, right_only = best[0], best[1]
            # the character right before agrees, or two around it do: one shared neighbour on the right is not
            # evidence (成 -> 宸 learned before 跟 turned 吳宇澄跟防疫 into 吳宇宸, the user: 「這邏輯不對」, 2026-10-14)
            # a character on the left and two in all (the start of the text counts as one on the left): 他在這裡
            # yes, 我在這裡 / 他在家 no
            left_ok = total - right_only >= 1 or best[2]
            # an example learned with less context around it (在 said alone) needs only what it has
            known = min(len([c for c in e["left"] if c != BOUNDARY]) + len(e["right"]) + (BOUNDARY in e["left"])
                        for e in top)
            if not (left_ok and total + bool(best[2]) >= min(2, known)) or len({e["value"] for e in top}) > 1:
                return None
        return max(top, key=lambda e: e["serial"])["value"]

    def values(self, key: str) -> list[str]:
        """Every value picked for key, newest first."""
        with self._lock:
            self.reload_if_changed()
            out = []
            for e in self.examples.get(key, []):
                if e["value"] not in out:
                    out.append(e["value"])
            return out

    def record(self, text: str, start: int, end: int, value: str) -> bool:
        """The user picked value for text[start:end] (the selection, with its line around it)."""
        old = text[start:end]
        key = reading_key(old)
        with self._lock:
            if not self.reload_if_changed():
                return False
            before = copy.deepcopy(self.examples), dict(self.styles), self._serial
            single = bool(key) and _single(old) and _single(value)
            if single:
                left, right = situation(text, start, end)
                new = {"value": value, "left": left, "right": right, "serial": self._serial}
                self._serial += 1
                exs = [e for e in self.examples.get(key, [])
                       if not ((e["value"] != value and not _conflict(e, new))
                               or (e["value"] == value and e["left"] == left and e["right"] == right))]
                self.examples[key] = [new] + exs
            self._learn_style(old, value)
            if not self._save():
                self.examples, self.styles, self._serial = before
                return False
            if single:
                log.info("smart pick: %s -> %s (%s %s / %s)", old, value, key, "".join(left), "-".join(right))
            return True

    def apply(self, text: str, prefix: str = "") -> str:
        """Replace every span of text that the examples decide otherwise. prefix: the line before it (context)."""
        with self._lock:
            self.reload_if_changed()
            return self._apply(text, prefix)

    def _apply(self, text: str, prefix: str) -> str:
        if not self.examples:
            return text
        lengths = [1]
        full = prefix + text
        toks = tokens(full)
        out, pos, i = [], len(prefix), 0
        while i < len(toks) and toks[i].start < len(prefix):
            i += 1
        while i < len(toks):
            for n in lengths:
                run = toks[i:i + n]
                if len(run) < n or run[0].kind != "cjk" or not _contiguous(full, run):
                    continue
                key = " ".join(t.reading for t in run)
                if key not in self.examples:
                    continue
                a, b = run[0].start, run[-1].end
                left, right = situation(full, a, b)
                value = self._lookup(key, left, right, single=(n == 1 and run[0].kind == "cjk"))
                if value is None:
                    continue
                if value != full[a:b]:
                    log.info("smart pick applied: %s -> %s", full[a:b], value)
                out.append(full[pos:a] + value)
                pos = b
                i += n
                break
            else:
                i += 1
        return "".join(out) + full[pos:]

    # --- English styles ---
    def _learn_style(self, old: str, new: str):
        for style, form in variants(old):
            if form == new:
                self.styles[style] = self.styles.get(style, 0) + 1
                return

    def ordered_variants(self, text: str) -> list[str]:
        """English ways of writing text, the styles picked most often first."""
        vs = variants(text)
        with self._lock:
            self.reload_if_changed()
            vs.sort(key=lambda sv: -self.styles.get(sv[0], 0))   # stable: default order breaks ties
        return [form for _, form in vs]


def _single(text: str) -> bool:
    return len(text) == 1 and bool(re.match(r"[㐀-䶿一-鿿]", text))


def _contiguous(text: str, run: list[Token]) -> bool:
    """Characters directly next to each other; words apart by spaces only."""
    for a, b in zip(run, run[1:]):
        gap = text[a.end:b.start]
        if a.kind == "cjk" and b.kind == "cjk":
            if gap:
                return False
        elif gap.strip():
            return False
    return True
