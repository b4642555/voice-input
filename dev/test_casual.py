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


class ZhiTests(unittest.TestCase):
    def test_zhi_before_yao_shi_you_neng_is_zhi_not_the_animal_counter(self):
        from voiceinput.textfmt import format_text
        self.assertEqual(format_text("隻要你來我就開心"), "只要你來我就開心")
        self.assertEqual(format_text("他隻是路過"), "他只是路過")
        self.assertEqual(format_text("隻有我一個人"), "只有我一個人")
        self.assertEqual(format_text("一隻是黑的"), "一隻是黑的")      # a counter after a number
        self.assertEqual(format_text("這隻有點胖"), "這隻有點胖")
        self.assertEqual(format_text("我養了兩隻貓"), "我養了兩隻貓")


if __name__ == "__main__":
    unittest.main()