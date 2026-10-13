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
# (id, name shown, start and end sources in dev/sound-preview): the 簡潔 UI and 柔和木質 sets, the user's choice;
# Pop's start sound and Kalimba's two were generated again on 2026-10-13 (Pop's was only a click once shortened,
# Kalimba's end sound was too like Xylophone's; picked by the user from three each)
SOUNDS = [
    ("blip", "Blip", "ui-start-1.mp3", "ui-stop-1.mp3"),
    ("chime", "Chime", "ui-start-2.mp3", "ui-stop-2.mp3"),
    ("pop", "Pop", "pop-start-new-1.mp3", "ui-stop-3.mp3"),
    ("glass", "Glass", "ui-start-4.mp3", "ui-stop-4.mp3"),
    ("bubble", "Bubble", "ui-start-5.mp3", "ui-stop-5.mp3"),
    ("triad", "Triad", "ui-start-6.mp3", "ui-stop-6.mp3"),
    ("marimba", "Marimba", "wood-start-1.mp3", "wood-stop-1.mp3"),
    ("woodblock", "Woodblock", "wood-start-2.mp3", "wood-stop-2.mp3"),
    ("kalimba", "Kalimba", "kalimba-start-new-3.mp3", "kalimba-stop-new-2.mp3"),
    ("xylophone", "Xylophone", "wood-start-4.mp3", "wood-stop-4.mp3"),
    ("mallet", "Mallet", "wood-start-5.mp3", "wood-stop-5.mp3"),
    ("music_box", "Music Box", "wood-start-6.mp3", "wood-stop-6.mp3"),
]
DEFAULT = SOUNDS[0][0]
MATCH = 0.6     # below it the start sound is subtracted, not cut out (see remove)
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


def _locate(audio: np.ndarray, x: np.ndarray, rate: int, at: float = 0.1) -> tuple[int, float]:
    """Where the start sound begins in the recording and how well it matches (normalized correlation). It played
    `at` seconds in and reaches the microphone ~0.17s later, so it is looked for in the 0.5s from there."""
    lo, hi = int(at * rate), int((at + 0.5) * rate)
    seg = audio[: hi + len(x)]
    size = 1 << (len(seg) + len(x)).bit_length()     # cross-correlation by FFT (np.correlate took ~100ms)
    cc = np.fft.irfft(np.fft.rfft(seg, size) * np.conj(np.fft.rfft(x, size)), size)[: len(seg)]
    lag = lo + int(np.argmax(np.abs(cc[lo:min(hi, len(cc))])))
    win = audio[lag: lag + len(x)]
    return lag, float(abs(cc[lag]) / (np.linalg.norm(x[: len(win)]) * np.linalg.norm(win) + 1e-9))


def remove(audio: np.ndarray, cue_id: str, rate: int, at: float = 0.1) -> np.ndarray:
    """The recording with the start sound the microphone heard cut out (2026-10-13, the user's choice: the
    recording counts from the end of the sound). X-ASR heard Bubble, Mallet and Kalimba as "Yeah.", Triad as "One"
    when nothing was said. The stretch from where the sound arrives to its end is spliced out with a 10ms
    crossfade, keeping the room noise before it: dropping everything before the end, or silencing it, made X-ASR
    hear short commands wrong with no lead-in (送出 -> Somehow, nothing said -> So). The start sounds were shortened
    for it (dev/build_cues.py). On the user's recordings: presses with nothing said that gave text 13/95 with
    `cancel` -> 2/95 (你好, 哇: probably said); 換行 clips with the sound inserted before the speech (the user waits
    for it) 204/216 (`cancel` 210/216), but spoken over the sound 99/216 (`cancel` 186/216): words said before it
    ends are lost. A weak match (headphones, or speech over it lowers it; recordings with no sound scored up to
    0.36) falls back to `cancel`."""
    x = _samples(cue_id, rate) if cue_id else None
    if x is None or len(audio) <= int(at * rate) + len(x):
        return audio
    lag, match = _locate(audio, x, rate, at)
    if match < MATCH:
        log.info("start sound match %.2f at %.2fs: cancelled, not cut", match, lag / rate)
        return cancel(audio, cue_id, rate, at=at)
    pre, post = audio[:lag], audio[lag + len(x):].copy()
    n = min(int(0.01 * rate), len(pre), len(post))
    if n:
        post[:n] = pre[len(pre) - n:] * np.linspace(1, 0, n) + post[:n] * np.linspace(0, 1, n)
        pre = pre[:len(pre) - n]
    log.info("start sound cut out %.2f-%.2fs (match %.2f)", lag / rate, (lag + len(x)) / rate, match)
    return np.concatenate([pre, post]).astype(np.float32)


def cancel(audio: np.ndarray, cue_id: str, rate: int, taps: int = 256, at: float = 0.1) -> np.ndarray:
    """The start sound subtracted where `remove` cannot cut it: a least-squares FIR (speaker and room) of `taps`
    samples. On the user's recordings (2026-10-13, before `remove`):
    presses with nothing said that gave text 6/29 -> 2/29 (Triad's One left); 換行 with a start sound mixed in
    178/216 -> 186/216 (83% without any sound). Turning that stretch down to 10% left no text but cut words said over
    it (162/216)."""
    x = _samples(cue_id, rate) if cue_id else None
    if x is None or len(audio) <= int(at * rate) + taps:
        return audio
    lag, _match = _locate(audio, x, rate, at)
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
