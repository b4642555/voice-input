# VoiceInput — notes for coding agents

## Working with the user

- Whenever you can infer what the user is really after and see a better approach or a missing detail,
  stop and ask before building, as many rounds as needed, with the recommended option first.
- Don't guess between materially different designs; asking is cheaper than rebuilding.
- Ask clarifying questions with the AskUserQuestion tool (when available); use it freely.
- After every commit (each new feature or change), restart VoiceInput so the user is testing the new code; start it
  even if it isn't running (the user's request, 2026-10-10). PowerShell:

  ```powershell
  $root = 'E:\Projects\voice-input'
  $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$root\run.pyw*" }
  if ($procs) {
      $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
      $procs | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue }
  }
  Start-Process "$root\.venv\Scripts\pythonw.exe" -ArgumentList "`"$root\run.pyw`"" -WorkingDirectory $root
  ```

  Then check the tail of `%LOCALAPPDATA%\VoiceInput\voiceinput.log` for "就緒" / errors.

## Lean branch (2026-10-10)

On the `lean` branch every popup was removed (選字 candidate menu, suspected-mishearing menu, context hotword
question, the hotword ✓ box) together with hotwords (`hotwords.py`, the hotwords page, 自動判斷 / 一律取代, learned
biasing), `picker.py`, `picks.py`, re-dictation and hand-made-edit detection, and the settings that only served
menus (fix menu, fuzzy readings, menu timeout). The user plans to redesign popups from scratch. The second opinion is
off: SenseVoice is not loaded and the suspects code in `app.py` is commented out (`suspects.py` and
`candidates.homophones`, which it needs, are kept). Tray error notices (model not ready, mic failure, terminal
selection lost) stay. Sections below describe
the branch; history of the removed features is in git (main, up to 71e5516). Brought back 2026-10-11 in a new form:
the 選字 menu (old look, `picker.py`), hotwords (a weighted word list) and pick memory (`memory.py`); see below.

## Typeless-style redesign (2026-10-13)

The user asked for Typeless's experience without its cost. Their choices (asked 2026-10-13): free cloud LLM with a
local option, pill + main window + 選字 menu restyled, a sync folder, context = app name + text near the caret.
- **AI 潤稿** (`llm.py`, off by default, AI 潤稿 page): after formatting and smart picks, the text goes to any
  OpenAI-compatible endpoint (presets: Groq, Gemini, OpenRouter, Ollama, custom; plain urllib). The prompt removes
  fillers and false starts, keeps the last version of a self-correction, numbers spoken lists, splits topics into
  paragraphs, and must never answer the text. Tone by app kind (chat / mail / terminal / code). Glossary = hotwords +
  on-screen terms. Guards: `plausible()` rejects an answer much longer or much shorter than the input; any failure
  pastes the formatted text and a tray notice says so once per run. Terminals get one line (a newline would be Enter);
  inserting mid-line and inputs without final punctuation get none. ≤4 content characters are not sent.
  The API key is saved DPAPI-encrypted in config.json (`config._protect`).
- **Claude Code as the AI** (2026-10-14, the user: 一堆規則修不完，要更強的辦法；設定要足夠簡單，一般人能用；用我付費的額度):
  provider `claude_cli` (the default): a long-running `claude -p --input-format stream-json` (no tools, no saved
  session, an empty working folder, CLAUDE_* / ANTHROPIC_* variables removed so it uses the user's own login),
  Haiku 5.5. Starting it takes ~6s, a request to the running one 1-4s (average 1.7s on 12 cases), so it is warmed
  up at startup and replaced after 8 requests (its conversation grows). Needs `claude auth login` (the stored token
  from April had expired: 401 OAuth access token is invalid). Gets the screen names (en 20, zh 40, the draft left
  out) as the glossary: 林若晨，曾冠偉 -> 林又宸，曾冠維, 嚴肅紀東海總店 -> 鹽酥紀東海總店, clock code -> Claude Code;
  a screen spelling survives the Traditional conversion (佳怜). Settings page: the service list starts with it, key /
  url / model rows hidden, one line says whether Claude Code was found. The rule-based fixes still run first and are
  the fallback when it fails or times out (15s).
- **Sign-in with Google, no CLI** (2026-10-14, the user: most people have only Chrome; one key / one login): a real
  "Sign in with Google" would need our own Google Cloud OAuth app (the quota and cost would be ours, and Google's
  verification past 100 users), and Gemini CLI's built-in login must not be borrowed. So: the button 「登入 Google 並取得
  金鑰」 opens https://aistudio.google.com/apikey in the default browser; the user signs in (Chrome, Google account),
  presses 建立 API 金鑰 (in a NEW project: a billing project without credit gave 402 and no free tier) and copies; the
  settings page watches the clipboard for a NEW copy that looks like the key (`llm.KEY_PATTERNS`: AIza… / gsk_… / sk-or-…,
  5 minutes), fills it in, turns the AI on and runs the test. Gemini API is a direct HTTPS call (~0.3-1s, nothing to
  start). First run without Claude Code installed: provider gemini. Untested with a real Gemini key (the user's has no
  credit); the Codex route (`codex exec` / `codex login`) is not built.
- **AI mode checks meaning and punctuation** (the user, 2026-10-14: in AI mode be smart, without it the program rules are
  fine): the prompt asks the model to fix words the recognizer wrote as homophones when they make no sense in the
  sentence (隻友 -> 只有, 隻要 -> 只要, 在見 -> 再見; names, nicknames and acronyms stay) and to make the punctuation
  right (？ ！ ， 、 。, no confetti, no fragments); the chat tone only drops the final 。 (it used spaces instead of
  punctuation). `Config.ai_scope`: "all" (default, every dictation of 3+ characters goes to the AI) or "saver" (the
  rules of the least-quota entry below). The AI's end punctuation stays except 。，at the end when inserting mid-line;
  `drop_casual_period` takes 。 off chat-length messages afterwards. Real Claude, 10 cases: 隻友 / 隻要 / 隻是 / 在見 / 在這裡
  all right. The correction watch survives a field that cannot be read for up to 5s (注音 composing in Claude's own
  box: its focused element was a Group for a moment).
- **The AI reads the screen text** (the user, 2026-10-14: tell it this is what is on my screen, the topic is probably related):
  `read_context` keeps the window's text (UIA + OCR, draft removed, blank/duplicate lines dropped, the newest 1500
  characters) in `Context.screen` and `app._polish` sends it as 【畫面文字】 (only with the 參考畫面上下文 switch on);
  prompt rule 11: use it to understand the original meaning, never copy or answer it. Memory only, emptied by
  `_forget_screen`, never logged. Costs more input characters per request (the quota trade-off the user accepted).
- **Frameless window and the Typeless home** (the user, 2026-10-14, a Typeless screenshot): the main window has no system
  title bar (`FramelessWindowHint` passed to `QDialog.__init__`: `setWindowFlags` afterwards hung); own — □ ✕ buttons,
  `nativeEvent` answers WM_NCHITTEST (edges resize, the top strip drags and double-click maximizes), DWM rounded
  corners / shadow on Windows 11. 首頁: big headline, 洞察 cards (words, time saved, speed, total spoken time), active /
  current / longest streak days and a 26-week heat map (`HeatMap`, `History.per_day`).
- **Laughter, experiment** (`laugh.py`, the user's request 2026-10-14): setting 聽到笑聲時自動加字 (off) loads SenseVoice
  (+150MB, only while on) and asks `Recognizer.sound_event` about the whole clip and its last 1.5s; on
  `<|Laughter|>` the chosen text (哈哈 / XD / 😂) is typed alone or after the sentence. TTS cannot laugh, so only the
  plumbing is tested (fake recognizer, SenseVoice answers <|Speech|> on real speech); accuracy on real laughter is unknown.
- **English tech-name list** (`techterms.py`, `dict/tech_terms.txt`, the user, 2026-10-19: 先只做英文的科技詞、軟體、遊戲名稱):
  about 430 curated names in three popularity groups (not downloaded data: there is no "world list"; nothing updates
  itself). After the screen fixes, `screen_en.fix(..., strict=True, rank=...)` writes a heard run the way the name is
  spelled (get hub -> GitHub, cloud code -> Claude Code, type script -> TypeScript, node js -> Node.js). Strict mode:
  the same letters, a whole run that sounds like the name part by part, or a near miss of one long non-word; no
  "another name is nearby" support (it gave via -> Vite and opens -> OpenAI on 760 doc / log sentences), numbers must be
  equal, a one-word ordinary name (unity) is not capitalized between English words. The higher group wins a tie.
  Setting 英文科技詞自動校正 (on). Not done: Chinese new words, automatic updates (the user wanted English only).
- **Least quota** (the user, 2026-10-14: 越省越好，不想浪費額度; 要自動設定好，也要有關掉 AI 額度的選項):
  `worth_polishing` sends only a dictation of 14+ content characters, or 5+ with a filler / self-correction / list
  (嗯、那個、不對、第一…); short clean ones are left to the rules. The rules sit in the Claude Code process as its fixed
  system prompt (one process per model + system text), so a request is the dictation, the tone line and the glossary
  (~100-150 characters, was ~900); the same dictation with the same context is answered from a cache (32). On 8
  inputs: 4 requests. First run: AI turns on by itself when Claude Code is found (`Config.load`); off switches: the
  AI page and the tray menu 「AI 潤稿（會用掉 AI 額度）」; the AI page shows today's count (`History.ai_today`). With
  the AI on, the 開會 -> 凱蕙 "screen first" rule is left to the model (`fix(common_wins=False)`: the rule had
  turned 在輕井澤鍋物開會 into 輕井澤鍋物凱蕙).
- **AI 指令** (`rules.INSTRUCT`, a command that records like 錄音): what is said is an instruction; on a selection the
  LLM rewrites it, without one it writes the text and it is pasted. Not bound by default; the AI page adds "hold the
  upper side button".
- **History** (`history.py`): every dictation, history-<COMPUTERNAME>.jsonl in the shared folder; 首頁 shows words,
  time saved (typing at 40 字/min vs the recording length), speed. The old 辨識紀錄 log is 除錯紀錄 on the history page.
- **Sync** (`sync.py`): `sync_dir` holds hotword-list.json, smart-picks.json and the history files; config stays per
  computer. Changing it copies missing files over; mtimes are checked at each key-down and the files reloaded.
- **UI**: `settings.py` is a sidebar window (首頁 / 歷史紀錄 / 字典 / AI 潤稿 / 自訂操作 / 設定), light theme, one
  style sheet, `Switch` widgets. The pill (`overlay.py`) has a symmetric smoothed waveform and a POLISHING state.
- **Screen terms without AI** (`screen_terms.py`, 2026-10-13, the user's request; always on): at key-down the whole
  foreground window's element names are read (`selection.window_text`, one cached UIA FindAll, 1.2s budget, ~0.1s in
  practice) besides the focused control. Chinese terms: runs of characters that make no dictionary word in any split
  (names: 張雨薇), rare dictionary words, and two two-character words that are not a word together (薔薇計畫), each
  seen twice or in the title; split by fewest pieces then frequency (left-longest mis-split 先打開設定頁). They bias
  X-ASR at 3.0 and, after formatting, a span that sounds the same (all pypinyin heteronyms: 柏 is bai and bo) and
  is not a common word is replaced by the screen's spelling (張玉薇 / 陳博瀚 -> 張雨薇 / 陳柏翰; 城市 never becomes
  程式). The user's condition: screen text is never stored or logged; only the terms leave read_context and they
  are emptied when the dictation is done (`_forget_screen`); the log has counts only. TTS check: 3/3 fixed, no change
  without the screen text. Tests: `dev/test_screen_terms.py`; headless step `screen:TEXT`.
  OCR (`ocr.py`, same day): LINE draws its messages itself (UIA FindAll on its window: 156 elements, no names),
  so when the readable text is under 300 characters the foreground window is drawn into memory (PrintWindow) and read
  by Windows.Media.Ocr (zh-Hant-TW is installed on the user's PC; winrt-* packages, installed with uv
  `--system-certs`). ~0.4s, +~25MB once loaded, imported only on first need (the user wants it light). A name seen
  once on screen (平偉本色 under one Reel) is not a term, but `fix_candidates` (3-4 characters, not a dictionary
  word, no function character) feed `fix` only: the TTS dictation 評委本色 became 平偉本色 with the OCR'd text.
  Recent pieces (`screen_terms.recent`, same day): 平丁天花板 under one LINE message biased recognition only while
  the input box repeated it; the last 15 name-like pieces on screen (between function characters, not a dictionary
  word, newest first) now bias too, and two-character pieces (包樑) join the fix candidates (包糧 -> 包樑).
  Pieces split only at grammar characters, not numerals (文心南二店 was cut at 二). A span of 4+ characters may differ
  from the screen in one character by zh/z, ch/c, sh/s or the nasal ending (文西南二殿 -> 文心南二店); shorter ones
  must sound the same. Checked on the 51 logged dictations (7 changes, all wanted) and 118 unrelated doc sentences
  (no false change). A dropped character (鍋物 heard as 鍋) is not recovered.
  OCR runs a line together (輕井澤鍋物文心南二店向心里): pieces over 6 characters give 4-6 character windows as fix
  candidates. A span of 5+ characters may have one character of a different sound (清景澤波物 -> 輕井澤鍋物), never
  in a span holding a grammar character. `fix` collects every match, then applies the longest / closest without
  overlap, and never touches text already written as on screen. ~3ms per dictation.
  The user's rule (2 個以上的跡象, after 頭尾對上): against names only (terms + recent, never the loose OCR windows: they
  turned 單一中文字 into 單澤鍋物文字), a span within one character of a name's length that shares all its sounds but one
  (exact toneless, in order; the fuzzy readings made 靜 / 機 / 今 alike) and, if its length differs, the name's first
  and last sounds, becomes the name: 清引影澤 / 清引澤 -> 輕井澤. Last in priority; not for spans with a grammar
  character or made of common words (請你選擇, 情況選擇). Rare dictionary words of 3+ characters (frequency < 200,
  輕井澤 is 45) count as names, also inside a longer phrase.
  Hardening for the user's goal 「超級準，奇葩情境都過關，英文也要」 (same day), with `dev/test_screen_scenarios.py`
  (screen, heard, expected / must-not-change, ~90 cases) as the spec:
  - Screen text is normalized (`normalize_screen`): a simplified line becomes Traditional, a Traditional line only
    臺 -> 台 (s2tw on it turned 向心里 into 向心裡, 包樑 into 包梁).
  - Given names: a 3-character name with a common surname also matches its last two characters (雨威 -> 雨薇).
  - An ambiguous span (張宇薇: 張雨薇 or 章宇威) blocks every fix inside it. A 2-3 character span that cuts a common
    word with its neighbour is not touched (下雨威力 keeps 雨威). Pieces split at grammar characters standing alone
    only (會 inside 會議室 made 議室 a name). The loose rule skips spans holding a common word or a given name
    (情境裡, 問雨薇), and needs the shared sounds in place when the length is the same (上面爆 vs 上爆紅).
  - English (`screen_en.py`): names are CamelCase / digits / acronyms / capitalized non-words and whole runs of 2-3
    capitalized words; a run of heard words becomes a name when the letters match (voice input, chat GPT, GPT 4o,
    you tube) or the sounds do (Soundex or the same first two letters per part, one part spelled the same: cloud /
    clock code -> Claude Code, get hub -> GitHub; several plain words into one long name: cover nets ->
    Kubernetes; one long non-word: covernet -> Kubernetes). Never: an ordinary English word by sound (Windows'
    own spell checker, `spell.py`, en-US: instead / installed had become Instagram), plain words inside an English
    sentence (We use voice input every day, I phone you later), what OCR misread on screen (lnstagram).
  - Checked: real X-ASR on TTS audio with a chat + map + English screen, 16/16 right where the screen matters;
    2,690 doc / code / log sentences against all scenario screens: no false change. ~9ms per dictation.
  - Chinese transliterations of English names on screen (`screen_en.fix_transliterated`): a span made only of
    transliteration characters, holding no common word, whose pinyin consonants (K L T P S F M N classes) equal
    the name's (one off for 5+): 克勞德 -> Claude, 吉特哈布 -> GitHub, 庫伯內提斯 -> Kubernetes, 因斯塔格拉姆 ->
    Instagram. Names of one word, 4+ letters, not ordinary English, 3+ consonants. 吉隆坡 / 克服 / 可樂的 / 雷克雅維克
    stay. English names are collected bottom up (a long window filled the list before reaching the newest lines).
  - From 36 TTS sentences through real X-ASR: a character different in sound may not be at a span's edge
    (文心南二店好 matched the window 文心南二店向: 好吃 became 向吃), and a name already written as on screen blocks only
    matches that cut into it, not ones holding all of it (輕井澤國物 -> 輕井澤鍋物). After that every screen-related
    sentence was right; the rest were recognizer errors with no screen evidence (程式碼 heard as 城市碼, Claude as
    Club, code as cold, 下雨 as 夏雨, a dropped issue), which no screen rule can or should touch.
  - The user's rule (2026-10-14, 「畫面上有的字要優先套用」, 一瓶 typed where the screen had 一萍): a heard common word
    under frequency 100 (一瓶 is 61) gives way to a name on screen that sounds exactly the same; everyday words at
    100 or more (城市 1902, 程式 1551, 陳列 217) never do, and the looser rules still skip common words.
    Only with support: another screen name of 2+ characters in the same dictation (一瓶的平丁天花板 -> 一萍;
    我買了一瓶水 stays). English the same way (`screen_en._supported_hits`): an ordinary word not in the everyday
    list that starts like a one-word name, is at least half its letters and within two letters of its length becomes
    it when a different screen name is in the dictation (Anthropic 的 club 很強 -> Claude; Club 很聰明, cloud 服務,
    OpenAI-compatible chat, installed ... Instagram stay). 50 TTS sentences: every screen-related word right.
- `dev/headless.py` patches History too; AI follows the real config unless `set:ai_enabled=0`. A fake
  OpenAI-compatible server was used to test (no key was available): real model quality is untested.

## Platform

Windows only (decided 2026-10-03, after the macOS paths failed on the user's Mac: no hotkey worked). The partial
macOS code was removed; don't add `sys.platform` branches back. What a port would need: `docs/macos-port.md`.

## File layout

Decided 2026-09-26 (the user plans to share the app with other users): code and runtime tools (`.tools/`, `.venv/`)
stay in the app folder; user data lives in `paths.DATA_DIR` (`%LOCALAPPDATA%\VoiceInput`): `config.json`, logs, lock
and `models/`. The models folder is fixed (the setting to move it was removed 2026-09-29). `paths.migrate_legacy()` moves the old in-app-folder data on first launch. Autostart is
re-registered on every launch so it follows the app if its folder moves. Next step, not started: package as an exe
with an installer (PyInstaller + Inno Setup).

## Models (fixed)

Decided 2026-09-29: the app stays light, with a fixed set of sherpa-onnx models and no model choice or LLM
(`models.py`). Download URL: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/<dir>.tar.bz2`.

