"""Sounds when the recording indicator shows and when the recording ends (2026-10-13, the user's request; picked on
the 一般 tab, the first pair by default). ElevenLabs sound effects generated for the app (dev/sound-preview), turned
into loudness-matched wav by dev/build_cues.py and played with winsound, which needs no package. Played from the
file: winsound cannot play asynchronously from memory (RuntimeError; the first version was silent, 2026-10-13).
"""
import logging
import threading
import wave
import winsound
from pathlib import Path

import numpy as np

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


def _samples(cue_id: str, rate: int) -> np.ndarray | None:
    """The start sound at the recording's sample rate."""
    try:
        with wave.open(str(DIR / f"{cue_id}-start.wav")) as w:
            src = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    except (OSError, wave.Error):
        return None
    if src == rate:
        return x
    return np.interp(np.arange(0, len(x) * rate / src) * src / rate, np.arange(len(x)), x).astype(np.float32)


def cancel(audio: np.ndarray, cue_id: str, rate: int, taps: int = 256) -> np.ndarray:
    """The recording with the start sound the microphone heard taken out (2026-10-13). X-ASR heard Bubble, Mallet
    and Kalimba as "Yeah.", Triad as "One" / "Four." when nothing was said. Found by cross-correlation 0.1-0.6s into
    the recording (it plays as the indicator shows, 0.1s, and reaches the microphone ~0.17s later), then a
    least-squares FIR (speaker and room) of `taps` samples is subtracted. On the user's recordings (2026-10-13):
    presses with nothing said that gave text 6/29 -> 2/29 (Triad's One left); 換行 with a start sound mixed in
    178/216 -> 186/216 (83% without any sound). Turning that stretch down to 10% left no text but cut words said over
    it (162/216)."""
    x = _samples(cue_id, rate) if cue_id else None
    if x is None or len(audio) < int(0.1 * rate) + taps:
        return audio
    lo, hi = int(0.1 * rate), int(0.6 * rate)
    seg = audio[: hi + len(x)]
    if len(seg) <= lo:
        return audio
    size = 1 << (len(seg) + len(x)).bit_length()     # cross-correlation by FFT (np.correlate took ~100ms)
    cc = np.fft.irfft(np.fft.rfft(seg, size) * np.conj(np.fft.rfft(x, size)), size)[: len(seg)]
    lag = lo + int(np.argmax(np.abs(cc[lo:min(hi, len(cc))])))
    n = min(len(x) + taps, len(audio) - lag)
    if n <= taps:
        return audio
    # least squares by the normal equations: R w = p (R the cue's autocorrelation, Toeplitz)
    xp = np.pad(x, (0, max(0, n - len(x))))[:n].astype(np.float64)
    target = audio[lag: lag + n].astype(np.float64)
    size = 1 << (2 * n).bit_length()
    fx = np.fft.rfft(xp, size)
    auto = np.fft.irfft(fx * np.conj(fx), size)[:taps]
    cross = np.fft.irfft(np.fft.rfft(target, size) * np.conj(fx), size)[:taps]
    idx = np.arange(taps)
    w = np.linalg.solve(auto[np.abs(idx[:, None] - idx[None, :])] + 1e-6 * auto[0] * np.eye(taps), cross)
    out = audio.copy()
    out[lag: lag + n] = (target - np.convolve(xp, w)[:n]).astype(np.float32)
    return out


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
