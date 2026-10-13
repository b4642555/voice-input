"""Hotwords: words that bias X-ASR, each with a percentage (2026-10-13, the user's design).

One list (DATA_DIR/hotword-list.json, shown on the 熱詞 tab). 100% leaves recognition alone, 200% pulls hardest
toward the word, 0% pushes hardest away from it; the scores behind the percentages are not shown.
- typed (source "manual"): a word typed in the 選字 menu's input box; 200%, never changed by picks.
- 選字: picking a word of two or more characters (or English) in the menu raises it by 30% and lowers the word it
  replaced by 30%, so swapping 程式 and 城市 back and forth returns both to 100% (where they leave the list).
- the −/+ buttons on the 熱詞 tab move any word by 10%.
Scores: 200% is +5 for Chinese (+3 for English), 0% is −3. Measured with TTS (dev experiments): with
max_active_paths 12, Chinese hotwords at 5 hit 16/16 targets with no false hits, at 6 台北是一個美麗的城市 became
程式, at 8 even English broke (2026-10-11); 程式 130% / 城市 70% (+1.5 / −0.9) already turns 寫了一個新的城市 into
程式 and every pair up to 200% / 0% leaves 台北是一個美麗的城市 alone, while −5 made it 城池 (2026-10-13).
English stays at 100% or above: a negative English word only changed its case (clock -> Clock).
Single characters never become hotwords: they are handled by the smart picks (memory.py).
"""
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

PATH = DATA_DIR / "hotword-list.json"
MANUAL, PICKED = "manual", "picked"
_CJK = re.compile(r"[㐀-䶿一-鿿]")
NEUTRAL, TOP = 100, 200
PICK_STEP, BUTTON_STEP = 30, 10
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
    added: str = ""          # date it was first added, YYYY-MM-DD


def _today() -> str:
    return time.strftime("%Y-%m-%d")


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
        try:
            for d in json.loads(path.read_text(encoding="utf-8")):
                if isinstance(d, str):    # the first format (2026-10-11): a plain list of 加入熱詞 words
                    d = {"word": d, "source": MANUAL}
                word, source = d.get("word", ""), d.get("source", MANUAL)
                if not eligible(word):
                    continue
                pct = d["pct"] if "pct" in d else _from_weight(word, float(d.get("weight", 0)), source)
                # entries from before dates were kept (2026-10-11) were all added that day
                self.items.append(Hotword(word, int(pct), source, d.get("added") or "2026-10-11"))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            lines = ",\n".join("  " + json.dumps(asdict(h), ensure_ascii=False) for h in self.items)
            self.path.write_text("[\n" + lines + "\n]\n", encoding="utf-8")

    def find(self, word: str) -> Hotword | None:
        return next((h for h in self.items if h.word == word), None)

    def add(self, word: str) -> bool:
        """A word typed in the 選字 menu: a hotword at 200% (a picked one is promoted). False if not eligible."""
        word = word.strip()
        if not eligible(word):
            return False
        h = self.find(word)
        if h is None:
            self.items.append(Hotword(word, TOP, MANUAL, _today()))
        else:
            h.pct, h.source = TOP, MANUAL
        self.save()
        return True

    def _move(self, word: str, delta: int):
        """A pick moves a word by delta; typed words stay put and a word back at 100% leaves the list."""
        h = self.find(word)
        if h is None:
            h = Hotword(word, NEUTRAL, PICKED, _today())
            self.items.append(h)
        elif h.source == MANUAL:
            return
        h.pct = max(floor(word), min(TOP, h.pct + delta))
        if h.pct == NEUTRAL:
            self.items.remove(h)

    def picked(self, old: str, new: str):
        """A pick in 選字 replaced old with new: new gains 30%, old loses 30%."""
        new, old = new.strip(), old.strip()
        if old == new:
            return
        moved = [w for w in (new, old) if eligible(w)]
        if not moved:
            return
        if new in moved:
            self._move(new, PICK_STEP)
        if old in moved:
            self._move(old, -PICK_STEP)
        log.info("hotwords after pick %s -> %s: %s", old, new,
                 [(h.word, h.pct) for h in self.items if h.word in moved])
        self.save()

    def adjust(self, word: str, steps: int):
        """The −/+ buttons on the 熱詞 tab: steps of 10%, within 0% (100% for English) .. 200%. A word set to
        100% stays listed (it does nothing) so the next click can go on."""
        h = self.find(word)
        if h is not None:
            h.pct = max(floor(word), min(TOP, h.pct + steps * BUTTON_STEP))
            self.save()

    def remove(self, word: str):
        h = self.find(word)
        if h is not None:
            self.items.remove(h)
            self.save()

    def bias(self) -> list[tuple[str, float]]:
        """(word, score) pairs for X-ASR; words at 100% are left out."""
        return [(h.word, score(h.word, h.pct)) for h in self.items if h.pct != NEUTRAL]

    def weights_for(self, words) -> dict[str, float]:
        """Scores of those of words that are hotwords (to put picked words first in 選字 and lowered ones last)."""
        return {h.word: score(h.word, h.pct) for h in self.items if h.word in words}
