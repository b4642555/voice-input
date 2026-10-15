r"""Windows hotkey regression tests; no global hooks or real input are sent.

Run: .venv\Scripts\python.exe -m unittest discover -s dev -p test_hotkey.py -v
"""
import ctypes
import unittest
from unittest.mock import patch

from pynput import keyboard
from pynput._util.win32 import SystemHook

from voiceinput import output
from voiceinput import rules
from voiceinput.hotkey import Hotkeys

INJECTED = 0x10
EXTENDED = 0x01
OWN_TAG = 0x56494E50
DOWN, UP = 0x104, 0x105


class HotkeyTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.other = []
        self.hooks = Hotkeys(lambda name, t: self.events.append(('down', name)),
                             lambda name, t: self.events.append(('up', name)), self.other.append)
        self.hooks._listener = keyboard.Listener()

    def send(self, vk, msg=DOWN, flags=0, tag=0):
        event = keyboard.Listener._KBDLLHOOKSTRUCT(vkCode=vk, flags=flags, dwExtraInfo=tag)
        try:
            return self.hooks._win32_filter(msg, event)
        except SystemHook.SuppressException:
            return 'suppressed'

    def press(self, vk, flags=0, tag=0):
        self.assertEqual(self.send(vk, flags=flags, tag=tag), 'suppressed')
        self.assertEqual(self.send(vk, UP, flags, tag), 'suppressed')

    def test_physical_right_alt_still_works(self):
        self.hooks.set_keys(['alt_r'])
        self.press(0xA5, EXTENDED)
        self.assertEqual(self.events, [('down', 'alt_r'), ('up', 'alt_r')])

    def test_mouse_remapped_right_alt_is_accepted(self):
        self.hooks.set_keys(['alt_r'])
        self.press(0xA5, INJECTED | EXTENDED, 0x123456)
        self.assertEqual(self.events, [('down', 'alt_r'), ('up', 'alt_r')])

    def test_general_alt_accepts_generic_left_and_right_events(self):
        self.hooks.set_keys(['alt'])
        for vk, flags in ((0x12, INJECTED), (0xA4, INJECTED),
                          (0xA5, INJECTED | EXTENDED), (0xA4, 0), (0xA5, EXTENDED)):
            with self.subTest(vk=vk, flags=flags):
                self.events.clear()
                self.press(vk, flags)
                name = 'alt_r' if vk == 0xA5 or flags & EXTENDED else 'alt_l'
                self.assertEqual(self.events, [('down', name), ('up', name)])

    def test_generic_extended_alt_matches_right_alt(self):
        self.hooks.set_keys(['alt_r'])
        self.press(0x12, INJECTED | EXTENDED)
        self.assertEqual(self.events, [('down', 'alt_r'), ('up', 'alt_r')])

    def test_right_alt_binding_leaves_left_alt_alone(self):
        self.hooks.set_keys(['alt_r'])
        self.assertTrue(self.send(0xA4))
        self.assertTrue(self.send(0x12))
        self.assertFalse(self.events)

    def test_specific_right_alt_wins_over_general_alt(self):
        self.hooks.set_keys(['alt', 'alt_r'])
        self.press(0xA5, EXTENDED)
        self.press(0xA4)
        self.assertEqual(self.events, [('down', 'alt_r'), ('up', 'alt_r'),
                                       ('down', 'alt_l'), ('up', 'alt_l')])

    def test_own_injected_bound_key_never_triggers(self):
        self.hooks.set_keys(['alt_r', 'f12'])
        for vk in (0xA5, 0x7B):
            self.assertTrue(self.send(vk, flags=INJECTED, tag=OWN_TAG))
            self.assertTrue(self.send(vk, UP, INJECTED, OWN_TAG))
        self.assertFalse(self.events)
        self.assertFalse(self.other)

    def test_unbound_injected_keys_do_not_end_dictation(self):
        self.hooks.set_keys(['alt_r'])
        self.assertTrue(self.send(0x56, flags=INJECTED))
        self.assertFalse(self.events)
        self.assertFalse(self.other)

    def test_remapped_auto_repeat_produces_one_press(self):
        self.hooks.set_keys(['alt_r'])
        self.send(0xA5, flags=INJECTED | EXTENDED)
        self.send(0xA5, flags=INJECTED | EXTENDED)
        self.send(0xA5, UP, INJECTED | EXTENDED)
        self.assertEqual(self.events, [('down', 'alt_r'), ('up', 'alt_r')])

    def test_general_alt_replay_preserves_the_original_side(self):
        self.hooks.set_keys(['alt'])
        for vk, flags in ((0xA4, 0), (0xA5, EXTENDED), (0x12, EXTENDED)):
            with self.subTest(vk=vk):
                self.press(vk, flags)
                with patch('voiceinput.hotkey.press_key') as press_key:
                    self.hooks.replay('alt_r' if flags & EXTENDED else 'alt_l', 2)
                press_key.assert_called_once_with(0xA5 if flags & EXTENDED else 0xA4, 2)

    def test_general_alt_keeps_overlapping_sides_independent(self):
        self.hooks.set_keys(['alt'])
        self.send(0xA4)
        self.send(0xA5, flags=EXTENDED)
        self.send(0xA4, UP)
        self.send(0xA5, flags=EXTENDED)   # auto-repeat while the right side is still held
        self.assertEqual(self.hooks._held, {'alt_r'})
        self.send(0xA5, UP, EXTENDED)
        self.assertEqual(self.events, [('down', 'alt_l'), ('down', 'alt_r'),
                                      ('up', 'alt_l'), ('up', 'alt_r')])

    def test_later_alt_press_cannot_change_an_earlier_replay(self):
        self.hooks.set_keys(['alt'])
        self.press(0xA4)
        self.press(0xA5, EXTENDED)
        with patch('voiceinput.hotkey.press_key') as press_key:
            self.hooks.replay(self.events[0][1])
            self.hooks.replay(self.events[2][1])
        self.assertEqual([call.args for call in press_key.call_args_list], [(0xA4, 1), (0xA5, 1)])


