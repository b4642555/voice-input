"""Hotwords the user added (2026-10-11): by the custom operation 加入熱詞, or by picking a way of writing English in
選字 (it is the user's spelling; it replaces other spellings of the same words: voiceInput replaces Voice Input).

Every hotword biases X-ASR at the highest score. X-ASR barely follows Chinese biasing (城市 never became 程式), so a
Chinese hotword is also absolute (the user's choice): after formatting, any run of characters read exactly like it,
tones included, becomes it (城市 / 乘勢 -> 程式). Stored in DATA_DIR/hotword-list.json.
"""
import json
import logging
import re
import threading

from .memory import split_words
from .paths import DATA_DIR
from .reading import tokens

log = logging.getLogger("voiceinput")

PATH = DATA_DIR / "hotword-list.json"
SCORE = 3.0
_CJK = re.compile(r"[㐀-䶿一-鿿]")


class Hotwords:
    def __init__(self, path=PATH):
        self.path = path
        self._lock = threading.Lock()
        self.words: list[str] = []
        try:
            self.words = [w for w in json.loads(path.read_text(encoding="utf-8")) if isinstance(w, str) and w]
        except (OSError, ValueError):
            pass

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.words, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, word: str) -> bool:
        """False if it is already a hotword."""
        word = word.strip()
        if not word or word in self.words:
            return False
        self.words.append(word)
        self.save()
        return True

    def add_spelling(self, word: str) -> list[str] | None:
        """Add an English spelling, removing other spellings of the same words. Returns the removed ones, or None
        if word was already a hotword."""
        if word in self.words:
            return None
        same = [w.lower() for w in split_words(word)]
        removed = [w for w in self.words if [x.lower() for x in split_words(w)] == same and not _CJK.search(w)]
        self.words = [w for w in self.words if w not in removed] + [word]
        self.save()
        return removed

    def remove(self, word: str):
        if word in self.words:
            self.words.remove(word)
            self.save()

    def bias(self) -> list[tuple[str, float]]:
        return [(w, SCORE) for w in self.words]

    def apply(self, text: str) -> str:
        """Chinese hotwords are absolute: every run read exactly like one becomes it (longest hotword first)."""
        words = sorted((w for w in self.words if _CJK.search(w) and all(_CJK.match(c) for c in w)), key=len,
                       reverse=True)
        if not words:
            return text
        toks = [t for t in tokens(text) if t.kind == "cjk"]
        keys = {w: [t.reading for t in tokens(w)] for w in words}
        out, pos, i = [], 0, 0
        while i < len(toks):
            for w in words:
                n = len(w)
                run = toks[i:i + n]
                if (len(run) == n and [t.reading for t in run] == keys[w]
                        and all(run[k + 1].start == run[k].end for k in range(n - 1))):
                    old = text[run[0].start:run[-1].end]
                    if old != w:
                        log.info("hotword: %s -> %s", old, w)
                    out.append(text[pos:run[0].start] + w)
                    pos = run[-1].end
                    i += n
                    break
            else:
                i += 1
        return "".join(out) + text[pos:]
