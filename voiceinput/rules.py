"""Custom operations (自訂操作, 2026-10-11): what a mouse button, a keyboard key or a spoken phrase does.

A rule is a trigger (a mouse button or keyboard key with a gesture: tap, double tap or hold; or an utterance that is
exactly a phrase), a situation (any, right after a dictation, text selected) and a list of steps run in order:
a command (錄音 record, 刪除選取的文字, 刪除剛輸入的文字, 選字), a key combination, text to type, or a wait.
錄音 is continuous listening (listen.py; on the continuous-mic branch since 2026-10-14): every pause ends a piece
that is typed at once or run as a command. Hold + 錄音 listens while held; tap or double tap + 錄音 turns it on and
the next tap or double tap that records turns it off (other gestures work meanwhile). A rule's steps before 錄音 run
when listening starts, the ones after it when it stops. 刪除選取的文字 works in terminals too; 刪除剛輸入的文字
deletes the last dictation while the caret is still after it (split from one 刪除 command 2026-10-11, the user's
choice). 選字 opens the candidate menu for the selection.

For one trigger and gesture the most specific situation wins: selected text, then right after a dictation, then
any. Unbound gestures are given back to the key (a keyboard key is replayed, a mouse button clicked).
"""
import re
from dataclasses import asdict, dataclass, field

MOUSE, KEY, SPEECH = "mouse", "key", "speech"
TAP, DOUBLE, HOLD = "tap", "double", "hold"
ANY, DICTATED, SELECTION = "any", "dictated", "selection"
COMMAND, KEYS, TEXT, WAIT = "command", "keys", "text", "wait"
RECORD, DELETE_SELECTION, DELETE_DICTATION = "record", "delete_selection", "delete_dictation"
PICK = "pick"
_LISTEN = "listen"   # 連續聽寫, a command for a few hours on 2026-10-14 until 錄音 itself became continuous
_ADD_HOTWORD = "add_hotword"   # 加入熱詞, removed 2026-10-11: hotwords are typed in the 選字 menu instead
VERSION = 4   # config rules_version: 2 added the tap on a selection that opens 選字, 3 a tap that listens, 4 drops 連續聽寫

_PUNCT = re.compile(r"[\s，。、！？；：,.!?;:…]+")


@dataclass
class Step:
    type: str = COMMAND
    value: object = RECORD      # command name, key combination ("Ctrl+Z"), text, or milliseconds to wait


@dataclass
class Rule:
    source: str = MOUSE
    key: str = "mouse_middle"   # mouse / keyboard trigger: a name in keys.MOUSE_TRIGGERS / keys.KEY_TRIGGERS
    phrase: str = ""            # speech trigger: the whole utterance
    gesture: str = HOLD         # not used by speech triggers
    when: str = ANY
    steps: list = field(default_factory=lambda: [Step()])
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "steps"}
        return cls(**known, steps=[Step(**s) for s in d.get("steps", [])])

    def to_dict(self) -> dict:
        return asdict(self)

    def record_index(self) -> int | None:
        return next((i for i, s in enumerate(self.steps) if s.type == COMMAND and s.value == RECORD), None)


def defaults(key: str = "mouse_middle", newline: bool = True, send: bool = True) -> list[Rule]:
    """The user's own setup (2026-10-14, their request): tap to listen until the next tap; double tap to delete the
    selection, or what was just dictated; hold on a selection for 選字; spoken 換行 / 送出 press Shift+Enter / Enter,
    標點 types "- ", 括號 types （） with the caret inside, 貼上 presses Alt+V, 空白 presses Space."""
    source = MOUSE if key.startswith("mouse") else KEY
    out = [Rule(source, key, gesture=TAP, steps=[Step(COMMAND, RECORD)]),
           Rule(source, key, gesture=DOUBLE, when=SELECTION, steps=[Step(COMMAND, DELETE_SELECTION)]),
           Rule(source, key, gesture=DOUBLE, when=DICTATED, steps=[Step(COMMAND, DELETE_DICTATION)]),
           Rule(source, key, gesture=HOLD, when=SELECTION, steps=[Step(COMMAND, PICK)])]
    if newline:
        out.append(Rule(SPEECH, phrase="換行", steps=[Step(KEYS, "Shift+Enter")]))
    if send:
        out.append(Rule(SPEECH, phrase="送出", steps=[Step(KEYS, "Enter")]))
    out += [Rule(SPEECH, phrase="標點", steps=[Step(TEXT, "- ")]),
            Rule(SPEECH, phrase="括號", steps=[Step(TEXT, "（）"), Step(KEYS, "Left")]),
            Rule(SPEECH, phrase="貼上", steps=[Step(KEYS, "Alt+V")]),
            Rule(SPEECH, phrase="空白", steps=[Step(KEYS, "Space")])]
    return out


