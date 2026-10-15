"""Dictation history and the numbers on the 首頁 (2026-10-13, Typeless-style): every dictation is kept with the app
it went to, the raw transcript and the text that was typed, so it can be copied again later.

One JSON object per line in history-<computer>.jsonl inside the data folder, or the sync folder when one is set
(sync.py): each computer appends only to its own file, so a cloud drive never has to merge two writers, and the
history page reads them all.
"""
import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("voiceinput")

_MACHINE = re.sub(r"[^\w-]", "_", os.environ.get("COMPUTERNAME", "pc")).lower()
_CJK = re.compile(r"[㐀-䶿一-鿿]")
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*")
TYPING_CJK_PER_MIN = 40     # typical typing speed with a Chinese input method
TYPING_WORDS_PER_MIN = 40


def words(text: str) -> int:
    """Words as a reader counts them: each CJK character, and each English word or number."""
    return len(_CJK.findall(text)) + len(_WORD.findall(text))


def typing_seconds(text: str) -> float:
    return 60 * (len(_CJK.findall(text)) / TYPING_CJK_PER_MIN + len(_WORD.findall(text)) / TYPING_WORDS_PER_MIN)


@dataclass
class Entry:
    t: float                  # unix time
    text: str                 # what was typed
    raw: str = ""             # the recognizer's transcript
    app: str = ""
    secs: float = 0.0         # length of the recording
    polished: bool = False    # the LLM cleaned it
    machine: str = field(default=_MACHINE)

    def day(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.t))


class History:
    def __init__(self, folder: Path):
        self._lock = threading.Lock()
        self.entries: list[Entry] = []
        self.set_folder(folder)

    def set_folder(self, folder: Path):
        with self._lock:
            self.folder = folder
            self.path = folder / f"history-{_MACHINE}.jsonl"
            self._mtimes = {}
            self.entries = []
        self.reload()

    def reload(self) -> bool:
        """Read every computer's file again when one changed. True when something changed."""
        try:
            files = sorted(self.folder.glob("history-*.jsonl"))
            mtimes = {p.name: p.stat().st_mtime for p in files}
        except OSError:
            return False
        if mtimes == self._mtimes:
            return False
        out = []
        for p in files:
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    try:
                        d = json.loads(line)
                        out.append(Entry(**{k: v for k, v in d.items() if k in Entry.__dataclass_fields__}))
                    except (ValueError, TypeError):
                        continue
            except OSError:
                continue
        out.sort(key=lambda e: e.t)
        with self._lock:
            self.entries = out
            self._mtimes = mtimes
        return True

    def add(self, entry: Entry):
        with self._lock:
            self.entries.append(entry)
            if len(self.entries) > 1 and self.entries[-2].t > entry.t:   # another computer's clock is ahead
                self.entries.sort(key=lambda e: e.t)
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
                self._mtimes[self.path.name] = self.path.stat().st_mtime
            except OSError:
                log.exception("writing the history failed")

    def delete(self, entry: Entry):
        """Remove one entry (from this computer's file only; another computer's entries are read-only here)."""
        with self._lock:
            if entry in self.entries:
                self.entries.remove(entry)
            mine = [e for e in self.entries if e.machine == _MACHINE]
            try:
                self.path.write_text("".join(json.dumps(asdict(e), ensure_ascii=False) + "\n" for e in mine),
                                     encoding="utf-8")
                self._mtimes[self.path.name] = self.path.stat().st_mtime
            except OSError:
                log.exception("rewriting the history failed")

    def clear(self):
        with self._lock:
            self.entries = [e for e in self.entries if e.machine != _MACHINE]
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            self._mtimes.pop(self.path.name, None)

    def ai_today(self) -> int:
        """Dictations the AI polished today (each one is a request on the user's Claude quota)."""
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            return sum(1 for e in self.entries if e.polished and e.day() == today)

    # --- the numbers on the 首頁 ---
    def stats(self) -> dict:
        with self._lock:
            entries = list(self.entries)
        total_words = sum(words(e.text) for e in entries)
        saved = sum(max(0.0, typing_seconds(e.text) - e.secs) for e in entries)
        spoken = sum(e.secs for e in entries)
        week_ago = time.time() - 7 * 86400
        week = [e for e in entries if e.t >= week_ago]
        days = {e.day() for e in entries}
        streak, d = 0, time.time()
        while time.strftime("%Y-%m-%d", time.localtime(d)) in days:
            streak += 1
            d -= 86400
        wpm = (total_words / (spoken / 60)) if spoken > 30 else 0
        return {"words": total_words, "week_words": sum(words(e.text) for e in week), "count": len(entries),
                "saved_min": saved / 60, "wpm": wpm, "streak": streak, "spoken_min": spoken / 60,
                "active_days": len(days), "longest_streak": _longest_streak(days)}

    def per_day(self) -> dict:
        """Words dictated per day (YYYY-MM-DD -> words), for the 首頁 heat map."""
        with self._lock:
            entries = list(self.entries)
        out: dict = {}
        for e in entries:
            out[e.day()] = out.get(e.day(), 0) + words(e.text)
        return out


def _longest_streak(days) -> int:
    """The longest run of consecutive calendar days in a set of YYYY-MM-DD strings."""
    from datetime import date, timedelta
    ds = sorted(date.fromisoformat(d) for d in days)
    best = run = 0
    prev = None
    for d in ds:
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    return best
