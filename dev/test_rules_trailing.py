"""Spoken commands said as the last sentences after a pause (rules.split_trailing, 2026-10-16)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput.rules import split_trailing  # noqa: E402

P = ["送出", "換行"]


class TrailingTests(unittest.TestCase):
    def test_after_a_pause(self):
        self.assertEqual(split_trailing("你檢查一下。送出。", P), ("你檢查一下。", ["送出"]))
        self.assertEqual(split_trailing("我要說，送出", P), ("我要說", ["送出"]))
        self.assertEqual(split_trailing("你好。換行", P), ("你好。", ["換行"]))

    def test_said_again_is_one_command(self):
        self.assertEqual(split_trailing("一二三，送出。送出。送出。", P), ("一二三", ["送出"]))
        self.assertEqual(split_trailing("送出。送出。", P), ("", ["送出"]))

    def test_several_commands_run_in_the_order_spoken(self):
        # the user's log, 2026-10-16: 還是沒有送出。你檢查一下。送出。換行。 left 送出 in the text
        self.assertEqual(split_trailing("還是沒有送出。你檢查一下。送出。換行。", P),
                         ("還是沒有送出。你檢查一下。", ["送出", "換行"]))

    def test_a_command_without_a_mark_before_it_is_still_a_command(self):
        # 2026-10-16: 「一二三一二三送出。」 typed 送出 as text
        self.assertEqual(split_trailing("一二三一二三送出。", P), ("一二三一二三", ["送出"]))
        self.assertEqual(split_trailing("報告寫好了送出", P), ("報告寫好了", ["送出"]))
        self.assertEqual(split_trailing("我想把它送出", P), ("我想把它送出", []))
        self.assertEqual(split_trailing("請傳送出", P), ("請傳送出", []))

    def test_a_word_inside_a_sentence_is_text(self):
        self.assertEqual(split_trailing("請先送出表格", P), ("請先送出表格", []))
        self.assertEqual(split_trailing("那個說送出就送出的功能", P), ("那個說送出就送出的功能", []))


class RemovedInstructTests(unittest.TestCase):
    def test_a_saved_ai_instruct_rule_is_dropped_on_load(self):
        from voiceinput import rules
        old = rules.Rule(rules.MOUSE, "mouse_x2", gesture=rules.HOLD, steps=[rules.Step(rules.COMMAND, "instruct")])
        keep = rules.Rule(rules.MOUSE, "mouse_middle", gesture=rules.HOLD, steps=[rules.Step(rules.COMMAND, rules.RECORD)])
        self.assertEqual(rules.migrate([old, keep]), [keep])


if __name__ == "__main__":
    unittest.main()
