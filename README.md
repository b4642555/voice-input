# VoiceInput

Offline push-to-talk voice input for Windows (macOS is not supported; see [docs/macos-port.md](docs/macos-port.md)). Hold **CapsLock**, speak, release — text is pasted into the focused app.
Single process: one tray icon, settings window, floating indicator. Speech recognition via [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx).

## Install / run

Install with `install.bat`, run with `start.bat`.

The installer puts uv, Python and the venv inside this folder. Nothing is installed system-wide.
If you move the folder, run the installer again (the venv records absolute paths).

User data lives outside the app folder, so it survives moving or reinstalling the app:

`%LOCALAPPDATA%\VoiceInput` holds settings, logs and `models\`.

Data from the old layout (`config.json`, `models/` inside the app folder) is moved there automatically on first launch.

## Usage

- Hold the hotkey (default CapsLock; right Alt / right Ctrl / F12 / a mouse side or middle button can be chosen) ≥ 0.3s to record; a quick tap still toggles CapsLock.
- Double-tap the hotkey to delete what you just dictated, or just spoke over a selection (the last one only, before
  the caret moves); hold the second press to say it again.
- Say only 逗號 / 句號 / 問號 / 驚嘆號 / 頓號 / 分號 / 冒號 / 點點點 to type that mark, 換行 for Shift+Enter, 送出 for Enter.
- Left-click the tray icon to open settings (microphone, hotkey, muting the speakers while recording, the 換行 / 送出
  commands, selected 。 → ，, launch at login) and a per-utterance log for this session. Changes apply immediately.
- Missing models are downloaded automatically on launch (progress shown in settings / tray tooltip).

## Correcting

Select text with the mouse, then:

| Hotkey | Does |
|---|---|
| double tap | delete the selection |
| hold and speak | replaces the selection |
| double tap, hold the second press and speak | deletes the selection, then types what you say |

## Models

Fixed [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) models, no LLM:

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
  hotkey.py    global hotkey, double tap (pynput; a low-level hook suppresses CapsLock)
  audio.py     microphone capture
  asr.py       sherpa-onnx recognizers, biasing toward on-screen terms
  textfmt.py   final text formatting (incl. repeated-word removal)
  session.py   what was just typed at the caret, deleting the last dictation
  edit.py      replacing a selection, punctuation names, 換行 / 送出
  suspects.py  second-opinion check for misheard words (off for now)
  selection.py selection / on-screen terms via UI Automation
  candidates.py word list (McBopomofo): is-a-word / frequency, same-sounding words
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
