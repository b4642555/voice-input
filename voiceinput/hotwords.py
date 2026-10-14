"""Hotwords: words that bias X-ASR, each with a percentage (2026-10-13, the user's design).

One list (DATA_DIR/hotword-list.json, shown on the 詞庫 tab; the UI calls hotwords 詞庫 since 2026-10-13). 100% leaves recognition alone, 200% pulls hardest
toward the word, 0% pushes hardest away from it; the scores behind the percentages are not shown.
- typed (source "manual"): a word typed in the 選字 menu's input box; 200%, never changed by picks.
- 選字: picking a word of two or more characters (or English) in the menu raises it by 30% and lowers the word it
  replaced by 30%, so swapping 程式 and 城市 back and forth returns both to 100% (where they leave the list).
- ✕ on the 詞庫 tab puts a word back where it would be unlisted; the −/+ buttons that moved a word by 10% were
  removed 2026-10-14 (the user's choice: priority comes from picks and typing only).
- words of ticked term packs (packs.py) count as being at the pack's percentage without being listed; a pick or a
  replacement lists one, moved from there, and the listed percentage wins (back at the pack's, it leaves the list).
Scores: 200% is +5 for Chinese (+3 for English), 0% is −3. Measured with TTS (dev experiments): with
max_active_paths 12, Chinese hotwords at 5 hit 16/16 targets with no false hits, at 6 台北是一個美麗的城市 became
程式, at 8 even English broke (2026-10-11); 程式 130% / 城市 70% (+1.5 / −0.9) already turns 寫了一個新的城市 into
程式 and every pair up to 200% / 0% leaves 台北是一個美麗的城市 alone, while −5 made it 城池 (2026-10-13).
English stays at 100% or above: a negative English word only changed its case (clock -> Clock), and negative
misheard spellings made English worse (Deep Seek fell back to Deepsec, 2026-10-13). Instead, a misheard English
spelling replaced in 選字 or by typing (Dipsick -> DeepSeek) is kept as an alias of the word and rewritten after
recognition (spelling.py); picking the alias back removes it.
Single characters never become hotwords: they are handled by the smart picks (memory.py).
"""
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

PATH = DATA_DIR / "hotword-list.json"
MANUAL, PICKED = "manual", "picked"
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'+#&_-]*$")
NEUTRAL, TOP = 100, 200
PICK_STEP = 30
_TOP_ZH, _TOP_EN, _BOTTOM = 5.0, 3.0, -3.0


def is_chinese(word: str) -> bool:
    return bool(_CJK.search(word))


def floor(word: str) -> int:
    """English cannot go below 100%."""
    return 0 if is_chinese(word) else NEUTRAL


def eligible(word: str) -> bool:
    """Single characters never become hotwords."""
    word = word.strip()
    return bool(word) and not (len(word) == 1 and _CJK.match(word))


def score(word: str, pct: int) -> float:
    """The X-ASR hotword score behind a percentage."""
    if pct >= NEUTRAL:
        return (pct - NEUTRAL) / (TOP - NEUTRAL) * (_TOP_ZH if is_chinese(word) else _TOP_EN)
    return (NEUTRAL - pct) / NEUTRAL * _BOTTOM


@dataclass
class Hotword:
    word: str
    pct: int
    source: str = MANUAL
    added: str = ""          # when it was first added, YYYY-MM-DD HH:MM:SS (the date alone before 2026-10-14)
    aliases: list = field(default_factory=list)   # misheard English spellings rewritten as this word (spelling.py)
    alias_added: dict = field(default_factory=dict)   # alias -> when it was learned (2026-10-14; before: the word's)


