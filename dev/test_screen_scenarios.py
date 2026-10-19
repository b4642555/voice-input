r"""Screen-term scenarios (2026-10-13, the user's goal: 「改到超級準，各種奇葩情境都可以過關，英文也要」).

Each case: what the screen shows, what recognition + formatting produced, and what must be typed. The cases that
must not change matter as much as the fixes: a wrong change is worse than a missed one.

Run: .venv\Scripts\python.exe -m unittest discover -s dev -p test_screen_scenarios.py -v
"""
import unittest

from voiceinput import candidates
from voiceinput import screen_terms as st

LINE_CHAT = """張雨薇：明天的薔薇計畫會議幾點？
我：下午三點，在信義區的會議室
張雨薇：好，我會帶薔薇計畫的簡報。順便問一下，陳柏翰會來嗎？
我：陳柏翰說他會晚點到"""
MAP_PREVIEW = "輕井澤鍋物文心南二店向心里 , 臺中市南\n利用「Google 地圖」尋找本地商家、檢視地圖\n或規劃行車路線。"
REEL = "8.5 萬次觀看 | Reel by 平偉本色\n點選此處以開啟此連結。"
QUOTE = "4300 一萍 平丁天花板\n包樑 19000\n儲存 另存新檔 分享 傳送至Keep筆記"
TRIP = "下週去輕井澤玩，住在星野集團的虹夕諾雅"
CODE = """Claude Code is running in the terminal. Open the VoiceInput settings.
Push to GitHub after the tests pass. We compare OpenAI and Anthropic models; ChatGPT and GPT-4o too.
The iPhone 15 build uses Kubernetes. Instagram and YouTube links. Visual Studio Code is open."""
SIMPLIFIED = "张雨薇发来的文件在这里，请查看张雨薇的备注"
ROSTER = chr(10).join(["6/15晚場（排球地帶 19:00-22:00", "女", "佳怜", "凱蕙", "芷柔", "阿霖", "宋嘉", "雅茹", "卉青",
                       "饅頭", "芋頭", "男", "琮浩", "朝棟", "智傑"])
CHAT_LIST = chr(10).join(["曾冠維", "常常搞到後來 發現跟別人很像", "裝潢 (3)", "林又宸", "用講話的聽寫功能",
                          "台新銀行", "呂昌衡 HAM", "我想問阿良奇的問題？", "下午3:06"])
OCR_ODD = "Al平凡人創業學院在 lnstagram: GitHub 上爆紅的四個 A gent 工具"

