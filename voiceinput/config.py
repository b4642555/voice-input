"""Settings persisted as config.json in the per-user data folder (see paths.py). The AI API key is stored
encrypted for the Windows user (DPAPI), so a copied config.json does not give it away."""
import base64
import ctypes
import ctypes.wintypes as wt
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field

from . import rules
from .rules import VERSION as RULES_VERSION
from .paths import CONFIG_PATH


@dataclass
class Config:
    mic: str = ""                  # input device name, "" = system default
    autostart: bool = False
    # off and not offered: on the user's PC the capture sessions share one mute, so muting Discord muted our own
    # recording too (flat waveform, poor recognition, 2026-10-15). Kept for a later approach.
    mic_exclusive: bool = False
    mouse_text_only: bool = True   # mouse buttons used as shortcuts act only on an input box; elsewhere they pass through
    voice_stop: bool = True        # saying 送出 (an operation that presses Enter) ends the recording at once (app._voice_tick)
    discord_mute: bool = False     # tap Discord's shortcut when a recording starts and when it stops (discord.py)
    discord_key: str = "Ctrl+Shift+D"   # Discord's own default 切換拒聽
    mute_while_recording: bool = False  # mute the speakers while recording, until the text is pasted
    period_to_comma: bool = True   # speaking on a selected 。 turns it into ， + the utterance + 。
    punctuation_names: bool = True  # an utterance of just a punctuation name (逗號, 句號 …) types the mark
    describe_char: bool = True     # 節日的節 types 節 (edit.described)
    copy_key: str = "Enter"        # copies the last dictation while the 複製最後的轉錄 card is up (copycard.py)
    tech_terms: bool = True        # English tech / software / game names heard wrong are written right (techterms.py)
    casual_no_period: bool = True  # a chat-length dictation ends without 。 / . (textfmt.drop_casual_period)
    theme: str = "mist"            # main window colours (settings.THEMES); the blue one the user picked
    laugh_enabled: bool = False    # experiment: SenseVoice listens for laughter (loaded only when on, +150MB)
    laugh_text: str = "哈哈"       # what is typed when laughter is heard
    hotword_sort: str = "weight"   # order of the 熱詞 tab (settings.HOTWORD_SORTS)
    rules: list = field(default_factory=rules.defaults)   # custom operations (rules.Rule)
    rules_version: int = RULES_VERSION
    # AI 潤稿 (llm.py)
    ai_enabled: bool = False
    ai_provider: str = "codex_cli"   # the subscriptions: Claude, then Flash (when on), then GPT (llm._with_fallback)
    ai_base_url: str = ""          # "" = the provider's
    ai_model: str = ""             # "" = the provider's default
    ai_claude_model: str = ""      # the Claude model, "" = the default; ai_model is the GPT model, ai_flash_model Flash's
    ai_flash_enabled: bool = False  # user accepts agy's conversation persistence
    ai_flash_model: str = ""
    ai_key: str = ""               # kept in memory in plain text, saved encrypted
    ai_style: str = "polish"
    ai_prompt: str = ""            # the user's own cleanup rules (AI 潤稿 page); "" = the built-in ones (llm.default_prompt)
    ai_scope: str = "all"          # "all": every dictation is checked by the AI; "saver": only suspicious / long ones
    ai_context: bool = True        # send the app name, window title and the text near the caret
    sync_dir: str = ""             # 同步 folder (sync.py), "" = the data folder

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k not in ("rules", "rules_version")}
        cfg = cls(**known)
        if cfg.ai_provider == "claude_cli":      # merged into the one subscription chain (2026-10-19)
            cfg.ai_claude_model, cfg.ai_model = cfg.ai_model or cfg.ai_claude_model, data.get("ai_gpt_model", "")
            cfg.ai_provider = "codex_cli"
        cfg.ai_key = _unprotect(cfg.ai_key)
        from . import llm
        if "ai_provider" not in data:
            cfg.ai_provider = ("codex_cli" if llm.codex_cli.cli_path() else
                               "claude_cli" if llm.claude_cli_path() else "gemini")
        if "ai_enabled" not in data:   # Existing configs keep an explicit off switch; enable a detected subscription client only once.
            cfg.ai_enabled = cfg.ai_provider in ("codex_cli", "claude_cli") and (llm.codex_cli.cli_path() is not None
                                                                            or llm.claude_cli_path() is not None)
        if "rules" in data:
            cfg.rules = rules.upgrade(rules.migrate([rules.Rule.from_dict(d) for d in data["rules"]]),
                                      data.get("rules_version", 1))
        else:   # before custom operations (2026-10-11): one hotkey and the 換行 / 送出 switches
            cfg.rules = rules.defaults(data.get("hotkey", "mouse_middle"), data.get("newline_command", True),
                                       data.get("send_command", True))
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=CONFIG_PATH.parent,
                    prefix=f".{CONFIG_PATH.name}.", suffix=".tmp", delete=False) as temp:
                temp_path = temp.name
                data = asdict(self)
                data["ai_key"] = _protect(self.ai_key)
                json.dump(data, temp, ensure_ascii=False, indent=2)
                temp.write("\n")
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, CONFIG_PATH)
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(dst)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _protect(key: str) -> str:
    if not key:
        return ""
    try:
        return "dpapi:" + base64.b64encode(_crypt(key.encode("utf-8"), True)).decode("ascii")
    except OSError:
        return key


def _unprotect(value: str) -> str:
    if not value.startswith("dpapi:"):
        return value
    try:
        return _crypt(base64.b64decode(value[6:]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""
