"""Continuous listening (2026-10-14, continuous-mic branch): the microphone stays on and silero VAD cuts the speech
at pauses into pieces; the app checks each for a spoken command and otherwise adds it to its draft, decoded again
and typed in place (App._piece_job). The audio since the app's last commit is kept for that.

Why these numbers (dev/streaming, the user's continuous recording of a script mixing text and commands): a 0.3s
pause ends a piece. The user paused 0.37-0.50s before a command said right after text and 0.34-0.69s inside a
sentence while thinking, so no length tells them apart; at 0.3s all 11 commands came out alone, at 0.5s 9 (句號 /
逗號 after a short pause joined the text), at 0.8s 8. Each piece is decoded with 0.5s of audio before it (not
reaching into the previous piece): with 0.25s 換行 / 送出 said alone came out as Huanghang / Song chu 5 times in 8,
with 0.5s none. The streaming X-ASR model (shown as it was said, then corrected) was tried the same day and dropped
by the user, with a preview box for the draft.
"""
import bisect
import logging
import queue
import threading
import time
from dataclasses import dataclass

import numpy as np
import sherpa_onnx
import sounddevice as sd

from .asr import SAMPLE_RATE
from .audio import resolve_device

log = logging.getLogger("voiceinput")

SILENCE = 0.3        # seconds of silence that end a piece
LEAD = 0.5           # seconds of audio decoded before a piece
TAIL = 0.25          # and after it
MAX_PIECE = 30       # seconds; X-ASR fails beyond 40s
_KEEP_IDLE = 1.0     # seconds of audio kept before the commit point while nobody speaks


@dataclass
class Piece:
    lo: int          # audio to decode: [lo, hi), in samples (numbered on across listening sessions)
    start: int       # the speech: [start, end)
    end: int
    hi: int


class Listener:
    """on_speech(): speech started. on_piece(Piece): a piece ended. Both are called on the listener thread.

    Sample numbers go on from one listening session to the next (they started at 0 each time until 2026-10-15: a
    piece or commit of the last session still queued then read the new session's audio at its old numbers). The
    audio is kept as a list of the microphone's chunks: appending to one array copied all of it every 30ms."""

    def __init__(self, vad_model: str, on_speech, on_piece):
        self._vad_model = vad_model
        self._on_speech = on_speech
        self._on_piece = on_piece
        self._queue: queue.Queue = queue.Queue()
        self._stream = None
        self._thread = None
        self._discard = False
        self._still_until = 0.0
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._starts: list[int] = []   # sample number of each chunk's first sample
        self._end = 0           # sample number after the last chunk
        self._keep = 0          # audio from here on is kept (the app's draft)
        self.level = 0.0

    @property
    def active(self) -> bool:
        return self._stream is not None

    @property
    def now(self) -> int:
        with self._lock:
            return self._end

    def start(self, device_name: str = ""):
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self._vad_model
        cfg.silero_vad.min_silence_duration = SILENCE
        cfg.silero_vad.max_speech_duration = MAX_PIECE
        cfg.sample_rate = SAMPLE_RATE
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                device=resolve_device(device_name), callback=self._callback,
                                blocksize=int(SAMPLE_RATE * 0.03))
        self._queue = queue.Queue()
        self._discard = False
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            args=(lambda: sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=MAX_PIECE + 10),
                  cfg.silero_vad.window_size, self.now))
        self._thread.start()
        try:
            stream.start()
        except Exception:   # the thread would wait on its queue forever (one more at each press)
            stream.close()
            self._queue.put(None)
            self._thread.join(5)
            self._thread = None
            raise
        self._stream = stream

    def stop(self, discard: bool = False):
        """Stop the microphone; speech still going on is cut there and handed over as a last piece (unless
        discard: the press that turned it on did something else). The audio stays readable."""
        self._discard = discard
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        self.level = 0.0

    def still(self, seconds: float):
        """Silence for the next seconds while the start sound plays: it is not heard as speech."""
        self._still_until = time.monotonic() + seconds
        self.level = 0.0

    def audio(self, lo: int, hi: int) -> np.ndarray:
        """The audio of samples [lo, hi), as far as it is still kept."""
        with self._lock:
            lo, hi = max(lo, self._starts[0] if self._starts else self._end), min(hi, self._end)
            if hi <= lo:
                return np.zeros(0, np.float32)
            i = max(0, bisect.bisect_right(self._starts, lo) - 1)
            j = bisect.bisect_left(self._starts, hi)
            joined = np.concatenate(self._chunks[i:j])
            off = self._starts[i]
            return joined[lo - off:hi - off].copy()

    def keep(self, at: int):
        """A commit: audio before sample `at` is not needed any more."""
        with self._lock:
            self._keep = at

    def _callback(self, indata, frames, time_info, status):
        mono = indata[:, 0].copy()
        if time.monotonic() < self._still_until:
            mono[:] = 0
            self.level = 0.0
        else:
            self.level = min(1.0, float(np.sqrt(np.mean(mono * mono))) * 12)
        self._queue.put(mono)

    def _run(self, make_vad, win, offset: int):
        """offset: the sample number this session starts at (the VAD counts from 0)."""
        vad = make_vad()
        rest = np.zeros(0, np.float32)
        prev_end = offset
        speaking = False

        def emit():
            nonlocal prev_end
            while not vad.empty():
                seg = vad.front
                start, end = offset + seg.start, offset + seg.start + len(seg.samples)
                vad.pop()
                piece = Piece(max(prev_end, start - int(LEAD * SAMPLE_RATE)), start, end,
                              end + int(TAIL * SAMPLE_RATE))
                prev_end = end
                try:
                    self._on_piece(piece)
                except Exception:
                    log.exception("listen: piece handler failed")

        while True:
            chunk = self._queue.get()
            if chunk is None:
                break
            with self._lock:
                self._chunks.append(chunk)
                self._starts.append(self._end)
                self._end += len(chunk)
                total = self._end
            rest = np.concatenate([rest, chunk])
            while len(rest) >= win:
                vad.accept_waveform(rest[:win])
                rest = rest[win:]
                now = vad.is_speech_detected()
                if now and not speaking:
                    try:
                        self._on_speech()
                    except Exception:
                        log.exception("listen: speech handler failed")
                speaking = now
                emit()
            if not speaking:   # forget audio nobody needs
                with self._lock:
                    if prev_end <= self._keep:
                        # nothing said since the commit point: only the lead-in of the next piece is needed (a
                        # silent half hour used to be kept, 2026-10-15)
                        cut = total - int((LEAD + _KEEP_IDLE) * SAMPLE_RATE)
                    else:   # the draft's audio
                        cut = min(self._keep, total) - int(_KEEP_IDLE * SAMPLE_RATE)
                    n = bisect.bisect_right(self._starts, cut) - 1   # chunks wholly before the cut
                    if n > 0:
                        del self._chunks[:n], self._starts[:n]
        if not self._discard:
            vad.flush()
            emit()
