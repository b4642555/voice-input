"""Opening smart-picks is independent of Windows JSON associations."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from voiceinput.settings import SettingsDialog
from voiceinput.config import Config
from voiceinput.hotwords import Hotwords
from voiceinput.memory import Memory
from PySide6.QtWidgets import QApplication


class MemoryEditorTests(unittest.TestCase):
    def test_invalid_external_word_file_error_is_visible_in_settings(self):
        qapp = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'smart-picks.json'
            path.write_text('invalid JSON', encoding='utf-8')
            with patch('voiceinput.settings.audio.list_input_devices', return_value=[]):
                dialog = SettingsDialog(Config(), Hotwords(Path(folder) / 'hotwords.json'), path)
            dialog.memory = Memory(path)
            dialog.set_status('就緒')
            self.assertIn('smart-picks.json', dialog.status.text())
            self.assertIn('格式無效', dialog.status.text())
            dialog.close()

    def test_opens_notepad_with_separate_path_argument(self):
        with tempfile.TemporaryDirectory(prefix='voiceinput editor ') as folder:
            path = Path(folder) / 'smart-picks.json'
            text = '{"examples":[],"styles":{"1:upper":2}}'
            path.write_text(text, encoding='utf-8')
            with patch('subprocess.Popen') as launch, patch('os.startfile'):
                SettingsDialog._open_memory(SimpleNamespace(memory_path=path))
            self.assertTrue(launch.called, 'open the editor explicitly without a JSON association')
            args = launch.call_args.args[0]
            self.assertEqual(Path(args[0]).name.lower(), 'notepad.exe')
            self.assertEqual(args[1], str(path))
            self.assertEqual(path.read_text(encoding='utf-8'), text)

    def test_editor_failure_is_reported_without_changing_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'smart-picks.json'
            path.write_text('{"examples":[],"styles":{}}', encoding='utf-8')
            with patch('subprocess.Popen', side_effect=OSError('editor unavailable')), \
                    patch('os.startfile'), patch('voiceinput.settings.QMessageBox.warning') as warning:
                SettingsDialog._open_memory(SimpleNamespace(memory_path=path))
            self.assertTrue(warning.called)


if __name__ == '__main__':
    unittest.main()
