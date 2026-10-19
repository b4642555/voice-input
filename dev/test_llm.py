r"""AI 潤稿 helpers that need no network (llm.py).

Run: .venv\Scripts\python.exe -m unittest discover -s dev -p test_llm.py -v
"""
import unittest

from voiceinput import llm


class LlmTests(unittest.TestCase):
    def test_a_screen_name_keeps_its_spelling(self):
        # the Traditional conversion turned the screen's 佳怜 into 佳憐 (2026-10-14)
        self.assertEqual(llm._keep_glossary_spelling("佳憐、凱蕙", ["佳怜", "凱蕙"]), "佳怜、凱蕙")
        self.assertEqual(llm._keep_glossary_spelling("佳怜、凱蕙", ["佳怜"]), "佳怜、凱蕙")

    def test_a_key_decides_only_between_key_services(self):
        self.assertEqual(llm.provider_for_key("AIzaXXXX"), "gemini")
        self.assertEqual(llm.provider_for_key("gsk_XXXX"), "groq")
        self.assertEqual(llm.provider_for_key("sk-or-XXXX"), "openrouter")
        self.assertEqual(llm.provider_for_key(""), "")

    def test_claude_code_needs_no_key(self):
        s = llm.Settings("claude_cli")
        self.assertEqual(s.model_name(), "claude-haiku-5-5")
        self.assertEqual(s.ready(), llm.claude_cli_path() is not None)

    def test_plausible_rejects_an_answer_and_an_empty_result(self):
        self.assertFalse(llm.plausible("幫我寫一封信給客戶", "當然！以下是一封完整的信件範本：" + "親愛的客戶您好，" * 20))
        self.assertFalse(llm.plausible("明天開會", ""))
        self.assertTrue(llm.plausible("嗯那個明天開會", "明天開會"))

    def test_screen_text_goes_to_the_model_as_reference(self):
        ctx = llm.Context(app="line.exe", screen="鹽酥紀 東海總店\n明天晚上七點訂位")
        user = llm._messages("我們明天去", ctx, [], "polish", cli=True)[1]["content"]
        self.assertIn("【畫面文字】", user)
        self.assertIn("鹽酥紀", user)
        self.assertNotIn("【畫面文字】", llm._messages("我們明天去", llm.Context(app="line.exe"), [], "polish")[1]["content"])


if __name__ == "__main__":
    unittest.main()