def migrate(rules: list[Rule]) -> list[Rule]:
    """The one 刪除 command (before the split, 2026-10-11) becomes the command for the rule's situation; a rule for
    any situation becomes two: one for a selection, one right after a dictation."""
    out = []
    for r in rules:
        if any(s.type == COMMAND and s.value == _ADD_HOTWORD for s in r.steps):
            r.steps = [s for s in r.steps if not (s.type == COMMAND and s.value == _ADD_HOTWORD)]
            if not r.steps:
                continue
        if not any(s.type == COMMAND and s.value == "delete" for s in r.steps):
            out.append(r)
            continue
        for when, command in ((SELECTION, DELETE_SELECTION), (DICTATED, DELETE_DICTATION)):
            if r.when in (when, ANY):
                steps = [Step(COMMAND, command) if s.type == COMMAND and s.value == "delete" else Step(s.type, s.value)
                         for s in r.steps]
                out.append(Rule(r.source, r.key, r.phrase, r.gesture, when, steps, r.enabled))
    return out


def upgrade(rules: list[Rule], version: int) -> list[Rule]:
    """Defaults added after a config was saved, on the key that records by holding unless that tap is taken:
    version 2 adds tap on a selection = 選字, version 3 tap = 錄音 (listening until the next tap). Version 4: the
    連續聽寫 command became 錄音."""
    hold = next((r for r in rules if r.source != SPEECH and r.gesture == HOLD and r.record_index() is not None), None)
    if hold and version < 2:
        if not any(r.key == hold.key and r.gesture == TAP and r.when in (SELECTION, ANY) for r in rules):
            rules.append(Rule(hold.source, hold.key, gesture=TAP, when=SELECTION, steps=[Step(COMMAND, PICK)]))
    if hold and version < 3:
        if not any(r.key == hold.key and r.gesture == TAP and r.when == ANY for r in rules):
            rules.append(Rule(hold.source, hold.key, gesture=TAP, steps=[Step(COMMAND, RECORD)]))
    if version < 4:
        for r in rules:
            r.steps = [Step(COMMAND, RECORD) if st.type == COMMAND and st.value == _LISTEN else st for st in r.steps]
    return rules


def normalize(phrase: str) -> str:
    return _PUNCT.sub("", phrase).casefold()


def _same_trigger(a: Rule, b: Rule) -> bool:
    if a.source != b.source:
        return False
    if a.source == SPEECH:
        return normalize(a.phrase) == normalize(b.phrase)
    return a.key == b.key and a.gesture == b.gesture


def candidates(rules: list[Rule], source: str, trigger: str, gesture: str = "") -> list[Rule]:
    """Enabled, valid rules for a trigger (a key name, or the utterance for speech) and gesture."""
    probe = Rule(source, key=trigger, phrase=trigger, gesture=gesture)
    return [r for r in rules if r.enabled and _same_trigger(r, probe) and not problems(r, rules)]


def pick(cands: list[Rule], selection: bool, dictated: bool) -> Rule | None:
    """The rule for the situation: selected text first, then right after a dictation, then any."""
    for when, applies in ((SELECTION, selection), (DICTATED, dictated), (ANY, True)):
        if applies:
            for r in cands:
                if r.when == when:
                    return r
    return None


def problems(rule: Rule, rules: list[Rule]) -> list[str]:
    """Why a rule cannot work (shown under it in the settings); it is skipped while it has any."""
    out = []
    if rule.source == SPEECH and not normalize(rule.phrase):
        out.append("請輸入要說的話")
    records = sum(1 for s in rule.steps if s.type == COMMAND and s.value == RECORD)
    if rule.source == SPEECH and records:
        out.append("「說出」的操作不能錄音")
    elif records > 1:
        out.append("一個操作最多一個「錄音」")
    if any(s.type == KEYS and not s.value for s in rule.steps):
        out.append("按鍵步驟還沒設定組合鍵")
    if not rule.steps:
        out.append("沒有步驟")
    if rule.enabled:
        for r in rules:
            if r is rule:
                break
            if r.enabled and _same_trigger(r, rule) and r.when == rule.when:
                out.append("和上面一條操作的觸發、按法、情境都相同，這條不會生效")
                break
    return out


def bound_keys(rules: list[Rule]) -> set[str]:
    """Mouse buttons and keyboard keys some rule uses: the hooks take these over."""
    return {r.key for r in rules if r.enabled and r.source != SPEECH and not problems(r, rules)}


def can_record(rules: list[Rule]) -> bool:
    return any(r.enabled and r.source != SPEECH and r.record_index() is not None and not problems(r, rules)
               for r in rules)
