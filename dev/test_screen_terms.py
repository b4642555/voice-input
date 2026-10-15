r"""Screen terms (screen_terms.py): names and topic words picked from the screen, and same-sounding fixes.

Run: .venv\Scripts\python.exe -m unittest discover -s dev -p test_screen_terms.py -v
"""
import unittest

from voiceinput import candidates, screen_terms as st

CHAT = """張雨薇：明天的薔薇計畫會議幾點？
我：下午三點，在信義區的會議室
張雨薇：好，我會帶薔薇計畫的簡報。順便問一下，陳柏翰會來嗎？
我：陳柏翰說他會晚點到
今天天氣很好，我們去公園散步吧。這是一個很好的城市。程式寫好了嗎？程式還在測試。"""


class ScreenTermsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates.load()

    def test_names_and_topics_are_picked(self):
        terms = st.terms(CHAT, "LINE - 張雨薇")
        for word in ("張雨薇", "陳柏翰", "薔薇計畫"):
            self.assertIn(word, terms)

    def test_common_words_and_fragments_are_not_terms(self):
        terms = st.terms(CHAT + "\n先打開設定頁\n先打開設定頁\n我我我們\n我我我們")
        for word in ("城市", "程式", "會議室", "打開設", "定頁", "我我"):
            self.assertNotIn(word, terms)

    def test_seen_once_is_not_enough_unless_in_the_title(self):
        self.assertNotIn("林岱萱", st.terms("林岱萱說好"))
        self.assertIn("林岱萱", st.terms("林岱萱說好", "林岱萱 - LINE"))

    def test_same_sound_is_fixed(self):
        terms = st.terms(CHAT, "LINE - 張雨薇")
        self.assertEqual(st.fix("我明天要跟張玉薇確認強薇計畫", terms), ("我明天要跟張雨薇確認薔薇計畫", 2))
        self.assertEqual(st.fix("陳伯翰會晚點到", terms)[0], "陳柏翰會晚點到")   # 柏 is bo as well as bai

    def test_common_words_are_never_replaced(self):
        self.assertEqual(st.fix("這是一個很好的城市", ["程式", "成式"]), ("這是一個很好的城市", 0))

    def test_both_spellings_on_screen_are_left_alone(self):
        self.assertEqual(st.fix("張玉薇和張雨薇", ["張雨薇", "張玉薇"])[1], 0)

    def test_a_name_seen_once_fixes_a_same_sounding_miss(self):
        screen = "8.5 萬次觀看 | Reel by 平偉本色\n點選此處以開啟此連結。"      # as OCR reads LINE
        self.assertNotIn("平偉本色", st.terms(screen))          # once: not enough to bias the recognizer
        loose = st.fix_candidates(screen)
        self.assertEqual(st.fix("我說評委本色的時候", st.terms(screen), loose)[0], "我說平偉本色的時候")
        self.assertEqual(st.fix("今天評審很嚴格", [], loose)[1], 0)

    def test_recent_pieces_seen_once_are_kept_newest_first(self):
        screen = "4300 一萍 平丁天花板\n包樑 19000\n儲存 另存新檔 分享"
        recent = st.recent(screen)
        self.assertLess(recent.index("包樑"), recent.index("平丁天花板"))
        self.assertEqual(st.fix("平丁天花板跟包糧", recent, st.fix_candidates(screen))[0], "平丁天花板跟包樑")


    def test_long_names_allow_one_close_sound(self):
        screen = "輕井澤鍋物 文心南二店 · 向心里, 臺中市南"
        recent = st.recent(screen)
        self.assertIn("文心南二店", recent)                      # not cut at 二
        loose = st.fix_candidates(screen)
        self.assertEqual(st.fix("清景澤鍋文西南二殿", recent, loose)[0], "輕井澤鍋文心南二店")
        self.assertEqual(st.fix("我在西南部", recent, loose)[1], 0)   # short: exact sound only


    def test_ocr_line_run_together(self):
        screen = "輕井澤鍋物文心南二店向心里 , 臺中市南"      # OCR drops the spaces between the parts
        terms, loose = st.terms(screen) + st.recent(screen), st.fix_candidates(screen)
        self.assertEqual(st.fix("清景澤波物，文心南二殿，向心里。", terms, loose)[0], "輕井澤鍋物，文心南二店，向心里。")
        self.assertEqual(st.fix("一平丁天花板", ["平丁天花板", "一萍"])[0], "一平丁天花板")   # written as on screen


    def test_all_but_one_sound_with_matching_ends(self):
        screen = "下週去輕井澤玩"                           # a rare dictionary word inside a longer phrase
        names = st.terms(screen) + st.recent(screen)
        self.assertIn("輕井澤", names)
        for heard in ("清引影澤", "清引澤"):
            self.assertEqual(st.fix(heard, names)[0], "輕井澤")
        for plain in ("請你選擇", "情況選擇很多", "平靜天花板"):
            self.assertEqual(st.fix(plain, names + ["主機版"])[1], 0)


    def test_two_stray_characters_without_a_surname_are_not_a_name(self):
        t = "話說你最近會想接老虎機案嗎"      # 按鈕 -> 案 learned 機案 as a hotword (2026-10-15)
        k = t.index("案")
        self.assertEqual(st.name_around(t, k, k + 1), "")
        self.assertEqual(st.name_around("林又成跟曾冠維", 2, 3), "林又成")


if __name__ == "__main__":
    unittest.main()


class ScreenTextTests(unittest.TestCase):
    """What counts as screen text (2026-10-14, the user's LINE roster)."""

    def test_the_draft_is_not_screen_evidence(self):
        screen = "佳怜\n凱慧\n雅婷\n佳琳凱慧雲庭晴雲阿嵐 , 餐家雅婷\n台新銀行"     # OCR also misread the draft
        kept = st.without_draft(screen, "佳琳凱慧雲庭晴雲阿嵐，宋家雅婷")
        self.assertNotIn("餐家", kept)
        for name in ("佳怜", "凱慧", "雅婷"):                  # names the draft also holds stay
            self.assertIn(name, kept)

    def test_one_simplified_looking_character_does_not_convert_a_traditional_screen(self):
        roster = "6/15晚場（排球地帶）\n女\n佳怜\n凱慧\n庭筠\n阿嵐\n宋嘉\n雅婷\n饅頭\n芋頭"
        self.assertIn("佳怜", st.normalize_screen(roster))
        self.assertIn("張雨薇", st.normalize_screen("张雨薇发来的文件在这里，请查看张雨薇的备注"))
