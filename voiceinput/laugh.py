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
    """True when SenseVoice hears laughter in the clip or in its last 1.5 seconds. The events it named are logged
    (never the words), so a laugh that was not found can be told from a laugh that was not asked about."""
    if recognizer is None or audio is None:
        return False
    whole = recognizer.sound_event(audio)
    tail_event = ""
    tail = int(SAMPLE_RATE * TAIL_SECONDS)
    if whole != "laughter" and audio.size > tail * 1.6:
        tail_event = recognizer.sound_event(audio[-tail:])
    import logging
    logging.getLogger("voiceinput").info("laugh check: whole clip %.1fs = %s%s", audio.size / SAMPLE_RATE,
                                         whole or "-", f", last 1.5s = {tail_event}" if tail_event else "")
    return whole == "laughter" or tail_event == "laughter"


def apply(text: str, insert: str, laughing: bool) -> str:
    """The text with the laugh typed: alone when nothing else was said (or only 哈哈-like characters), else after
    the sentence. Left alone when the text already ends with a laugh."""
    insert = insert.strip()
    if not insert:
        return text
    if not laughing:
        # the recognizer wrote the laugh as words (哈哈哈) and the model named no laughter event: a laugh that is the
        # whole utterance or its ending becomes the chosen text too; 哈哈 in the middle of a sentence is left alone
        if _ONLY_LAUGH.match(text) and re.search(r"[哈呵嘿]{2,}|[xX][dD]", text):
            return insert
        m = re.search(r"[哈呵嘿]{2,}\s*[~～!！。.]*$", text.rstrip())
        if m and m.start() > 0:
            return text[:m.start()].rstrip("，,、 ") + insert
        return text
    if _ONLY_LAUGH.match(text):
        return insert
    if _ENDS_LAUGH.search(text.rstrip()):
        return text
    base = text.rstrip().rstrip("。.，,")
    gap = " " if base[-1:].isascii() and insert[:1].isascii() and base[-1:].isalnum() else ""
    return base + gap + insert
