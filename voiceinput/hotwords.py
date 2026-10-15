"""Hotwords: words that bias X-ASR, each with a weight (2026-10-11, reworked the same day).

Two sources, one list (DATA_DIR/hotword-list.json, shown on the 熱詞 tab with weights):
- typed (source "manual"): a word typed in the 選字 menu's input box; weight at the cap. (Until 2026-10-11 the
  custom operation 加入熱詞 added these; the 熱詞 tab no longer shows the source.)
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
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import date

from .paths import DATA_DIR
from .word_storage import JsonFile

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
    if not isinstance(word, str):
        return False
    word = word.strip()
    return bool(word) and not (len(word) == 1 and _CJK.match(word))


@dataclass
class Hotword:
    word: str
    weight: float
    source: str = MANUAL
    added: str = ""          # date it was first added, YYYY-MM-DD


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _parse(data) -> list[Hotword]:
    if not isinstance(data, list):
        raise ValueError("熱詞檔須為清單")
    items, seen = [], set()
    for row in data:
        if isinstance(row, str):  # first format: a plain list of manual words
            row = {"word": row, "weight": cap(row), "source": MANUAL}
        if not isinstance(row, dict) or not isinstance(row.get("word"), str):
            raise ValueError("熱詞須有文字 word 欄位")
        word = row["word"].strip()
        if not eligible(word):
            continue
        weight = row.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float, str)):
            raise ValueError("熱詞權重須為數字")
        weight = float(weight)
        if not math.isfinite(weight) or not 0 <= weight <= cap(word):
            raise ValueError("熱詞權重須在 0 與該詞上限之間")
        source = row.get("source", MANUAL)
        if source not in (MANUAL, PICKED):
            raise ValueError("熱詞 source 須為 manual 或 picked")
        added = row.get("added") or "2026-10-11"
        if not isinstance(added, str):
            raise ValueError("熱詞日期須為 YYYY-MM-DD")
        date.fromisoformat(added)
        if word in seen:
            raise ValueError("熱詞清單有重複詞")
        seen.add(word)
        items.append(Hotword(word, weight, source, added))
    return items


class Hotwords:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.RLock()
        self._items: list[Hotword] = []
        self._file = JsonFile(path, _parse, list)
        self.reload_if_changed()

    @property
    def error(self) -> str | None:
        return self._file.error

    @property
    def items(self) -> list[Hotword]:
        with self._lock:
            self.reload_if_changed()
            return list(self._items)

    def reload_if_changed(self) -> bool:
        with self._lock:
            valid, items = self._file.load()
            if items is not None:
                self._items = items
            return valid

    def save(self) -> bool:
        with self._lock:
            return self.reload_if_changed() and self._save()

    def _save(self) -> bool:
        lines = ",\n".join("  " + json.dumps(asdict(h), ensure_ascii=False) for h in self._items)
        return self._file.save("[\n" + lines + "\n]\n")

    def _find(self, word: str) -> Hotword | None:
        return next((h for h in self._items if h.word == word), None)

    def find(self, word: str) -> Hotword | None:
        with self._lock:
            self.reload_if_changed()
            return self._find(word)

    def add(self, word: str) -> bool:
        """A word typed in the 選字 menu: a hotword at the cap (a picked one is promoted). False if not eligible."""
        if not eligible(word):
            return False
        word = word.strip()
        with self._lock:
            if not self.reload_if_changed():
                return False
            before = [replace(h) for h in self._items]
            h = self._find(word)
            if h is None:
                self._items.append(Hotword(word, cap(word), MANUAL, _today()))
            else:
                h.weight, h.source = cap(word), MANUAL
            if self._save():
                return True
            self._items = before
            return False

    def picked(self, old: str, new: str) -> bool:
        """A pick in 選字 replaced old with new: new gains weight, old loses some."""
        new, old = new.strip(), old.strip()
        with self._lock:
            if not self.reload_if_changed():
                return False
            before = [replace(h) for h in self._items]
            changed = False
            if eligible(new):
                h = self._find(new)
                if h is None:
                    self._items.append(Hotword(new, cap(new) / 2, PICKED, _today()))
                elif h.source == PICKED:
                    h.weight = min(cap(new), h.weight + _STEP)
                changed = True
            h = self._find(old)
            if h is not None and h.source == PICKED and old != new:
                h.weight -= _DROP
                if h.weight <= 0:
                    self._items.remove(h)
                changed = True
            if not changed:
                return True
            if not self._save():
                self._items = before
                return False
            log.info("hotword weights after pick %s -> %s: %s", old, new,
                     [(h.word, h.weight) for h in self._items if h.word in (old, new)])
            return True

    def rename(self, old: str, new: str) -> bool:
        """Change one manual word atomically, retaining its original date."""
        if not eligible(new):
            return False
        new = new.strip()
        with self._lock:
            if not self.reload_if_changed():
                return False
            h = self._find(old)
            if h is None or (new != old and self._find(new) is not None):
                self._file.error = "熱詞已變更或新名稱已存在，請重新確認列表。"
                return False
            before = [replace(item) for item in self._items]
            h.word, h.weight, h.source = new, cap(new), MANUAL
            if self._save():
                return True
            self._items = before
            return False

    def remove(self, word: str) -> bool:
        with self._lock:
            if not self.reload_if_changed():
                return False
            h = self._find(word)
            if h is None:
                return True
            before = list(self._items)
            self._items.remove(h)
            if self._save():
                return True
            self._items = before
            return False

    def percent(self, h: Hotword) -> float:
        return 100 * h.weight / cap(h.word)

    def bias(self) -> list[tuple[str, float]]:
        with self._lock:
            self.reload_if_changed()
            return [(h.word, h.weight) for h in self._items]

    def weights_for(self, words) -> dict[str, float]:
        """Weights of those of words that are hotwords (to put picked words first in 選字)."""
        with self._lock:
            self.reload_if_changed()
            return {h.word: h.weight for h in self._items if h.word in words}
