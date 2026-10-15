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

    def test_failed_replace_keeps_previous_file_and_cleans_temp(self):
        self.config_path.write_text('{"kept": true}', encoding="utf-8")
        cfg = Config()
        with patch("voiceinput.config.CONFIG_PATH", self.config_path), \
                patch("voiceinput.config.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                cfg.save()
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), '{"kept": true}')
        self.assertEqual(list(self.root.iterdir()), [self.config_path])

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
