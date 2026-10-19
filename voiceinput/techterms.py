"""English tech, software and game names (2026-10-19, the user: 先只做英文的科技詞或軟體、遊戲名稱; light, offline).

voiceinput/dict/tech_terms.txt is a small bundled list (about 430 names, three popularity groups). After recognition,
an English word or run of words in the dictation that sounds like one of them is written the way the name is spelled
(get hub -> GitHub, cloud code -> Claude Code, type script -> TypeScript). It reuses the screen-name rules of
screen_en.py in its strict mode: the same letters (get hub, node js), a whole run that sounds like the name part by
part (cloud code, pie torch), or a near miss of one long word; an ordinary English word is never changed by its sound
alone (cloud service stays), a name between ordinary English words stays, and there is no guessing from "another
name is nearby" (that made via -> Vite on a 2,000-line check). When two names fit, the more used group wins. Loaded on first use (a few ms), the
index is a few hundred kB of Python strings; nothing is downloaded.
"""
import logging
import re
from pathlib import Path

from . import screen_en

log = logging.getLogger("voiceinput")
PATH = Path(__file__).with_name("dict") / "tech_terms.txt"
_names: list[str] = []
_rank: dict[str, int] = {}
_by_first: dict[str, list[str]] = {}


def _load():
    if _names:
        return
    weight = 2
    try:
        lines = PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        log.warning("tech term list is missing")
        _names.append("")
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("=="):
            weight = int(line[2:].strip() or 2)
            continue
        for name in (x.strip() for x in line.split(",")):
            if name and name not in _rank:
                _rank[name] = weight
                _names.append(name)
                key = screen_en._key(name)
                if key:
                    _by_first.setdefault(key[0].translate(screen_en._SOUND_CLASS), []).append(name)


_TOKEN = re.compile(r"[A-Za-z0-9]+")


def candidates(text: str) -> list[str]:
    """The names whose first sound is one a word of the text starts with (the list is searched per run of words: this
    keeps that to a few dozen names)."""
    _load()
    firsts = {t[0].lower().translate(screen_en._SOUND_CLASS) for t in _TOKEN.findall(text)}
    out: list[str] = []
    for f in firsts:
        out += _by_first.get(f, [])
    return out


def fix(text: str) -> tuple[str, int]:
    """The text with misheard tech names written right, and how many were changed. Text without Latin letters is
    returned at once."""
    if not re.search(r"[A-Za-z]{2}", text):
        return text, 0
    names = candidates(text)
    if not names:
        return text, 0
    return screen_en.fix(text, names, rank=_rank, strict=True)
