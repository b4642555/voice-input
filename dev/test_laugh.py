"""Laughter: when it is typed, and how SenseVoice is asked (a fake recognizer; the real one is checked separately)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput import laugh  # noqa: E402


class _Fake:
    def __init__(self, whole="speech", tail="speech"):
        self.whole, self.tail, self.calls = whole, tail, []

    def sound_event(self, audio):
        self.calls.append(audio.size)
        return self.whole if audio.size > 16000 * 3 else self.tail


class LaughTests(unittest.TestCase):
    def test_apply(self):
        a = laugh.apply
        self.assertEqual(a("好啊", "哈哈", True), "好啊哈哈")
        self.assertEqual(a("好啊。", "哈哈", True), "好啊哈哈")
        self.assertEqual(a("好啊，", "😂", True), "好啊😂")
        self.assertEqual(a("", "哈哈", True), "哈哈")
        self.assertEqual(a("哈", "哈哈", True), "哈哈")
        self.assertEqual(a("呵呵呵。", "XD", True), "XD")
        self.assertEqual(a("真的很好笑哈哈", "哈哈", True), "真的很好笑哈哈")     # already ends with a laugh
        self.assertEqual(a("that is great", "XD", True), "that is great XD")
        self.assertEqual(a("好啊", "哈哈", False), "好啊")
        self.assertEqual(a("好啊", "", True), "好啊")

    def test_heard_asks_about_the_whole_clip_and_its_tail(self):
        clip = np.zeros(16000 * 5, dtype=np.float32)
        self.assertTrue(laugh.heard(_Fake("laughter", "speech"), clip))
        self.assertTrue(laugh.heard(_Fake("speech", "laughter"), clip))        # laughing at the end of a sentence
        self.assertFalse(laugh.heard(_Fake(), clip))
        short = np.zeros(16000 * 2, dtype=np.float32)
        f = _Fake()
        self.assertFalse(laugh.heard(f, short))
        self.assertEqual(len(f.calls), 1)                                       # a short clip has no separate tail
        self.assertFalse(laugh.heard(None, clip))


if __name__ == "__main__":
    unittest.main()
