"""Validated reloads and atomic, version-checked writes for the editable word files.

The caller holds its RLock while loading, changing state and saving. Byte comparisons
also catch editor changes that keep a file's timestamp and size unchanged.
"""
import json
import os
from pathlib import Path
import tempfile

_UNREAD = object()


class JsonFile:
    def __init__(self, path, parse, empty):
        self.path = Path(path)
        self._parse, self._empty = parse, empty
        self._checked = _UNREAD
        self._version = _UNREAD
        self._valid = False
        self.error: str | None = None

    def _read(self):
        try:
            return self.path.read_bytes()
        except FileNotFoundError:
            return None

    def load(self):
        """(valid, new state or None). Invalid edits never replace the caller's state."""
        try:
            raw = self._read()
        except OSError as exc:
            self._checked = _UNREAD  # retry after a transient failure, even if bytes are unchanged
            self._valid = False
            self.error = f"讀取 {self.path.name} 失敗：{exc}"
            return False, None
        if raw == self._checked:
            return self._valid, None
        self._checked = raw
        try:
            if raw is None:
                state = self._empty()
            else:
                data = json.loads(raw.decode("utf-8-sig"))
                json.dumps(data, ensure_ascii=False).encode("utf-8")  # reject unpaired Unicode surrogates
                state = self._parse(data)
        except (UnicodeError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self._valid = False
            self.error = f"{self.path.name} 格式無效，已保留最後有效資料：{exc}"
            return False, None
        self._version = raw
        self._valid = True
        self.error = None
        return True, state

    def save(self, text: str) -> bool:
        """Write beside the target, then check its loaded version before replacing it.

        Editors do not share our lock, so the final check and replace still have a
        small race window. A detected conflict is reported without overwriting it.
        """
        if not self._valid:
            return False
        temporary = None
        try:
            raw = text.encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent,
                                             prefix=f".{self.path.name}.", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if self._read() != self._version:
                self.error = f"{self.path.name} 已被其他程式修改，請重新載入後再試。"
                return False
            os.replace(temporary, self.path)
        except (OSError, UnicodeError) as exc:
            self.error = f"儲存 {self.path.name} 失敗，原檔已保留：{exc}"
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass  # a locked temporary file must not hide the original write error
        self._checked = self._version = raw
        self._valid = True
        self.error = None
        return True
