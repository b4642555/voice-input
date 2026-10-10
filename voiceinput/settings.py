"""Settings window in four tabs: 一般 (general), 辨識紀錄 (live transcript log), 自訂操作 (custom operations,
rules.py) and 熱詞 (hotwords.py: every word with its weight and source, removable). Every change is applied
and saved immediately."""
import html
import os

from pypinyin import lazy_pinyin
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTabWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

from . import audio, hotwords, rules
from .keys import KEY_TRIGGERS, MOUSE_TRIGGERS, combo_text, is_modifier
from .rules import Rule, Step

SOURCES = [(rules.MOUSE, "滑鼠鍵"), (rules.KEY, "鍵盤鍵"), (rules.SPEECH, "說出")]
GESTURES = [(rules.TAP, "按一下"), (rules.DOUBLE, "連按兩下"), (rules.HOLD, "按住")]
SITUATIONS = [(rules.ANY, "不限"), (rules.DICTATED, "剛輸入完"), (rules.SELECTION, "有選取文字")]
STEP_TYPES = [(rules.COMMAND, "命令"), (rules.KEYS, "按鍵"), (rules.TEXT, "輸入文字"), (rules.WAIT, "等待")]
COMMANDS = [(rules.RECORD, "錄音"), (rules.PICK, "選字"),
            (rules.DELETE_SELECTION, "刪除選取的文字"),
            (rules.DELETE_DICTATION, "刪除剛輸入的文字")]
_STEP_DEFAULTS = {rules.COMMAND: rules.DELETE_SELECTION, rules.KEYS: "", rules.TEXT: "", rules.WAIT: 100}

HOTWORD_SORTS = [("weight", "權重比例（高→低）"), ("name", "名稱 A→Z"), ("name_desc", "名稱 Z→A"),
                 ("date", "日期（新→舊）"), ("date_old", "日期（舊→新）")]
_CARD_STYLE = "QFrame#card { border: 1px solid palette(mid); border-radius: 6px; }"


def _scroll_instead(widget, e):
    """Give a wheel event to the scroll area around widget: on the list of operations the wheel scrolls and never
    changes a value (the user kept changing them by accident); only a click opens a drop-down."""
    area = widget.parentWidget()
    while area is not None and not isinstance(area, QScrollArea):
        area = area.parentWidget()
    if area is not None:
        QApplication.sendEvent(area.viewport(), e)


class _NoWheelCombo(QComboBox):
    def wheelEvent(self, e):
        _scroll_instead(self, e)


class _NoWheelSpin(QSpinBox):
    def wheelEvent(self, e):
        _scroll_instead(self, e)


def _combo(items, value, tip="") -> QComboBox:
    c = _NoWheelCombo()
    for key, label in items:
        c.addItem(label, key)
    c.setCurrentIndex(max(0, c.findData(value)))
    if tip:
        c.setToolTip(tip)
    return c


