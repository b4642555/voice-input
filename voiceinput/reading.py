"""Text as tokens for matching by sound: each Chinese character with its toned pinyin (pypinyin reads a run of
characters together, so 多音字 follow their word), each English word or number casefolded."""
import re
from dataclasses import dataclass

from pypinyin import Style, lazy_pinyin

_RUN = re.compile(r"[㐀-䶿一-鿿]+|[A-Za-z0-9]+(?:'[A-Za-z]+)?")


@dataclass
class Token:
    kind: str        # "cjk" or "word"
    text: str
    start: int
    end: int
    reading: str     # toned pinyin (cheng2) or the casefolded word

    @property
    def toneless(self) -> str:
        return self.reading.rstrip("012345")


def tokens(text: str) -> list[Token]:
    out = []
    for m in _RUN.finditer(text):
        run = m.group(0)
        if run[0].isascii():
            out.append(Token("word", run, m.start(), m.end(), run.casefold()))
            continue
        for i, (ch, py) in enumerate(zip(run, lazy_pinyin(run, style=Style.TONE3, neutral_tone_with_five=True))):
            out.append(Token("cjk", ch, m.start() + i, m.start() + i + 1, py))
    return out


def reading_key(text: str) -> str:
    """How a span sounds, for looking it up: "cheng2 shi4", "voice input"."""
    return " ".join(t.reading for t in tokens(text))
