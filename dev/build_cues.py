r"""Builds voiceinput/cues/*.wav from the ElevenLabs mp3s in dev/sound-preview (2026-10-13): decoded, made mono,
loudness matched like the preview page at its default volume 0.6 (RMS 0.15 × 0.6 over the audible part, peak at
most 0.95), the tail cut and written as 16-bit wav, which winsound plays without any package. An end sound keeps
its tail down to 40 dB below the loudest 10 ms window (then a 15 ms fade); a start sound only down to 20 dB, then
fades out over 50 ms (2026-10-13): it is cut out of the recording before recognition, and with the longer tails
(0.36-0.60s) the user's first word often came while it was still playing. miniaudio is only needed here:

  .tools\uv.exe run --no-project --with miniaudio --with numpy python dev\build_cues.py

Only the mp3s cues.SOUNDS names are in git (.gitignore skips the rest of dev/sound-preview): add a new one with
`git add -f`.
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


def _trim(x: np.ndarray, rate: int, db: float, fade_s: float) -> np.ndarray:
    """Up to the last 10 ms window within `db` of the loudest, then a linear fade of `fade_s` (inside it for the
    end sounds' 15 ms, after it for the start sounds' 50 ms)."""
    n = int(rate * 0.01)
    win = np.sqrt([np.mean(x[i:i + n] ** 2) for i in range(0, len(x) - n + 1, n)])
    end = (int(np.nonzero(win > win.max() * 10 ** (db / 20))[0][-1]) + 1) * n
    fade = int(rate * fade_s)
    x = x[:min(len(x), end + (fade if fade_s > 0.02 else 0))].copy()
    fade = min(fade, len(x))
    x[len(x) - fade:] *= np.linspace(1, 0, fade)
    return x


def main():
    OUT.mkdir(exist_ok=True)
    for cue_id, _name, *sources in SOUNDS:
        for kind, source in zip(("start", "stop"), sources):
            mp3 = SRC / source
            d = miniaudio.decode_file(str(mp3), output_format=miniaudio.SampleFormat.FLOAT32)
            x = np.frombuffer(d.samples, np.float32).reshape(-1, d.nchannels).mean(axis=1)
            loud = np.abs(x) > 0.005
            rms = float(np.sqrt(np.mean(x[loud] ** 2))) if loud.any() else 1e-4
            gain = min(0.15 * 0.6 / max(rms, 1e-4), 0.95 / max(float(np.abs(x).max()), 1e-4))
            x = _trim(x * gain, d.sample_rate, *((-20, 0.05) if kind == "start" else (-40, 0.015)))
            pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
            with wave.open(str(OUT / f"{cue_id}-{kind}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(d.sample_rate)
                w.writeframes(pcm.tobytes())
            print(f"{cue_id}-{kind}: {len(x) / d.sample_rate:.2f}s gain {gain:.2f}")


if __name__ == "__main__":
    main()
