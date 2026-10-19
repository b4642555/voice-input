"""Recording regressions: no hooks, microphone, native input or user data."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

import numpy as np

from voiceinput.app import App, _Press
from voiceinput import rules


class Signal:
    def __init__(self, invoke=False):
        self.values, self.invoke = [], invoke

    def emit(self, value):
        self.values.append(value)
        if self.invoke:
            value()


class Harness:
    # Exercise real state transitions without initializing the tray app.
    on_key_down = App.on_key_down
    on_key_up = App.on_key_up
    _hold_check = App._hold_check
    _decide = App._decide
    _apply = App._apply
    _finish = App._finish
    _records_on = App._records_on
    _rules_for = App._rules_for
    _pending_expired = App._pending_expired
    _start_mic = App._start_mic
    _release_mic = App._release_mic
    _discard_mic = App._discard_mic
    _input_done = App._input_done

    def reload_shared(self):   # 同步 files: nothing shared in this harness
        pass

    def __init__(self):
        self.cfg = NS(rules=[rules.Rule(rules.KEY, 'alt_r', gesture=rules.TAP),
                            rules.Rule(rules.KEY, 'alt_r', gesture=rules.DOUBLE,
                                       steps=[rules.Step(rules.COMMAND, rules.DELETE_DICTATION)])],
                      mic='', mute_while_recording=False)
        self._press = self._pending = self._toggle = self._mic = None
        self._ignored = set()
        self.recognizer = object()
        self.starts = self.stops = 0
        self.recorder = NS(start=self.start, stop=self.stop)
        self.session = NS(deletable=lambda: 'dictation')
        self._pending_timer = NS(start=lambda _: None, stop=lambda: None)
        self.tray = NS(setIcon=lambda _: None)
        self.overlay = NS(hide_overlay=lambda: None)
        self.muter = NS(restore=lambda: None)
        self.micguard = NS(restore=lambda: None, mute=lambda: None, recover=lambda: None)
        self.discord = NS(start=lambda *a: None, stop=lambda: None)
        self._voice_timer = NS(stop=lambda: None, start=lambda: None)
        self.icon_idle = None
        self.hotkeys = NS(replay=lambda *args: None)

    def start(self, _):
        self.starts += 1

    def stop(self):
        self.stops += 1
        return np.zeros(16000, dtype=np.float32)

    def _show_recording(self, _):
        pass

    def _run_steps(self, _, steps, done=None):
        if done:
            done()

    def _close_menu(self):
        pass


class RecordingTests(unittest.TestCase):
    def test_double_delete_stops_the_first_taps_microphone(self):
        a = Harness()
        with patch('voiceinput.app.QTimer.singleShot'), patch('voiceinput.app.foreground', return_value=1), \
                patch('voiceinput.app.ContextProbe', return_value=object()):
            a.on_key_down('alt_r', 10)
            a.on_key_up('alt_r', 10.05)
            self.assertIsNotNone(a._mic)
            a.on_key_down('alt_r', 10.1)
            a.on_key_up('alt_r', 10.15)
        self.assertIsNone(a._mic, 'double delete must release the first tap recording')
        self.assertEqual((a.starts, a.stops), (1, 1))
        self.assertIsNone(a._toggle)

    def test_late_hold_timer_gives_same_result_as_an_on_time_timer(self):
        a = Harness()
        with patch('voiceinput.app.QTimer.singleShot'), patch('voiceinput.app.foreground', return_value=1), \
                patch('voiceinput.app.ContextProbe', return_value=object()):
            a.on_key_down('alt_r', 10)
            a.on_key_up('alt_r', 10.4)
        self.assertIsNone(a._pending, 'a held key must not become a pending tap')
        self.assertIsNone(a._mic)
        self.assertEqual((a.starts, a.stops), (1, 1))

    def test_transcription_exception_leaves_failure_record_and_notice(self):
        notices, hidden, restored = [], [], []
        a = NS(hotwords=NS(bias=lambda: []), record=Signal(), main=Signal(invoke=True),
               overlay=NS(hide_overlay=lambda: hidden.append(True)),
               _input_done=lambda: restored.append(True), _notify=notices.append)
        press = _Press('alt_r', NS(result=lambda: NS(terms=[])), 0, False)
        press.resolved.set()
        rec = NS(recognize=lambda *args: (_ for _ in ()).throw(RuntimeError('model failure')))
        with self.assertLogs('voiceinput', level='ERROR'):
            App._transcribe_job(a, rec, np.zeros(16000, np.float32), press, 1)
        self.assertEqual(len(a.record.values), 1, 'failed recording must remain in history')
        self.assertIn('model failure', a.record.values[0]['error'])
        self.assertEqual(a.record.values[0]['audio_s'], 1.0)
        self.assertTrue(notices)
        self.assertTrue(hidden)
        self.assertTrue(restored)


if __name__ == '__main__':
    unittest.main()
