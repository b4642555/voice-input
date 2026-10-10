"""Hotwords: words that bias X-ASR, each with a weight (2026-10-11, reworked the same day).

Two sources, one list (DATA_DIR/hotword-list.json, shown on the 熱詞 tab with weights):
- 手動: the custom operation 加入熱詞; weight at the cap.
- 選字: a word of two or more characters (or English) picked in the candidate menu; it starts at half the cap and
  gains 0.5 per pick up to the cap. The word a pick replaced loses 1 and is dropped at 0 (程式 picked over 城市,
  later 城市 over 程式); a 手動 hotword never loses weight.
Caps: Chinese 5, English 3. Measured with TTS (dev experiment, 2026-10-11): with max_active_paths 12, Chinese
hotwords at 5 hit 16/16 targets with no false hits; at 6, 台北是一個美麗的城市 became 程式; at 8 even English
broke. Single characters never become hotwords: they are handled by the smart picks (memory.py). There is no
replacement after recognition any more: words of two or more characters are left to X-ASR biasing.
"""
import json
import logging
import re
import threading
from dataclasses import asdict, dataclass

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

PATH = DATA_DIR / "hotword-list.json"
MANUAL, PICKED = "manual", "picked"
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_CAP_ZH, _CAP_EN = 5.0, 3.0
_STEP, _DROP = 0.5, 1.0


def cap(word: str) -> float:
    return _CAP_ZH if _CJK.search(word) else _CAP_EN


def eligible(word: str) -> bool:
    """Single characters never become hotwords."""
    word = word.strip()
    return bool(word) and not (len(word) == 1 and _CJK.match(word))


@dataclass
class Hotword:
    word: str
    weight: float
    source: str = MANUAL


class Hotwords:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self.items: list[Hotword] = []
        try:
            for d in json.loads(path.read_text(encoding="utf-8")):
                if isinstance(d, str):    # the first format, the same day: a plain list of 加入熱詞 words
                    d = {"word": d, "weight": cap(d), "source": MANUAL}
                if eligible(d.get("word", "")):
                    self.items.append(Hotword(d["word"], float(d["weight"]), d.get("source", MANUAL)))
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
        """加入熱詞: a 手動 hotword at the cap (a 選字 one is promoted). False if not eligible."""
        word = word.strip()
        if not eligible(word):
            return False
        h = self.find(word)
        if h is None:
            self.items.append(Hotword(word, cap(word), MANUAL))
        else:
            h.weight, h.source = cap(word), MANUAL
        self.save()
        return True

    def picked(self, old: str, new: str):
        """A pick in 選字 replaced old with new: new gains weight, old loses some."""
        new, old = new.strip(), old.strip()
        changed = False
        if eligible(new):
            h = self.find(new)
            if h is None:
                self.items.append(Hotword(new, cap(new) / 2, PICKED))
            elif h.source == PICKED:
                h.weight = min(cap(new), h.weight + _STEP)
            changed = True
        h = self.find(old)
        if h is not None and h.source == PICKED and old != new:
            h.weight -= _DROP
            if h.weight <= 0:
                self.items.remove(h)
            changed = True
        if changed:
            log.info("hotword weights after pick %s -> %s: %s", old, new,
                     [(h.word, h.weight) for h in self.items if h.word in (old, new)])
            self.save()

    def remove(self, word: str):
        h = self.find(word)
        if h is not None:
            self.items.remove(h)
            self.save()

    def bias(self) -> list[tuple[str, float]]:
        return [(h.word, h.weight) for h in self.items]

    def weights_for(self, words) -> dict[str, float]:
        """Weights of those of words that are hotwords (to put picked words first in 選字)."""
        return {h.word: h.weight for h in self.items if h.word in words}
