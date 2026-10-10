"""Spelling one character with Bopomofo (拼讀, 2026-10-11): the user says the symbols' names and then the
character, the way a child spells it out (ㄔ ㄤ 昌 "chi ang chang", ㄐ ㄧㄝ 節 "ji ye jie", ㄐ ㄧ ㄝ 節, ㄧ ㄢ 煙),
and exactly that one character is typed. Like a punctuation name, only a whole utterance counts.

What X-ASR writes for the names (TTS experiment, 2026-10-11) is any character with that sound: 吃 / 昂 for ㄔ / ㄤ,
雞 / 爺 or 吉 / 野 for ㄐ / ㄧㄝ, 一 / 安 for ㄧ / ㄢ, with tones all over the place. So the utterance is matched by
the characters' toneless readings (McBopomofo's, from candidates.py): every character but the last must read as a
symbol name (an initial's name ㄐㄧ = ㄐ, ㄉㄜ = ㄉ, ㄓ = ㄓ … first; the rest medials and finals, alone or
together), and together they must make a real syllable. ㄝ has no common character of its own: a ㄟ / ㄞ (欸)
counts as ㄝ where only ㄝ makes a syllable. Names heard as English letters (ㄐ ㄧ ㄝ → "G E A") are not handled.

The spoken symbols carry no tone (the user's correction: the last one does not necessarily carry it); the tone is
the last character's. When the last character reads as the spelled syllable (one of its readings, so 長 counts for
chang), it is typed as heard. When it was misheard (煙 came out as 音) but still starts with the same sound, the
spelled syllable wins: its most common character in the last character's tone. Otherwise it is not a spelling
(西洋棋 is ㄒ + ㄧㄤ + 棋, but 棋 does not start like xiang).
"""
import re
from dataclasses import dataclass
from itertools import product

from pypinyin import Style, lazy_pinyin

from . import candidates

_TONES = re.compile("[ˊˇˋ˙]")
_CJK = re.compile(r"^[㐀-䶿一-鿿]+$")
_RHYME = set("ㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ")
# an initial's name -> the initial (ㄓㄔㄕㄖㄗㄘㄙ are written as the bare initial anyway)
_INITIALS = {"ㄅㄛ": "ㄅ", "ㄆㄛ": "ㄆ", "ㄇㄛ": "ㄇ", "ㄈㄛ": "ㄈ", "ㄉㄜ": "ㄉ", "ㄊㄜ": "ㄊ", "ㄋㄜ": "ㄋ",
             "ㄌㄜ": "ㄌ", "ㄍㄜ": "ㄍ", "ㄎㄜ": "ㄎ", "ㄏㄜ": "ㄏ", "ㄐㄧ": "ㄐ", "ㄑㄧ": "ㄑ", "ㄒㄧ": "ㄒ",
             **{c: c for c in "ㄓㄔㄕㄖㄗㄘㄙ"}}
_SAME_START = [set("ㄓㄗ"), set("ㄔㄘ"), set("ㄕㄙ"), set("ㄖㄌ")]   # the fuzzy initials (candidates._FUZZY)
_COMPONENTS = (2, 3)   # symbol names before the character: initial + final … initial + medial + final

_syllables: set[str] | None = None   # toneless readings of single characters


@dataclass
class Spelled:
    char: str              # what is typed
    reading: str           # its toned Bopomofo reading (ㄐㄧㄝˊ)
    options: list[str]     # other characters for 選字 (_options)


def _readings(ch: str) -> list[str]:
    words = candidates._words
    return list(words[ch][1]) if ch in words else []


def _tone(reading: str) -> str:
    m = _TONES.search(reading)
    return m.group(0) if m else ""


def _valid(syllable: str) -> bool:
    global _syllables
    if _syllables is None:
        candidates.load()
        _syllables = {_TONES.sub("", r) for w, (_, rs) in candidates._words.items() if len(w) == 1 for r in rs}
    return syllable in _syllables


def _names(ch: str, first: bool) -> set[str]:
    """What the character can stand for as a spoken symbol name."""
    out = set()
    for r in _readings(ch):
        r = _TONES.sub("", r)
        if first and r in _INITIALS:
            out.add(_INITIALS[r])
        if r and set(r) <= _RHYME:
            out.add(r)
            if r in ("ㄟ", "ㄞ"):
                out.add("ㄝ")
    return out


def _same_start(a: str, b: str) -> bool:
    return a[0] == b[0] or any(a[0] in s and b[0] in s for s in _SAME_START)


def _chars(syllable: str) -> list[tuple[str, str, int]]:
    """(character, toned reading, frequency) of every single character read as syllable (toneless)."""
    out = []
    for w, r, freq in candidates.load().get(candidates._key(syllable), []):
        if len(w) == 1 and _TONES.sub("", r) == syllable:
            out.append((w, r, freq))
    return out


def parse(text: str) -> Spelled | None:
    """The spelled character when text (the whole utterance, Traditional, punctuation allowed) is a spelling."""
    text = re.sub(r"[\s\W_]+", "", text)
    if not _CJK.match(text) or len(text) - 1 not in _COMPONENTS:
        return None
    candidates.load()
    *names, last = text
    heard = _readings(last)
    choices = [_names(c, i == 0) for i, c in enumerate(names)]
    if not heard or not all(choices):
        return None
    spelled = {"".join(p) for p in product(*choices)}
    spelled = {s for s in spelled if _valid(s)}
    # the last character as heard, when one of its readings is the spelled syllable (of 結 ㄐㄧㄝ / ㄐㄧㄝˊ the one
    # pypinyin gives, the usual one)
    usual = candidates._toned(lazy_pinyin(last, style=Style.BOPOMOFO)[0])
    heard.sort(key=lambda r: r != usual)
    for r in heard:
        if _TONES.sub("", r) in spelled:
            return Spelled(last, r, _options(_TONES.sub("", r), r, last))
    # misheard: the spelled syllable in the heard tone, when the last character at least starts like it
    for s in sorted(spelled):
        for r in heard:
            if _same_start(s, _TONES.sub("", r)):
                chars = _chars(s)
                if not chars:
                    continue
                tone = _tone(r)
                pool = [c for c in chars if _tone(c[1]) == tone] or chars
                char, reading, _ = max(pool, key=lambda c: c[2])
                return Spelled(char, reading, _options(s, reading, char))
    return None


def _options(syllable: str, reading: str, char: str) -> list[str]:
    """Common characters before rare ones (鷨 / 錵 before 化 / 話 otherwise), then the same tone first, then
    frequency."""
    chars = sorted(_chars(syllable), key=lambda c: (c[2] < candidates._COMMON, c[1] != reading, -c[2]))
    out = []
    for w, _, _ in chars:
        if w != char and w not in out:
            out.append(w)
    return out
