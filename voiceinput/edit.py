"""What an utterance means for selected text, and punctuation names.

Speaking with text selected replaces it (as typing would). Deleting it is a custom operation (刪除, rules.py).

A bare punctuation name (逗號, 句號, 驚嘆號 …) is typed as the mark itself; only when the whole utterance is exactly
the name, so 逗號很重要 is dictated as spoken. Other spoken phrases (換行, 送出) are custom operations (rules.py).

A character described by a word (「節日的節」, a setting since 2026-10-11): an utterance that is exactly <word>的<one
character> types only that character, when the word contains it or a character that sounds like it (X-ASR may write
節日的結: the 節 of 節日 is typed). This is how people in Taiwan say which character they mean; a word said in natural
Chinese is recognized far more reliably than Bopomofo names (spelling with ㄔ ㄤ 昌 was tried and reverted the same
day: X-ASR heard the names as English letters).

「X改成Y」 said alone (a setting since 2026-10-14, the user's request) changes the last X typed before the caret to
Y: the same characters first, then the same reading without tones (the recognizer may write X another way each time),
punctuation and spaces in the text skipped.

An utterance on a selected 。 (setting, 2026-10-07) continues the sentence: the 。 becomes ， and moves to the end
(今天很好。明天 → 今天很好，我們去公園。明天).
"""
import re
from functools import lru_cache

from pypinyin import Style, pinyin

SYMBOLS = {"句號": "。", "句點": "。", "逗號": "，", "問號": "？", "驚嘆號": "！", "感嘆號": "！", "驚歎號": "！",
           "感歎號": "！", "頓號": "、", "分號": "；", "冒號": "：", "點點點": "……", "刪節號": "……", "省略號": "……"}

_PUNCT = re.compile(r"[\s，。、！？；：,.!?;:…]+")
_TRAILING = re.compile(r"[。，,.！？!?；;]+$")


def symbol(formatted: str) -> str | None:
    """The punctuation mark when the whole utterance is its name (逗號 / 逗號。), else None."""
    return SYMBOLS.get(_PUNCT.sub("", formatted))


_DESCRIBED = re.compile(r"^([㐀-䶿一-鿿]{2,8})的([㐀-䶿一-鿿])$")   # also 這個字是教育的育, 偉是偉大的偉
_WORD = 4   # the word is the end of what comes before 的


_CJK = re.compile(r"[㐀-䶿一-鿿]")


@lru_cache(maxsize=4096)
def _sounds(ch: str) -> frozenset[str]:
    return frozenset(pinyin(ch, style=Style.NORMAL, heteronym=True)[0])


def described(formatted: str) -> str | None:
    """The character when the whole utterance describes one (節日的節 -> 節), else None."""
    m = _DESCRIBED.match(_PUNCT.sub("", formatted))
    if not m:
        return None
    before, ch = m.groups()
    word = before[-_WORD:]
    if ch in word:
        return ch
    # a character that only sounds like it, when the word is all there is: 我這邊想要顯示的是 is a sentence, not
    # 顯示的示 (2026-10-14); the misheard ones in the log (一張的章, 測試的是) have two characters before 的
    if len(before) > _WORD:
        return None
    return next((c for c in reversed(word) if _sounds(c) & _sounds(ch)), None)


_CHANGE = re.compile(r"^把?(.+?)改成(.+)$")
_EDGE = " ，。、！？；：,.!?;:…「」"


def change(formatted: str) -> tuple[str, str] | None:
    """(X, Y) when the whole utterance is 「X改成Y」 or 「把X改成Y」, else None."""
    m = _CHANGE.match(formatted.strip(_EDGE))
    if not m:
        return None
    old, new = m.group(1).strip(_EDGE), m.group(2).strip(_EDGE)
    return (old, new) if old and new else None


def _skip(ch: str) -> bool:
    return ch.isspace() or ch in _EDGE


def find(text: str, old: str) -> tuple[int, int] | None:
    """The span of the last X in text: the same characters, then the same characters with punctuation and spaces
    skipped, then (two characters or more) the same reading without tones."""
    i = text.rfind(old)
    if i >= 0:
        return i, i + len(old)
    want = [c for c in old if not _skip(c)]
    spots = [i for i, c in enumerate(text) if not _skip(c)]
    tests = [lambda a, b: a.lower() == b.lower()]
    if len(want) >= 2:
        tests.append(lambda a, b: a.lower() == b.lower() or bool(
            _CJK.match(a) and _CJK.match(b) and _sounds(a) & _sounds(b)))
    for same in tests:
        for k in range(len(spots) - len(want), -1, -1):
            if all(same(text[spots[k + j]], want[j]) for j in range(len(want))):
                return spots[k], spots[k + len(want) - 1] + 1
    return None


def continue_sentence(formatted: str) -> str:
    """The replacement for a selected 。: ， + the utterance + 。"""
    return "，" + _TRAILING.sub("", formatted.strip()) + "。"


_SPELLED = re.compile(r"\s*((?:[A-Za-z]\s+)+[A-Za-z])\s*[.。,，!！?？]?\s*")
_LATIN_WORD = re.compile(r"(\W*)([A-Za-z](?:[A-Za-z0-9'_ -]*[A-Za-z0-9])?)(\W*)")


def spelled(selected: str, raw: str) -> tuple[str, str, str] | None:
    """Letters spelled over a selected English word (2026-10-13, the user's request): B L I P over Bleep types Blip,
    A P P over API types APP, D O G over doggy types dog: the letters written in the selection's case (all
    capitals, a capital first, or lower case), the selection's surrounding punctuation kept. raw is the recognizer's
    text (X-ASR writes spelled letters as "B L I P."). Returns (replacement, old word, new word)."""
    m, w = _SPELLED.fullmatch(raw), _LATIN_WORD.fullmatch(selected)
    if not m or not w:
        return None
    letters = re.sub(r"\s+", "", m.group(1)).lower()
    word = w.group(2)
    cased = [c for c in word if c.isalpha()]
    if len(cased) > 1 and all(c.isupper() for c in cased):
        new = letters.upper()
    elif word[0].isupper():
        new = letters.capitalize()
    else:
        new = letters
    return w.group(1) + new + w.group(3), word, new


def replacement(selected: str, formatted: str) -> str:
    """The text that replaces a selection: sentence-final punctuation only if the selection had some."""
    text = formatted.strip()
    if not _TRAILING.search(selected):
        text = _TRAILING.sub("", text)
    return text

