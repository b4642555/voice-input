"""Subscription routing must never call a paid API or lose dictation on failure."""
import unittest
from unittest.mock import patch

from voiceinput import llm


class SubscriptionPolishTests(unittest.TestCase):
    def setUp(self):
        llm._polished.clear()
        self.settings = llm.Settings("codex_cli")
        self.text = "嗯明天下午我們一起開會討論新的計畫"
        self.result = "明天下午我們一起開會，討論新的計畫。"

    def test_claude_success_does_not_call_gpt_or_api(self):
        with patch.object(llm, "_chat_cli", return_value=self.result) as claude, \
                patch.object(llm, "_chat_codex") as gpt, patch.object(llm, "_post") as api:
            self.assertEqual(llm.polish(self.settings, self.text, llm.Context(), []), self.result)
            claude.assert_called_once()
            gpt.assert_not_called()
            api.assert_not_called()
            self.assertEqual(self.settings.used_provider, "claude_cli")

    def test_claude_error_uses_gpt_with_its_model_and_fast(self):
        with patch.object(llm, "_chat_cli", side_effect=llm.LLMError("Claude 逾時")), \
                patch.object(llm, "_chat_codex", return_value=self.result) as gpt:
            self.assertEqual(llm.polish(self.settings, self.text, llm.Context(), []), self.result)
            self.assertEqual(gpt.call_args.args[0].model_name(), "gpt-6-luna")
            self.assertEqual(self.settings.used_provider, "codex_cli")
            self.assertIn("Claude 逾時", self.settings.fallback_reason)
            self.assertIn("Fast", llm.result_label(self.settings))

    def test_gpt_is_always_asked_to_run_fast(self):
        with patch.object(llm.codex_cli, "chat", return_value=self.result) as chat:
            llm._chat_codex(self.settings, [{"role": "user", "content": "x"}])
            self.assertTrue(chat.call_args.kwargs["fast"])

    def test_the_order_is_fixed_claude_flash_gpt(self):
        seen = []

        def fake_chat(s, messages, max_tokens=0):
            seen.append(s.provider)
            raise llm.LLMError("x")
        self.settings.flash_enabled = True
        with patch.object(llm, "chat", fake_chat):
            with self.assertRaises(llm.LLMError):
                llm.polish(self.settings, self.text, llm.Context(), [])
        self.assertEqual(seen, ["claude_cli", "antigravity_cli", "codex_cli"])

    def test_invalid_claude_output_also_falls_back(self):
        with patch.object(llm, "_chat_cli", return_value=""), \
                patch.object(llm, "_chat_codex", return_value=self.result) as gpt:
            self.assertEqual(llm.polish(self.settings, self.text, llm.Context(), []), self.result)
            gpt.assert_called_once()

    def test_all_fail_raise_for_existing_transcript_fallback(self):
        with patch.object(llm, "_chat_cli", side_effect=llm.LLMError("Claude 失敗")), \
                patch.object(llm, "_chat_codex", side_effect=llm.LLMError("GPT 失敗")), \
                patch.object(llm, "_post") as api:
            with self.assertRaises(llm.LLMError):
                llm.polish(self.settings, self.text, llm.Context(), [])
            api.assert_not_called()

    def test_cache_does_not_mix_caret_context(self):
        with patch.object(llm, "_chat_cli", return_value=self.result) as claude:
            llm.polish(self.settings, self.text, llm.Context(before="甲"), [])
            llm.polish(self.settings, self.text, llm.Context(before="乙"), [])
            self.assertEqual(claude.call_count, 2)

    def test_recovery_of_the_first_service_is_not_hidden_by_the_fallback_cache(self):
        with patch.object(llm, "_chat_cli", side_effect=[llm.LLMError("離線"), self.result]) as claude, \
                patch.object(llm, "_chat_codex", return_value=self.result) as gpt:
            llm.polish(self.settings, self.text, llm.Context(), [])
            llm.polish(self.settings, self.text, llm.Context(), [])
            self.assertEqual(claude.call_count, 2)
            self.assertEqual(gpt.call_count, 1)
            self.assertEqual(self.settings.used_provider, "claude_cli")
            self.assertEqual(self.settings.fallback_reason, "")

    def test_custom_models_are_preserved(self):
        self.settings.fallback_model = "claude-test"
        self.settings.model = "gpt-test"
        with patch.object(llm, "_chat_cli", return_value=self.result) as claude:
            llm.polish(self.settings, self.text, llm.Context(), [])
            self.assertEqual(claude.call_args.args[0].model_name(), "claude-test")
        llm._polished.clear()
        with patch.object(llm, "_chat_cli", side_effect=llm.LLMError("離線")), \
                patch.object(llm, "_chat_codex", return_value=self.result) as gpt:
            llm.polish(self.settings, self.text, llm.Context(), [])
            self.assertEqual(gpt.call_args.args[0].model_name(), "gpt-test")

    def test_custom_prompt_is_used_by_every_service_and_invalidates_cache(self):
        self.settings.prompt = "使用我指定的規則：{format_rule}"
        with patch.object(llm, "_chat_cli", side_effect=llm.LLMError("離線")) as claude, \
                patch.object(llm, "_chat_codex", return_value=self.result) as gpt:
            llm.polish(self.settings, self.text, llm.Context(screen="參考畫面"), ["專有名詞"])
            self.assertEqual(claude.call_args.args[1], gpt.call_args.args[1])
            self.assertIn("使用我指定的規則", gpt.call_args.args[1][0]["content"])
        with patch.object(llm, "_chat_cli", return_value=self.result) as claude:
            llm.polish(self.settings, self.text, llm.Context(), [])
            self.settings.prompt = "更新過的整理規則"
            llm.polish(self.settings, self.text, llm.Context(), [])
            self.assertEqual(claude.call_count, 2)

    def test_terminal_and_midline_guards_apply_to_fallback(self):
        with patch.object(llm, "_chat_codex"), \
                patch.object(llm, "_chat_cli", return_value="明天下午我們一起開會，\n討論新的計畫。"):
            out = llm.polish(self.settings, self.text, llm.Context(app="windowsterminal.exe", inserting=True), [])
            self.assertNotIn('\n', out)
            self.assertFalse(out.endswith('。'))


if __name__ == "__main__":
    unittest.main()
