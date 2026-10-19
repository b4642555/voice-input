# VoiceInput

Offline push-to-talk voice input for Windows (macOS is not supported; see [docs/macos-port.md](docs/macos-port.md)). Hold **CapsLock**, speak, release — text is pasted into the focused app.
Single process: one tray icon, settings window, floating indicator. Speech recognition via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).

## Install / run

1. Get the code: `git clone https://github.com/b4642555/voice-input` (or press **Code → Download ZIP** on the GitHub page
   and unzip it somewhere you will keep it).
2. Double-click `install.bat` (a few minutes: it downloads Python and the speech model, about 300MB), then `start.bat`.
3. The VoiceInput icon appears in the tray. Hold the **middle mouse button**, speak, release: the text is typed where
   your cursor is.
4. Optional, for cleaner text (no stray 嗯／呃, fixed homophones, lists): open the window → **AI 潤稿** → follow the
   Claude Code line (install, 登入 Claude). It uses your own Claude subscription; no key to paste.

Windows 10/11 only.

The installer puts uv, Python and the venv inside this folder. Nothing is installed system-wide.
If you move the folder, run the installer again (the venv records absolute paths).

User data lives outside the app folder, so it survives moving or reinstalling the app:

`%LOCALAPPDATA%\VoiceInput` holds settings, logs and `models\`.

Data from the old layout (`config.json`, `models/` inside the app folder) is moved there automatically on first launch.

## Usage

- By default: hold the middle mouse button ≥ 0.3s to record; double-click it to delete the selected text, or what
  you just dictated (the last one only, before the caret moves); hold the second press to say it again. Other middle
  clicks and double clicks still work.
- Say only 換行 for Shift+Enter, 送出 for Enter; 逗號 / 句號 / 問號 / 驚嘆號 / 頓號 / 分號 / 冒號 / 點點點 type that mark.
- Left-click the tray icon to open settings: 一般 (microphone, muting the speakers while recording, punctuation names,
  selected 。 → ，, 節日的節,
  launch at login), 辨識紀錄 (a per-utterance log for this session) and 自訂操作. Changes apply immediately.
- Click the middle button on selected text for 選字: same-sounding words, or ways of writing English (voiceInput,
  Voice Input …). A picked word gains recognition weight (熱詞); a picked single character is remembered with the
  characters around it and applied to later dictations (智慧選字). Or type the right text in the menu's input box
  (any input method): it replaces the selection and becomes a 熱詞. The menu closes on any click elsewhere.
- Say a word and the character you mean (節日的節, 這個字是教育的育) to type just that character.
- 自訂操作: choose a mouse button, keyboard key or spoken phrase, a gesture (tap / double tap / hold), a situation
  (any / right after dictating / text selected) and steps run in order: 錄音, 選字, delete the selection,
  delete what was just dictated, a key combination, text, a wait.
- For a mouse button mapped to ordinary Alt in Logi Options+ or another remapper, choose the keyboard trigger
  **Alt（左右皆可）**. It accepts either Alt key and mapped Alt presses. **右 Alt** remains right-side-only.
- 熱詞: words with a weight that biases recognition: typed in the 選字 menu (highest weight) or picked in it
  (weight grows with each pick, drops when picked away). Listed with their weights (and % of the cap) on the 熱詞 tab.
  Steps after 錄音 run once the text is typed (e.g. 錄音 → Enter: speak and send).
- Missing models are downloaded automatically on launch (progress shown in settings / tray tooltip).

## AI 潤稿 (optional, free)

The easiest way is **Claude Code with your own Claude subscription** (no API key, nothing to paste): install
[Claude Code](https://claude.com/product/claude-code), then in the AI 潤稿 page press 「登入 Claude」 (it opens
`claude auth login` and your browser) and 「重新檢查」. The page shows whether Claude Code is missing, installed
but signed out, or ready, with a button for the next step. VoiceInput talks to your local `claude` only; it never
sees your credentials, and every dictation costs a little of your own Claude quota (turn AI off in the tray menu
or pick 「省額度」 to spend less). No Claude subscription? Paste a free API key instead (Groq or Google Gemini; or
run Ollama locally, no key). Dictations are then cleaned up like Typeless: fillers and repeats removed, self-corrections resolved
(「三點，不對，四點」→「四點」), spoken lists numbered, paragraphs split, tone matched to the app. If the AI is
unreachable the plain transcript is pasted.

History (searchable, copy again), the dictionary (字典) and stats are in the main window. Set a 同步資料夾
(OneDrive / Google Drive / Dropbox) in 設定 to share the dictionary, smart picks and history between computers.

## Names on screen (no AI needed)

While you speak, VoiceInput reads the foreground window (a chat, a page, a mail) and picks out names and topic words
(張雨薇, 薔薇計畫). Recognition leans toward them, and a same-sounding miss (張玉薇) is written the way the screen
writes it. Common words are never changed. The screen text is not saved or logged; the words are forgotten after
each dictation.

## Correcting

Select text with the mouse, then:

| Default (middle button) | Does |
|---|---|
| double tap | delete the selection |
| hold and speak | replaces the selection |
| double tap, hold the second press and speak | deletes the selection, then types what you say |

## Models

Fixed [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) models (the optional AI 潤稿 LLM is a remote or Ollama endpoint):

| Model | Size | Role |
|---|---|---|
| X-ASR int8 | 130MB | Recognition (~0.75s for 18s of audio on CPU); punctuation, good English |
| SenseVoice Small | 155MB | Second opinion (off for now, not downloaded) |

## Pipeline

ASR (biased toward English terms on screen) → formatting (`textfmt.py`) → paste.

- **Formatting**: Traditional Chinese glyphs (OpenCC `s2tw`, wording kept), repeated words and restarts removed
  (我們我們, 先打開那個，先打開設定頁), multi-character Chinese numerals → digits (single-character ones like 一個 / 兩個
  stay), 百分之X / X percent → X%, 嗯 / 呃 removed, spelled letters joined (A P I → API), 點 between digits/letters →
  `.`, half-width punctuation inside English, a space between CJK and English/digits, no sentence-final
  punctuation when inserting between text on the caret's line.

## Layout

```
voiceinput/
  app.py       tray, wiring, push-to-talk flow
  hotkey.py    hooks for the keys and buttons operations use (pynput, suppressed)
  rules.py     custom operations: triggers, gestures, situations, steps
  keys.py      trigger keys and key-combination names
  audio.py     microphone capture
  asr.py       sherpa-onnx recognizers, biasing toward on-screen terms
  textfmt.py   final text formatting (incl. repeated-word removal)
  session.py   what was just typed at the caret, deleting the last dictation
  edit.py      replacing a selection, punctuation names
  suspects.py  second-opinion check for misheard words (off for now)
  selection.py selection / on-screen terms via UI Automation
  candidates.py word list (McBopomofo): is-a-word / frequency, same-sounding words
  memory.py    smart picks: single characters remembered with their context, English writing styles
  hotwords.py  the weighted hotword list (recognition bias)
  reading.py   text as tokens with pinyin, for matching by sound
  picker.py    the 選字 menu
  overlay.py   floating recording / thinking indicator
  settings.py  settings dialog + transcript log
  output.py    clipboard paste, Delete / Backspace
  models.py    the fixed model set + downloader
  paths.py     app folder vs per-user data folder, legacy data migration
  autostart.py launch at login
dev/
  headless.py  end-to-end test with TTS audio against a simulated text box
  e2e.py       the same against a real Notepad / Windows Terminal window
  tts.ps1      Windows zh-TW TTS to 16kHz wav
```
