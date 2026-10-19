"""Mouse shortcuts act only on an input box: a press the gate refuses passes through (hotkey.Hotkeys, 2026-10-19)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput.hotkey import Hotkeys  # noqa: E402

WM_MBUTTONDOWN, WM_MBUTTONUP = 0x207, 0x208


class GateTests(unittest.TestCase):
    def make(self, allowed):
        events = []
        h = Hotkeys(lambda n, t: events.append(("down", n)), lambda n, t: events.append(("up", n)),
                    mouse_gate=lambda name, x, y: allowed[0])
        h.set_keys({"mouse_middle"})
        h._mouse_listener = NS(suppress_event=lambda: events.append("suppressed"))
        return h, events

    def data(self):
        return NS(flags=0, mouseData=0, pt=NS(x=10, y=10))

    def test_a_press_outside_an_input_box_passes_through_with_its_release(self):
        allowed = [False]
        h, events = self.make(allowed)
        self.assertFalse(h._win32_mouse_filter(WM_MBUTTONDOWN, self.data()))
        self.assertFalse(h.is_bound_mouse(WM_MBUTTONUP, 0, self.data().pt))
        allowed[0] = True                      # the focus moved meanwhile: this release still belongs to the press
        self.assertFalse(h._win32_mouse_filter(WM_MBUTTONUP, self.data()))
        self.assertEqual(events, [])

    def test_a_press_on_an_input_box_is_ours(self):
        allowed = [True]
        h, events = self.make(allowed)
        h._win32_mouse_filter(WM_MBUTTONDOWN, self.data())
        h._win32_mouse_filter(WM_MBUTTONUP, self.data())
        self.assertEqual(events, ["down" if False else ("down", "mouse_middle"), "suppressed", ("up", "mouse_middle"), "suppressed"])


if __name__ == "__main__":
    unittest.main()