def _now() -> str:
    """When something is added, to the second (2026-10-14, for a true order; the 詞庫 tab shows the date only).
    Older entries hold the date alone and sort before same-day ones with a time."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _from_weight(word: str, weight: float, source: str) -> int:
    """The percentage for an entry saved with a weight (before 2026-10-13: 0 .. cap, Chinese 5, English 3)."""
    if source == MANUAL:
        return TOP
    top = _TOP_ZH if is_chinese(word) else _TOP_EN
    return min(TOP, NEUTRAL + round(weight / top * 10) * 10)


class Hotwords:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self.items: list[Hotword] = []
        self.pack_pct: dict[str, int] = {}    # words of the ticked term packs -> their percentage
        try:
            for d in json.loads(path.read_text(encoding="utf-8")):
                if isinstance(d, str):    # the first format (2026-10-11): a plain list of 加入熱詞 words
                    d = {"word": d, "source": MANUAL}
                word, source = d.get("word", ""), d.get("source", MANUAL)
                if not eligible(word):
                    continue
                pct = d["pct"] if "pct" in d else _from_weight(word, float(d.get("weight", 0)), source)
                # entries from before dates were kept (2026-10-11) were all added that day
                self.items.append(Hotword(word, int(pct), source, d.get("added") or "2026-10-11",
                                          list(d.get("aliases", [])), dict(d.get("alias_added", {}))))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = ",\n".join("  " + json.dumps(asdict(h), ensure_ascii=False) for h in self.items)
            self.path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")

    def find(self, word: str) -> Hotword | None:
        return next((h for h in self.items if h.word == word), None)

    def set_packs(self, words, pct: int):
        self.pack_pct = {w: pct for w in words}

    def base(self, word: str) -> int:
        """The percentage of a word that is not listed: its term pack's, or 100%."""
        return self.pack_pct.get(word, NEUTRAL)

    def pct_of(self, word: str) -> int:
        h = self.find(word)
        return h.pct if h is not None else self.base(word)

    def levels(self) -> dict[str, int]:
        """Words raised or lowered (listed, or in a ticked term pack) -> percentage: smart picks neither break a
        raised one nor make a lowered one (2026-10-14)."""
        with self._lock:
            out = {w: p for w, p in self.pack_pct.items() if p != NEUTRAL}
            out.update({h.word: h.pct for h in self.items})
            return {w: p for w, p in out.items() if p != NEUTRAL}

    def add(self, word: str) -> bool:
        """A word typed in the 選字 menu: a hotword at 200% (a picked one is promoted). False if not eligible."""
        word = word.strip()
        if not eligible(word):
            return False
        h = self.find(word)
        if h is None:
            self.items.append(Hotword(word, TOP, MANUAL, _now()))
        else:
            h.pct, h.source = TOP, MANUAL
        self.save()
        return True

    def _move(self, word: str, delta: int):
        """A pick moves a word by delta from where it is (listed, in a term pack or 100%); typed words stay put and
        a word back where it would be unlisted leaves the list."""
        h = self.find(word)
        if h is None:
            h = Hotword(word, self.base(word), PICKED, _now())
            self.items.append(h)
        elif h.source == MANUAL:
            return
        h.pct = max(floor(word), min(TOP, h.pct + delta))
        if h.pct == self.base(word) and not h.aliases:
            self.items.remove(h)

    def picked(self, old: str, new: str):
        """A pick in 選字 replaced old with new: new gains 30%, old loses 30%."""
        new, old = new.strip(), old.strip()
        if old == new:
            return
        moved = [w for w in (new, old) if eligible(w)]
        if not moved:
            return
        self._learn_alias(old, new)
        if new in moved:
            self._move(new, PICK_STEP)
        if old in moved:
            self._move(old, -PICK_STEP)
        log.info("hotwords after pick %s -> %s: %s", old, new,
                 [(h.word, h.pct) for h in self.items if h.word in moved])
        self.save()

    def _learn_alias(self, old: str, new: str):
        """English replaced by English with other letters (Dipsick -> DeepSeek): old becomes an alias of new. A pick
        that puts an alias back (DeepSeek -> Dipsick) removes it."""
        def letters(w):
            return re.sub(r"[^a-z0-9]", "", w.lower())
        for h in self.items:
            if any(letters(a) == letters(new) for a in h.aliases):
                h.aliases = [a for a in h.aliases if letters(a) != letters(new)]
                log.info("alias %s of %s removed", new, h.word)
        if not (_LATIN.match(old) and _LATIN.match(new)) or letters(old) == letters(new) or not eligible(new):
            return
        h = self.find(new)
        if h is None:
            h = Hotword(new, self.base(new), PICKED, _now())
            self.items.append(h)
        if all(letters(a) != letters(old) for a in h.aliases):
            h.aliases.append(old)
            h.alias_added[old] = _now()
            log.info("alias %s of %s learned", old, new)

    def remove_alias(self, word: str, alias: str):
        h = self.find(word)
        if h is not None and alias in h.aliases:
            h.aliases.remove(alias)
            h.alias_added.pop(alias, None)
            if h.pct == self.base(word) and h.source == PICKED and not h.aliases:
                self.items.remove(h)
            self.save()

    def aliases(self) -> list[tuple[str, str]]:
        return [(a, h.word) for h in self.items for a in h.aliases]

    def alias_date(self, word: str, alias: str) -> str:
        h = self.find(word)
        return (h.alias_added.get(alias) or h.added) if h is not None else ""

    def listed(self) -> list[Hotword]:
        """The words the 詞庫 list shows: those whose 優先度 differs from where they would be unlisted (one kept only
        for its aliases is not shown)."""
        return [h for h in self.items if h.pct != self.base(h.word)]

    def remove(self, word: str):
        """✕ on the 詞庫 tab: the word goes back to where it would be unlisted (100%, or its term pack's); its
        aliases stay, removed only under 英文強制替換 (the user's choice, 2026-10-14)."""
        h = self.find(word)
        if h is None:
            return
        if h.aliases:
            h.pct, h.source = self.base(word), PICKED
        else:
            self.items.remove(h)
        self.save()

    def bias(self) -> list[tuple[str, float]]:
        """(word, score) pairs for X-ASR: the list, then the term packs' words not in it; words at 100% are left
        out."""
        listed = {h.word for h in self.items}
        out = [(h.word, score(h.word, h.pct)) for h in self.items if h.pct != NEUTRAL]
        return out + [(w, score(w, pct)) for w, pct in self.pack_pct.items() if w not in listed and pct != NEUTRAL]

    def weights_for(self, words) -> dict[str, float]:
        """Scores of those of words that are hotwords or in a ticked term pack (to put picked words first in 選字
        and lowered ones last)."""
        return {w: score(w, self.pct_of(w)) for w in words if self.find(w) is not None or w in self.pack_pct}
