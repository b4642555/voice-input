"""The last word is a command only after a pause (pause.after_pause, 2026-10-19)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput import pause  # noqa: E402

SR = 16000


def speech(seconds, amp=0.1):
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def quiet(seconds):
    return (0.0004 * np.random.default_rng(1).standard_normal(int(SR * seconds))).astype(np.float32)


class PauseTests(unittest.TestCase):
    def test_a_short_last_word_after_a_pause_is_a_command(self):
        self.assertTrue(pause.after_pause(np.concatenate([speech(2.0), quiet(1.0), speech(0.5), quiet(0.2)])))

    def test_no_pause_before_it_means_it_is_a_word(self):
        # 我說的那個送出
        self.assertFalse(pause.after_pause(np.concatenate([speech(2.0), quiet(0.08), speech(0.5)])))
        self.assertFalse(pause.after_pause(np.concatenate([speech(2.0), quiet(0.2), speech(0.5)])))

    def test_a_half_second_pause_is_too_short(self):
        # the user, 2026-10-20: the pause before 送出 must be at least 0.8s
        self.assertFalse(pause.after_pause(np.concatenate([speech(2.0), quiet(0.5), speech(0.5)])))
        self.assertFalse(pause.after_pause(np.concatenate([speech(2.0), quiet(0.7), speech(0.5)])))
        self.assertTrue(pause.after_pause(np.concatenate([speech(2.0), quiet(0.9), speech(0.5)])))

    def test_a_long_last_stretch_is_a_sentence_not_a_command(self):
        self.assertFalse(pause.after_pause(np.concatenate([speech(3.0), quiet(1.0), speech(3.0)])))

    def test_other_words_joined_to_the_command_are_not_a_pause_before_it(self):
        # 可以嗎，123123測試，就這樣送出: a pause before 就, none before 送出 (the user, 2026-10-19)
        joined = np.concatenate([speech(2.0), quiet(1.0), speech(1.3)])
        self.assertFalse(pause.after_pause(joined, chars=2))
        self.assertTrue(pause.after_pause(np.concatenate([speech(2.0), quiet(1.0), speech(0.5)]), chars=2))

    def test_the_command_said_again_is_longer(self):
        again = np.concatenate([speech(2.0), quiet(1.0), speech(1.7)])        # 送出。送出。送出。
        self.assertFalse(pause.after_pause(again, chars=2))
        self.assertTrue(pause.after_pause(again, chars=6))

    def test_only_the_command_said_counts(self):
        self.assertTrue(pause.after_pause(np.concatenate([quiet(0.3), speech(0.5), quiet(0.3)])))

    def test_silence_is_nothing(self):
        self.assertFalse(pause.after_pause(quiet(2.0)))


if __name__ == "__main__":
    unittest.main()
