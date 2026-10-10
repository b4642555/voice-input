# VoiceInput — notes for coding agents

## Working with the user

- Whenever you can infer what the user is really after and see a better approach or a missing detail,
  stop and ask before building, as many rounds as needed, with the recommended option first.
- Don't guess between materially different designs; asking is cheaper than rebuilding.
- Ask clarifying questions with the AskUserQuestion tool (when available); use it freely.
- After every commit, restart VoiceInput if it is running so the user is testing the new code (don't start it if
  it isn't running). PowerShell:

  ```powershell
  $root = 'E:\Projects\voice-input'
  $procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*$root\run.pyw*" }
  if ($procs) {
      $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
      $procs | ForEach-Object { Wait-Process -Id $_.ProcessId -Timeout 10 -ErrorAction SilentlyContinue }
      Start-Process "$root\.venv\Scripts\pythonw.exe" -ArgumentList "`"$root\run.pyw`"" -WorkingDirectory $root
  }
  ```

  Then check the tail of `%LOCALAPPDATA%\VoiceInput\voiceinput.log` for "就緒" / errors.

## Lean branch (2026-10-10)

On the `lean` branch every popup was removed (選字 candidate menu, suspected-mishearing menu, context hotword
question, the hotword ✓ box) together with hotwords (`hotwords.py`, the hotwords page, 自動判斷 / 一律取代, learned
biasing), `picker.py`, `picks.py`, re-dictation and hand-made-edit detection, and the settings that only served
menus (fix menu, fuzzy readings, menu timeout). The user plans to redesign popups from scratch. The second opinion is
off: SenseVoice is not loaded and the suspects code in `app.py` is commented out (`suspects.py` and
`candidates.homophones`, which it needs, are kept). Tray error notices (model not ready, mic failure, terminal
selection lost) stay. A single tap of the hotkey is a plain key press, also on a selection. Sections below describe
the branch; history of the removed features is in git (main, up to 71e5516).

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

`asr.py` (raw text) → `textfmt.format_text` → paste. Order inside `format_text` matters: s2tw → filler
removal → disfluencies → 百分之 → numerals → spelled letters → 點 → percent → number+letter → English punctuation →
CJK/ASCII spacing → trailing punctuation.

- Trailing punctuation (2026-10-07, replacing the "strip trailing punctuation" setting): the model's sentence-final
  。，,. is kept, except when the caret's line has non-blank text on both sides of it (the user is inserting).
  `selection.read_caret` reads that at key-down with the selection probe: UIA in normal apps, the input box row the
  Windows Terminal cursor is on in terminals (same behaviour everywhere, the user's requirement). Unknown → kept.

- OpenCC `s2tw` only (glyph conversion). `s2twp` was dropped because it rewrites vocabulary (程序 → 程式).
- After s2tw, 臺 is mapped back to 台 (s2tw turns 台北 into 臺北; the user wants 台).
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

## Settings (2026-09-29)

Only microphone, hotkey (CapsLock / right Alt / right Ctrl / F12 / the mouse's upper or lower side button XButton2 /
XButton1, added 2026-10-06, or the middle button, 2026-10-08: suppressed by a mouse hook and never replayed: the
button's own action is replaced entirely), the 換行 / 送出 commands and "selected 。 → ，" (all three on by default,
2026-10-07), autostart and "mute the speakers while recording" (off by default, added 2026-10-06, `mute.py`: Core
Audio via comtypes; muted when the recording indicator shows, unmuted once the text is pasted or the press ends in
nothing; a mute the user set is left alone). Selection edits and on-screen terms are always on.

## Voice edits on selected text

Plan agreed 2026-09-27, cut down 2026-09-29, spoken triggers replaced by the hotkey 2026-09-30. The selection is
read when the hotkey goes down (`selection.py`, UI Automation TextPattern, on a background thread). On a selection:
double tap → Delete key; hold and speak → replaces it; double tap with the second press held → Delete at the second
press, then the dictation is pasted there. A single tap is a plain key press (lean branch). There are no spoken
commands on a selection. CapsLock: the app, not `hotkey.py`, decides replays (`PushToTalk.tap()`): a single tap is
replayed at once; on a double tap one more tap is replayed (undoing the first tap's toggle). Other hotkeys: a
single tap is replayed, a tapped second press only when there is no selection.
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
answers `is_word` / `frequency` for disfluencies; `homophones` is kept for the (commented-out) suspects. jieba was
used until 2026-10-02 (commit 58c1535) and dropped for missing Taiwanese words and mainland frequencies.

## After a dictation (2026-09-29)

Voice commands on what was just dictated (復原, 送出, 換行, 刪掉上一句, X改成Y, 刪掉X, symbol names, trailing 送出) were
removed the same day they were built: the user corrects by selecting with the mouse instead. What remains:

- **Punctuation names** (restored 2026-09-30, `edit.symbol`): an utterance that is exactly 逗號 / 句號 / 句點 / 問號 /
  驚嘆號 / 感嘆號 / 頓號 / 分號 / 冒號 / 點點點 / 刪節號 / 省略號 types the mark, appended after any existing
  punctuation (the user chose not to replace it). Exact text only: 都好 is not 逗號. On a selection it replaces it.
  Commands (2026-10-07, each a setting, `edit.command`): an utterance of exactly 換行 presses Shift+Enter, 送出
  presses Enter; same exact-text rule.
- **Selected 。** (setting, 2026-10-07): speaking on a selected 。 replaces it with ， + the utterance + 。
  (今天很好。明天見 → 今天很好，我們去公園。明天見).

- **Dictated-text buffer (`session.py`)**: the text we pasted is known to sit right before the caret until the user
  presses a key (keyboard hook, injected keys ignored), clicks (mouse hook) or another
  window comes to the front. Changes to it are Backspace up to the edit point (one `SendInput` burst) + paste of
  the new tail; works in terminals too.
- **Double tap of the hotkey (`hotkey.py`)**, nothing selected, deletes the last dictation while it is
  still the last change and the caret has not moved; only that one (2026-10-04: the user had undo of picked
  suggestions and repeated undo removed). Text spoken over a selection counts as a dictation (2026-10-07, the
  user's model: like typing over it): a double tap deletes it, the selected text stays gone (a selected 。 turned
  into ，…。 gets its 。 back). Otherwise the taps are plain. Holding the second
  press records again. CapsLock: the
  two replayed taps cancel out; a held second press replays one extra tap to undo the first tap's toggle.
  Recording starts at key-down, but the indicator (overlay, tray icon) only after 0.1s (0.3s = TAP_THRESHOLD, then 0.2s,
  felt slow), so taps and double taps do not flash it.
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
