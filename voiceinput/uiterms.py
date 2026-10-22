"""Interface and game design words for the recognition API (VA's feedback): `dict/ui_terms.txt` (2026-10-21, the user:
置中, 對齊, 重疊 come up all the time; 置中 was written 治中). They bias recognition, and `fix` writes a span that sounds
exactly like one of the Chinese words as that word, strictly: same toneless reading for every character, the span is no
dictionary word, and no dictionary word crosses its edges (對其他人 keeps 對其: 其他 is a word). screen_terms.fix was
tried first and is far too loose for this: on the user's 698 past sentences it changed 93, nearly all wrongly
(自己 -> 字級, 網路 -> 網格, 作品集 -> 左對齊)."""
import re
from functools import lru_cache
from pathlib import Path

FILE = Path(__file__).parent / "dict" / "ui_terms.txt"
SCORE = 3.0   # like Chinese words seen on screen: a nudge, below hotwords
# grammar characters: one of these heard is meant, never a design word (給 read ji became 貼齊, 的 read di 敵人 / 置底,
# 他也 拖曳, 圖是 圖示 on the user's past sentences)
_GRAMMAR = set("的了是在我你他她它們和與或也都就要會有不沒很個著吧嗎呢啊喔哦把被讓給到說從跟又才而且還再")
_CJK = re.compile(r"^[一-鿿]+$")


@lru_cache(maxsize=1)
def words() -> tuple[str, ...]:
    out = []
    for line in FILE.read_text(encoding="utf-8").splitlines():
        w = line.split("#", 1)[0].strip()
        if w:
            out.append(w)
    return tuple(dict.fromkeys(out))


def chinese() -> list[str]:
    return [w for w in words() if _CJK.match(w) and 2 <= len(w) <= 6]


def bias() -> list[tuple[str, float]]:
    return [(w, SCORE) for w in words()]


def _readings(text: str) -> list[set[str]]:
    from pypinyin import Style, pinyin
    return [set(r) for r in pinyin(text, style=Style.NORMAL, heteronym=True)]


@lru_cache(maxsize=1)
def _index() -> dict[int, list[tuple[str, list[set[str]]]]]:
    out: dict[int, list] = {}
    for w in chinese():
        if len(w) <= 4:
            out.setdefault(len(w), []).append((w, _readings(w)))
    return out


def fix(text: str) -> tuple[str, int]:
    """text with spans that sound exactly like an interface word written as it; (text, how many)."""
    from .candidates import is_word
    if not text or is_word("的") is None:   # the dictionary is still loading: without it nothing is safe to change
        return text, 0
    reads = _readings(text)
    if len(reads) != len(text):   # pypinyin merges non-Chinese runs: work on Chinese-only text pieces
        return _fix_pieces(text)
    out, i, n = list(text), 0, 0
    while i < len(text):
        hit = None
        for size in (4, 3, 2):
            span = text[i:i + size]
            if len(span) < size or not _CJK.match(span):
                continue
            for w, wr in _index().get(size, []):
                if span == w or not all(reads[i + k] & wr[k] for k in range(size)):
                    continue
                if any(span[k] in _GRAMMAR and span[k] != w[k] for k in range(size)):
                    continue
                if is_word(span):
                    continue
                crossing = any(is_word(text[a:b]) for a in range(max(0, i - 3), i + size)
                               for b in range(max(a + 2, i + 1), min(len(text), i + size + 3) + 1)
                               if (a < i < b or a < i + size < b) and _CJK.match(text[a:b] or "x"))
                if not crossing:
                    hit = w
                    break
            if hit:
                break
        if hit:
            out[i:i + len(hit)] = list(hit)
            n += 1
            i += len(hit)
        else:
            i += 1
    return "".join(out), n


def _fix_pieces(text: str) -> tuple[str, int]:
    parts, n = re.split(r"([一-鿿]+)", text), 0
    for k, p in enumerate(parts):
        if p and _CJK.match(p):
            parts[k], m = fix(p)
            n += m
    return "".join(parts), n
