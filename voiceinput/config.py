"""Settings persisted as config.json in the per-user data folder (see paths.py)."""
import json
from dataclasses import asdict, dataclass, field

from . import rules
from .rules import VERSION as RULES_VERSION
from .paths import CONFIG_PATH


GESTURE_TIME_RANGE = (0.15, 0.8)


@dataclass
class Config:
    mic: str = ""                  # input device name, "" = system default
    autostart: bool = False
    mute_while_recording: bool = True   # mute the speakers while listening
    period_to_comma: bool = True   # speaking on a selected 。 turns it into ， + the utterance + 。
    punctuation_names: bool = True  # an utterance of just a punctuation name (逗號, 句號 …) types the mark
    describe_char: bool = True     # 節日的節 types 節 (edit.described)
    hotword_sort: str = "date"     # order of the 詞庫 tab (settings.HOTWORD_SORTS)
    pick_sort: str = "date"        # order of 選字記憶 on the 詞庫 tab (settings.PICK_SORTS)
    alias_sort: str = "date"       # order of 英文強制替換 (settings.ALIAS_SORTS)
    hotword_section: str = ""      # the open section of the 詞庫 tab ("" none, packs, words, picks, aliases)
    packs: list = field(default_factory=lambda: ["ai"])   # ids of the ticked term packs (packs.py)
    pack_hints: list = field(default_factory=list)   # packs a notice already suggested (App._suggest_pack)
    start_sound: str = "blip"     # cues.py id played when the recording indicator shows, "" for none
    stop_sound: str = "blip"      # cues.py id played when the recording ends, "" for none
    # seconds: a press this long is a hold, a press this soon after a tap's release makes a double tap (on the
    # 自訂操作 tab since 2026-10-13; 0.3 / 0.35 constants before, made the same at the user's request)
    gesture_time: float = 0.15
    stop_sound_at: str = "stop"   # when the end sound plays: "stop" (recording stops) or "done" (text is in)
    save_recordings: bool = False   # development only, to be removed before release (recordings.py)
    rules: list = field(default_factory=rules.defaults)   # custom operations (rules.Rule)
    rules_version: int = RULES_VERSION

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k not in ("rules", "rules_version")}
        cfg = cls(**known)
        cfg.gesture_time = min(max(float(cfg.gesture_time), GESTURE_TIME_RANGE[0]), GESTURE_TIME_RANGE[1])
        if "rules" in data:
            cfg.rules = rules.upgrade(rules.migrate([rules.Rule.from_dict(d) for d in data["rules"]]),
                                      data.get("rules_version", 1))
        else:   # before custom operations (2026-10-11): one hotkey and the 換行 / 送出 switches
            cfg.rules = rules.defaults(data.get("hotkey", "mouse_middle"), data.get("newline_command", True),
                                       data.get("send_command", True))
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
