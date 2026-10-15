"""Synthetic COM-like UIA provider tests; no desktop reads or input."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from voiceinput import selection


class Range:
    def __init__(self, text, start=0, end=None):
        self.text, self.start, self.end = text, start, len(text) if end is None else end

    def GetText(self, maximum):
        return self.text[self.start:self.end][:maximum]

    def Clone(self):
        return Range(self.text, self.start, self.end)

    def MoveEndpointByRange(self, endpoint, other, source_endpoint):
        position = other.start if source_endpoint == 0 else other.end
        if endpoint == 1:
            self.end = position
        else:
            self.start = position

    def GetAttributeValue(self, _):
        return False


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.u = NS(UIA_EditControlTypeId=50004, UIA_DocumentControlTypeId=50030,
                    UIA_TextPatternId=10014, UIA_ValuePatternId=10002,
                    UIA_IsReadOnlyAttributeId=40015, IUIAutomationTextPattern="text",
                    IUIAutomationValuePattern="value")
        self.document = Range("前文城市後文")
        self.selected = Range(self.document.text, 2, 4)
        self.text_pattern = NS(DocumentRange=self.document,
                               GetSelection=lambda: NS(Length=1, GetElement=lambda i: self.selected))
        self.text_pattern.QueryInterface = lambda _: self.text_pattern
        self.value = NS(CurrentValue=self.document.text, CurrentIsReadOnly=False)
        self.value.QueryInterface = lambda _: self.value
        self.el = NS(CurrentProcessId=2, CurrentClassName="Edit", CurrentIsPassword=False,
                     CurrentIsEnabled=True, CurrentControlType=50004, GetRuntimeId=lambda: (3,))
        self.pattern_reads = []

        def pattern(kind):
            self.pattern_reads.append(kind)
            return self.text_pattern if kind == self.u.UIA_TextPatternId else self.value
        self.el.GetCurrentPattern = pattern
        self.uia = NS(GetFocusedElement=lambda: self.el)

    def read(self, app="test.exe", hwnd=1):
        self.assertTrue(callable(getattr(selection, "read_input_snapshot", None)), "snapshot reader is missing")
        with patch.object(selection, "_uia", return_value=(self.uia, self.u)), \
                patch.object(selection, "_proc_name", return_value=app), \
                patch.object(selection.ctypes.windll.user32, "GetForegroundWindow", return_value=hwnd):
            return selection.read_input_snapshot()

    def test_text_pattern_captures_exact_control_and_selection_offsets(self):
        snapshot = self.read()
        self.assertEqual((snapshot.scope, snapshot.text, snapshot.start, snapshot.end),
                         ((1, 2, (3,)), "前文城市後文", 2, 4))

    def test_value_only_edit_is_available_for_unique_span_verification(self):
        self.text_pattern = None
        snapshot = self.read()
        self.assertEqual(snapshot.text, "前文城市後文")
        self.assertIsNone(snapshot.start)
        self.assertIsNone(snapshot.end)

    def test_terminal_password_and_noneditable_controls_are_skipped_before_reading_text(self):
        self.assertIsNone(self.read(app="windowsterminal.exe"))
        self.assertEqual(self.pattern_reads, [])
        self.el.CurrentIsPassword = True
        self.assertIsNone(self.read())
        self.assertEqual(self.pattern_reads, [])
        self.el.CurrentIsPassword = False
        self.el.CurrentControlType = 50000
        self.assertIsNone(self.read())
        self.assertEqual(self.pattern_reads, [])

    def test_readonly_and_unknown_writability_are_skipped(self):
        self.value.CurrentIsReadOnly = True
        self.assertIsNone(self.read())
        self.value = None
        self.document.GetAttributeValue = lambda _: object()
        self.assertIsNone(self.read())

    def test_editable_document_is_allowed_but_readonly_document_is_skipped(self):
        self.el.CurrentControlType = 50030
        self.assertIsNotNone(self.read())
        self.value = None
        self.document.GetAttributeValue = lambda _: True
        self.assertIsNone(self.read())

    def test_scope_switch_during_read_discards_snapshot(self):
        reads = [self.el, NS(CurrentProcessId=2, GetRuntimeId=lambda: (4,))]
        self.uia.GetFocusedElement = lambda: reads.pop(0)
        self.assertIsNone(self.read())

    def test_missing_identity_foreground_or_oversize_text_are_skipped(self):
        self.assertIsNone(self.read(hwnd=0))
        self.el.GetRuntimeId = lambda: ()
        self.assertIsNone(self.read())
        self.el.GetRuntimeId = lambda: (3,)
        self.document = Range("a" * 20001)
        self.text_pattern.DocumentRange = self.document
        self.assertIsNone(self.read())

    def test_warmup_does_not_read_a_focused_element(self):
        self.assertTrue(callable(getattr(selection, "initialize_input_snapshot_reader", None)))
        with patch.object(selection, "_uia", return_value=(self.uia, self.u)) as initialize:
            selection.initialize_input_snapshot_reader()
        initialize.assert_called_once()
        self.assertEqual(self.pattern_reads, [])


if __name__ == "__main__":
    unittest.main()
