"""Settings window in four tabs: 一般 (general), 辨識紀錄 (live transcript log), 自訂操作 (custom operations,
rules.py) and 詞庫 (hotwords.py, the UI name for hotwords since 2026-10-13), in four sections one open at a time:
term packs to tick (packs.py), every listed word with its 優先度 (−100% .. +100%, 0 does nothing; stored as 0 ..
200%; read only); 選字記憶 (the smart picks, memory.py); English replacements (hotword aliases). Each list is dated,
sortable, and its entries removable. Every change is applied and saved immediately."""
import html
import os

from pypinyin import lazy_pinyin
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QTextBrowser, QToolButton,
                               QToolTip, QVBoxLayout, QWidget)

from . import audio, cues, hotwords, packs, recordings, rules
from .config import GESTURE_TIME_RANGE
from .memory import BOUNDARY
from .keys import KEY_TRIGGERS, MOUSE_TRIGGERS, combo_text, is_modifier
from .rules import Rule, Step
from .textfmt import changes

SOURCES = [(rules.MOUSE, "滑鼠鍵"), (rules.KEY, "鍵盤鍵"), (rules.SPEECH, "說出")]
GESTURES = [(rules.TAP, "按一下"), (rules.DOUBLE, "連按兩下"), (rules.HOLD, "按住")]
SITUATIONS = [(rules.ANY, "不限"), (rules.DICTATED, "剛輸入完"), (rules.SELECTION, "有選取文字")]
STEP_TYPES = [(rules.COMMAND, "命令"), (rules.KEYS, "按鍵"), (rules.TEXT, "輸入文字"), (rules.WAIT, "等待")]
COMMANDS = [(rules.RECORD, "錄音"), (rules.PICK, "選字"),
            (rules.DELETE_SELECTION, "刪除選取的文字"),
            (rules.DELETE_DICTATION, "刪除剛輸入的文字")]
_STEP_DEFAULTS = {rules.COMMAND: rules.DELETE_SELECTION, rules.KEYS: "", rules.TEXT: "", rules.WAIT: 100}

HOTWORD_SORTS = [("weight", "優先度（高→低）"), ("name", "名稱 A→Z"), ("name_desc", "名稱 Z→A"),
                 ("date", "日期（新→舊）"), ("date_old", "日期（舊→新）")]
PICK_SORTS = [("date", "日期（新→舊）"), ("date_old", "日期（舊→新）"), ("reading", "讀音 A→Z"),
              ("reading_desc", "讀音 Z→A")]
ALIAS_SORTS = [("date", "日期（新→舊）"), ("date_old", "日期（舊→新）"), ("name", "名稱 A→Z"), ("name_desc", "名稱 Z→A")]
def _shown(pct: int) -> str:
    """The 優先度 as the user sees it (2026-10-13, the user's choice): 0 does nothing, lowered words are negative."""
    d = pct - hotwords.NEUTRAL
    return f"{d:+d}%" if d else "0%"


def _show_pct(label, pct: int):
    label.setText(_shown(pct))
    label.setStyleSheet("color: #d03030" if pct < hotwords.NEUTRAL else "")


_SET_PAUSE = 0.8   # seconds between the start and end sound when a set is previewed
_CARD_STYLE = "QFrame#card { border: 1px solid palette(mid); border-radius: 6px; }"
_KIND_COLORS = {"輸入": "#1F5FAF", "操作": "#B35A00", "取代": "#0E7A6E", "選字": "#6B3FA0", "刪除": "#B42318"}


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


def _card_list(empty_text: str, top=None):
    """A section of the 詞庫 tab: optional controls on top, a gray note shown while empty, then cards in a scroll
    area. Returns (layout the cards go in, the note, the section's widget)."""
    cards = QVBoxLayout()
    cards.setSpacing(4)
    inner = QWidget()
    v = QVBoxLayout(inner)
    v.setContentsMargins(0, 0, 0, 0)
    v.addLayout(cards)
    v.addStretch(1)
    scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
    scroll.setWidget(inner)
    scroll.viewport().setAutoFillBackground(False)   # the page's background, not a gray box under few cards
    inner.setAutoFillBackground(False)
    empty = QLabel(empty_text)
    empty.setStyleSheet("color: gray")
    empty.setWordWrap(True)
    body = QWidget()
    layout = QVBoxLayout(body)
    layout.setContentsMargins(0, 0, 0, 0)
    if top is not None:
        layout.addLayout(top)
    layout.addWidget(empty)
    layout.addWidget(scroll, 1)
    return cards, empty, body


