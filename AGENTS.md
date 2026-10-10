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
the 選字 menu (old look, `picker.py`), hotwords (a plain word list) and pick memory (`memory.py`); see below.

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

`asr.py` (raw text) → `textfmt.format_text` → smart picks (`memory.apply`) → Chinese hotwords (`hotwords.apply`,
absolute, so last) → paste. Order inside `format_text` matters: s2tw → filler
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
，", autostart, menu timeout (default 10s, back 2026-10-11, for the 選字 menu and the hotword notice), a button that
opens smart-picks.json), 辨識紀錄 (the transcript log), 自訂操作 (custom operations, below) and 熱詞 (the hotword list,
remove only). Mute (2026-10-06, `mute.py`): Core Audio via
comtypes; muted when the recording indicator shows, unmuted once the text is pasted or the press ends in nothing; a
mute the user set is left alone. Selection edits and on-screen terms are always on.

## Custom operations (自訂操作, 2026-10-11)

`rules.py`, stored as `rules` in config.json; they replaced the single hotkey setting and the 換行 / 送出 switches
(migrated by `Config.load`). A rule: trigger (滑鼠鍵: middle / upper / lower side button; 鍵盤鍵: CapsLock, right Alt,
right Ctrl, Insert, ScrollLock, Pause, F1-F24; 說出: an utterance that is exactly the phrase) → 按法 (按一下 / 連按兩下
/ 按住; not for speech) → 情境 (不限 / 剛輸入完 / 有選取文字; the most specific wins) → steps run in order: 命令 (錄音,
選字, 加入熱詞, 刪除選取的文字, 刪除剛輸入的文字), 按鍵 (a captured combination such as Ctrl+Z, sent with SendInput), 輸入文字 (pasted, counts as a dictation),
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

## 選字, smart picks and hotwords (2026-10-11)

Only two popups exist (`picker.py`, the pre-lean look; a redesign is planned): the 選字 menu and the hotword notice.
- **選字** (command): the menu under the selection lists what the smart picks decide in this context, everything
  picked for that reading before (newest first), then homophones (`candidates.homophones`, Chinese: common words (frequency ≥ 5) first, since McBopomofo
  lists obscure entries like 逆號 at 1; then same reading with tones, without, fuzzy; then frequency) or ways of writing
  English (`memory.variants`: Claude / claude / CLAUDE; voiceInput / VoiceInput / Voice Input / voice input / VOICE
  INPUT / voice_input / voice-input / voiceinput), the styles the user picks most first, counted apart for one word
  and for several. 8 rows. A pick replaces the selection and is recorded.
- **Smart picks** (`memory.py`, DATA_DIR/smart-picks.json, one example per line so the user can read it): modelled on
  art-shuangpin's core/user_preferences (spec §7): an example is key (toned pinyin per character / casefolded words)
  + left (two characters, `^` boundary) + right (two toneless readings) + value. Applied after formatting: most
  agreeing tokens wins, then the right side, the boundary, the newest; one Chinese character needs an agreeing token
  and is left alone on a tie; a phrase applies anywhere (the user's choice: picked once, used everywhere until picked
  back elsewhere). A pick deletes older examples with another value whose context does not conflict.
- **Hotwords** (`hotwords.py`, DATA_DIR/hotword-list.json; the old key -> value hotwords.json is not read): added by
  the 加入熱詞 command, and by picking an English spelling in 選字 (the user's spelling; it replaces other spellings
  of the same words). Added at once; the notice offers 撤銷 (which also brings replaced spellings back). Removed on
  the 熱詞 tab. All bias X-ASR at 3.0, the
  highest. Chinese ones are also absolute (the user's choice, because X-ASR barely follows Chinese biasing in our
  tests): any run read exactly like one, tones included, becomes it, after the smart picks.
- Dropped the same day: picks raising a word's X-ASR bias (a hidden weight); Chinese biasing is too weak and the user
  chose not to keep it for English alone.

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
- **Clipboard**: every format is restored after a paste (images copied before dictating used to be lost).
- Tests without a microphone, with Windows TTS audio (zh-TW Hanhan voice via `dev/tts.ps1`):
  `dev/headless.py` runs the real App against a simulated text box; use it by default. Never let a test write the
  user's real data files while their VoiceInput is running.
  `dev/e2e.py` types into a real Notepad / Windows Terminal window: only when the user is away from the machine
  (Win11 Notepad opens new windows as tabs, and a run once typed into the user's own Notepad document).
