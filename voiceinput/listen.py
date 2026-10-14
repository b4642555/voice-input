"""Continuous listening (連續聽寫, 2026-10-14): the microphone stays on and silero VAD cuts the speech at pauses; each
piece is recognized and typed at once, or run as a command when it is exactly one (換行, 送出, 句號 …).

Why these numbers (dev/streaming, the user's continuous recording of a script mixing text and commands): a 0.3s
pause ends a piece. The user paused 0.37-0.50s before a command said right after text and 0.34-0.69s inside a
sentence while thinking, so no length tells them apart; at 0.3s all 11 commands came out alone, at 0.5s 9 (句號 /
逗號 after a short pause joined the text), at 0.8s 8. The cost is a sentence cut where the user stopped to think;
pieces are typed without sentence-final punctuation, so the cut joins up. Each piece is decoded with 0.5s of audio
before it (not reaching into the previous piece): with 0.25s 換行 / 送出 said alone came out as Huanghang /
Song chu 5 times in 8, with 0.5s none. The streaming X-ASR models were slower to settle (0.7-1.2s after speech vs
~0.4s) and differed from the offline model in 6-8% of characters.
"""
import logging
import queue
import threading
import time

import numpy as np
import sherpa_onnx
import sounddevice as sd

from .asr import SAMPLE_RATE
from .audio import _resolve_device

log = logging.getLogger("voiceinput")

SILENCE = 0.3        # seconds of silence that end a piece
LEAD = 0.5           # seconds of audio decoded before a piece
TAIL = 0.25          # and after it
MAX_PIECE = 30       # seconds; X-ASR fails beyond 40s
_KEEP_IDLE = 1.0     # seconds of audio kept while nobody speaks (the next piece's lead)


class Listener:
    """on_speech(): speech started (listener thread). on_piece(audio): a piece ended (listener thread)."""

    def __init__(self, vad_model: str, on_speech, on_piece):
        self._vad_model = vad_model
        self._on_speech = on_speech
        self._on_piece = on_piece
        self._queue: queue.Queue = queue.Queue()
        self._stream = None
        self._thread = None
        self._discard = False
        self._still_until = 0.0
        self.level = 0.0

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self, device_name: str = ""):
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = self._vad_model
        cfg.silero_vad.min_silence_duration = SILENCE
        cfg.silero_vad.max_speech_duration = MAX_PIECE
        cfg.sample_rate = SAMPLE_RATE
        self._queue = queue.Queue()
        self._discard = False
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            args=(lambda: sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=MAX_PIECE + 10),
                  cfg.silero_vad.window_size))
        self._thread.start()
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                      device=_resolve_device(device_name), callback=self._callback,
                                      blocksize=int(SAMPLE_RATE * 0.03))
        self._stream.start()

    def stop(self, discard: bool = False):
        """Stop the microphone; speech still going on is cut there and handed over as a last piece (unless
        discard: the press that turned it on did something else)."""
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

    def _callback(self, indata, frames, time_info, status):
        mono = indata[:, 0].copy()
        if time.monotonic() < self._still_until:
            mono[:] = 0
            self.level = 0.0
        else:
            self.level = min(1.0, float(np.sqrt(np.mean(mono * mono))) * 12)
        self._queue.put(mono)

    def _run(self, make_vad, win):
        vad = make_vad()
        buf = np.zeros(0, np.float32)   # recent audio; buf[0] is sample number base of what the VAD was fed
        base = prev_end = 0
        rest = np.zeros(0, np.float32)
        speaking = False

        def emit():
            nonlocal buf, base, prev_end
            while not vad.empty():
                seg = vad.front
                start, end = seg.start, seg.start + len(seg.samples)
                vad.pop()
                lo = max(prev_end, base, start - int(LEAD * SAMPLE_RATE))
                hi = min(base + len(buf), end + int(TAIL * SAMPLE_RATE))
                piece = buf[lo - base:hi - base].copy()
                prev_end = end
                buf, base = buf[end - base:], end
                if piece.size:
                    try:
                        self._on_piece(piece)
                    except Exception:
                        log.exception("listen: piece handler failed")

        while True:
            chunk = self._queue.get()
            if chunk is None:
                break
            buf = np.concatenate([buf, chunk])
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
            if not speaking and len(buf) > 2 * _KEEP_IDLE * SAMPLE_RATE:
                cut = len(buf) - int(_KEEP_IDLE * SAMPLE_RATE)
                buf, base = buf[cut:], base + cut
        if not self._discard:
            vad.flush()
            emit()
