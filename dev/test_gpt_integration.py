"""Claude/Codex integration: shared settings, prompts and subscription routing."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput import llm  # noqa: E402
from voiceinput.config import Config


def _cfg(**kw):
    base = dict(ai_provider="codex_cli", ai_base_url="", ai_model="", ai_key="", ai_style="polish",
                ai_prompt="MY RULES {format_rule}", ai_claude_model="claude-haiku-5-5")
    base.update(kw)
    return Config(**base)


class IntegrationTests(unittest.TestCase):
    def test_dictation_and_settings_share_all_routing_fields(self):
        from voiceinput.app import App
        from voiceinput.settings import SettingsDialog
        c = _cfg(ai_flash_enabled=True, ai_flash_model="gemini-3.8-flash-low")
        holder = SimpleNamespace(cfg=c)
        self.assertEqual(App._llm_settings(holder), SettingsDialog._llm_settings(holder))
        self.assertEqual(App._llm_settings(holder), llm.settings_from(c))

    def test_settings_from_config(self):
        s = llm.settings_from(_cfg())
        self.assertEqual((s.provider, s.timeout, s.fallback_model, s.prompt),
                         ("codex_cli", 15.0, "claude-haiku-5-5", "MY RULES {format_rule}"))
        c = llm.settings_from(_cfg(ai_provider="claude_cli"))
        self.assertEqual((c.provider, c.timeout), ("claude_cli", 15.0))

    def test_the_prompt_reaches_gpt_and_the_claude_fallback_and_is_in_the_cache_key(self):
        seen = []

        def fake_chat(s, messages, max_tokens=0):
            seen.append((s.provider, messages[0]["content"], s.timeout))
            if s.provider == "claude_cli":
                raise llm.LLMError("Claude 暫時無法回應")
            return "明天開會"

        llm._polished.clear()
        s = llm.settings_from(_cfg())
        with patch.object(llm, "chat", fake_chat):
            out = llm.polish(s, "嗯明天開會", llm.Context(app="line.exe"), [])
        self.assertEqual(out, "明天開會")
        self.assertEqual([p for p, _, _ in seen], ["claude_cli", "codex_cli"])
        self.assertTrue(all(sys_.startswith("MY RULES") for _, sys_, _ in seen))
        self.assertEqual(seen[1][2], 15.0)                      # the fallback waits as long as the first (15s)
        self.assertIn("接續", llm.result_label(s))                # the page says the next service took over
        s2 = llm.settings_from(_cfg(ai_prompt="OTHER {format_rule}"))
        with patch.object(llm, "chat", fake_chat):
            llm.polish(s2, "嗯明天開會", llm.Context(app="line.exe"), [])
        self.assertEqual(len(seen), 4)                          # another prompt is another question, not a cached answer

    def test_three_provider_chain_is_fixed_and_keeps_prompt(self):
        seen = []
        c = _cfg(ai_flash_enabled=True, ai_flash_model="gemini-3.8-flash-low")

        def fake_chat(s, messages, max_tokens=0):
            seen.append((s.provider, s.model_name(), messages[0]["content"]))
            if s.provider != "codex_cli":
                raise llm.LLMError("unavailable")
            return "明天開會"
        with patch.object(llm, "chat", fake_chat):
            s = llm.settings_from(c)
            self.assertEqual(llm.polish(s, "嗯明天開會", llm.Context(), []), "明天開會")
        self.assertEqual([row[0] for row in seen], ["claude_cli", "antigravity_cli", "codex_cli"])
        self.assertEqual(seen[1][1], c.ai_flash_model)
        self.assertTrue(all(row[2].startswith("MY RULES") for row in seen))


if __name__ == "__main__":
    unittest.main()