class AltRuleTests(unittest.TestCase):
    def test_general_alt_rule_matches_each_physical_side(self):
        general = rules.Rule(source=rules.KEY, key='alt', gesture=rules.TAP)
        for key in ('alt_l', 'alt_r'):
            with self.subTest(key=key):
                self.assertEqual(rules.candidates([general], rules.KEY, key, rules.TAP), [general])

    def test_specific_side_rule_has_priority_in_the_same_situation(self):
        general = rules.Rule(source=rules.KEY, key='alt', gesture=rules.TAP)
        right = rules.Rule(source=rules.KEY, key='alt_r', gesture=rules.TAP)
        candidates = rules.candidates([general, right], rules.KEY, 'alt_r', rules.TAP)
        self.assertIs(rules.pick(candidates, False, False), right)

    def test_other_side_and_disabled_rules_do_not_match(self):
        right = rules.Rule(source=rules.KEY, key='alt_r', gesture=rules.TAP)
        general = rules.Rule(source=rules.KEY, key='alt', gesture=rules.TAP, enabled=False)
        self.assertEqual(rules.candidates([right, general], rules.KEY, 'alt_l', rules.TAP), [])


class OutputTagTests(unittest.TestCase):
    def check_tagged(self, sender):
        events = []

        def capture(count, inputs, size):
            for i in range(count):
                raw_tag = inputs[i].ki.dwExtraInfo
                tag = raw_tag if isinstance(raw_tag, int) else ctypes.cast(raw_tag, ctypes.c_void_p).value
                events.append((inputs[i].ki.wVk, inputs[i].ki.dwFlags, tag))
            return count

        with patch.object(output.ctypes.windll.user32, 'SendInput', side_effect=capture):
            sender()
        self.assertTrue(events)
        self.assertTrue(all(tag == OWN_TAG for _, _, tag in events), events)
        return events

    def test_key_replay_is_marked_as_our_own(self):
        events = self.check_tagged(lambda: output.press_key(0xA5, 2))
        self.assertEqual([(vk, flags) for vk, flags, _ in events],
                         [(0xA5, 1), (0xA5, 3), (0xA5, 1), (0xA5, 3)])

    def test_custom_chord_is_marked_as_our_own(self):
        events = self.check_tagged(lambda: output.press_chord(0x12, 0x7B))
        self.assertEqual([vk for vk, _, _ in events], [0x12, 0x7B, 0x7B, 0x12])


if __name__ == '__main__':
    unittest.main()