def _clear(layout):
    while layout.count():
        w = layout.takeAt(0).widget()
        w.setParent(None)
        w.deleteLater()


def _card() -> tuple[QFrame, QHBoxLayout]:
    card = QFrame(objectName="card")
    card.setStyleSheet(_CARD_STYLE)
    row = QHBoxLayout(card)
    row.setContentsMargins(8, 4, 4, 4)
    return card, row


_PACK_SHOWN = 5   # terms shown after a pack's name


def _date_label(added: str) -> QLabel:
    """The date of an entry; the time, kept for sorting (2026-10-14), only in the tooltip."""
    label = QLabel(added[:10], toolTip=f"加入時間：{added}")
    label.setStyleSheet("color: gray")
    return label


def _remove_button(tip: str, slot) -> QPushButton:
    b = QPushButton("✕", toolTip=tip, clicked=slot)
    b.setFixedWidth(28)
    return b


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

    def __init__(self, cfg, hotwords, memory):
        super().__init__()
        self.cfg = cfg
        self.hotwords = hotwords
        self.memory = memory
        self._loading = False
        self.setWindowTitle("VoiceInput 設定")
        self.resize(620, 560)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_page(), "一般")
        self.tabs.addTab(self._log_page(), "辨識紀錄")
        self.tabs.addTab(self._rules_page(), "自訂操作")
        self.tabs.addTab(self._hotwords_page(), "詞庫")

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
        self.start_sound, self.stop_sound = QComboBox(), QComboBox()
        for box in (self.start_sound, self.stop_sound):
            for cue_id, name, *_sources in cues.SOUNDS:
                box.addItem(name, cue_id)
            box.addItem("無", "")
        self.start_sound.setToolTip("確定開始錄音時播放（按住約 0.3 秒，連按兩下不會響）；選了會先播一次")
        self.stop_sound.setToolTip("錄音結束時播放；選了會先播一次")
        # a set picks both sounds at once; 自定義 shows a drop-down for each (the user's design, 2026-10-13)
        self.sound_set = QComboBox(toolTip="同一組的開始音與結束音；選「自定義」可分別挑選")
        for cue_id, name, *_sources in cues.SOUNDS:
            self.sound_set.addItem(name, cue_id)
        self.sound_set.addItem("無", "")
        self.sound_set.addItem("自定義", None)
        self._set_timer = QTimer(self, singleShot=True,
                                 timeout=lambda: cues.play(self._set_timer.property("cue"), "stop"))
        self.sound_listen = QPushButton("試聽", clicked=self._play_sounds, toolTip="依序播放開始音與結束音")
        self.sound_listen.setFixedWidth(44)
        self.custom_sounds = QWidget()
        custom = QHBoxLayout(self.custom_sounds)
        custom.setContentsMargins(8, 0, 0, 0)
        for label, box in (("開始", self.start_sound), ("結束", self.stop_sound)):
            custom.addWidget(QLabel(label))
            custom.addWidget(box)
            custom.addSpacing(8)
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.sound_set)
        sound_row.addWidget(self.sound_listen)
        sound_row.addWidget(self.custom_sounds)
        sound_row.addStretch()
        self.stop_at = QComboBox(toolTip="開了「錄音時暫時靜音電腦音效」時，停止錄音就恢復聲音，好播結束音")
        self.stop_at.addItem("停止錄音時", "stop")
        self.stop_at.addItem("文字輸入完成時", "done")
        stop_at_row = QHBoxLayout()
        stop_at_row.addWidget(QLabel("結束音在"))
        stop_at_row.addWidget(self.stop_at)
        stop_at_row.addWidget(QLabel("播放"))
        stop_at_row.addStretch()
        self.mute = QCheckBox("錄音時暫時靜音電腦音效")
        self.mute.setToolTip("錄音期間把喇叭靜音，文字輸入完成後恢復；原本就靜音的話不會動")
        self.describe = QCheckBox("說「詞＋的＋字」時只輸入那個字，例如「節日的節」")
        self.describe.setToolTip("整句剛好是「某個詞的某個字」才算，輸入的是詞裡讀音相同的那個字")
        # development only, to be removed before release (the user's plan): recordings.py
        self.keep_audio = QCheckBox("保存錄音（測試用）")
        self.keep_audio.setToolTip("每句話存成 wav，並記下辨識結果與之後的選字、刪除，用來以真實聲音檢查辨識；不會自動刪除")
        folder = QPushButton("開啟錄音資料夾", clicked=self._open_recordings)
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.keep_audio)
        audio_row.addWidget(folder)
        audio_row.addStretch()
        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("麥克風", self.mic)
        form.addRow("提示音", sound_row)
        form.addRow("", stop_at_row)
        form.addRow("", self.mute)
        form.addRow("", self.punct)
        form.addRow("", self.period_comma)
        form.addRow("", self.describe)
        form.addRow("", self.autostart)
        form.addRow("開發測試", audio_row)
        page = QWidget()
        v = QVBoxLayout(page)
        v.addLayout(form)
        v.addStretch(1)
        v.addWidget(self.status)

        self.mic.currentIndexChanged.connect(self._apply)
        self.start_sound.currentIndexChanged.connect(lambda: self._sound_changed(self.start_sound, "start"))
        self.stop_sound.currentIndexChanged.connect(lambda: self._sound_changed(self.stop_sound, "stop"))
        self.sound_set.currentIndexChanged.connect(self._sound_set_changed)
        self.stop_at.currentIndexChanged.connect(self._apply)
        for box in (self.punct, self.period_comma, self.describe, self.autostart, self.mute, self.keep_audio):
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
        self.keep_audio.setChecked(self.cfg.save_recordings)
        self.start_sound.setCurrentIndex(max(0, self.start_sound.findData(self.cfg.start_sound)))
        self.stop_sound.setCurrentIndex(max(0, self.stop_sound.findData(self.cfg.stop_sound)))
        self.stop_at.setCurrentIndex(max(0, self.stop_at.findData(self.cfg.stop_sound_at)))
        self.gesture_time.setValue(self.cfg.gesture_time)
        same = self.cfg.start_sound == self.cfg.stop_sound
        self.sound_set.setCurrentIndex(self.sound_set.findData(self.cfg.start_sound) if same
                                       else self.sound_set.count() - 1)
        self._show_sounds()
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
        self.cfg.save_recordings = self.keep_audio.isChecked()
        self.cfg.start_sound = self.start_sound.currentData()
        self.cfg.stop_sound = self.stop_sound.currentData()
        self.cfg.stop_sound_at = self.stop_at.currentData()
        self._save()

    def _sound_changed(self, box, kind: str):
        if not self._loading:
            cues.play(box.currentData(), kind)
            self._show_sounds()
        self._apply()

    def _custom(self) -> bool:
        return self.sound_set.currentData() is None

    def _show_sounds(self):
        """The 開始 / 結束 drop-downs only for 自定義; 試聽 and the timing only when there is something to play."""
        self.custom_sounds.setVisible(self._custom())
        self.sound_listen.setEnabled(bool(self.start_sound.currentData() or self.stop_sound.currentData()))
        self.stop_at.setEnabled(bool(self.stop_sound.currentData()))

    def _sound_set_changed(self):
        cue_id = self.sound_set.currentData()
        if cue_id is not None and not self._loading:   # a set: both sounds, previewed together
            self._loading = True
            self.start_sound.setCurrentIndex(self.start_sound.findData(cue_id))
            self.stop_sound.setCurrentIndex(self.stop_sound.findData(cue_id))
            self._loading = False
            self._play_sounds()
            self._apply()
        self._show_sounds()   # 自定義: the sounds stay as they are, their drop-downs show

    def _play_sounds(self):
        """Previewed as a recording: the start sound, a pause, the end sound (the user's request)."""
        self._set_timer.stop()
        start, stop = self.start_sound.currentData(), self.stop_sound.currentData()
        cues.play(start, "start")
        if stop:
            self._set_timer.setProperty("cue", stop)
            self._set_timer.start(int(((cues.duration(start, "start") + _SET_PAUSE) if start else 0) * 1000))

    def _save(self):
        self.cfg.save()
        self.applied.emit()

    def _open_recordings(self):
        recordings.DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(recordings.DIR)

    # --- 詞庫 (hotwords) ---
    def _hotwords_page(self) -> QWidget:
        """Four sections, one open at a time (2026-10-14, the user's design): the open one fills the page and
        scrolls inside, the other headers stay in view; the open one is remembered (hotword_section)."""
        self.pack_list, no_packs, pack_body = _card_list("")
        no_packs.hide()
        self._pack_stamp = None

        self.hw_list, self.hw_empty, hw_body = _card_list(
            "詞庫裡還沒有詞。選字時選了詞語（被換掉的詞也會列出，優先度較低），或在選字選單的輸入框輸入文字，就會加到這裡。",
            self._sort_row(HOTWORD_SORTS, "hotword_sort"))
        self.pick_list, self.pick_empty, pick_body = _card_list(
            "還沒有記下的字。用選字換掉單一個中文字，就會記下它前後的字，之後在同樣的前後文自動換成那個字。",
            self._sort_row(PICK_SORTS, "pick_sort"))
        self.alias_list, self.alias_empty, alias_body = _card_list(
            "還沒有強制替換。選字或打字把英文換成另一種拼法的詞（例如 Dipsick 換成 DeepSeek），之後辨識出舊拼法就一律改成新的。",
            self._sort_row(ALIAS_SORTS, "alias_sort"))

        self._sections = {}
        page = QWidget()
        self._section_layout = QVBoxLayout(page)
        self._section_layout.setSpacing(2)
        for key, body in (("packs", pack_body), ("words", hw_body), ("picks", pick_body), ("aliases", alias_body)):
            header = QToolButton(checkable=True, autoRaise=True, toolButtonStyle=Qt.ToolButtonTextBesideIcon)
            header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            header.setStyleSheet("QToolButton { font-weight: bold; padding: 4px; }"
                                 " QToolButton:checked { background: transparent; border: none; }")
            header.clicked.connect(lambda on, k=key: self._open_section(k if on else ""))
            body.setVisible(False)
            self._section_layout.addWidget(header)
            self._section_layout.addWidget(body)
            self._sections[key] = (header, body)
        self._section_layout.addStretch(1)
        self._open_section(self.cfg.hotword_section, save=False)
        return page

    def _open_section(self, key: str, save: bool = True):
        for k, (header, body) in self._sections.items():
            on = k == key
            header.setChecked(on)
            header.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
            body.setVisible(on)
            self._section_layout.setStretchFactor(body, 1 if on else 0)
        self._section_layout.setStretch(self._section_layout.count() - 1, 0 if key in self._sections else 1)
        if save and key != self.cfg.hotword_section:
            self.cfg.hotword_section = key
            self.cfg.save()

    def _sort_row(self, sorts, setting: str) -> QHBoxLayout:
        """排序 and its drop-down; the choice is saved as cfg.<setting>."""
        combo = _combo(sorts, getattr(self.cfg, setting))
        combo.currentIndexChanged.connect(lambda: self._sort_changed(setting, combo.currentData()))
        row = QHBoxLayout()
        row.addWidget(QLabel("排序"))
        row.addWidget(combo)
        row.addStretch()
        return row

    def _sort_changed(self, setting: str, value: str):
        setattr(self.cfg, setting, value)
        self.cfg.save()
        self.refresh_hotwords()

    def _set_headers(self):
        texts = {"packs": f"詞包（已勾選 {len(self.cfg.packs)}）", "words": f"詞庫列表（{len(self.hotwords.listed())}）",
                 "picks": f"選字記憶（{len(self.memory.listed())}）",
                 "aliases": f"英文強制替換（{len(self.hotwords.aliases())}）"}
        for k, (header, _) in self._sections.items():
            header.setText(texts[k])

    def refresh_hotwords(self):
        """The whole 詞庫 tab: term packs, hotwords, 選字記憶 (smart picks), English replacements and the section
        headers."""
        self._refresh_packs()
        self._refresh_words()
        self._refresh_picks()
        self._refresh_aliases()
        self._set_headers()

    def _refresh_packs(self):
        """One row per pack (2026-10-14, the user's design): check box with the count, its first terms (the files
        list the best known first), and 編輯, which opens the pack's own file in DATA_DIR/packs."""
        self._pack_stamp = packs.stamp()
        _clear(self.pack_list)
        for pack in packs.available():
            card, row = _card()
            box = QCheckBox(f"{pack.name}（{len(pack.words)}）", checked=pack.id in self.cfg.packs)
            box.setToolTip(f"整包的詞以優先度 {_shown(packs.PCT)} 辨識；選字選過或被換掉的詞會列在詞庫列表，以那裡的優先度為準")
            box.toggled.connect(lambda on, i=pack.id: self._toggle_pack(i, on))
            box.setMinimumWidth(150)
            row.addWidget(box)
            terms = QLabel("、".join(pack.words[:_PACK_SHOWN]) + ("…" if len(pack.words) > _PACK_SHOWN else ""))
            terms.setStyleSheet("color: gray")
            terms.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # cut, never widening the window
            terms.setToolTip("、".join(pack.words))
            row.addWidget(terms, 1)
            edit = QPushButton("編輯", toolTip="用記事本開啟這個詞包的文字檔：一行一個詞，存檔後下次說話就生效",
                               clicked=lambda _=False, i=pack.id: self._edit_pack(i))
            row.addWidget(edit)
            self.pack_list.addWidget(card)

    def _edit_pack(self, pack_id: str):
        path = packs.path(pack_id)
        if not path.exists():
            packs.install()
        os.startfile(path)

    def changeEvent(self, e):
        """Back from editing a pack file: show its new terms."""
        super().changeEvent(e)
        if e.type() == QEvent.ActivationChange and self.isActiveWindow() and packs.stamp() != self._pack_stamp:
            self.refresh_hotwords()

    def _refresh_words(self):
        """Each word: date added, its 優先度 (read only since 2026-10-14) and ✕, which puts it back where it would be
        unlisted."""
        _clear(self.hw_list)
        items = self._sorted_hotwords()
        for h in items:
            card, row = _card()
            row.addWidget(QLabel(h.word))
            row.addStretch(1)
            row.addWidget(_date_label(h.added))
            percent = QLabel()
            _show_pct(percent, h.pct)
            percent.setToolTip("優先度：0% 不影響辨識；正的越容易辨識成這個詞，負的越不容易。選字選到會提高，被換掉會降低")
            percent.setMinimumWidth(48)
            percent.setAlignment(Qt.AlignCenter)
            row.addWidget(percent)
            row.addWidget(_remove_button("移除，優先度回到預設", lambda _=False, w=h.word: self._remove_hotword(w)))
            self.hw_list.addWidget(card)
        self.hw_empty.setVisible(not items)

    def _refresh_picks(self):
        """Each example as heard -> as picked, the character shown by its reading on the left: 這個ying3片是 ->
        這個影片是 (the user's design, 2026-10-14)."""
        _clear(self.pick_list)
        rows = self.memory.listed()
        order = self.cfg.pick_sort
        if order in ("reading", "reading_desc"):
            rows.sort(key=lambda e: (e["key"], e["value"]), reverse=order == "reading_desc")
        else:
            rows.sort(key=lambda e: (e["added"], e["serial"]), reverse=order != "date_old")
        for e in rows:
            card, row = _card()
            left = "".join(c for c in e["left"] if c != BOUNDARY)
            right = "".join(e["right"])
            label = QLabel(f"{html.escape(left + e['key'] + right)} <span style='color:gray'>→</span> "
                           f"{html.escape(left + e['value'] + right)}")
            label.setToolTip(f"聽成 {e['key']}、前後是這些字時，換成「{e['value']}」")
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(_date_label(e["added"]))
            row.addWidget(_remove_button("移除這筆", lambda _=False, k=e["key"], n=e["serial"]:
                                         self._remove_pick(k, n)))
            self.pick_list.addWidget(card)
        self.pick_empty.setVisible(not rows)

    def _remove_pick(self, key: str, serial: int):
        self.memory.remove(key, serial)
        self.refresh_hotwords()

    def _refresh_aliases(self):
        _clear(self.alias_list)
        rows = [(a, w, self.hotwords.alias_date(w, a)) for a, w in self.hotwords.aliases()]
        order = self.cfg.alias_sort
        if order in ("name", "name_desc"):
            rows.sort(key=lambda r: (r[0].casefold(), r[1].casefold()), reverse=order == "name_desc")
        else:
            rows.sort(key=lambda r: r[2], reverse=order != "date_old")
        for alias, word, added in rows:
            card, row = _card()
            label = QLabel(f"{html.escape(alias)} <span style='color:gray'>→</span> {html.escape(word)}")
            label.setToolTip(f"辨識出「{alias}」時一律改成「{word}」")
            row.addWidget(label)
            row.addStretch(1)
            row.addWidget(_date_label(added))
            row.addWidget(_remove_button("不再替換", lambda _=False, w=word, a=alias: self._remove_alias(w, a)))
            self.alias_list.addWidget(card)
        self.alias_empty.setVisible(not rows)

    def _sorted_hotwords(self) -> list:
        """The list in the chosen order; ties by percentage, then name. Names sort by their pinyin, so Chinese words
        sit among the English ones by sound (程式 near Claude)."""
        def name(h):
            return "".join(lazy_pinyin(h.word)).casefold()
        items = sorted(self.hotwords.listed(), key=lambda h: (-h.pct, name(h)))
        order = self.cfg.hotword_sort
        if order in ("name", "name_desc"):
            items.sort(key=name, reverse=order == "name_desc")
        elif order in ("date", "date_old"):
            items.sort(key=lambda h: h.added, reverse=order == "date")
        return items

    def _toggle_pack(self, pack_id: str, on: bool):
        ids = [i for i in self.cfg.packs if i != pack_id] + ([pack_id] if on else [])
        self.cfg.packs = ids
        self.cfg.save()
        self.hotwords.set_packs(packs.words_for(ids), packs.PCT)
        self._refresh_words()   # a listed word now at its pack's percentage leaves the list
        self._set_headers()

    def _remove_alias(self, word: str, alias: str):
        self.hotwords.remove_alias(word, alias)
        self.refresh_hotwords()

    def _remove_hotword(self, word: str):
        self.hotwords.remove(word)
        self.refresh_hotwords()

    # --- 辨識紀錄 ---
    def _log_page(self) -> QWidget:
        self.log = QTextBrowser(openLinks=False)
        self.log.setPlaceholderText("每次說話的辨識結果、格式化結果與操作會顯示在這裡（只保留這次執行期間）")
        self.log.anchorClicked.connect(self._log_link)
        self._log_entries = []
        return self.log

    def _log_link(self, url):
        """複製 on an entry: its typed text to the clipboard."""
        if url.scheme() == "copy":
            QApplication.clipboard().setText(self._log_entries[int(url.path())]["copy"])
            QToolTip.showText(QCursor.pos(), "已複製", self.log)

    def set_log(self, entries):
        """Render entries newest first (2026-10-14, the user's design): what happened first, in normal size (【輸入】
        the typed text, 【操作】 a spoken operation, 【取代】 old → new, 【選字】, 【刪除】), then the details smaller:
        the recognizer's text and time, and each step that changed the text as its changed fragments. Each entry:
        time, kind, main, and optionally asr, asr_s, steps [(name, before, after or None for a note)]. Each entry is
        a framed block whose first row, on a darker ground, holds the kind, the main text and the time (the user's
        pick among three designs, 2026-10-14). An entry with copy (the text it typed) has a 複製 link."""
        gray = "#5F6670"
        self._log_entries = list(entries)

        def row(label, text):
            return (f"<tr><td style='color:{gray}; padding-right:8px; white-space:nowrap' width=80>{label}</td>"
                    f"<td style='color:#3D434B'>{text}</td></tr>")

        def step(name, before, after):
            if after is None:
                return row(html.escape(name), html.escape(before))
            arrow = f" <span style='color:{gray}'>→</span> "
            frags = changes(before, after) or [(before, after)]
            return row(html.escape(name),
                       "；".join(html.escape(a or "（空）") + arrow + html.escape(b or "（空）") for a, b in frags))

        def block(i, e):
            copy = (f"<a href='copy:{i}' style='color:#1F5FAF; text-decoration:none'>複製</a>&nbsp;&nbsp;"
                    if e.get("copy") else "")
            main = html.escape(e.get("main") or "") or f"<span style='color:{gray}'>（沒有辨識到文字）</span>"
            kind = e.get("kind", "輸入")
            rows = ""
            if e.get("asr_s") is not None:
                rows += row(f"ASR {e['asr_s']:.2f}s", html.escape(e.get("asr") or ""))
            rows += "".join(step(*s) for s in e.get("steps", []))
            details = (f"<tr><td style='padding:3px 8px 4px 8px'>"
                       f"<table cellspacing=0 cellpadding=0 style='font-size:12px'>{rows}</table></td></tr>"
                       if rows else "")
            # the kind in a cell of its own, so a wrapped main text stays indented right of it
            return (f"<table width='100%' cellspacing=0 cellpadding=0 border=1 style='border-collapse:collapse; "
                    f"border-color:#D5DAE1; border-style:solid; margin-bottom:6px'>"
                    f"<tr><td bgcolor='#E6EAF0' style='padding:4px 8px'>"
                    f"<table width='100%' cellspacing=0 cellpadding=0 border=0 style='font-size:14px'><tr>"
                    f"<td valign=top width=38 style='white-space:nowrap'>"
                    f"<b style='color:{_KIND_COLORS.get(kind, '#1F2328')}'>{html.escape(kind)}</b></td><td>{main}</td>"
                    f"<td align=right valign=top width=96 style='padding-top:2px; font-size:12px; color:{gray}; "
                    f"white-space:nowrap'>{copy}{e['time']}</td></tr></table></td></tr>{details}</table>")

        self.log.setHtml("".join(block(i, e) for i, e in reversed(list(enumerate(entries)))))
        self.log.verticalScrollBar().setValue(0)

    # --- 自訂操作: one card per rule ---
    def _gesture_time_changed(self, value: float):
        if not self._loading:
            self.cfg.gesture_time = round(value, 2)
            self._save()

    def _rules_page(self) -> QWidget:
        add = QPushButton("＋ 新增操作", clicked=self._add_rule)
        reset = QPushButton("還原預設", clicked=self._reset_rules)
        self.gesture_time = QDoubleSpinBox(decimals=2, singleStep=0.05, suffix=" 秒", minimum=GESTURE_TIME_RANGE[0],
                                           maximum=GESTURE_TIME_RANGE[1], value=self.cfg.gesture_time)
        self.gesture_time.setToolTip("按著超過這個時間算「按住」；放開後在這個時間內再按一次算「連按兩下」。"
                                     "也是按住時錄音提示出現的時間")
        self.gesture_time.valueChanged.connect(self._gesture_time_changed)
        top = QHBoxLayout()
        top.addWidget(add)
        top.addSpacing(16)
        top.addWidget(QLabel("按住／連按判定時間"))
        top.addWidget(self.gesture_time)
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

