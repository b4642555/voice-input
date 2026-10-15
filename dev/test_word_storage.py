"""Word-storage regressions using isolated temporary files; no app, models or input.

Run: .venv\\Scripts\\python.exe -B -m unittest discover -s dev -p test_word_storage.py -v
"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voiceinput.hotwords import Hotwords
from voiceinput.memory import Memory


def example(value="再", serial=1):
    return {"key": "zai4", "value": value, "left": ["^"], "right": [], "serial": serial}


class WordStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hotword_path = self.root / "hotword-list.json"
        self.memory_path = self.root / "smart-picks.json"

    def write_hotwords(self, rows):
        self.hotword_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")

    def write_memory(self, rows=(), styles=None):
        self.memory_path.write_text(json.dumps({"examples": list(rows), "styles": styles or {}},
                                               ensure_ascii=False), encoding="utf-8")

    def test_hotword_read_sees_external_changes(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        self.write_hotwords(["Beta"])
        self.assertEqual(store.bias(), [("Beta", 3.0)])
        self.assertEqual(store.weights_for(["Beta"]), {"Beta": 3.0})
        self.assertIsNone(store.find("Alpha"))
        self.assertEqual([h.word for h in store.items], ["Beta"])

    def test_hotword_add_preserves_external_addition(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        self.write_hotwords(["Alpha", "Beta"])
        self.assertTrue(store.add("Gamma"))
        self.assertEqual({h.word for h in Hotwords(self.hotword_path).items}, {"Alpha", "Beta", "Gamma"})

    def test_hotword_pick_and_remove_use_external_version(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        self.write_hotwords([{"word": "Beta", "weight": 1, "source": "picked"}])
        store.picked("Beta", "Gamma")
        self.assertEqual(store.bias(), [("Gamma", 1.5)])
        self.write_hotwords(["Delta", "Gamma"])
        store.remove("Gamma")
        self.assertEqual(store.bias(), [("Delta", 3.0)])

    def test_hotword_invalid_edit_keeps_last_value_and_blocks_writes(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        invalid = [{"word": "Beta", "weight": 3}, {"word": "Gamma"}]
        self.write_hotwords(invalid)
        original = self.hotword_path.read_bytes()
        self.assertEqual(store.bias(), [("Alpha", 3.0)])
        self.assertFalse(store.add("Delta"))
        store.picked("Alpha", "Gamma")
        store.remove("Alpha")
        store.save()
        self.assertEqual(self.hotword_path.read_bytes(), original)
        self.assertEqual(store.bias(), [("Alpha", 3.0)])
        self.assertTrue(getattr(store, "error", None))
        self.write_hotwords(["Beta"])
        self.assertEqual(store.bias(), [("Beta", 3.0)])
        self.assertIsNone(store.error)

    def test_memory_reads_see_external_examples_and_styles(self):
        self.write_memory()
        store = Memory(self.memory_path)
        self.write_memory([example()], {"1:upper": 9})
        self.assertEqual(store.values("zai4"), ["再"])
        self.assertEqual(store.lookup("zai4", ["^"], [], single=True), "再")
        self.assertEqual(store.apply("在"), "再")
        self.assertEqual(store.ordered_variants("alpha")[0], "ALPHA")

    def test_memory_record_preserves_external_examples_and_styles(self):
        self.write_memory()
        store = Memory(self.memory_path)
        self.write_memory([example(serial=8)], {"1:upper": 4})
        store.record("Alpha", 0, 5, "ALPHA")
        reloaded = Memory(self.memory_path)
        self.assertEqual(reloaded.values("zai4"), ["再"])
        self.assertEqual(reloaded.styles, {"1:upper": 5})
        store.record("天在", 1, 2, "在")
        self.assertGreater(store.examples["zai4"][0]["serial"], 8)

    def test_memory_invalid_edit_keeps_last_value_and_blocks_writes(self):
        self.write_memory([example()], {"1:upper": 4})
        store = Memory(self.memory_path)
        self.write_memory([example("在"), {}])
        original = self.memory_path.read_bytes()
        self.assertEqual(store.values("zai4"), ["再"])
        store.record("Alpha", 0, 5, "ALPHA")
        store.record("在", 0, 1, "在")
        store.save()
        self.assertEqual(self.memory_path.read_bytes(), original)
        self.assertEqual(store.values("zai4"), ["再"])
        self.assertEqual(store.styles, {"1:upper": 4})
        self.assertTrue(getattr(store, "error", None))
        self.write_memory([example("在")])
        self.assertEqual(store.values("zai4"), ["在"])
        self.assertIsNone(store.error)

    def test_valid_empty_files_clear_loaded_data(self):
        self.write_hotwords(["Alpha"])
        self.write_memory([example()], {"1:upper": 4})
        hotwords, memory = Hotwords(self.hotword_path), Memory(self.memory_path)
        self.write_hotwords([])
        self.write_memory()
        self.assertEqual(hotwords.bias(), [])
        self.assertEqual(memory.values("zai4"), [])
        self.assertEqual(memory.styles, {})

    def test_empty_bytes_are_invalid_and_recovery_accepts_utf8_bom(self):
        self.write_hotwords(["Alpha"])
        self.write_memory([example()])
        hotwords, memory = Hotwords(self.hotword_path), Memory(self.memory_path)
        self.hotword_path.write_bytes(b"")
        self.memory_path.write_bytes(b"")
        self.assertEqual(hotwords.bias(), [("Alpha", 3.0)])
        self.assertEqual(memory.values("zai4"), ["再"])
        self.assertFalse(hotwords.add("Beta"))
        memory.record("在", 0, 1, "在")
        self.assertEqual(self.hotword_path.read_bytes(), b"")
        self.assertEqual(self.memory_path.read_bytes(), b"")
        self.hotword_path.write_text('["Beta"]', encoding="utf-8-sig")
        self.memory_path.write_text(json.dumps({"examples": [example("在")], "styles": {}}),
                                    encoding="utf-8-sig")
        self.assertEqual(hotwords.bias(), [("Beta", 3.0)])
        self.assertEqual(memory.values("zai4"), ["在"])

    def test_invalid_initial_documents_are_safe_and_remain_untouched(self):
        cases = [(Hotwords, self.hotword_path, ['{}', '"Alpha"', '[1]', '[{"word": 1, "weight": 3}]',
                                              '[{"word": "Alpha", "weight": 999}]',
                                              '[{"word": "Alpha", "weight": NaN}]',
                                              '[{"word": "Alpha", "weight": ' + str(10 ** 400) + '}]',
                                              r'["\ud800"]']),
                 (Memory, self.memory_path, ['[]', 'null', '{"examples": [], "styles": []}',
                                            '{"examples": [1], "styles": {}}',
                                            r'{"examples": [], "styles": {"\ud800": 1}}'])]
        for factory, path, documents in cases:
            for document in documents:
                with self.subTest(factory=factory.__name__, document=document):
                    path.write_text(document, encoding="utf-8")
                    try:
                        store = factory(path)
                    except Exception as exc:
                        self.fail(f"Invalid user data escaped validation: {type(exc).__name__}")
                    self.assertTrue(getattr(store, "error", None))
                    store.save()
                    self.assertEqual(path.read_text(encoding="utf-8"), document)

    def test_explicit_reload_returns_validity(self):
        store = Hotwords(self.hotword_path)
        self.assertTrue(callable(getattr(store, "reload_if_changed", None)))
        self.assertTrue(store.reload_if_changed())
        self.write_hotwords(["Alpha"])
        self.assertTrue(store.reload_if_changed())
        self.hotword_path.write_text("{", encoding="utf-8")
        self.assertFalse(store.reload_if_changed())

    def test_temporary_read_failure_recovers_without_file_change(self):
        self.write_hotwords(["Alpha"])
        self.write_memory([example()])
        hotwords, memory = Hotwords(self.hotword_path), Memory(self.memory_path)
        with patch.object(Path, "read_bytes", side_effect=PermissionError("test read failure")):
            self.assertEqual(hotwords.bias(), [("Alpha", 3.0)])
            self.assertEqual(memory.apply("在"), "再")
            self.assertFalse(hotwords.add("Beta"))
            self.assertFalse(memory.record("在", 0, 1, "在"))
        self.assertTrue(hotwords.reload_if_changed())
        self.assertTrue(memory.reload_if_changed())
        self.assertIsNone(hotwords.error)
        self.assertIsNone(memory.error)
        self.assertTrue(hotwords.add("Beta"))
        self.assertTrue(memory.record("在", 0, 1, "在"))

    def test_temp_cleanup_failure_does_not_escape_rejected_write(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        original = self.hotword_path.read_bytes()
        with patch("os.replace", side_effect=PermissionError("test write failure")), \
                patch.object(Path, "unlink", side_effect=PermissionError("test cleanup failure")):
            try:
                result = store.add("Beta")
            except OSError as exc:
                self.fail(f"Rejected write escaped error handling: {type(exc).__name__}")
        self.assertFalse(result)
        self.assertEqual(self.hotword_path.read_bytes(), original)
        self.assertEqual(store.bias(), [("Alpha", 3.0)])

    def test_missing_files_can_be_created_and_deleted_files_clear_data(self):
        hotwords, memory = Hotwords(self.hotword_path), Memory(self.memory_path)
        self.assertTrue(hotwords.add("Alpha"))
        memory.record("在", 0, 1, "再")
        self.assertEqual(Memory(self.memory_path).values("zai4"), ["再"])
        self.hotword_path.unlink()
        self.memory_path.unlink()
        self.assertEqual(hotwords.bias(), [])
        self.assertEqual(memory.values("zai4"), [])

    def test_replace_failure_preserves_file_and_rolls_back_hotword(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        original = self.hotword_path.read_bytes()
        with patch("os.replace", side_effect=OSError("test write failure")):
            self.assertFalse(store.add("Beta"))
        self.assertEqual(self.hotword_path.read_bytes(), original)
        self.assertEqual(store.bias(), [("Alpha", 3.0)])
        self.assertTrue(store.error)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [self.hotword_path.name])

    def test_unencodable_hotword_input_is_rejected_without_changing_data(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        original = self.hotword_path.read_bytes()
        try:
            self.assertFalse(store.add("\ud800"))
            self.assertFalse(store.picked("Alpha", "\ud800"))
        except UnicodeError as exc:
            self.fail(f"Invalid Unicode escaped error handling: {type(exc).__name__}")
        self.assertEqual(self.hotword_path.read_bytes(), original)
        self.assertEqual(store.bias(), [("Alpha", 3.0)])
        self.assertTrue(store.error)

    def test_replace_failure_preserves_file_and_rolls_back_memory(self):
        self.write_memory([example()], {"1:upper": 4})
        store = Memory(self.memory_path)
        original = self.memory_path.read_bytes()
        with patch("os.replace", side_effect=OSError("test write failure")):
            self.assertFalse(store.record("Alpha", 0, 5, "ALPHA"))
        self.assertEqual(self.memory_path.read_bytes(), original)
        self.assertEqual(store.styles, {"1:upper": 4})
        self.assertTrue(store.error)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [self.memory_path.name])

    def test_hotword_write_detects_external_edit_during_temp_write(self):
        self.write_hotwords(["Alpha"])
        store = Hotwords(self.hotword_path)
        real_fsync = __import__("os").fsync

        def external_edit(fd):
            real_fsync(fd)
            self.write_hotwords(["Delta"])

        with patch("os.fsync", side_effect=external_edit):
            self.assertFalse(store.add("Beta"))
        self.assertTrue(store.error)
        self.assertEqual(Hotwords(self.hotword_path).bias(), [("Delta", 3.0)])
        self.assertEqual(store.bias(), [("Delta", 3.0)])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [self.hotword_path.name])

    def test_memory_write_detects_external_edit_during_temp_write(self):
        self.write_memory([example()], {"1:upper": 4})
        store = Memory(self.memory_path)
        real_fsync = __import__("os").fsync

        def external_edit(fd):
            real_fsync(fd)
            self.write_memory([example("在")], {"1:lower": 7})

        with patch("os.fsync", side_effect=external_edit):
            self.assertFalse(store.record("Alpha", 0, 5, "ALPHA"))
        self.assertTrue(getattr(store, "error", None))
        self.assertEqual(store.values("zai4"), ["在"])
        self.assertEqual(store.styles, {"1:lower": 7})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [self.memory_path.name])


if __name__ == "__main__":
    unittest.main()
