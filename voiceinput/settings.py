"""Settings window with a live transcript log. Every change is applied and saved immediately."""
import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QTextBrowser, QVBoxLayout)

from . import audio
from .hotkey import HOTKEYS


class SettingsDialog(QDialog):
    applied = Signal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._loading = False
        self.setWindowTitle("VoiceInput 設定")
        self.resize(940, 480)

        self.mic = QComboBox()
        self.mic.addItem("系統預設", "")
        for name in audio.list_input_devices():
            self.mic.addItem(name, name)

        self.hotkey = QComboBox()
        for key, (label, _) in HOTKEYS.items():
            self.hotkey.addItem(label, key)

        self.newline_cmd = QCheckBox("只說「換行」時，按 Shift+Enter 換行")
        self.send_cmd = QCheckBox("只說「送出」時，按 Enter 送出訊息")
        self.period_comma = QCheckBox("選取句號後講話，句號改成逗號並接上講的內容")
        self.period_comma.setToolTip("今天很好。明天見 → 選取「。」講「我們去公園」→ 今天很好，我們去公園。明天見")
        self.autostart = QCheckBox("開機時自動啟動")
        self.mute = QCheckBox("錄音時暫時靜音電腦音效")
        self.mute.setToolTip("錄音期間把喇叭靜音，文字輸入完成後恢復；原本就靜音的話不會動")

        self.status = QLabel()
        self.status.setStyleSheet("color: gray")
        self.status.setWordWrap(True)

        form = QFormLayout()
        form.addRow("麥克風", self.mic)
        hotkey_note = QLabel("按住說話；快按兩下撤銷剛輸入的文字，第二下按住可直接重講。\n"
                             "選取文字時：快按兩下刪除，第二下按住可刪除後重講")
        hotkey_note.setStyleSheet("color: gray")
        hotkey_note.setWordWrap(True)
        form.addRow("錄音快捷鍵", self.hotkey)
        form.addRow("", hotkey_note)
        form.addRow("", self.mute)
        form.addRow("", self.newline_cmd)
        form.addRow("", self.send_cmd)
        form.addRow("", self.period_comma)
        form.addRow("", self.autostart)

        left = QVBoxLayout()
        left.addLayout(form)
        left.addStretch(1)
        left.addWidget(self.status)

        self.log = QTextBrowser()
        self.log.setPlaceholderText("每次說話的辨識結果、格式化結果與指令會顯示在這裡（只保留這次執行期間）")
        right = QVBoxLayout()
        right.addWidget(QLabel("辨識紀錄"))
        right.addWidget(self.log, 1)

        cols = QHBoxLayout()
        cols.addLayout(left, 4)
        cols.addSpacing(12)
        cols.addLayout(right, 5)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)

        layout = QVBoxLayout(self)
        layout.addLayout(cols, 1)
        layout.addWidget(buttons)

        for combo in (self.mic, self.hotkey):
            combo.currentIndexChanged.connect(self._apply)
        for box in (self.newline_cmd, self.send_cmd, self.period_comma, self.autostart, self.mute):
            box.toggled.connect(self._apply)

    def load_values(self):
        self._loading = True
        self._select(self.mic, self.cfg.mic)
        self._select(self.hotkey, self.cfg.hotkey)
        self.newline_cmd.setChecked(self.cfg.newline_command)
        self.send_cmd.setChecked(self.cfg.send_command)
        self.period_comma.setChecked(self.cfg.period_to_comma)
        self.autostart.setChecked(self.cfg.autostart)
        self.mute.setChecked(self.cfg.mute_while_recording)
        self._loading = False

    def set_status(self, text: str):
        self.status.setText(text)

    def set_log(self, entries):
        """Render entries newest first, in pipeline order. Each entry: time, asr, asr_s, fmt, and edit (a
        command or edit description, optional)."""
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

    @staticmethod
    def _select(combo, value):
        i = combo.findData(value)
        combo.setCurrentIndex(max(0, i))

    def _apply(self):
        if self._loading:
            return
        self.cfg.mic = self.mic.currentData()
        self.cfg.hotkey = self.hotkey.currentData()
        self.cfg.newline_command = self.newline_cmd.isChecked()
        self.cfg.send_command = self.send_cmd.isChecked()
        self.cfg.period_to_comma = self.period_comma.isChecked()
        self.cfg.autostart = self.autostart.isChecked()
        self.cfg.mute_while_recording = self.mute.isChecked()
        self.cfg.save()
        self.applied.emit()
