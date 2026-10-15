"""Correction learning tests: synthetic fields only, no UIA or real input."""
import threading
import time
import unittest

try:
    from voiceinput.correction import CorrectionMonitor, CorrectionWatcher, InputSnapshot
except ImportError:
    CorrectionMonitor = CorrectionWatcher = InputSnapshot = None


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CorrectionMonitor, "correction module is missing")
        self.monitor = CorrectionMonitor()

    def snapshot(self, text, start=None, end=None, identity=(3,), hwnd=1):
        return InputSnapshot(hwnd, 2, identity, text, start, end)

    def arm(self, text="我們去城市。", prefix="", suffix=""):
        before = self.snapshot(prefix + suffix, len(prefix), len(prefix))
        after = self.snapshot(prefix + text + suffix, len(prefix) + len(text), len(prefix) + len(text))
        self.assertTrue(self.monitor.arm(before, after, text, now=0))

    def stable(self, text, start=0.4):
        for at in (start, start + 0.15):
            ticket = self.monitor.ticket(now=at)
            self.assertEqual(self.monitor.observe(self.snapshot(text), ticket, now=at), [])

    def commit(self, text):
        self.stable(text)
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        ticket = self.monitor.ticket(now=0.75)
        return self.monitor.observe(self.snapshot(""), ticket, now=0.75)

    def test_confirmed_replacement_is_learned_only_after_field_clears(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("我們去程式。")
        self.assertEqual([(c.old, c.new) for c in changes], [("城市", "程式")])
        self.assertEqual(changes[0].text[changes[0].start:changes[0].end], "城市")
        self.assertFalse(self.monitor.watching)

    def test_retyped_after_deleting_everything_is_learned_as_one_word(self):
        # the user's case, 2026-10-14: 醉夜狂喜 dictated in LINE, all deleted, 罪夜狂襲 typed, Enter
        self.arm("醉夜狂喜")
        self.monitor.on_key(0x08, hwnd=1, now=0.02)
        self.assertEqual(self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.05), now=0.05), [])
        self.assertTrue(self.monitor.watching)                 # an empty field mid-edit is not the end
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("罪夜狂襲")
        self.assertEqual([(c.old, c.new) for c in changes], [("醉夜狂喜", "罪夜狂襲")])

    def test_slow_field_learns_from_the_latest_read_after_the_last_key(self):
        # LINE answers slowly: one read after the last key, then Enter before a second, settled read
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.monitor.observe(self.snapshot("我們去程式。"), self.monitor.ticket(now=0.25), now=0.4)
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        changes = self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.75), now=0.75)
        self.assertEqual([(c.old, c.new) for c in changes], [("城市", "程式")])

    def test_line_ime_enter_then_send_without_a_read_between(self):
        # the user's case, 2026-10-14: LINE, 注音; the IME's Enter, then the send 0.9s later, no read in between
        self.arm("幫你發娛樂城更正宇澄醫生")
        self.monitor.on_key(0x41, hwnd=1, now=0.1)                    # typing
        self.monitor.observe(self.snapshot("包你發娛樂城跟ㄓ"), self.monitor.ticket(now=0.3), now=0.5)   # composing
        self.monitor.on_key(0x41, hwnd=1, now=0.6)
        self.monitor.observe(self.snapshot("包你發娛樂城跟吳宇澄"), self.monitor.ticket(now=0.8), now=1.4)
        self.monitor.on_key(0x0D, hwnd=1, now=1.5)                    # 注音's Enter: the field stays
        self.monitor.observe(self.snapshot("包你發娛樂城跟吳宇澄"), self.monitor.ticket(now=2.3), now=2.3)
        self.assertTrue(self.monitor.watching)
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=2.4))    # the send
        changes = self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=2.6), now=2.6)
        self.assertIn(("更正", "跟吳"), [(c.old, c.new) for c in changes])

    def test_last_edit_not_covered_by_cache_skips_learning(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.monitor.on_key(0x42, hwnd=1, now=0.58)
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        self.assertEqual(self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.75), now=0.75), [])

    def test_in_flight_read_cannot_cover_a_newer_edit(self):
        self.arm()
        ticket = self.monitor.ticket(now=0.4)
        self.monitor.on_key(0x41, hwnd=1, now=0.45)
        self.assertEqual(self.monitor.observe(self.snapshot("我們去程式。"), ticket, now=0.5), [])
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=0.6))

    def test_read_started_before_quiet_period_is_not_accepted(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        ticket = self.monitor.ticket(now=0.11)
        self.monitor.observe(self.snapshot("我們去程式。"), ticket, now=0.5)
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=0.6))

    def test_ctrl_modifier_does_not_invalidate_ctrl_enter(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.monitor.on_key(0x11, hwnd=1, now=0.58)
        self.assertTrue(self.monitor.on_key(0x0D, ctrl=True, hwnd=1, now=0.6))
        changes = self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.75), now=0.75)
        self.assertEqual(changes[0].new, "程式")

    def test_shift_enter_and_ime_confirmation_do_not_learn(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.assertFalse(self.monitor.on_key(0x0D, shift=True, hwnd=1, now=0.6))
        self.assertEqual(self.monitor.observe(self.snapshot("我們去程式。\n"),
                                             self.monitor.ticket(now=0.8), now=0.8), [])
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        self.assertEqual(self.monitor.observe(self.snapshot("我們去程式。"),
                                             self.monitor.ticket(now=0.8), now=0.8), [])
        self.assertEqual(self.monitor.observe(self.snapshot("我們去程式。"),
                                             self.monitor.ticket(now=1.5), now=1.5), [])

    def test_same_window_other_field_cancels_scope(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.monitor.observe(self.snapshot("我們去程式。", identity=(4,)),
                             self.monitor.ticket(now=0.4), now=0.4)
        self.assertFalse(self.monitor.watching)

    def test_immediate_foreground_check_blocks_enter_after_window_switch(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=9, now=0.6))
        self.assertFalse(self.monitor.watching)

    def test_pointer_event_must_be_covered_before_enter(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.monitor.on_pointer(hwnd=1, now=0.58)
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=0.6))

    def test_tab_alt_escape_and_unknown_foreground_cancel(self):
        for vk in (0x09, 0x12, 0x1B):
            with self.subTest(vk=vk):
                self.arm()
                self.monitor.on_key(vk, hwnd=1, now=0.1)
                self.assertFalse(self.monitor.watching)
        self.arm()
        self.monitor.on_key(0x0D, hwnd=0, now=0.1)
        self.assertFalse(self.monitor.watching)

    def test_an_earlier_dictation_edited_in_a_chat_box_is_learned(self):
        # 2026-10-14: the box still held 貓米發娛樂城 when 我說包你發娛樂城 was dictated; the user fixed both
        self.arm("我說包你發娛樂城", prefix="貓米發娛樂城，")
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("包你發娛樂城，我說包你發娛樂城")
        self.assertEqual([(c.old, c.new) for c in changes], [("貓米", "包你")])

    def test_outer_text_changes_cancel_even_when_corrected_words_match(self):
        # a long document: only the dictation is watched, the text around it must stay
        self.arm(prefix="預先輸入：" + "長" * 1000, suffix="後文")
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.monitor.observe(self.snapshot("其他內容：" + "長" * 1000 + "我們去程式。後文"),
                             self.monitor.ticket(now=0.4), now=0.4)
        self.assertFalse(self.monitor.watching)

    def test_plain_value_snapshot_needs_unique_verified_insertion(self):
        self.assertTrue(self.monitor.arm(self.snapshot("前文後文"), self.snapshot("前文城市後文"), "城市", now=0))
        self.assertFalse(self.monitor.arm(self.snapshot("甲"), self.snapshot("甲甲"), "甲", now=0))
        self.assertFalse(self.monitor.arm(self.snapshot("前文"), self.snapshot("前文其他文字"), "城市", now=0))

    def test_selected_text_replacement_has_verified_offsets(self):
        before = self.snapshot("前文舊字後文", 2, 4)
        after = self.snapshot("前文城市後文", 4, 4)
        self.assertTrue(self.monitor.arm(before, after, "城市", now=0))
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("前文程式後文")
        self.assertEqual((changes[0].old, changes[0].new, changes[0].start), ("城市", "程式", 2))

    def test_case_and_single_character_replacements_keep_offsets(self):
        self.arm("我用 claude。")
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("我用 Claude。")
        self.assertEqual((changes[0].old, changes[0].new), ("claude", "Claude"))
        self.arm("現在再試。")
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        changes = self.commit("現在在試。")
        self.assertEqual((changes[0].old, changes[0].new), ("再", "在"))

    def test_insert_delete_punctuation_and_large_rewrites_are_ignored(self):
        for original, changed in [("我們去城市。", "我們去城市。再說一句。"),
                                  ("我們去城市。", "我們去。"), ("我們去城市。", "我們去城市！"),
                                  ("這是很長的一段不同的句子。", "另外寫一段全新的內容。")]:
            with self.subTest(original=original, changed=changed):
                self.arm(original)
                self.monitor.on_key(0x41, hwnd=1, now=0.1)
                self.stable(changed)
                self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=0.6))

    def test_stale_generation_provider_failure_and_stale_cache_skip(self):
        self.arm()
        old_ticket = self.monitor.ticket(now=0.4)
        self.arm("新一次城市。")
        self.monitor.observe(self.snapshot("我們去程式。"), old_ticket, now=0.5)
        self.assertTrue(self.monitor.watching)
        self.monitor.observe(None, self.monitor.ticket(now=0.6), now=0.6)
        self.assertTrue(self.monitor.watching)                      # a moment without a read is no reason to stop
        self.monitor.observe(None, self.monitor.ticket(now=6.0), now=6.0)
        self.assertFalse(self.monitor.watching)                     # five seconds is
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        # stale: older than LAST_GOOD_AGE (3s since 2026-10-14; LINE's 注音 Enter + send can take ~1.5s)
        self.assertFalse(self.monitor.on_key(0x0D, hwnd=1, now=4))

    def test_edit_after_submit_cancels_pending_learning(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        self.monitor.on_key(0x08, hwnd=1, now=0.61)
        self.assertEqual(self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.9), now=0.9), [])

    def test_read_started_before_enter_cannot_confirm_submission(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        prior_ticket = self.monitor.ticket(now=0.58)
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        self.assertEqual(self.monitor.observe(self.snapshot(""), prior_ticket, now=0.75), [])
        changes = self.monitor.observe(self.snapshot(""), self.monitor.ticket(now=0.9), now=0.9)
        self.assertEqual(changes[0].new, "程式")

    def test_whitespace_only_rich_text_field_counts_as_cleared(self):
        self.arm()
        self.monitor.on_key(0x41, hwnd=1, now=0.1)
        self.stable("我們去程式。")
        self.assertTrue(self.monitor.on_key(0x0D, hwnd=1, now=0.6))
        changes = self.monitor.observe(self.snapshot("\r\n "), self.monitor.ticket(now=0.75), now=0.75)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].new, "程式")


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(CorrectionWatcher, "correction watcher is missing")

    def test_before_snapshot_timeout_does_not_block_paste_or_close(self):
        release = threading.Event()
        watcher = CorrectionWatcher(lambda changes: None, reader=lambda: release.wait(1), warmup=lambda: None)
        self.addCleanup(release.set)
        self.addCleanup(watcher.close)
        started = time.monotonic()
        self.assertIsNone(watcher.prepare_before_paste(timeout=0.01))
        watcher.close()
        self.assertLess(time.monotonic() - started, 0.15)

    def test_idle_poll_interval_fits_inside_pre_paste_budget(self):
        self.assertLess(CorrectionWatcher.POLL_INTERVAL, 0.12)

    def test_before_reader_error_is_optional_and_safe(self):
        def fail():
            raise RuntimeError("synthetic UIA error")
        watcher = CorrectionWatcher(lambda changes: None, reader=fail, warmup=lambda: None)
        self.addCleanup(watcher.close)
        self.assertIsNone(watcher.prepare_before_paste())
        watcher.after_paste(None, "城市")
        watcher.cancel()

    def test_after_snapshot_that_exceeds_deadline_cannot_arm(self):
        completed = threading.Event()
        reads = []

        def reader():
            reads.append(1)
            if len(reads) == 1:
                return InputSnapshot(1, 2, (3,), "", 0, 0)
            time.sleep(0.65)
            completed.set()
            return InputSnapshot(1, 2, (3,), "城市", 2, 2)

        watcher = CorrectionWatcher(lambda changes: None, reader=reader, warmup=lambda: None)
        self.addCleanup(watcher.close)
        token = watcher.prepare_before_paste()
        self.assertIsNotNone(token)
        watcher.after_paste(token, "城市")
        self.assertTrue(completed.wait(1))
        time.sleep(0.02)
        self.assertFalse(watcher.watching)


if __name__ == "__main__":
    unittest.main()
