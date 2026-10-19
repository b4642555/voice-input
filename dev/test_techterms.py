"""The bundled English tech-name list (techterms.py, 2026-10-19): what it fixes and, more important, what it leaves."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voiceinput import techterms  # noqa: E402


class FixesTests(unittest.TestCase):
    def test_misheard_names_are_written_right(self):
        for heard, right in (("我用 cloud code 寫程式", "我用 Claude Code 寫程式"),
                             ("推到 get hub 上", "推到 GitHub 上"),
                             ("我在 chat gpt 問", "我在 ChatGPT 問"),
                             ("用 open AI 的 API", "用 OpenAI 的 API"),
                             ("這是 type script 寫的", "這是 TypeScript 寫的"),
                             ("我在 you tube 看", "我在 YouTube 看"),
                             ("裝 pie torch", "裝 PyTorch"),
                             ("我用 vs code", "我用 VS Code"),
                             ("我用 node js 跑", "我用 Node.js 跑"),
                             ("我在玩 mine craft", "我在玩 Minecraft"),
                             ("我在 get hub 開 cloud code", "我在 GitHub 開 Claude Code"),
                             ("用 notion 記筆記", "用 Notion 記筆記")):
            with self.subTest(heard=heard):
                self.assertEqual(techterms.fix(heard)[0], right)

    def test_ordinary_english_and_near_misses_stay(self):
        for text in ("We use voice input every day", "The cloud service is down", "I phone you later",
                     "I want to open a new window", "Please check the code and send it",
                     "he went to the club last night", "get a hub for the network",
                     "I like to play with the unity of the team",
                     "Speech recognition via the model",            # via is not Vite because another name is near
                     "it opens the file and Claude reads it",          # opens is not OpenAI
                     "只有訂閱 Google Dream",                           # not Google Drive (dream and drive sound apart)
                     "Windows 10 or Windows 11",                        # numbers are not guessed
                     "明天下午三點開會", "我今天去買東西"):
            with self.subTest(text=text):
                self.assertEqual(techterms.fix(text)[0], text)

    def test_text_without_latin_letters_is_returned_at_once(self):
        self.assertEqual(techterms.fix("今天天氣很好"), ("今天天氣很好", 0))

    def test_the_more_used_name_wins_a_tie(self):
        techterms._load()
        self.assertGreater(techterms._rank["Claude"], techterms._rank["Claude Opus"] - 1)
        self.assertEqual(techterms._rank["GitHub"], 3)


if __name__ == "__main__":
    unittest.main()