class KeyCapture(QLineEdit):
    """Click, then press a key combination: shows and stores it as text (Ctrl+Shift+Z)."""
    captured = Signal(str)

    def __init__(self, text: str):
        super().__init__(text, placeholderText="點這裡後按下組合鍵")
        self.setReadOnly(True)

    def event(self, e):
        if e.type() == QEvent.KeyPress:   # here, not keyPressEvent: Tab would move the focus first
            self._key(e)
            return True
        return super().event(e)

    def _key(self, e):
        vk = e.nativeVirtualKey()
        if is_modifier(vk):
            return
        mods = e.modifiers()
        names = [n for n, flag in (("Ctrl", Qt.ControlModifier), ("Shift", Qt.ShiftModifier),
                                   ("Alt", Qt.AltModifier), ("Win", Qt.MetaModifier)) if mods & flag]
        text = combo_text(names, vk)
        if text:
            self.setText(text)
            self.captured.emit(text)


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, cfg, hotwords, memory_path):
        super().__init__()
        self.cfg = cfg
        self.hotwords = hotwords
        self.memory_path = memory_path
        self._loading = False
        self.setWindowTitle("VoiceInput 設定")
        self.resize(620, 560)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_page(), "一般")
        self.tabs.addTab(self._log_page(), "辨識紀錄")
        self.tabs.addTab(self._rules_page(), "自訂操作")
        self.tabs.addTab(self._hotwords_page(), "熱詞")

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)

    # --- 一般 ---
    def _general_page(self) -> QWidget:
        self.mic = QComboBox()
        self.mic.addItem("系統預設", "")
        for name in audio.list_input_devices():
            self.mic.addItem(name, name)
        self.punct = QCheckBox("只說標點符號的名字時，輸入該符號")
        self.punct.setToolTip("逗號、句號、句點、問號、驚嘆號、感嘆號、頓號、分號、冒號、點點點、刪節號、省略號；"
                              "整句只有這個名字才算，例如「逗號很重要」照常輸入")
        self.period_comma = QCheckBox("選取句號後講話，句號改成逗號並接上講的內容")
        self.period_comma.setToolTip("今天很好。明天見 → 選取「。」講「我們去公園」→ 今天很好，我們去公園。明天見")
        self.autostart = QCheckBox("開機時自動啟動")
        self.mute = QCheckBox("錄音時暫時靜音電腦音效")
        self.mute.setToolTip("錄音期間把喇叭靜音，文字輸入完成後恢復；原本就靜音的話不會動")
        self.describe = QCheckBox("說「詞＋的＋字」時只輸入那個字，例如「節日的節」")
        self.describe.setToolTip("整句剛好是「某個詞的某個字」才算，輸入的是詞裡讀音相同的那個字")
        memory = QPushButton("開啟紀錄檔", clicked=self._open_memory)
        memory.setToolTip("選字時學到的前後文例子與英文寫法偏好（smart-picks.json）")
        memory_row = QHBoxLayout()
        memory_row.addWidget(memory)
        memory_row.addStretch()
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("麥克風", self.mic)
        form.addRow("", self.mute)
        form.addRow("", self.punct)
        form.addRow("", self.period_comma)
        form.addRow("", self.describe)
        form.addRow("", self.autostart)
        form.addRow("智慧選字", memory_row)
        page = QWidget()
        v = QVBoxLayout(page)
        v.addLayout(form)
        v.addStretch(1)
        v.addWidget(self.status)

        self.mic.currentIndexChanged.connect(self._apply)
        for box in (self.punct, self.period_comma, self.describe, self.autostart, self.mute):
            box.toggled.connect(self._apply)
        return page

    def load_values(self):
        self._loading = True
        self.mic.setCurrentIndex(max(0, self.mic.findData(self.cfg.mic)))
        self.punct.setChecked(self.cfg.punctuation_names)
        self.period_comma.setChecked(self.cfg.period_to_comma)
        self.autostart.setChecked(self.cfg.autostart)
        self.mute.setChecked(self.cfg.mute_while_recording)
        self.describe.setChecked(self.cfg.describe_char)
        self._loading = False
        self.refresh_rules()
        self.refresh_hotwords()

    def set_status(self, text: str):
        self.status.setText(text)

    def _apply(self):
        if self._loading:
            return
        self.cfg.mic = self.mic.currentData()
        self.cfg.punctuation_names = self.punct.isChecked()
        self.cfg.period_to_comma = self.period_comma.isChecked()
        self.cfg.autostart = self.autostart.isChecked()
        self.cfg.mute_while_recording = self.mute.isChecked()
        self.cfg.describe_char = self.describe.isChecked()
        self._save()

    def _save(self):
        self.cfg.save()
        self.applied.emit()

    def _open_memory(self):
        if not self.memory_path.exists():
            self.memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.memory_path.write_text('{\n"examples": [\n],\n"styles": {}\n}\n', encoding="utf-8")
        os.startfile(self.memory_path)

    # --- 熱詞 ---
    def _hotwords_page(self) -> QWidget:
        self.hw_list = QVBoxLayout()
        self.hw_list.setSpacing(4)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.hw_list)
        v.addStretch(1)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        scroll.setWidget(inner)
        self.hw_sort = _combo(HOTWORD_SORTS, self.cfg.hotword_sort)
        self.hw_sort.currentIndexChanged.connect(self._sort_hotwords)
        top = QHBoxLayout()
        top.addWidget(QLabel("排序"))
        top.addWidget(self.hw_sort)
        top.addStretch()
        self.hw_empty = QLabel("還沒有熱詞。選字時選了詞語，或在選字選單的輸入框輸入文字，就會加到這裡。")
        self.hw_empty.setStyleSheet("color: gray")
        self.hw_empty.setWordWrap(True)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(top)
        layout.addWidget(self.hw_empty)
        layout.addWidget(scroll, 1)
        return page

    def refresh_hotwords(self):
        while self.hw_list.count():
            w = self.hw_list.takeAt(0).widget()
            w.setParent(None)
            w.deleteLater()
        for h in self._sorted_hotwords():
            card = QFrame(objectName="card")
            card.setStyleSheet(_CARD_STYLE)
            row = QHBoxLayout(card)
            row.setContentsMargins(8, 4, 4, 4)
            row.addWidget(QLabel(h.word), 1)
            added = QLabel(h.added)
            added.setStyleSheet("color: gray")
            added.setToolTip("加入的日期")
            row.addWidget(added)
            weight = QLabel(f"{h.weight:g} / {hotwords.cap(h.word):g}")
            weight.setToolTip("權重 / 上限（中文 5、英文 3）")
            weight.setMinimumWidth(56)
            weight.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(weight)
            percent = QLabel(f"{self.hotwords.percent(h):.0f}%")
            percent.setToolTip("權重佔上限的比例；依權重排序時看這個")
            percent.setMinimumWidth(40)
            percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            row.addWidget(percent)
            remove = QPushButton("✕", toolTip="刪除這個熱詞", clicked=lambda _=False, w=h.word: self._remove_hotword(w))
            remove.setFixedWidth(28)
            row.addWidget(remove)
            self.hw_list.addWidget(card)
        self.hw_empty.setVisible(not self.hotwords.items)

    def _sorted_hotwords(self) -> list:
        """The list in the chosen order; ties by weight (as a share of the cap: Chinese and English caps differ),
        then name. Names sort by their pinyin, so Chinese words sit among the English ones by sound (程式 near
        Claude)."""
        def name(h):
            return "".join(lazy_pinyin(h.word)).casefold()
        items = sorted(self.hotwords.items, key=lambda h: (-self.hotwords.percent(h), name(h)))
        order = self.cfg.hotword_sort
        if order in ("name", "name_desc"):
            items.sort(key=name, reverse=order == "name_desc")
        elif order in ("date", "date_old"):
            items.sort(key=lambda h: h.added, reverse=order == "date")
        return items

    def _sort_hotwords(self):
        self.cfg.hotword_sort = self.hw_sort.currentData()
        self.cfg.save()
        self.refresh_hotwords()

    def _remove_hotword(self, word: str):
        self.hotwords.remove(word)
        self.refresh_hotwords()

    # --- 辨識紀錄 ---
    def _log_page(self) -> QWidget:
        self.log = QTextBrowser()
        self.log.setPlaceholderText("每次說話的辨識結果、格式化結果與操作會顯示在這裡（只保留這次執行期間）")
        return self.log

    def set_log(self, entries):
        """Render entries newest first, in pipeline order. Each entry: time, asr, asr_s, fmt, and edit (an
        operation or edit description, optional)."""
        def row(label, secs, text, color=""):
            t = f"{secs:.2f}s" if secs is not None else ""
            style = f" style='color:{color}'" if color else ""
            return (f"<tr><td width=40><b>{label}</b></td><td width=46 style='color:gray'>{t}</td>"
                    f"<td{style}>{html.escape(text)}</td></tr>")

        def block(e):
            return (f"<div style='color:gray'>{e['time']}</div><table cellspacing=0 cellpadding=2>"
                    + row("ASR", e["asr_s"], e["asr"])
                    + row("格式", None, e["fmt"])
                    + (row("編輯", None, e["edit"]) if e.get("edit") else "")
                    + "</table>")

        self.log.setHtml("<br>".join(block(e) for e in reversed(entries)))
        self.log.verticalScrollBar().setValue(0)

    # --- 自訂操作: one card per rule ---
    def _rules_page(self) -> QWidget:
        add = QPushButton("＋ 新增操作", clicked=self._add_rule)
        reset = QPushButton("還原預設", clicked=self._reset_rules)
        top = QHBoxLayout()
        top.addWidget(add)
        top.addStretch()
        top.addWidget(reset)

        self.cards = QVBoxLayout()
        self.cards.setSpacing(6)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(self.cards)
        v.addStretch(1)
        self.rules_scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
        self.rules_scroll.setWidget(inner)

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addLayout(top)
        layout.addWidget(self.rules_scroll, 1)
        return page

    def refresh_rules(self):
        bar = self.rules_scroll.verticalScrollBar()
        pos = bar.value()
        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            w.setParent(None)
            w.deleteLater()
        self._problem_labels = []
        for rule in self.cfg.rules:
            self.cards.addWidget(self._rule_card(rule))
        self._show_problems()
        bar.setValue(pos)

    def _changed(self, rebuild: bool = False):
        """A rule was edited: save, and redraw the cards when their layout changes."""
        self._save()
        if rebuild:
            self.refresh_rules()
        else:
            self._show_problems()

    def _show_problems(self):
        for rule, label in self._problem_labels:
            text = "、".join(rules.problems(rule, self.cfg.rules))
            label.setText(f"⚠ {text}" if text else "")
            label.setVisible(bool(text))

    def _rule_card(self, rule: Rule) -> QFrame:
        card = QFrame(objectName="card")
        card.setStyleSheet(_CARD_STYLE)
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 6, 6, 6)
        v.setSpacing(4)

        enabled = QCheckBox(checked=rule.enabled, toolTip="啟用")
        enabled.toggled.connect(lambda on: (setattr(rule, "enabled", on), self._changed()))
        source = _combo(SOURCES, rule.source, "觸發方式")
        source.currentIndexChanged.connect(lambda _: self._set_source(rule, source.currentData()))
        head = QHBoxLayout()
        head.addWidget(enabled)
        head.addWidget(source)
        if rule.source == rules.SPEECH:
            phrase = QLineEdit(rule.phrase, placeholderText="完整說出的話，例如：送出")
            phrase.setToolTip("整句只說這些話時才執行（標點、空白不計）")
            phrase.textEdited.connect(lambda t: (setattr(rule, "phrase", t.strip()), self._changed()))
            head.addWidget(phrase, 1)
        else:
            names = MOUSE_TRIGGERS.items() if rule.source == rules.MOUSE else (
                (k, label) for k, (label, _) in KEY_TRIGGERS.items())
            key = _combo(names, rule.key)
            key.currentIndexChanged.connect(lambda _: (setattr(rule, "key", key.currentData()), self._changed()))
            gesture = _combo(GESTURES, rule.gesture, "按法")
            gesture.currentIndexChanged.connect(
                lambda _: (setattr(rule, "gesture", gesture.currentData()), self._changed()))
            head.addWidget(key, 1)
            head.addWidget(gesture)
        when = _combo(SITUATIONS, rule.when, "情境：什麼時候才執行")
        when.currentIndexChanged.connect(lambda _: (setattr(rule, "when", when.currentData()), self._changed()))
        head.addWidget(when)
        remove = QPushButton("✕", toolTip="刪除這條操作", clicked=lambda: self._remove_rule(rule))
        remove.setFixedWidth(28)
        head.addWidget(remove)
        v.addLayout(head)

        for i, step in enumerate(rule.steps):
            v.addLayout(self._step_row(rule, i, step))
        add = QPushButton("＋ 新增步驟", flat=True, clicked=lambda: self._add_step(rule))
        add_row = QHBoxLayout()
        add_row.addSpacing(24)
        add_row.addWidget(add)
        add_row.addStretch()
        v.addLayout(add_row)

        problem = QLabel()
        problem.setStyleSheet("color: #d9534f")
        problem.setWordWrap(True)
        v.addWidget(problem)
        self._problem_labels.append((rule, problem))
        return card

    def _step_row(self, rule: Rule, i: int, step: Step) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QLabel(f"{i + 1}."))
        kind = _combo(STEP_TYPES, step.type)
        kind.currentIndexChanged.connect(lambda _: self._set_step_type(step, kind.currentData()))
        row.addWidget(kind)
        if step.type == rules.COMMAND:
            value = _combo(COMMANDS, step.value)
            value.currentIndexChanged.connect(lambda _: self._set_value(step, value.currentData()))
        elif step.type == rules.KEYS:
            value = KeyCapture(step.value)
            value.captured.connect(lambda t: self._set_value(step, t))
        elif step.type == rules.TEXT:
            value = QLineEdit(step.value, placeholderText="要輸入的文字")
            value.textEdited.connect(lambda t: self._set_value(step, t))
        else:
            value = _NoWheelSpin(minimum=0, maximum=10000, singleStep=50, suffix=" 毫秒", value=int(step.value or 0))
            value.valueChanged.connect(lambda n: self._set_value(step, n))
        row.addWidget(value, 1)
        for text, tip, delta in (("↑", "上移", -1), ("↓", "下移", 1)):
            b = QPushButton(text, toolTip=tip, clicked=lambda _=False, d=delta: self._move_step(rule, i, d))
            b.setFixedWidth(28)
            b.setEnabled(0 <= i + delta < len(rule.steps))
            row.addWidget(b)
        remove = QPushButton("✕", toolTip="刪除這個步驟", clicked=lambda: self._remove_step(rule, i))
        remove.setFixedWidth(28)
        row.addWidget(remove)
        return row

    def _set_source(self, rule: Rule, source: str):
        rule.source = source
        if source == rules.MOUSE and rule.key not in MOUSE_TRIGGERS:
            rule.key = next(iter(MOUSE_TRIGGERS))
        elif source == rules.KEY and rule.key not in KEY_TRIGGERS:
            rule.key = next(iter(KEY_TRIGGERS))
        self._changed(rebuild=True)

    def _set_step_type(self, step: Step, kind: str):
        step.type, step.value = kind, _STEP_DEFAULTS[kind]
        self._changed(rebuild=True)

    def _set_value(self, step: Step, value):
        step.value = value
        self._changed()

    def _move_step(self, rule: Rule, i: int, delta: int):
        j = i + delta
        rule.steps[i], rule.steps[j] = rule.steps[j], rule.steps[i]
        self._changed(rebuild=True)

    def _remove_step(self, rule: Rule, i: int):
        del rule.steps[i]
        self._changed(rebuild=True)

    def _add_step(self, rule: Rule):
        rule.steps.append(Step(rules.KEYS, ""))
        self._changed(rebuild=True)

    def _add_rule(self):
        self.cfg.rules.append(Rule(rules.MOUSE, "mouse_x2", gesture=rules.TAP, steps=[Step(rules.KEYS, "")]))
        self._changed(rebuild=True)
        bar = self.rules_scroll.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    def _remove_rule(self, rule: Rule):
        self.cfg.rules.remove(rule)
        self._changed(rebuild=True)

    def _reset_rules(self):
        if QMessageBox.question(self, "還原預設", "把自訂操作還原成預設？目前的設定會被取代。") == QMessageBox.Yes:
            self.cfg.rules = rules.defaults()
            self._changed(rebuild=True)

