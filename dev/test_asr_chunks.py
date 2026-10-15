"""Long audio regressions using an in-memory decoder; no models or microphone."""
import unittest
from types import SimpleNamespace

import numpy as np

from voiceinput.asr import Recognizer, SAMPLE_RATE


class Decoder:
    def __init__(self, texts):
        self.texts, self.inputs, self.specs = iter(texts), [], []

    def create_stream(self, spec=''):
        self.specs.append(spec)
        stream = SimpleNamespace(result=SimpleNamespace(text=''))
        stream.accept_waveform = lambda rate, audio: self.inputs.append(audio.copy())
        return stream

    def decode_stream(self, stream):
        if self.inputs[-1].size > SAMPLE_RATE * 40:
            raise RuntimeError('model input exceeds positional encoding limit')
        stream.result.text = next(self.texts)


class AudioChunkTests(unittest.TestCase):
    def recognizer(self, texts):
        rec = Recognizer.__new__(Recognizer)
        rec.key, rec._pieces = 'xasr', {'▁测', '▁试'}
        rec._rec = Decoder(texts)
        return rec

    def test_long_audio_keeps_all_samples_and_hotwords(self):
        rec = self.recognizer(['第一段。', '尾段。'])
        audio = np.linspace(-.2, .2, SAMPLE_RATE * 45, dtype=np.float32)
        self.assertEqual(rec.recognize(audio, [('測試', 5)]), '第一段。尾段。')
        np.testing.assert_array_equal(np.concatenate(rec._rec.inputs), audio)
        self.assertEqual(len(rec._rec.inputs), 2)
        self.assertTrue(all(x.size <= SAMPLE_RATE * 30 for x in rec._rec.inputs))
        self.assertEqual(rec._rec.specs, ['测 试 :5'] * 2)

    def test_english_chunks_have_word_separator(self):
        rec = self.recognizer(['hello', 'world'])
        self.assertEqual(rec.recognize(np.zeros(SAMPLE_RATE * 45)), 'hello world')

    def test_short_tail_is_decoded_instead_of_skipped(self):
        rec = self.recognizer(['前段', '最後'])
        audio = np.zeros(int(SAMPLE_RATE * 30.1), np.float32)
        self.assertEqual(rec.recognize(audio), '前段最後')
        self.assertTrue(all(x.size >= SAMPLE_RATE * .2 for x in rec._rec.inputs))
        self.assertEqual(sum(x.size for x in rec._rec.inputs), audio.size)

    def test_cut_prefers_a_pause_near_the_chunk_end(self):
        rec = self.recognizer(['前段', '後段'])
        audio = np.full(SAMPLE_RATE * 45, .25, np.float32)
        audio[SAMPLE_RATE * 27:SAMPLE_RATE * 28] = 0
        rec.recognize(audio)
        split_s = rec._rec.inputs[0].size / SAMPLE_RATE
        self.assertGreaterEqual(split_s, 27)
        self.assertLessEqual(split_s, 28)

    def test_short_audio_uses_one_decode(self):
        rec = self.recognizer(['測試。'])
        self.assertEqual(rec.recognize(np.zeros(SAMPLE_RATE)), '測試。')
        self.assertEqual(len(rec._rec.inputs), 1)


if __name__ == '__main__':
    unittest.main()