FIX = [
    # (screen, heard, expected)
    (LINE_CHAT, "我明天要跟張玉薇確認強薇計畫", "我明天要跟張雨薇確認薔薇計畫"),
    (LINE_CHAT, "陳伯翰會晚點到", "陳柏翰會晚點到"),
    (LINE_CHAT, "陳博瀚說他會晚點到。", "陳柏翰說他會晚點到。"),
    (LINE_CHAT, "張宇威的報告寫好了", "張雨薇的報告寫好了"),
    (LINE_CHAT, "幫我問雨威明天有沒有空", "幫我問雨薇明天有沒有空"),            # given name alone
    (MAP_PREVIEW, "清景澤波物，文心南二殿，向心里。", "輕井澤鍋物，文心南二店，向心里。"),
    (MAP_PREVIEW, "你說清景澤波物，文心南二殿向心里。", "你說輕井澤鍋物，文心南二店向心里。"),
    (REEL, "我說評委本色的時候", "我說平偉本色的時候"),
    (QUOTE, "4310 平丁天花板跟包糧 19000。", "4310 平丁天花板跟包樑 19000。"),
    (TRIP, "我們去清引影澤吃飯", "我們去輕井澤吃飯"),
    (TRIP, "清引澤", "輕井澤"),
    (TRIP, "住在星夜集團", "住在星野集團"),
    (SIMPLIFIED, "張宇薇的備註", "張雨薇的備註"),                          # simplified screen, traditional output
    # English
    (CODE, "我在用 cloud code 寫程式", "我在用 Claude Code 寫程式"),
    (CODE, "push 到 get hub", "push 到 GitHub"),
    (CODE, "打開 voice input 的設定", "打開 VoiceInput 的設定"),
    (CODE, "open ai 跟 anthropic 的模型", "OpenAI 跟 Anthropic 的模型"),
    (CODE, "問 chat GPT", "問 ChatGPT"),
    (CODE, "GPT 4o 很快", "GPT-4o 很快"),
    (CODE, "i phone 15 的版本", "iPhone 15 的版本"),
    (CODE, "用 visual studio code 打開", "用 Visual Studio Code 打開"),
    (CODE, "看 youtube 的影片", "看 YouTube 的影片"),
    (CODE, "Claude code is great", "Claude Code is great"),
    (CODE, "push 到 git hub", "push 到 GitHub"),
    (CODE, "push 到 github", "push 到 GitHub"),
    (CODE, "看 you tube", "看 YouTube"),
    (CODE, "用 kubernetes 部署", "用 Kubernetes 部署"),
    (CODE, "Cloud Code 開好了", "Claude Code 開好了"),
    (CODE, "我在用 clock code 寫程序。", "我在用 Claude Code 寫程序。"),          # real X-ASR output (TTS)
    (CODE, "用 cover Nets 部署到 iPhone。", "用 Kubernetes 部署到 iPhone。"),     # real X-ASR output (TTS)
    (CODE, "用 covernet 部署到 iPhone", "用 Kubernetes 部署到 iPhone"),         # the same, with the screen's bias
    (MAP_PREVIEW, "我們約在輕井澤國物。", "我們約在輕井澤鍋物。"),     # real X-ASR output (TTS)
    (QUOTE, "一瓶的平丁天花板要包樑 19000。", "一萍的平丁天花板要包樑 19000。"),   # the user's case, 2026-10-14
    (CODE, "Anthropic 的 club 很強。", "Anthropic 的 Claude 很強。"),        # real X-ASR output (TTS)
    (ROSTER, "佳怜開會阿霖", "佳怜凱蕙阿霖"),                   # 開會 is common, but names around it: 凱蕙
    (ROSTER, "佳琳凱蕙雲亭晴霞阿霖，宋家雅茹", "佳琳凱蕙雲亭晴霞阿霖，宋嘉雅茹"),
    (CHAT_LIST, "林若晨，曾冠偉，陸寶寶，遊戲場外閒聊群", "林又宸，曾冠維，陸寶寶，遊戲場外閒聊群"),   # 2026-10-14
    # near sounds of a name on screen (the user's rule, 2026-10-14)
    (CHAT_LIST, "曾冠偉", "曾冠維"),
    (CHAT_LIST, "曾冠位", "曾冠維"),
    (CHAT_LIST, "曾關為", "曾冠維"),
    (CHAT_LIST, "鄭冠維", "曾冠維"),
    (CHAT_LIST, "跟曾冠偉說", "跟曾冠維說"),
    (CHAT_LIST, "林又成", "林又宸"),
    (CHAT_LIST, "林友辰", "林又宸"),
    (CHAT_LIST, "林佑臣", "林又宸"),
    (CHAT_LIST, "鄰又宸", "林又宸"),
    (CHAT_LIST, "呂常衡", "呂昌衡"),
    (CHAT_LIST, "旅昌衡", "呂昌衡"),
    # names heard as a Chinese transliteration
    (CODE, "我在用克勞德寫程式", "我在用 Claude 寫程式"),
    (CODE, "推到吉特哈布", "推到 GitHub"),
    (CODE, "用庫伯內提斯部署", "用 Kubernetes 部署"),
    (CODE, "因斯塔格拉姆上很紅", "Instagram 上很紅"),
    # tricky Chinese
    (LINE_CHAT, "張玉薇，你好", "張雨薇，你好"),
    (LINE_CHAT, "張玉薇跟張玉薇", "張雨薇跟張雨薇"),
    (LINE_CHAT, "請轉告張玉薇。", "請轉告張雨薇。"),
    (LINE_CHAT, "跟張玉薇開 meeting", "跟張雨薇開 meeting"),
    (LINE_CHAT, "薔薇計劃的簡報", "薔薇計畫的簡報"),
    (LINE_CHAT + "\n" + CODE, "張玉薇用 cloud code 寫的", "張雨薇用 Claude Code 寫的"),
]

