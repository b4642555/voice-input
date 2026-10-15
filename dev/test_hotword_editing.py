import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from voiceinput.config import Config
from voiceinput.hotwords import Hotwords
from voiceinput.settings import SettingsDialog


class EditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'hotwords.json'
        self.store = Hotwords(self.path)
        cfg = Config()
        cfg.save = lambda: None
        with patch('voiceinput.settings.audio.list_input_devices', return_value=[]):
            self.ui = SettingsDialog(cfg, self.store, Path(self.tmp.name) / 'memory.json')
        self.addCleanup(self.ui.close)

    def test_add_and_enter_persist_and_change_next_bias(self):
        self.assertTrue(hasattr(self.ui, 'hw_input'), 'hotwords need a direct entry field')
        self.ui.hw_input.setText('程式設計')
        self.ui.hw_input.returnPressed.emit()
        self.assertEqual(self.store.bias(), [('程式設計', 5)])
        self.assertEqual(Hotwords(self.path).bias(), [('程式設計', 5)])
        self.assertEqual(self.ui.hw_input.text(), '')

    def test_rename_preserves_date_and_persists(self):
        self.store.add('Alpha')
        added = self.store.find('Alpha').added
        self.assertTrue(callable(getattr(self.store, 'rename', None)), 'rename must be atomic')
        self.assertTrue(self.store.rename('Alpha', 'Beta'))
        reloaded = Hotwords(self.path)
        self.assertEqual(reloaded.bias(), [('Beta', 3)])
        self.assertEqual(reloaded.find('Beta').added, added)

    def test_rename_duplicate_and_write_failure_preserve_both_words(self):
        self.store.add('Alpha'); self.store.add('Beta')
        self.assertTrue(callable(getattr(self.store, 'rename', None)))
        self.assertFalse(self.store.rename('Alpha', 'Beta'))
        with patch('os.replace', side_effect=PermissionError('locked')):
            self.assertFalse(self.store.rename('Alpha', 'Gamma'))
        self.assertEqual(self.store.bias(), [('Alpha', 3), ('Beta', 3)])
        self.assertEqual(Hotwords(self.path).bias(), [('Alpha', 3), ('Beta', 3)])

    def test_single_chinese_character_is_explained(self):
        self.assertTrue(hasattr(self.ui, 'hw_input'))
        self.ui.hw_input.setText('在')
        self.ui.hw_input.returnPressed.emit()
        self.assertIn('智慧選字', self.ui.hw_feedback.text())
        self.assertEqual(self.store.bias(), [])

    def test_model_rejects_unsupported_word_without_saving(self):
        self.ui.hotword_validator = lambda word: '模型不支援此詞'
        self.ui.hw_input.setText('Unsupported')
        self.ui.hw_input.returnPressed.emit()
        self.assertEqual(self.ui.hw_feedback.text(), '模型不支援此詞')
        self.assertEqual(self.store.bias(), [])

    def test_edit_button_saves_changed_word(self):
        self.store.add('Alpha')
        self.ui.refresh_hotwords()
        self.assertTrue(callable(getattr(self.ui, '_edit_hotword', None)))
        with patch('voiceinput.settings.QInputDialog.getText', return_value=('Beta', True)):
            self.ui._edit_hotword('Alpha')
        self.assertEqual(self.store.bias(), [('Beta', 3)])


if __name__ == '__main__':
    unittest.main()
