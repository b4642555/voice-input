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
，", "節日的節" (described characters), autostart; the menu timeout was removed
2026-10-11), 辨識紀錄 (the transcript log), 自訂操作 (custom operations, below) and 詞庫 (hotwords; the UI
says 詞庫 and 優先度 instead of 熱詞 and weight since 2026-10-13, the code keeps "hotwords"). Since 2026-10-14 the
詞庫 tab has four collapsible sections, one open at a time (the user's design): the open one fills the tab and
scrolls inside, the other headers stay in view; the open one is remembered (`hotword_section`). 詞包: one row per
pack: check box with the count, its first 5 terms, 編輯 (opens the user's copy of the file; see Term packs). 詞庫列表: date added, the 優先度 (shown as stored − 100: 0% does nothing, −30% in red is lowered, +100%
is the top; the user found 70% did not read as lowered, 2026-10-13), ✕; the source is not shown. Since 2026-10-14
the 優先度 is read only (the −/+ buttons were removed, the user's choice) and ✕ puts a word back where it would be
unlisted (100%, or its ticked pack's), keeping its aliases; only words away from that level are listed. 選字記憶 (the
UI name of the smart picks since 2026-10-14; 智慧選字 did not fit something this mechanical): each example as
這個ying3片是 → 這個影片是 (context with the reading, then with the pick; the user's design), date, ✕ (it replaced the
一般 tab's button that opened smart-picks.json; editing the character was there for a few hours). 英文強制替換: the
aliases (Dipsick → DeepSeek), date (`alias_added`; older ones show the word's date), ✕. Entries record when they were added to
the second since 2026-10-14 (older ones the date alone, sorting before same-day ones with a time); the tab shows
the date, the time is in its tooltip. Every list has its own 排序
drop-down, remembered (`hotword_sort`: 優先度, name A→Z / Z→A by pinyin, date; `pick_sort`: date, reading;
`alias_sort`: date, name). Mute (2026-10-06, `mute.py`): Core Audio via
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
Gestures (`app.py`, `hotkey.Hotkeys`; `gesture_time`, 0.3s by default, is both how long a press makes a hold and
how soon after a tap a press makes a double tap: one setting on the 自訂操作 tab, 0.15-0.8s, the user's request
2026-10-13; constants of 0.3 / 0.35s before): bound keys are suppressed by the hooks; the microphone starts at key-down when
the press may record; a single tap waits `gesture_time` only when that key has a double-tap rule; a gesture no rule
takes is given back (keyboard key replayed, mouse button clicked: the user's choice, so an unbound middle click
still works, `gesture_time` late when a double tap is bound; a hold is given back as one click). Invalid rules (two 錄音,
錄音 in a speech rule, empty combination, a duplicate of an earlier rule) are flagged in red and skipped. When no
rule records any more, a tray notice warns that dictation is impossible (the user's request).

## 選字, smart picks and hotwords (2026-10-11, reworked the same day)

The one popup is the 選字 menu (`picker.py`, the pre-lean look; a redesign is planned). The 加入熱詞 notice with 撤銷
lived a few hours: the user wants hotwords added quietly (they come from deliberate picks and typing).
- **The menu** (reworked 2026-10-11, the user's design): no title row; on top an input box as tall as a row,
  placeholder 輸入加入詞庫 (輸入加入熱詞 until 2026-10-13). A click there activates the menu (WS_EX_NOACTIVATE is lifted, SetForegroundWindow) so an
  input method works; Enter hides it, gives focus back to the window that had it and, 150ms later, replaces the
  selection with the text like a pick and makes it a hotword at the cap (a single character is learned as a smart
  pick instead); Esc closes. No timeout: any click outside the menu or key in another window closes it (mouse /
  keyboard hooks; clicks in it and keys typed into it leave the session and the terminal selection alone), and an
  activated menu closes when it loses focus. Not yet tried with a real IME and a terminal selection.
- **選字** (command): the menu under the selection. One Chinese character: what the smart picks decide here, what
  was picked for it before, then same-sounding characters. A Chinese word: homophones (`candidates.ranked`), the same reading with tones
  first (the user's choice, 2026-10-11), then without tones, then fuzzy; within each: hotwords by weight (lowered ones last), common
  words (frequency ≥ 5, since McBopomofo lists obscure entries like 逆號 at 1), frequency. English (2026-10-13):
  first up to 4 known terms that sound like the selection (`sounds.py`; the user's list, ticked packs, terms on
  screen: clock code → Claude Code, lanchin → LangChain, Deepsec → DeepSeek; 20 of 24 TTS mishearings first, 23
  in the menu), then the spellings below. No threshold separates mishearings from ordinary words (commit ~ ComfyUI
  scores like clock ~ Claude), so an ordinary word gets a few look-alikes above its spellings.
- **English spelling after recognition** (`spelling.py`, 2026-10-13, the user's choice): X-ASR often hears a term
  right and spells it its own way (Deep Seek, Notebook LM, fine tune). After `format_text`, a run of English words
  whose letters equal a known term's (user list, ticked packs, on-screen terms) is written as the term; case alone
  only when it adds inner capitals (deepseek → DeepSeek; cursor, Open Source stay). Aliases: an English spelling with
  other letters replaced in 選字 or by typing (Dipsick → DeepSeek) is stored on the word (`aliases` in
  hotword-list.json, listed under 英文強制替換 on the 詞庫 tab, ✕ removes one; picking it back removes it) and always
  rewritten. This is replacement after recognition, for English only. Why not more: the user's real DeepSeek at
  200% still came out as Dipsick / Dipsi / deep sick; TTS showed +4 helps a little (Ollama alone), +5 breaks
  (DeepSeek → Deep, queue → que), and negative scores for misheard spellings made it worse (Deep Seek → Deepsec). (Single characters still
  use `candidates.homophones`: common first, then the reading tiers.) English: spellings (`memory.variants`: Claude / claude / CLAUDE; voiceInput /
  VoiceInput / Voice Input / voice input / VOICE INPUT / voice_input / voice-input / voiceinput, the styles picked
  most first, counted apart for one word and for several), hotwords among them first by weight. 8 rows.
- **Smart picks** (`memory.py`, DATA_DIR/smart-picks.json, one example per line, listed on the 詞庫 tab): single
  Chinese characters only (the user's choice). Modelled on art-shuangpin's core/user_preferences (spec §7): key
  (toned pinyin) + left (two characters, `^` boundary) + right (two characters; toneless readings until 2026-10-14, when the user chose
  characters on both sides: both are recognized text here, unlike an input method's unconverted pinyin, and the
  examples learned before were dropped) + value; applied after
  formatting when at least one token agrees, most agreement wins, left alone on a tie. Since 2026-10-14 a change that breaks
  a raised hotword or makes a lowered one is not made (`Hotwords.levels`; 一 -> 依 learned in 右的依序 turned
  一瞬間 into 依瞬間 through the agreeing shun, though the user had 依瞬間 at −100%), and a pick that puts back
  characters a smart pick changed (依瞬間 -> 一瞬間, or 景 -> 影) learns "keep it here" under the reading the
  recognizer wrote (an example with the heard character as value), so the example that changed it stays for its own
  context. Until the same evening such a pick deleted every example agreeing by a token: the user's 這段影片是 -> 景
  and 這個影片是 -> 影 kept deleting each other, and the second was learned under 景's reading, which never applies
  since X-ASR writes 影. Phrases and English were
  learned for a few hours the same day and are ignored on load.
- **Hotwords** (`hotwords.py`, DATA_DIR/hotword-list.json, one list on the 熱詞 tab; the old key -> value
  hotwords.json is not read). Percentages since 2026-10-13 (the user's design; the scores behind them are not
  shown): 100% does nothing, 200% pulls hardest (score +5 Chinese, +3 English), 0% pushes away (−3). A word typed in
  the menu (source "manual") is 200% and picks never move it; a 選字 pick (2+ characters or English) raises the
  picked word 30% and lowers the replaced one 30%, so swapping 程式 / 城市 back and forth returns both to 100%, where
  they leave the list. The −/+ buttons that moved a word 10% were removed 2026-10-14. English never goes below 100%: a negative English word only
  changed its case (clock -> Clock). Measured with TTS: 程式 130% / 城市 70% already turns 寫了一個新的城市 into 程式;
  up to 200% / 0% 台北是一個美麗的城市 is left alone; −5 made it 城池, and lowering 城市 alone gave 程序, not 程式. Old
  weights were converted (typed 200%, picked 100% + weight / cap). Single characters never become hotwords. No
  replacement after recognition: words are left to X-ASR biasing. A biasing word that another one starts with is not
  sent (剪貼 next to 剪貼簿 made X-ASR write 剪貼布), and a lowered word sharing a start with another is dropped.
- **Hotword volume** (TTS, 2026-10-13): random dictionary words at score 2 cost nothing up to 5k (0.50s → 0.54s for
  9.5s of audio), 20k 0.73s, 50k 1.12s (the hotword graph is rebuilt per stream), but from 20k on 熱詞 came out as
  熱刺: large term lists hurt accuracy before speed.
- **Term packs** (詞包, `packs.py`, `voiceinput/packs/<id>.txt`, ticked on the 詞庫 tab, `packs` in
  config.json; 2026-10-13): seven curated lists in `packs.ORDER` (AI 工具與模型, 辦公與協作, 程式開發, 雲端服務,
  設計與影音, 行銷與數據, 硬體與系統; 723 words), no version numbers (textfmt writes spoken numbers), no common words
  as capitalized products (Box, Go, Pages, Numbers, Render, Railway, Linear were dropped), nothing hotword_spec cannot
  encode (UI/UX, 4K, CI/CD). Wikidata is only a cross-check (`dev/pack_candidates.py`): it misses tools (Cursor,
  Ollama) and is noisy (DeviantArt). Words of ticked packs bias at 150% (+50% on screen) without being listed; a pick
  or replacement lists a word, moved from there. Strength: see `packs.py` (the user's recordings: Gemini 8/14 at
  120%, 10/14 at 150%, 12/14 at 170% but Cloud → Claude). Until 2026-10-13 evening `_bias` dropped any word another
  one starts with, English too, so pack words like Gemini (Gemini CLI) and Claude (Claude Code) were never boosted;
  now only Chinese (剪貼 / 剪貼簿).
  User copies (2026-10-14, the user's request): each launch copies shipped packs missing from DATA_DIR/packs there
  (`packs.install`; the installer should do it once there is one) and only those are read; the user edits them
  (one file per pack, the best known terms first since the tab shows the first five) and an edit applies at the
  next dictation (`packs.stamp`). An app update merges (the user's choice): DATA_DIR/packs/shipped keeps each pack
  as last shipped, and terms a new version adds are appended to the user's copy under a dated comment; the user's
  additions stay, deletions are not undone (unless a new version adds the term again), dropped terms stay. Curate
  the shipped files in `voiceinput/packs`; `dev/pack_candidates.py` compares against those.
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
  punctuation mark (replaces a selection, no smart picks). A character that only sounds like it counts only when
  at most 4 characters come before 的 (2026-10-14: 我這邊想要顯示的是 typed 示); on 3931 logged results that was the
  only false hit; TTS 節日的節,
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
  indicator (overlay, tray icon) only once the press is known to record, with the start sound (see Sounds). It was
  0.3s, then 0.2s and 0.1s (felt slow); on 2026-10-13 the user chose to show it with the sound at the decision.
- **Long dictations** (`asr._pieces`, 2026-10-14): X-ASR fails on more than 40.05s of audio (onnxruntime Reshape
  error in the encoder; a 43s dictation was lost). Longer audio is decoded in pieces of at most 30s, each cut at the
  quietest 0.2s after 15s, and the texts joined (a space between English words). A piece may end in 。 where the
  user only paused (TTS-free check on the user's recordings: 最常用的。術語); 150s took 8s.
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
- **Sounds** (`cues.py`, `voiceinput/cues/*.wav`, 2026-10-13): the 一般 tab's 提示音 row: a set drop-down (12 sets,
  無, 自定義; the user's design), a 試聽 button (start sound, 0.8s, end sound; picking a set plays it too), and only
  for 自定義 the 開始 / 結束 drop-downs (a sound picked there plays). Below it 結束音在 [停止錄音時 / 文字輸入完成時]
  播放 (`stop_sound_at`, the user's request): at stop, muted speakers are unmuted then (the microphone is off). Blip
  by default. The 簡潔 UI and 柔和木質 sets of ElevenLabs effects
another agent made in `dev/sound-preview` (untracked), converted by `dev/build_cues.py` (loudness matched like the
  preview page; an end sound's tail cut at -40 dB, a start sound's at -20 dB plus a 50ms fade, 0.07s (Pop) to 0.45s
  (Triad), since it is cut out of the recording). The indicator fades in and the start sound plays together once the press is known to record
  (`_show_recording`, the user's design, 2026-10-13): a hold at `gesture_time` (0.3s), a tap at its release
  (`gesture_time` later when the key has a double tap), at key-down when the key's only operations are recording taps. The
  indicator at 0.1s with the sound beeped on double taps; at 0.1s after the microphone opened, the user's double
  taps (first press ~0.12s) rarely flashed it. Muting and the end sound follow. `cues.remove` looks for it from when it played (`_Press.cue_at`). For its length plus `_CUE_LATENCY` (0.25s; PlaySound to microphone measured 0.17s
  on the user's machine) the level bars stay still (`Recorder.still`) and the speakers on; muting starts after.
  Recording still runs from key-down: dropping the audio while the sound played (tried the same day, the user's
  idea) cut the first syllable, as the user starts speaking 0.25-0.98s after the press (median 0.45s; 換行 15/18 →
  1-2/18 on their recordings). The end sound plays only for a recording that goes to recognition (a tap that flashed the indicator played it before). A double tap's second press shows no indicator
  until it records (it flashed with a second start sound). Each press and gesture is logged (`press ...`,
  `gesture ...`).
- **Start sound cut out before recognition** (`cues.remove`, 2026-10-13, the user's choice: the recording counts from
  the end of the start sound): with nothing said, X-ASR heard the sound the microphone picked up as text (Bubble /
  Mallet / Kalimba "Yeah.", Triad "One" / "Four.", Blip "Blip."). It is found by cross-correlation in the 0.5s after it
  played and that stretch spliced out (10ms crossfade), keeping the room noise before it: dropping everything before its end, or
  silencing it, made X-ASR hear short commands wrong (送出 -> Somehow). The start sounds were shortened for this
  (their -40 dB tails reached 0.36-0.60s and the user's first word, ASR timestamps: 0.80s after the press, median,
  vs 0.48s before sounds existed, often came while the tail still played). User's recordings: silent presses with
  text 13/95 -> 2/95 (probably said); 換行 with the sound before the speech 204/216 (subtracting it: 210/216), but
  spoken over the sound 99/216 (subtracting: 186/216): words said before it ends are lost, the cost the user chose.
  A weak match (below `cues.MATCH` 0.6: headphones, or speech over it) falls back to `cues.cancel`, a least-squares
  FIR (256 taps, ~10 dB) subtracted. Saved recordings keep the raw audio.
- **Hold check uses the hook's key state** (`Hotkeys.is_down`, 2026-10-13): the 0.3s hold timer runs on the main
  thread, which a double tap's delete in a terminal keeps busy; the release was still queued, the second press
  counted as held, recorded (delete and say it again) and played the end sound. Now the timer asks the hook whether
  the key is still down, and `on_key_up` decides from the hook's times. The microphone hears the start
  sound: mixed into 91 of the user's recordings (not played through speakers), Woodblock, Kalimba and Marimba
  changed long sentences no more than inaudible noise (~2%), Blip, Glass, Triad, Xylophone, Mallet and Music Box
  ~5-7%, commands barely; the user kept all 12 until real recordings (with the sound picked up) can be checked.
  A 試聽 button follows each drop-down. Played from the wav file: winsound cannot play asynchronously from memory
  (RuntimeError), so the first version made no sound at all.
- **Pack hints** (`App._suggest_pack`, 2026-10-13): a word picked, typed in the menu or spelled that an unticked
  term pack lists brings a tray notice suggesting the pack, once per pack (`pack_hints` in config.json).
- **Spelling over a selection** (`edit.spelled`, 2026-10-13, the user's request): letters spoken over a selected
  English word (X-ASR writes "B L I P.") type that word in the selection's case: Bleep → Blip, API → APP, doggy →
  dog. It counts like a 選字 pick (priority +30%, and the old word becomes an alias: the user chose that knowing
  doggy would then always become dog).
- **Saved recordings** (`recordings.py`, 2026-10-13, DEVELOPMENT ONLY: the user will remove it before release so
  users never keep recordings locally; keep it in that module plus `App._keep`, the 一般 tab's 開發測試 row and
  `save_recordings` in config): when ticked, every utterance is saved to DATA_DIR/recordings/<time>.wav with an
  index.jsonl line (raw ASR, formatted, typed result or operation) and later picks / typed replacements /
  刪除剛輸入的文字 as lines pointing at the last saved id. Not deleted automatically (the user's choice). Use these
  for spot checks with the user's real voice instead of TTS; note the app decoded them with the hotwords and packs
  of that moment. `dev/headless.py` redirects them to its test folder.
- Tests without a microphone, with Windows TTS audio (zh-TW Hanhan voice via `dev/tts.ps1`):
  `dev/headless.py` runs the real App against a simulated text box; use it by default. Never let a test write the
  user's real data files while their VoiceInput is running.
  `dev/e2e.py` types into a real Notepad / Windows Terminal window: only when the user is away from the machine
  (Win11 Notepad opens new windows as tabs, and a run once typed into the user's own Notepad document).
