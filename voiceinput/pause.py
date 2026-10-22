"""Is the last word set off by a pause? (2026-10-19, the user: 「送出」前面要剛好有停頓才有效。「我說的那個送出」沒有停頓，
就只是文字.)

The recognizer's own punctuation is no proof: it left no mark before 送出 although the user had paused. So the
recording decides: the last stretch of speech must be short (a command, not a sentence) and be preceded by at least
MIN_PAUSE seconds of quiet that follows earlier speech. Plain energy in 20ms frames, no model.
"""
import numpy as np

SAMPLE_RATE = 16000
FRAME = 320                 # 20 ms
MIN_PAUSE = 0.80            # seconds of quiet before the command (the user, 2026-10-20: at least 0.8s)
SECONDS_PER_CHAR = 0.32     # a command is about this long per character (送出 is 0.45s measured); 就這樣送出 is not
SLACK = 0.20
_JOIN = 0.14                # quiet shorter than this inside a word does not split it


def measure(audio: np.ndarray):
    """(seconds of quiet before the last stretch of speech or None when nothing came before it, seconds the last
    stretch lasts), or None when nothing is audible."""
    n = audio.size // FRAME
    if n < 8:
        return None
    rms = np.sqrt(np.mean(audio[:n * FRAME].astype(np.float32).reshape(n, FRAME) ** 2, axis=1))
    peak = float(np.percentile(rms, 95))
    if peak < 0.004:
        return None                                    # nothing audible
    voiced = rms > max(0.004, 0.12 * peak)
    runs, i = [], 0                                    # speech runs, short gaps closed
    while i < n:
        if not voiced[i]:
            i += 1
            continue
        j = i
        while j < n and voiced[j]:
            j += 1
        if runs and (i - runs[-1][1]) * FRAME / SAMPLE_RATE < _JOIN:
            runs[-1] = (runs[-1][0], j)
        else:
            runs.append((i, j))
        i = j
    if not runs:
        return None
    start, end = runs[-1]
    last = (end - start) * FRAME / SAMPLE_RATE
    before = (start - runs[-2][1]) * FRAME / SAMPLE_RATE if len(runs) > 1 else None
    return before, last


def after_pause(audio: np.ndarray, chars: int = 2) -> bool:
    """True when the last voiced stretch of audio is only as long as the command (chars characters) and comes after a
    pause that follows earlier speech. A longer last stretch has other words in it, joined to the command without a
    pause (「就這樣送出」: the pause before 就 is not a pause before 送出, the user, 2026-10-19). True also when nothing but
    the command was said."""
    m = measure(audio)
    if m is None:
        return False
    before, last = m
    if last > max(0.8, SECONDS_PER_CHAR * chars + SLACK):
        return False                                   # more than the command is in the last stretch
    return before is None or before >= MIN_PAUSE
