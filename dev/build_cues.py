r"""Builds voiceinput/cues/*.wav from the ElevenLabs mp3s in dev/sound-preview (2026-10-13): decoded, made mono,
loudness matched like the preview page at its default volume 0.6 (RMS 0.15 × 0.6 over the audible part, peak at
most 0.95), the silent tail cut (after the last 10 ms window within 40 dB of the loudest, then a 15 ms fade: the
start sound's length decides when recording and muting begin) and written as 16-bit wav, which winsound plays
without any package. miniaudio is only needed here:

  .tools\uv.exe run --no-project --with miniaudio --with numpy python dev\build_cues.py
"""
import sys
import wave
from pathlib import Path

import miniaudio
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from voiceinput.cues import SOUNDS  # noqa: E402

SRC = ROOT / "dev" / "sound-preview"
OUT = ROOT / "voiceinput" / "cues"


def _trim(x: np.ndarray, rate: int) -> np.ndarray:
    n = int(rate * 0.01)
    win = np.sqrt([np.mean(x[i:i + n] ** 2) for i in range(0, len(x) - n + 1, n)])
    end = (int(np.nonzero(win > win.max() * 10 ** (-40 / 20))[0][-1]) + 1) * n
    x = x[:end].copy()
    fade = min(int(rate * 0.015), len(x))
    x[len(x) - fade:] *= np.linspace(1, 0, fade)
    return x


def main():
    OUT.mkdir(exist_ok=True)
    for cue_id, _name, source in SOUNDS:
        for kind in ("start", "stop"):
            mp3 = SRC / source.format(kind=kind)
            d = miniaudio.decode_file(str(mp3), output_format=miniaudio.SampleFormat.FLOAT32)
            x = np.frombuffer(d.samples, np.float32).reshape(-1, d.nchannels).mean(axis=1)
            loud = np.abs(x) > 0.005
            rms = float(np.sqrt(np.mean(x[loud] ** 2))) if loud.any() else 1e-4
            gain = min(0.15 * 0.6 / max(rms, 1e-4), 0.95 / max(float(np.abs(x).max()), 1e-4))
            x = _trim(x * gain, d.sample_rate)
            pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
            with wave.open(str(OUT / f"{cue_id}-{kind}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(d.sample_rate)
                w.writeframes(pcm.tobytes())
            print(f"{cue_id}-{kind}: {len(x) / d.sample_rate:.2f}s gain {gain:.2f}")


if __name__ == "__main__":
    main()
