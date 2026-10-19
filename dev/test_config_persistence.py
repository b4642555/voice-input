import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QComboBox

from voiceinput.config import Config
from voiceinput.hotwords import Hotwords
from voiceinput import rules
from voiceinput.settings import SettingsDialog


class ConfigPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config_path = self.root / "config.json"

    def test_config_round_trip_saves_recording_key(self):
        cfg = Config()
        cfg.rules[0].source = rules.KEY
        cfg.rules[0].key = "alt_r"
        with patch("voiceinput.config.CONFIG_PATH", self.config_path):
            cfg.save()
            loaded = Config.load()
        self.assertEqual(loaded.rules[0].key, "alt_r")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_subscription_settings_roundtrip_and_provider_switch(self):
        cfg = Config(ai_provider="codex_cli", ai_claude_model="my-claude-model", ai_prompt="自訂規則")
        with patch("voiceinput.settings.SettingsDialog._check_cli"), \
                patch("voiceinput.settings.SettingsDialog._check_codex"), \
                patch("voiceinput.settings.SettingsDialog._load_catalog"), \
                patch("voiceinput.config.CONFIG_PATH", self.config_path):
            dialog = self._dialog(cfg)
            dialog._apply_ai()
            loaded = Config.load()
            self.assertEqual(loaded.ai_provider, "codex_cli")
            self.assertEqual(loaded.ai_claude_model, "my-claude-model")
            self.assertEqual(loaded.ai_prompt, "自訂規則")
            self.assertEqual(dialog._llm_settings().fallback_model, "my-claude-model")
            self.assertTrue(dialog._key_row.isHidden())
            self.assertFalse(dialog._codex_row.isHidden())
            self.assertFalse(dialog._cli_row.isHidden())

    def test_failed_replace_keeps_previous_file_and_cleans_temp(self):
        self.config_path.write_text('{"kept": true}', encoding="utf-8")
        cfg = Config()
        with patch("voiceinput.config.CONFIG_PATH", self.config_path), \
                patch("voiceinput.config.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                cfg.save()
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), '{"kept": true}')
        self.assertEqual(list(self.root.iterdir()), [self.config_path])

    def test_the_three_model_menus_and_the_flash_switch_roundtrip(self):
        cfg = Config(ai_provider="codex_cli", ai_model="gpt-6-luna", ai_flash_enabled=True,
                     ai_flash_model="gemini-3.8-flash-low", ai_claude_model="haiku")
        with patch("voiceinput.settings.SettingsDialog._check_cli"), \
                patch("voiceinput.settings.SettingsDialog._check_codex"), \
                patch("voiceinput.settings.SettingsDialog._load_catalog"), \
                patch("voiceinput.config.CONFIG_PATH", self.config_path):
            dialog = self._dialog(cfg)
            self.assertEqual(dialog.ai_claude_model.currentData(), "haiku")       # a saved model stays selectable
            self.assertEqual(dialog.ai_gpt_model.currentData(), "gpt-6-luna")
            dialog._set_models(dialog.ai_gpt_model, "gpt-6-luna", [("gpt-6-sol", "gpt-6-sol"), ("gpt-6-luna", "gpt-6-luna")])
            dialog.ai_gpt_model.setCurrentIndex(dialog.ai_gpt_model.findData("gpt-6-sol"))
            loaded = Config.load()
            s = dialog._llm_settings()
            self.assertEqual((loaded.ai_model, loaded.ai_flash_enabled), ("gpt-6-sol", True))
            self.assertEqual((s.model_name(), s.flash_model, s.fallback_model), ("gpt-6-sol", "gemini-3.8-flash-low", "haiku"))
            self.assertTrue(dialog.ai_provider.findData("claude_cli") < 0)        # one subscription entry, no separate Claude

    def test_a_saved_claude_only_setting_becomes_the_subscription_chain(self):
        self.config_path.write_text('{"ai_provider": "claude_cli", "ai_model": "claude-haiku-5-5", "ai_enabled": true}',
                                    encoding="utf-8")
        with patch("voiceinput.config.CONFIG_PATH", self.config_path):
            loaded = Config.load()
        self.assertEqual((loaded.ai_provider, loaded.ai_claude_model, loaded.ai_model),
                         ("codex_cli", "claude-haiku-5-5", ""))

    def _dialog(self, cfg):
        hotwords = Hotwords(self.root / "hotwords.json")
        with patch("voiceinput.settings.audio.list_input_devices", return_value=[]):
            dialog = SettingsDialog(cfg, hotwords, self.root / "memory.json")
        dialog.load_values()
        self.addCleanup(dialog.close)
        return dialog

    def test_settings_reports_success_after_saving(self):
        cfg = Config()
        cfg.rules[0].source = rules.KEY
        cfg.rules[0].key = "alt_r"
        dialog = self._dialog(cfg)
        spy = QSignalSpy(dialog.applied)
        key_combo = next(combo for combo in dialog.findChildren(QComboBox)
                         if combo.findData("alt_r") >= 0)
        with patch("voiceinput.config.CONFIG_PATH", self.config_path):
            key_combo.setCurrentIndex(key_combo.findData("f4"))
            loaded = Config.load()
        self.assertEqual(loaded.rules[0].key, "f4")
        self.assertEqual(dialog.save_status.text(), "")      # a successful save says nothing (the user: 不用寫)
        self.assertEqual(spy.count(), 1)

    def test_gpt_account_is_replaced_and_cleared_without_saving(self):
        with patch.object(SettingsDialog, '_check_cli'), patch.object(SettingsDialog, '_check_codex'):
            dialog = self._dialog(Config())
        with patch.object(dialog, '_save') as save:
            dialog._show_codex_state('ok', 'first@example.test')
            self.assertIn('first@example.test', dialog.ai_codex_state.text())
            dialog._show_codex_state('ok', 'second@example.test')
            self.assertNotIn('first@example.test', dialog.ai_codex_state.text())
            self.assertIn('second@example.test', dialog.ai_codex_state.text())
            for stage in ('unknown', 'out', 'missing'):
                dialog._show_codex_state(stage)
                self.assertNotIn('@', dialog.ai_codex_state.text())
            self.assertTrue(dialog.ai_codex_recheck.isEnabled())
            save.assert_not_called()

    def test_a_copied_gemini_key_is_taken_over(self):
        # 登入 Google 並取得金鑰: the key page opens in the browser, the key that is copied there is picked up
        from PySide6.QtGui import QGuiApplication
        cfg = Config()
        cfg.ai_provider = "gemini"
        dialog = self._dialog(cfg)
        key = "AIza" + "x" * 35
        with patch("voiceinput.settings.QDesktopServices.openUrl") as opened,                 patch.object(dialog, "_test_ai") as tested,                 patch.object(dialog, "_save", return_value=True),                 patch("voiceinput.settings.QGuiApplication.clipboard") as clip:
            clip.return_value.text.return_value = "an old thing in the clipboard"
            dialog._open_key_page()
            self.assertTrue(opened.called)
            dialog._poll_clipboard()
            self.assertEqual(cfg.ai_key, "")                       # nothing new copied yet
            clip.return_value.text.return_value = "我的金鑰 " + key + " 複製了"
            dialog._poll_clipboard()
        self.assertEqual(cfg.ai_key, key)
        self.assertTrue(cfg.ai_enabled)
        self.assertTrue(tested.called)

    def test_settings_reports_save_failure_without_applying(self):
        dialog = self._dialog(Config())
        spy = QSignalSpy(dialog.applied)
        with patch("voiceinput.config.CONFIG_PATH", self.config_path), \
                patch("voiceinput.config.os.replace", side_effect=PermissionError("locked")):
            self.assertFalse(dialog._save())
        self.assertIn("設定未能儲存", dialog.save_status.text())
        self.assertIn("locked", dialog.save_status.text())
        self.assertEqual(spy.count(), 0)


if __name__ == "__main__":
    unittest.main()