KEEP = [
    (LINE_CHAT, "這是一個很好的城市"),
    (LINE_CHAT, "程式還在測試"),
    (LINE_CHAT, "今天天氣很好"),
    (LINE_CHAT, "張雨薇明天會來"),                                  # already right
    (MAP_PREVIEW, "我在西南部"),
    (MAP_PREVIEW, "我們約在台中市"),                                 # 台 is the user's spelling, never 臺
    (QUOTE, "一平丁天花板"),
    (TRIP, "請你選擇"),
    (TRIP, "情況選擇很多"),
    (TRIP, "我們去輕井澤"),
    (REEL, "今天評審很嚴格"),
    (REEL, "為了本色演出"),
    ("", "什麼都沒有的畫面"),
    ("", "cloud code"),
    ("張雨薇 章宇威", "張宇薇來了"),                                 # two names sound alike: unsure, leave it
    # English that must stay
    (CODE, "upload to the cloud"),
    (CODE, "the code is clean"),
    (CODE, "open the door"),
    (CODE, "I get it"),
    (CODE, "我們在 open 一個 issue"),
    (CODE, "python 很好用"),
    (OCR_ODD, "Instagram 上很紅"),                                   # the screen's lnstagram is an OCR misread
    (OCR_ODD, "AI agent 工具"),
    (CODE, "visual 的效果"),
    (CODE, "studio 錄音"),
    (CODE, "I phone you later"),
    (CODE, "in the open air"),
    (CODE, "We use voice input every day"),
    (CODE, "get the hub"),
    (CODE, "chat with GPT"),
    (CODE, "the clock is ticking"),
    (CODE, "cover the nets"),
    (CODE, "the governor said"),
    (CODE, "a cabinet meeting"),
    (CODE, "concrete numbers"),
    (LINE_CHAT, "下雨威力很強"),
    (LINE_CHAT, "我覺得張力很好"),
    (LINE_CHAT, "雨很大"),
    (LINE_CHAT, "陳列的商品"),
    (TRIP, "清淨的地方"),
    (TRIP, "心情不好"),
    (MAP_PREVIEW, "文心路上"),
    (MAP_PREVIEW, "檢查地圖"),
    (QUOTE, "天花板很高"),
    (QUOTE, "另外存檔"),
    (TRIP + chr(10) + LINE_CHAT, "不要把情境裡的文字加進來"),
    (LINE_CHAT, "幫我問雨薇明天有沒有空"),
    (LINE_CHAT, "一石二鳥"),
    (CODE, "turned into Instagram before"),
    (CODE, "我們去吉隆坡玩"),
    (CODE, "克服困難"),
    (CODE, "可樂的味道"),
    (CODE, "阿姨來了"),
    (CODE, "卡拉 OK 很好玩"),
    (CODE, "雷克雅維克很冷"),
    (MAP_PREVIEW, "這間文心南二店好吃嗎？"),                      # real X-ASR output (TTS)
    (QUOTE + chr(10) + "程式 城市 陳列", "這是一個很好的城市"),       # an everyday word never gives way
    (QUOTE, "我買了一瓶水"),                                        # no other screen word: a bottle of water
    (QUOTE, "給我一瓶"),
    (CODE, "Club 很聰明。"),                                          # no other screen name to support it
    (CODE, "我參加了一個 club"),
    (CODE, "Anthropic 的模型跟 cloud 服務"),
    (CODE, "Any OpenAI-compatible chat endpoint works"),
    (CODE, "installed had become Instagram"),
    (CODE, "OpenAI-compatible chat, installed"),
    (LINE_CHAT, "我們明天見"),
    (ROSTER, "我們明天開會"),                                     # no other name: a meeting
    (CHAT_LIST, "曾冠文"),
    (CHAT_LIST, "說好喂"),
    (CHAT_LIST, "謝謝你"),
    (CHAT_LIST, "蘇州很美"),
    (ROSTER + chr(10) + "下午三點", "四點要跟客戶開會"),               # a time is no supporting name
    (ROSTER + chr(10) + "程式", "這是一個很好的城市"),
    (OCR_ODD, "這個 Instagram 在 GitHub 上面爆紅。"),
]


def run(screen: str, heard: str) -> str:
    names = st.terms(screen) + st.recent(screen)
    text, _ = st.fix(heard, names, st.fix_candidates(screen))
    text, _ = st.fix_english(text, st.english_names(screen))
    return text


class ScreenScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        candidates.load()

    def test_fixes(self):
        for screen, heard, expected in FIX:
            with self.subTest(heard=heard):
                self.assertEqual(run(st.normalize_screen(screen), heard), expected)

    def test_keeps(self):
        for screen, heard in KEEP:
            with self.subTest(heard=heard):
                self.assertEqual(run(st.normalize_screen(screen), heard), heard)


    def test_a_long_window_keeps_the_newest_names(self):
        filler = chr(10).join(f"Item{i}Name and Thing{i}Value in row {i}" for i in range(400))   # 800 names above
        screen = st.normalize_screen(filler + chr(10) + CODE)
        self.assertEqual(run(screen, "我在用 cloud code 寫程式"), "我在用 Claude Code 寫程式")
        self.assertEqual(run(screen, "push 到 get hub"), "push 到 GitHub")


if __name__ == "__main__":
    unittest.main()
