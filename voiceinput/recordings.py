"""Saved recordings for checking recognition with the user's real voice (2026-10-13, the user's request for the
testing period; 一般 tab, off by default, never deleted automatically). Development only: the user will remove it
before release so that users never keep recordings locally.

DATA_DIR/recordings/<YYYYMMDD-HHMMSS-mmm>.wav (16 kHz mono 16-bit) and index.jsonl, one JSON object per line:
- {"type": "dictation", "id", "time", "asr", "fmt", "result", "op", "selection" | "replaced"}: one utterance; asr
  is the raw X-ASR text, fmt the formatted one, result what was typed (or the operation run: op); selection: the
  text it was spoken over, replaced: the text a double tap deleted before it.
- {"type": "pick" | "typed" | "delete_last", "time", "last", ...}: a correction afterwards; last is the id of the
  latest saved dictation, which is usually but not always the one corrected (match by text).
"""
import json
import logging
import threading
import time
import wave

import numpy as np

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

DIR = DATA_DIR / "recordings"
INDEX = DIR / "index.jsonl"
_lock = threading.Lock()
_last: str | None = None


def _append(entry: dict):
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        with INDEX.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def save(audio: np.ndarray, sample_rate: int, **info) -> str | None:
    """Writes the audio and its index line; returns the id."""
    global _last
    try:
        now = time.time()
        rid = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"
        DIR.mkdir(parents=True, exist_ok=True)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(DIR / f"{rid}.wav"), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm.tobytes())
        _append({"type": "dictation", "id": rid, "time": time.strftime("%Y-%m-%d %H:%M:%S"), **info})
        _last = rid
        return rid
    except OSError:
        log.exception("saving the recording failed")
        return None


def note(kind: str, **info):
    """A correction after a dictation (only while recordings are saved)."""
    try:
        _append({"type": kind, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "last": _last, **info})
    except OSError:
        log.exception("saving the recording note failed")
