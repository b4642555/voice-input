"""Laughter (experiment, 2026-10-14, the user's request): SenseVoice names a sound event per clip, <|Laughter|> among
them. When it hears laughter in a dictation, the text the user chose (哈哈, XD, 😂 ...) is typed.

The model is loaded only while the setting is on (about 150MB). It names one event for the whole clip it is given,
so the clip is asked about as a whole and about its last 1.5 seconds (a laugh at the end of a sentence is a small part
of the whole clip). How reliable it is on real laughter has not been measured: it is an experiment.
"""
import re

SAMPLE_RATE = 16000
TAIL_SECONDS = 1.5
_ONLY_LAUGH = re.compile(r"^[\s哈呵嘿呵ㄏhH,，。.!！~～xXDd😂🤣]*$")
_ENDS_LAUGH = re.compile(r"(?:[哈呵嘿]{2,}|[xX][dD]|[hH]{2,}|[😂🤣])\s*[~～!！]*$")


def heard(recognizer, audio) -> bool:
    """True when SenseVoice hears laughter in the clip or in its last 1.5 seconds."""
    if recognizer is None or audio is None:
        return False
    if recognizer.sound_event(audio) == "laughter":
        return True
    tail = int(SAMPLE_RATE * TAIL_SECONDS)
    return audio.size > tail * 1.6 and recognizer.sound_event(audio[-tail:]) == "laughter"


def apply(text: str, insert: str, laughing: bool) -> str:
    """The text with the laugh typed: alone when nothing else was said (or only 哈哈-like characters), else after
    the sentence. Left alone when the text already ends with a laugh."""
    insert = insert.strip()
    if not laughing or not insert:
        return text
    if _ONLY_LAUGH.match(text):
        return insert
    if _ENDS_LAUGH.search(text.rstrip()):
        return text
    base = text.rstrip().rstrip("。.，,")
    gap = " " if base[-1:].isascii() and insert[:1].isascii() and base[-1:].isalnum() else ""
    return base + gap + insert
