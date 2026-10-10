"""Settings persisted as config.json in the per-user data folder (see paths.py)."""
import json
from dataclasses import asdict, dataclass, field

from . import rules
from .paths import CONFIG_PATH


@dataclass
class Config:
    mic: str = ""                  # input device name, "" = system default
    autostart: bool = False
    mute_while_recording: bool = False  # mute the speakers while recording, until the text is pasted
    period_to_comma: bool = True   # speaking on a selected 。 turns it into ， + the utterance + 。
    rules: list = field(default_factory=rules.defaults)   # custom operations (rules.Rule)

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "rules"}
        cfg = cls(**known)
        if "rules" in data:
            cfg.rules = rules.migrate([rules.Rule.from_dict(d) for d in data["rules"]])
        else:   # before custom operations (2026-10-11): one hotkey and the 換行 / 送出 switches
            cfg.rules = rules.defaults(data.get("hotkey", "mouse_middle"), data.get("newline_command", True),
                                       data.get("send_command", True))
        return cfg

    def save(self) -> None:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
