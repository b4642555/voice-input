"""Smart picks (智慧選字, 2026-10-11): what the user picked in the candidate menu, learned like an input method.

Modelled on art-shuangpin's core/user_preferences (its docs/spec.md §7). Every pick is one EXAMPLE: "the span that
sounds like <key>, between <left> and <right>, was picked as <value>". key is the span's reading (toned pinyin per
character, casefolded English words: "cheng2 shi4", "voice input"); left is up to two characters in front, nearest
last, `^` for "nothing usable in front" (the start, punctuation, English); right is up to two toneless readings after
it, nearest first. Readings on the right because what follows may be misheard too.

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
import json
import logging
import re
import threading

from .paths import DATA_DIR
from .reading import Token, reading_key, tokens

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


class Memory:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.RLock()
        self.examples: dict[str, list[dict]] = {}   # key -> examples, newest first
        self.styles: dict[str, int] = {}
        self._serial = 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for ex in data.get("examples", []):
                self.examples.setdefault(ex["key"], []).append(
                    {"value": ex["value"], "left": list(ex["left"]), "right": list(ex["right"]),
                     "serial": int(ex["serial"])})
            self.styles = {k: int(v) for k, v in data.get("styles", {}).items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        for exs in self.examples.values():
            exs.sort(key=lambda e: -e["serial"])
            self._serial = max([self._serial] + [e["serial"] + 1 for e in exs])

    def save(self):
        with self._lock:
            rows = [{"key": k, "value": e["value"], "left": e["left"], "right": e["right"], "serial": e["serial"]}
                    for k, exs in sorted(self.examples.items()) for e in exs]
            # one example per line, so the file reads as a list
            lines = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
            text = ('{\n"examples": [\n' + lines + '\n],\n"styles": '
                    + json.dumps(self.styles, ensure_ascii=False) + "\n}\n")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")

    # --- examples ---
    def lookup(self, key: str, left, right, single: bool) -> str | None:
        with self._lock:
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
                if best == (0, 0, False) or len({e["value"] for e in top}) > 1:
                    return None
            return max(top, key=lambda e: e["serial"])["value"]

    def values(self, key: str) -> list[str]:
        """Every value picked for key, newest first."""
        with self._lock:
            out = []
            for e in self.examples.get(key, []):
                if e["value"] not in out:
                    out.append(e["value"])
            return out

    def record(self, text: str, start: int, end: int, value: str):
        """The user picked value for text[start:end] (the selection, with its line around it)."""
        old = text[start:end]
        key = reading_key(old)
        if not key:
            return
        left, right = situation(text, start, end)
        new = {"value": value, "left": left, "right": right, "serial": self._serial}
        with self._lock:
            self._serial += 1
            exs = [e for e in self.examples.get(key, [])
                   if not ((e["value"] != value and not _conflict(e, new))
                           or (e["value"] == value and e["left"] == left and e["right"] == right))]
            self.examples[key] = [new] + exs
            self._learn_style(old, value)
        log.info("smart pick: %s -> %s (%s %s / %s)", old, value, key, "".join(left), "-".join(right))
        self.save()

    def apply(self, text: str, prefix: str = "") -> str:
        """Replace every span of text that the examples decide otherwise. prefix: the line before it (context)."""
        with self._lock:
            if not self.examples:
                return text
            lengths = sorted({len(k.split(" ")) for k in self.examples}, reverse=True)
        full = prefix + text
        toks = tokens(full)
        out, pos, i = [], len(prefix), 0
        while i < len(toks) and toks[i].start < len(prefix):
            i += 1
        while i < len(toks):
            for n in lengths:
                run = toks[i:i + n]
                if len(run) < n or not _contiguous(full, run):
                    continue
                key = " ".join(t.reading for t in run)
                if key not in self.examples:
                    continue
                a, b = run[0].start, run[-1].end
                left, right = situation(full, a, b)
                value = self.lookup(key, left, right, single=(n == 1 and run[0].kind == "cjk"))
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
            vs.sort(key=lambda sv: -self.styles.get(sv[0], 0))   # stable: default order breaks ties
        return [form for _, form in vs]


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
