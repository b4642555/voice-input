"""Sounds when the recording indicator shows and when the recording ends (2026-10-13, the user's request; picked on
the 一般 tab, the first pair by default). ElevenLabs sound effects generated for the app (dev/sound-preview), turned
into loudness-matched wav by dev/build_cues.py and played with winsound, which needs no package. Played from the
file: winsound cannot play asynchronously from memory (RuntimeError; the first version was silent, 2026-10-13).
"""
import logging
import threading
import winsound
from pathlib import Path

log = logging.getLogger("voiceinput")

DIR = Path(__file__).resolve().parent / "cues"
# (id, name shown, source in dev/sound-preview): the 簡潔 UI and 柔和木質 sets, the user's choice
SOUNDS = [
    ("blip", "Blip", "ui-{kind}-1.mp3"),
    ("chime", "Chime", "ui-{kind}-2.mp3"),
    ("pop", "Pop", "ui-{kind}-3.mp3"),
    ("glass", "Glass", "ui-{kind}-4.mp3"),
    ("bubble", "Bubble", "ui-{kind}-5.mp3"),
    ("triad", "Triad", "ui-{kind}-6.mp3"),
    ("marimba", "Marimba", "wood-{kind}-1.mp3"),
    ("woodblock", "Woodblock", "wood-{kind}-2.mp3"),
    ("kalimba", "Kalimba", "wood-{kind}-3.mp3"),
    ("xylophone", "Xylophone", "wood-{kind}-4.mp3"),
    ("mallet", "Mallet", "wood-{kind}-5.mp3"),
    ("music_box", "Music Box", "wood-{kind}-6.mp3"),
]
DEFAULT = SOUNDS[0][0]
_cache: dict[str, bytes] = {}
_lock = threading.Lock()


def _data(cue_id: str, kind: str) -> bytes | None:
    key = f"{cue_id}-{kind}"
    with _lock:
        if key not in _cache:
            try:
                _cache[key] = (DIR / f"{key}.wav").read_bytes()
            except OSError:
                log.warning("sound %s missing", key)
                return None
        return _cache[key]


def duration(cue_id: str, kind: str) -> float:
    """Seconds the sound lasts (0 for none): the speakers are muted only after the start sound."""
    data = _data(cue_id, kind) if cue_id else None
    if not data:
        return 0.0
    rate = int.from_bytes(data[24:28], "little")
    return (len(data) - 44) / (2 * rate)


def play(cue_id: str, kind: str):
    """kind: start / stop; cue_id "" plays nothing. Asynchronous; a new sound cuts the one playing."""
    if not cue_id or not _data(cue_id, kind):
        return
    try:
        winsound.PlaySound(str(DIR / f"{cue_id}-{kind}.wav"),
                           winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except RuntimeError:
        log.exception("playing %s-%s failed", cue_id, kind)
