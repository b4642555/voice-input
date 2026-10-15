"""App integration uses temporary stores and simulated input only."""
import tempfile
import unittest
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from voiceinput.app import App
from voiceinput import rules
from voiceinput.correction import CorrectionWatcher, InputSnapshot, _replacements
from voiceinput.hotwords import Hotwords
from voiceinput.memory import Memory

class LearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.records=[]
        self.toasts=[]
        self.app=NS(memory=Memory(Path(self.tmp.name)/'memory.json'),
                    toast=NS(show_text=self.toasts.append),
                    copycard=NS(dismiss=lambda: None, show_text=lambda *a: None, active=False),
                    hotwords=Hotwords(Path(self.tmp.name)/'words.json'),
                    _hotword_error=lambda word: '',
                    record=NS(emit=self.records.append),
                    settings=NS(refresh_hotwords=lambda: None))

    def learn(self, text, start, end, new):
        App._learn_corrections(self.app,[NS(text=text,start=start,end=end,new=new,old=text[start:end])])

    def test_submitted_word_is_available_for_next_decode(self):
        self.learn('這個城市很好',2,4,'程式')
        self.assertEqual(self.app.hotwords.bias(), [('程式',5)])
        self.assertIn('城市 → 程式',self.records[0]['edit'])
        self.assertEqual(self.toasts, ['「程式」 已加入字典'])

    def test_one_character_of_a_name_learns_the_whole_name(self):
        # the user's case, 2026-10-14: 林又成跟曾冠維, 成 corrected to 宸
        self.learn('林又成跟曾冠維',2,3,'宸')
        self.assertIn(('林又宸',5), self.app.hotwords.bias())
        self.assertEqual(self.toasts, ['「林又宸」 已加入字典'])

    def test_a_name_inside_a_bigger_edit(self):
        # the user's case, 2026-10-14: 幫你發娛樂城更正宇澄醫生 -> 包你發娛樂城跟吳宇澄 (醫生 deleted)
        from voiceinput.correction import _replacements
        text = '幫你發娛樂城更正宇澄醫生'
        changes = _replacements(text, '包你發娛樂城跟吳宇澄')
        self.assertIn((6, 8, '跟吳'), changes)
        for a, b, new in changes:
            self.learn(text, a, b, new)
        self.assertIn('吳宇澄', [w for w, _ in self.app.hotwords.bias()])
        self.assertNotIn('跟吳', [w for w, _ in self.app.hotwords.bias()])

    def test_a_name_is_learned_without_a_smart_pick(self):
        # 成 -> 宸 learned as a smart pick for 林又宸 later turned 吳宇澄 into 吳宇宸 (2026-10-14)
        self.learn('林又成跟曾冠維',2,3,'宸')
        self.assertEqual(self.app.memory.apply('吳宇澄跟防疫'), '吳宇澄跟防疫')

    def test_one_shared_neighbour_is_not_evidence(self):
        # 成 -> 宸 learned before 跟 must not change 澄 before 跟 elsewhere (the user: 「這邏輯不對」, 2026-10-14)
        self.app.memory.record('林又成跟曾冠維', 2, 3, '宸')
        self.assertEqual(self.app.memory.apply('吳宇澄跟防疫'), '吳宇澄跟防疫')
        self.assertEqual(self.app.memory.apply('吳宇成跟曾冠維'), '吳宇成跟曾冠維')     # nothing on the left
        self.assertEqual(self.app.memory.apply('我跟林又成說'), '我跟林又宸說')

    def test_the_whole_phrase_around_a_corrected_word_is_learned(self):
        # the user's case, 2026-10-14: 貓米發娛樂城 -> 包你發娛樂城, the phrase is what to remember
        self.learn('貓米發娛樂城', 0, 2, '包你')
        self.assertIn('包你發娛樂城', [w for w, _ in self.app.hotwords.bias()])
        self.assertEqual(self.toasts, ['「包你發娛樂城」 已加入字典'])

    def test_single_character_learns_context_without_hotword(self):
        self.learn('他在這裡',1,2,'再')
        self.assertEqual(self.app.hotwords.bias(), [])
        self.assertEqual(self.app.memory.apply('他在這裡'), '他再這裡')

    def test_unsupported_word_is_not_reported_as_learned(self):
        self.app._hotword_error=lambda word: '不支援'
        self.learn('這個城市很好',2,4,'☃☃')
        self.assertEqual(self.app.hotwords.bias(), [])
        self.assertEqual(self.records, [])
        self.assertEqual(self.toasts, [])          # nothing learned, no notice

    def test_dictation_brackets_paste_and_keeps_input_when_snapshot_missing(self):
        for token in (None, 42):
            with self.subTest(token=token):
                events=[]
                watcher=NS(prepare_before_paste=lambda: events.append('before') or token,
                           after_paste=lambda t,s: events.append(('after',t,s)))
                a=NS(corrections=watcher, overlay=NS(hide_overlay=lambda: None), copycard=NS(dismiss=lambda: None),
                     _input_done=lambda: None, session=NS(dictated=lambda *args: None),
                     _after_steps=lambda p: [], _run_steps=lambda *args: None)
                with patch('voiceinput.app.paste_text', side_effect=lambda text: events.append(('paste',text))), \
                     patch('voiceinput.app.foreground', return_value=123):
                    App._dictate(a, '測試', None, 123, None)
                self.assertEqual(events, ['before', ('paste','測試'), ('after',token,'測試')])

    def test_spoken_submit_checks_learning_before_sending_key(self):
        events=[]
        a=NS(corrections=NS(on_key=lambda *args,**kw: events.append(('observe',args,kw))),
             session=NS(on_input=lambda: None))
        with patch('voiceinput.app.press_chord', side_effect=lambda *keys: events.append(('keys',keys))), \
             patch('voiceinput.app.foreground', return_value=123):
            App._steps_job(a,None,[rules.Step(rules.KEYS,'Ctrl+Enter')],None)
        self.assertEqual(events[0][0], 'observe')
        self.assertTrue(events[0][2]['ctrl'])
        self.assertEqual(events[1], ('keys',(0x11,0x0D)))

    def test_learning_failure_never_discards_dictation(self):
        def fail(*args):
            raise RuntimeError('optional observer failed')
        watcher=NS(prepare_before_paste=fail, after_paste=fail)
        a=NS(corrections=watcher, overlay=NS(hide_overlay=lambda: None), copycard=NS(dismiss=lambda: None),
             _input_done=lambda: None, session=NS(dictated=lambda *args: None),
             _after_steps=lambda p: [], _run_steps=lambda *args: None)
        with patch('voiceinput.app.paste_text') as paste, \
             patch('voiceinput.app.foreground', return_value=123), \
             self.assertLogs('voiceinput', level='ERROR'):
            App._dictate(a, '這句必須保留', None, 123, None)
        paste.assert_called_once_with('這句必須保留')

    def test_background_observer_to_persisted_hotword(self):
        field=['']
        learned=threading.Event()
        def save(changes):
            App._learn_corrections(self.app, changes)
            learned.set()
        def read():
            return InputSnapshot(1,2,(3,),field[0])
        watcher=CorrectionWatcher(save, reader=read, warmup=lambda: None)
        self.addCleanup(watcher.close)
        token=watcher.prepare_before_paste()
        self.assertIsNotNone(token)
        field[0]='我們去城市。'
        watcher.after_paste(token,field[0])
        deadline=time.monotonic()+1
        while not watcher.watching and time.monotonic()<deadline:
            time.sleep(0.01)
        self.assertTrue(watcher.watching)
        watcher.on_key(0x41,hwnd=1)
        field[0]='我們去程式。'
        # Allow the documented quiet period and two complete UIA sampling intervals.
        time.sleep(0.7)
        self.assertTrue(watcher.on_key(0x0D,hwnd=1))
        field[0]=''
        self.assertTrue(learned.wait(1))
        self.assertEqual(Hotwords(self.app.hotwords.path).bias(), [('程式',5)])

    def test_long_repeated_text_only_matches_the_small_changed_region(self):
        import difflib
        with patch('voiceinput.correction.difflib.SequenceMatcher', wraps=difflib.SequenceMatcher) as match:
            original='今天' * 4000 + '城市。'
            changed='今天' * 4000 + '程式。'
            self.assertEqual(_replacements(original,changed), [(8000,8002,'程式')])
            self.assertEqual(len(match.call_args.kwargs['a']),2)
            self.assertEqual(len(match.call_args.kwargs['b']),2)

if __name__=='__main__':
    unittest.main()
