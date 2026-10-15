r"""Chat-length dictations end without a full stop (the user's rule, 2026-10-14).

Run: .venv\Scripts\python.exe -m unittest discover -s dev -p test_casual.py -v
"""
import unittest

from voiceinput.textfmt import drop_casual_period

CASES = [
    ("好的，我晚點回你。", "好的，我晚點回你"),
    ("今天天氣很好。", "今天天氣很好"),
    ("好的。明天見。", "好的。明天見"),                 # only the final one
    ("我在用 Claude Code。", "我在用 Claude Code"),
    ("See you tomorrow.", "See you tomorrow"),
    ("Sure, I will send it, thanks.", "Sure, I will send it, thanks"),
    ("OK.", "OK"),
    # kept
    ("你明天要來嗎？", "你明天要來嗎？"),
    ("太好了！", "太好了！"),
    ("I think so...", "I think so..."),
    ("嗯…", "嗯…"),
    ("Version 2.0", "Version 2.0"),
    ("我們先開會，然後吃飯，再去公園，最後回家，晚上看電影，明天再說。",
     "我們先開會，然後吃飯，再去公園，最後回家，晚上看電影，明天再說。"),   # five commas: not chat
    ("First, open it, then save it, and close it.", "First, open it, then save it, and close it."),
]


class CasualPeriodTests(unittest.TestCase):
    def test_cases(self):
        for text, expected in CASES:
            with self.subTest(text=text):
                self.assertEqual(drop_casual_period(text), expected)


if __name__ == "__main__":
    unittest.main()