| Role | Model (archive dir) | Size | Notes |
|---|---|---|---|
| Recognition | X-ASR int8 `sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03` | 130MB | ~0.75s for 18s audio; punctuation, good English |
| Second opinion (off on lean) | SenseVoice Small `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` | 155MB | ~0.1s per sentence; emits tags like `<\|zh\|>` that are stripped |

Live captions (streaming X-ASR 480ms, 134MB) were removed 2026-09-29: they appeared too slowly to help; the pill
shows only the level. Qwen3-ASR 0.6B (840MB, ~4.4s per 18s) and Fun-ASR-Nano (1GB, ~7.5s) were selectable before and
were removed with the picker. As a second opinion Qwen3-ASR gave better alternatives but took ~1.2GB of the user's 8GB machine.
Memory with everything on: ~1.1GB (was ~3GB with the LLM). The word index held as Python objects is a large part.

## Text pipeline

`asr.py` (raw text, biased toward hotwords and on-screen terms) → `textfmt.format_text` → smart picks
(`memory.apply`, single characters) → paste. Order inside `format_text` matters: s2tw → filler
removal → disfluencies → 百分之 → numerals → spelled letters → 點 → percent → number+letter → English punctuation →
CJK/ASCII spacing → trailing punctuation.

- Trailing punctuation (2026-10-07, replacing the "strip trailing punctuation" setting): the model's sentence-final
  。，,. is kept, except when the caret's line has non-blank text on both sides of it (the user is inserting).
  `selection.read_caret` reads that at key-down with the selection probe: UIA in normal apps, the input box row the
  Windows Terminal cursor is on in terminals (same behaviour everywhere, the user's requirement). Unknown → kept.

- Chat-length messages (2026-10-14, the user's rule, setting 聊天短句不加句號, on by default): with Chinese, fewer
  than five commas / line breaks; pure English, two commas at most: the final 。 / . is dropped after formatting,
  smart picks and AI 潤稿 (`textfmt.drop_casual_period`); ？！… keep their tone, inner full stops stay.
- Manual correction learning, Typeless-style (2026-10-14): a learned correction shows a notice 「林又宸」 已加入關鍵字
  (`overlay.Toast`). In LINE nothing was ever learned (64 captures): every read was dropped while 注音 was composing
  (caret and text disagree: now the text is kept, caret unknown), a field emptied mid-edit stopped the watch (now
  it keeps watching), and the send came before two settled reads (now the latest read begun 0.1s after the last
  key, under 1s old, stands in). Edits a character or two apart in one Chinese run are one word (醉夜狂喜 ->
  罪夜狂襲). One corrected character of a name learns the whole name (`screen_terms.name_around`: single
  characters that make no word, up to a stop character, from a surname: 林又成 -> 林又宸, the user had retyped
  all three). Why a watch stopped / a field was unreadable is logged, never the text.
- Screen reading, from the user's LINE roster (2026-10-14): the focused field under 1000 characters is the draft
  being written and is not screen evidence; its lines are removed from the OCR text too, also when OCR misread them
  (宋家雅茹 read as 餐家雅茹 protected / "fixed" 宋家). OCR reads the window enlarged 1.5x (GDI StretchBlt, HALFTONE;
  17 names: 12 read as is, 15 enlarged). GDI calls declare 64-bit handle types (ctypes' int cut them: crashes).
  Simplified vs Traditional is decided for the whole screen (佳怜 alone became 佳憐). Up to 30 recent names; pieces
  with a grammar character are never names (明天的); rare dictionary names under frequency 80 (會議室 is 102).
  The loose rule needs the first and last sounds to match at any length (明天見 is not 明天的). English support
  rule only for words of 3-5 letters. The 除錯紀錄 page shows what each dictation took from the screen (row 畫面),
  in memory only: the log file never has it.
- Screen first for names (2026-10-14, the user's example 開會 / 凱蕙): any common word, however frequent (開會 is 495),
  gives way to a screen name that sounds exactly the same when another screen name is in the same dictation
  (佳怜開會阿霖 -> 佳怜凱蕙阿霖); alone (我們明天開會) it stays, and dictionary words on screen never win (城市 stays).
  A time or number on screen is no supporting name (下午三點 made 跟客戶開會 into 凱蕙).
- OCR is not stable: one read of LINE's chat list missed the bold 曾冠維 that the next read found. Two reads (1x and
  2x, joined; ~1.2s in the background). The grammar characters that split pieces are only those (almost) never in a
  name (又 cut 林又宸). The draft is matched by overall similarity (陸寶瓦 for 陸寶寶 kept the draft's 曾冠偉).
- Near sounds (the user's rule): tones never count (偉 / 位 / 微 -> 維), and a three-character name on screen may also
  have one character only close in sound, also at an edge (鄭冠維 -> 曾冠維, 林又成 -> 林又宸); loose OCR windows
  still need four characters for that.
- The screen wins over smart picks: the screen fix runs again after `memory.apply` (成 -> 宸, a smart pick learned for
  林又宸 by the earlier single-character learning, turned 吳宇澄 from the screen into 吳宇宸). A correction that
  learns a name no longer also records the single character as a smart pick. ㄥ/ㄣ (eng/en, ing/in) count as
  close sounds (`_fuzzy`).
- Smart picks need real context (the user: 「跟為啥要改成宸，這邏輯不對」): a single character is changed only when the
  character right before it agrees (or the text starts there, as when learned) and two positions agree in all, or
  as many as the example was learned with (在 said alone). One shared neighbour on the right (跟) was enough before.
- Correction learning, 2026-10-14 (the user: 想記住包你發娛樂城): the whole chat box (under 1000 characters) is compared,
  not only the last dictation (an earlier one still in the box stopped the watch as "text around changed"); a
  corrected word is learned with the phrase it sits in (貓米 -> 包你 learns 包你發娛樂城: Chinese characters around it
  up to punctuation, a stop character or 8 characters). Screen fixes may overlap a name already on screen when they
  keep its characters (嚴肅紀東海總店 -> 鹽酥紀東海總店 next to 東海總店).
- OpenCC `s2tw` only (glyph conversion). `s2twp` was dropped because it rewrites vocabulary (程序 → 程式).
- After s2tw, 臺 is mapped back to 台 (s2tw turns 台北 into 臺北; the user wants 台), and 熒幕 / 熒光 to 螢幕 / 螢光
  (X-ASR writes 荧幕, which s2tw turns into 熒幕; 2026-10-11).
- 嗯 / 呃 are removed by regex.
- Disfluencies (`remove_disfluencies`, 2026-09-29): repeated 2-4 character chunks (我們我們, 你可不可不可以, 今天今天),
  restarts (先打開那個，先打開設定頁 → 先打開設定頁) and stutters of a few characters (我我, 先先). ABAB reduplication is
  kept when the dictionary lists it (研究研究) or the word is in `_ABAB` (討論, 休息 …), as are counting (一個一個,
  一個字一個字), laughter and 好的好的. A-not-A is a repeat only when A comes a third time (可不可不可以 → 可不可以;
  是不是不會, 能不能不用 stay: before 2026-10-02 they lost 是不 / 能不 and flipped the meaning). 在 is not a stutter
  character (現在在猶豫). Single repeated characters (2026-10-02, `_dedupe_chars`): one is dropped when the pair is
  not inside a dictionary word (選選字, 預預期, 很很, 以以及, 不不要); kept: 剛剛, 框框, 渾渾噩噩, 試試看, AA的 (髒髒的),
  two words sharing the character (開關關掉, 勾選選項), numerals. Each drop is logged as `stutter:`. On the 1204
  logged sentences 34 change; the only doubtful one is 摸貓貓 → 摸貓 (baby talk not in the dictionary).
- Numerals: single-character numbers stay Chinese (一個, 兩個計劃, 第二種, 十個) unless part of a decimal,
  percentage or followed by a single letter; multi-character ones become digits (十五 → 15, 七百二十八 → 728).
  Also skipped: `_KEEP_WORDS` (統一, 星期三, 十分 …), ranges like 三四, anything with 幾, fractions.
- A number directly followed by a single letter is joined, case kept: 二 B → 2B, 五 h → 5h.
- After an English letter or word a single numeral becomes a digit unless a classifier follows (M 三 → M3,
  GPT 四 → GPT 4, but Python 三個月, LM 一次 stay); a single letter is joined to the number (V 四點一 → V4.1,
  Opus 4.8 keeps its space). Decimals are read digit by digit (零點一五 → 0.15, 零點零五 → 0.05), stopping at a kept
  word (三點八一樣 → 3.8 一樣) and leaving the last digit to a following letter (三點五二 B → 3.5 2B). 2026-09-30.
- `百分之X` and `<number> percent` → `X%`. English number words (eighty) are not converted.
- 點 becomes `.` only when both sides are digits or both are letters (三點 meeting stays).

## Settings window (tabs since 2026-10-11)

Four tabs, sized for the log: 一般 (microphone, "mute the speakers while recording", punctuation names, "selected 。 →
，", "節日的節" (described characters), autostart, a button that opens smart-picks.json; the menu timeout was
removed 2026-10-11), 辨識紀錄 (the transcript log), 自訂操作 (custom operations, below) and 熱詞 (the hotword list:
date added, weight / cap and its percentage; add/edit/remove since 2026-10-12; the source is not shown since 2026-10-11; a 排序
drop-down: weight percentage (the caps differ), name A→Z / Z→A by pinyin, date; remembered). Mute (2026-10-06, `mute.py`): Core Audio via
comtypes; muted when the recording indicator shows, unmuted once the text is pasted or the press ends in nothing; a
mute the user set is left alone. Selection edits and on-screen terms are always on.

## Custom operations (自訂操作, 2026-10-11)

`rules.py`, stored as `rules` in config.json; they replaced the single hotkey setting and the 換行 / 送出 switches
(migrated by `Config.load`). A rule: trigger (滑鼠鍵: middle / upper / lower side button; 鍵盤鍵: CapsLock, right Alt,
right Ctrl, Insert, ScrollLock, Pause, F1-F24; 說出: an utterance that is exactly the phrase) → 按法 (按一下 / 連按兩下
/ 按住; not for speech) → 情境 (不限 / 剛輸入完 / 有選取文字; the most specific wins) → steps run in order: 命令 (錄音,
選字, 刪除選取的文字, 刪除剛輸入的文字; 加入熱詞 was removed 2026-10-11 and `rules.migrate` drops it), 按鍵 (a captured combination such as Ctrl+Z, sent with SendInput), 輸入文字 (pasted, counts as a dictation),
等待 (ms). Steps before 錄音 run at the start, those after it once the text is pasted (skipped if nothing was said).
Hold + 錄音 records while held; tap / double tap + 錄音 starts there and the next press of any bound key stops it.
刪除選取的文字 works in terminals too (why it is a command, not the Delete key); 刪除剛輸入的文字 deletes the last
dictation. They were one 刪除 command until the user had them split (2026-10-11; `rules.migrate` converts old rules).
When a key's double tap is a non-recording rule and its hold records, a held second press records after the double
tap's steps (delete and say it again; the user's choice). 復原 is not a command: the user adds Ctrl+Z if wanted.
Defaults: hold middle = 錄音; double middle on a selection = 刪除選取的文字, right after a dictation = 刪除剛輸入的文字
(in neither situation the double click goes back to the button); tap middle on a selection = 選字 (added
2026-10-11; `rules.upgrade` appends it to configs saved before, rules_version 2); 說出 換行 = Shift+Enter, 說出 送出 =
Enter.
Gestures (`app.py`, `hotkey.Hotkeys`): bound keys are suppressed by the hooks; the microphone starts at key-down when
the press may record; a single tap waits DOUBLE_TAP_GAP only when that key has a double-tap rule; a gesture no rule
takes is given back (keyboard key replayed, mouse button clicked: the user's choice, so an unbound middle click
still works, 0.35s late when a double tap is bound; a hold is given back as one click). Invalid rules (two 錄音,
錄音 in a speech rule, empty combination, a duplicate of an earlier rule) are flagged in red and skipped. When no
rule records any more, a tray notice warns that dictation is impossible (the user's request).

## 選字, smart picks and hotwords (2026-10-11, reworked the same day)

The one popup is the 選字 menu (`picker.py`, the pre-lean look; a redesign is planned). The 加入熱詞 notice with 撤銷
lived a few hours: the user wants hotwords added quietly (they come from deliberate picks and typing).
- **The menu** (reworked 2026-10-11, the user's design): no title row; on top an input box as tall as a row,
  placeholder 輸入加入熱詞. A click there activates the menu (WS_EX_NOACTIVATE is lifted, SetForegroundWindow) so an
  input method works; Enter hides it, gives focus back to the window that had it and, 150ms later, replaces the
  selection with the text like a pick and makes it a hotword at the cap (a single character is learned as a smart
  pick instead); Esc closes. No timeout: any click outside the menu or key in another window closes it (mouse /
  keyboard hooks; clicks in it and keys typed into it leave the session and the terminal selection alone), and an
  activated menu closes when it loses focus. Not yet tried with a real IME and a terminal selection.
- **選字** (command): the menu under the selection. One Chinese character: what the smart picks decide here, what
  was picked for it before, then same-sounding characters. A Chinese word: homophones (`candidates.ranked`), the same reading with tones
  first (the user's choice, 2026-10-11), then without tones, then fuzzy; within each: hotwords by weight, common
  words (frequency ≥ 5, since McBopomofo lists obscure entries like 逆號 at 1), frequency. (Single characters still
  use `candidates.homophones`: common first, then the reading tiers.) English: spellings (`memory.variants`: Claude / claude / CLAUDE; voiceInput /
  VoiceInput / Voice Input / voice input / VOICE INPUT / voice_input / voice-input / voiceinput, the styles picked
  most first, counted apart for one word and for several), hotwords among them first by weight. 8 rows.
- **Smart picks** (`memory.py`, DATA_DIR/smart-picks.json, one example per line, opened from the 一般 tab): single
  Chinese characters only (the user's choice). Modelled on art-shuangpin's core/user_preferences (spec §7): key
  (toned pinyin) + left (two characters, `^` boundary) + right (two toneless readings) + value; applied after
  formatting when at least one token agrees, most agreement wins, left alone on a tie. Phrases and English were
  learned for a few hours the same day and are ignored on load.
- **Hotwords** (`hotwords.py`, DATA_DIR/hotword-list.json, one list with weights on the 熱詞 tab, removable; the old
  key -> value hotwords.json is not read). A word typed in the menu (source "manual"; until 2026-10-11 the 加入熱詞
  command) sits at the cap; 選字 (a word of 2+ characters or English picked in the menu) starts at half the cap,
  +0.5 per pick, and the word a pick replaced loses 1 (dropped at 0; typed ones never lose). Caps: Chinese 5, English 3. Single characters never become hotwords. No replacement after
  recognition: words are left to X-ASR biasing. A biasing word that another one starts with is not sent (剪貼 next to
  剪貼簿 made X-ASR write 剪貼布).
- **Why Chinese biasing works now** (dev experiment by a subagent, 2026-10-11, Windows TTS, 16 target words + 4
  counter-examples; the scripts were deleted): sherpa-onnx's modified beam search keeps the top max_active_paths
  tokens by the model's own score and only then adds the hotword boost (offline-transducer-modified-beam-search-
  decoder.cc), so at 4 a word that missed the first cut was never boosted (程式 failed at any score). At 12 with
  score 5: 16/16, no false hits; score 6: 台北是一個美麗的城市 → 程式; score 8: English broke (a long sentence came out
  as "VoiceIn。"). Encoding stays as it was: Simplified, characters space-separated (every CJK token is "▁X"; ▁ in
  the hotword does nothing, the whole word in one piece encodes wrongly, Traditional works only where the glyphs are
  the same). 14s of audio 0.62s → 0.80s. Not yet checked with real recordings.

## Voice edits on selected text

Plan agreed 2026-09-27, cut down 2026-09-29, spoken triggers replaced by the hotkey 2026-09-30. The selection is
read when a bound key goes down (`selection.py`, UI Automation TextPattern, on a background thread). Speaking with
text selected replaces it; 刪除選取的文字 deletes it; with the default operations a double tap held deletes it and the
dictation is pasted there.
UIA probe (2026-09-27): Chrome/Edge inputs, Win11 Notepad, LINE give selection + line context; LINE's first read ~1.2s.
Terminals: Claude Code / Codex / agy in Windows Terminal (inside herdr) hide their mouse selection from UIA,
Shift+arrow does not select, and a paste goes to the caret. Since 2026-10-01 a mouse drag there works like a
selection anywhere else (`selection.TermWatcher`, probed with `dev/term_probe.py`): UIA `RangeFromPoint` maps the
press and release points to characters and the text between them is read from the screen (no clipboard since
2026-10-02: the user keeps herdr's `copy_on_select` off), so a repeated word is told apart. herdr selects up to the cell edge nearest the pointer, so the
character under an end point is often not selected: the ends are narrowed to the cells herdr highlighted (inverted
colors, read as UIA cell attributes; 2026-10-03). Codex keeps a drag as its own selection, and a left-to-right drag
leaves the cursor right after it, so a Backspace there would delete the whole selection and the next one a
character more: when the cursor already sits there, one Left key clears it first. A double or triple click
(2026-10-03) is a selection too: the run of highlighted cells around the click (background unlike the cells on both
sides). Claude Code / agy select a line up to punctuation or a space on a double click; Codex a word (one CJK
character) on a double click and the whole line on a triple click. Used only on one line
(the user's choice) of the input box: a row starting with ❯ / › / > at the pane's left edge plus the rows below it
indented under the prompt (blank rows from Shift+Enter included since 2026-10-04, except between the cursor
and a selection below it: that is how Codex's status line sits under its box), and the terminal cursor must be in
that box (a past prompt in the transcript and status
lines are rejected). Keyboard only since 2026-10-02 (clicks misplaced Codex's cursor, agy ignores them):
WT reports its cursor as an empty UIA selection, so arrow keys move it (Up/Down to the row, then Left/Right),
re-reading it after each burst, until it sits right after the selection; then Backspace 20ms apart (a burst lost
one in Codex) and paste. If the cursor cannot get there nothing is deleted and a notice says so. Tested with
herdr-driven panes in all three tools: soft-wrapped rows, other lines, cursor starting anywhere. In the background
WT reported the cursor one character left; in front it is exact. Forgotten on any key, click or window switch.

The dictionary (`candidates.py`, since 2026-10-02): McBopomofo's word list (MIT, `voiceinput/dict/words.tsv.gz`,
built by `dev/build_dict.py` from a pinned commit): ~146k Taiwanese phrases + Big5 single characters with Bopomofo
readings and Taiwanese-corpus frequencies, cached in DATA_DIR/cache/word_index.pkl (build ~10s, load ~0.4s). It
answers `is_word` / `frequency` for disfluencies and `homophones` for 選字 (and the commented-out suspects). jieba was
used until 2026-10-02 (commit 58c1535) and dropped for missing Taiwanese words and mainland frequencies.

## After a dictation (2026-09-29)

Voice commands on what was just dictated (復原, 送出, 換行, 刪掉上一句, X改成Y, 刪掉X, symbol names, trailing 送出) were
removed the same day they were built: the user corrects by selecting with the mouse instead. What remains:

- **Punctuation names** (restored 2026-09-30, `edit.symbol`; a setting on the 一般 tab since 2026-10-11, on by
  default, so they need no custom operations): an utterance that is exactly 逗號 / 句號 / 句點 / 問號 /
  驚嘆號 / 感嘆號 / 頓號 / 分號 / 冒號 / 點點點 / 刪節號 / 省略號 types the mark, appended after any existing
  punctuation (the user chose not to replace it). Exact text only: 都好 is not 逗號. On a selection it replaces it.
  換行 / 送出 are custom operations since 2026-10-11 (speech rules, checked before punctuation names).
  Spoken commands are boosted (2026-10-11, the user's request: 換行 was often misheard): when the first decode is
  short (at most the longest command + 2 characters) it runs again with every speech operation's phrase and the
  punctuation names (when on) at score 5. Not by audio length: TTS 換行 alone was 1.7s with its silences. Fixed
  據點 -> 句點, 珊姐好 -> 刪節號; cost: 問好 / 分好 said alone become 問號 / 分號. Words of one repeated character
  (點點點) are not boosted: they ran away (點點點點點點點點點).
- **Described characters** (`edit.described`, 2026-10-11, the user picked it among ideas for exact text by voice;
  a 一般 checkbox, on by default): an utterance that is exactly <words>的<character> types only that character when
  the last 4 characters before 的 contain it, or one that sounds like it (X-ASR wrote 一張的章: 張 is typed). Also
  這個字是教育的育, 偉是偉大的偉 (said that way in the log before the feature existed). Typed as is, like a
  punctuation mark (replaces a selection, no smart picks). No false hits on 3182 logged results; TTS 節日的節,
  薔薇的薇, 這個字是教育的育 right (程式的程 was heard as 城市的城 and typed 城).
- **Bopomofo spelling** (拼讀: ㄔ ㄤ 昌 types 昌) was built and reverted on 2026-10-11 (db3f0e7, c0ae24e): with a real
  voice X-ASR heard most symbol names as English letters (ㄐ ㄧ ㄝ → "G E A"), and there is no way to force Chinese,
  so it missed too often for a feature meant for frequent use (the user's verdict).
- **Selected 。** (setting, 2026-10-07): speaking on a selected 。 replaces it with ， + the utterance + 。
  (今天很好。明天見 → 今天很好，我們去公園。明天見).

- **Dictated-text buffer (`session.py`)**: the text we pasted is known to sit right before the caret until the user
  presses a key (keyboard hook, injected keys ignored), clicks (mouse hook) or another
  window comes to the front. Changes to it are Backspace up to the edit point (one `SendInput` burst) + paste of
  the new tail; works in terminals too.
- **刪除剛輸入的文字** deletes the last dictation while it is still the last change and the caret has not
  moved; only that one (2026-10-04: the user had undo of picked suggestions and repeated undo removed). Text spoken
  over a selection counts as a dictation (2026-10-07, the user's model: like typing over it): it removes it, the
  selected text stays gone (a selected 。 turned into ，…。 gets its 。 back). Recording starts at key-down, but the
  indicator (overlay, tray icon) only after 0.1s (0.3s = TAP_THRESHOLD, then 0.2s, felt slow), so taps and double
  taps do not flash it.
- **X-ASR biasing** (`asr.py`, used for on-screen terms): modified beam search + per-stream hotwords, same speed as greedy (0.11-0.22s
  on 3s clips). The model's vocab is pure BPE with CJK as "▁X" pieces: `modeling_unit="bpe"`, CJK hotwords
  space-separated and converted to Simplified, and a `bpe.vocab` exported from `bpe.model` by a tiny protobuf reader
  (sentencepiece is not installed). One unencodable word makes sherpa-onnx drop the whole list, so words are
  checked against tokens.txt. Results: L M → LLM, work tree → worktree at 2.0; cloud code → Claude Code needs 3.0;
  Chinese biasing barely works (張雨薇 → 張玉薇 at 3.0, 城市 never → 程式).
- **On-screen terms**: at key-down UIA reads the focused control's visible text (0.12s in WT) and English terms bias
  X-ASR at 1.5: CamelCase / acronyms / digits always, other 5+ letter words if seen twice, dotted names skipped.
- **Suspects** (`suspects.py`, commented out on lean): SenseVoice re-decodes each dictation in the background; where
  it heard other Chinese characters and its version is the likelier word (a dictionary word where ours is not, or
  ≥20× as frequent), a menu offered it. Rare, because both models often share an error (主機版, 剪貼布). Guessing
  from the dictionary alone was rejected: every flag on 80 real utterances was wrong.
- **Terminal edits and the overlay** (2026-10-11): a terminal edit runs on the main thread, which waits up to 4s
  for the arrow keys to bring the cursor there (UI Automation on a thread). The overlay's 140ms fade-out was still
  running over Claude Code's input box (both sit at the bottom of the screen), so UIA's ElementFromPoint waited for
  our own frozen window until the timeout: the three dots stood still and 找不到選取的文字 appeared. The overlay is
  now hidden at once (`Overlay.hide_now`) before a terminal edit, delete or pick.
- **Clipboard**: every format is restored after a paste (images copied before dictating used to be lost).
- Tests without a microphone, with Windows TTS audio (zh-TW Hanhan voice via `dev/tts.ps1`):
  `dev/headless.py` runs the real App against a simulated text box; use it by default. Never let a test write the
  user's real data files while their VoiceInput is running.
  `dev/e2e.py` types into a real Notepad / Windows Terminal window: only when the user is away from the machine
  (Win11 Notepad opens new windows as tabs, and a run once typed into the user's own Notepad document).

## Manual correction learning (2026-10-12)

The mouse-Alt repair branch adds scoped correction learning; see `docs/manual-correction-learning.md` for the user-visible boundaries. `correction.py` owns an independent monitor and optional UIA worker; do not reuse Session.dirty as its scope. A confirmed insertion is tracked by field identity and exact outer text. Only a cached small replacement followed by Enter/Ctrl+Enter and the same field clearing may be learned. Unsupported fields and stale snapshots skip learning without blocking paste. Terminal history must never be used as a correction source.

Tests must inject synthetic snapshot readers or a no-op CorrectionWatcher; the headless harness must never inspect the user's current input fields. Stores stay in temporary files. Learning UI updates are marshalled to the Qt main thread.
