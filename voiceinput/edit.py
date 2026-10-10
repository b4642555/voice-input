"""What an utterance means for selected text, and punctuation names.

Speaking with text selected replaces it (as typing would). Deleting it is a custom operation (刪除, rules.py).

A bare punctuation name (逗號, 句號, 驚嘆號 …) is typed as the mark itself; only when the whole utterance is exactly
the name, so 逗號很重要 is dictated as spoken. Other spoken phrases (換行, 送出) are custom operations (rules.py).

A character described by a word (「節日的節」, a setting since 2026-10-11): an utterance that is exactly <word>的<one
character> types only that character, when the word contains it or a character that sounds like it (X-ASR may write
節日的結: the 節 of 節日 is typed). This is how people in Taiwan say which character they mean; a word said in natural
Chinese is recognized far more reliably than Bopomofo names (spelling with ㄔ ㄤ 昌 was tried and reverted the same
day: X-ASR heard the names as English letters).

An utterance on a selected 。 (setting, 2026-10-07) continues the sentence: the 。 becomes ， and moves to the end
(今天很好。明天 → 今天很好，我們去公園。明天).
"""
import re

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


def _sounds(ch: str) -> set[str]:
    return set(pinyin(ch, style=Style.NORMAL, heteronym=True)[0])


def described(formatted: str) -> str | None:
    """The character when the whole utterance describes one (節日的節 -> 節), else None."""
    m = _DESCRIBED.match(_PUNCT.sub("", formatted))
    if not m:
        return None
    before, ch = m.groups()
    word = before[-_WORD:]
    if ch in word:
        return ch
    return next((c for c in reversed(word) if _sounds(c) & _sounds(ch)), None)


def continue_sentence(formatted: str) -> str:
    """The replacement for a selected 。: ， + the utterance + 。"""
    return "，" + _TRAILING.sub("", formatted.strip()) + "。"


def replacement(selected: str, formatted: str) -> str:
    """The text that replaces a selection: sentence-final punctuation only if the selection had some."""
    text = formatted.strip()
    if not _TRAILING.search(selected):
        text = _TRAILING.sub("", text)
    return text

