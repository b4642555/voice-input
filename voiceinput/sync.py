"""同步 (2026-10-13, the user's choice: no server, no account): the files that hold what VoiceInput learned about
the user live in a folder they pick, typically inside OneDrive / Google Drive / Dropbox, so every computer with that
folder shares them. Settings (microphone, API key, custom operations) stay per computer in config.json.

Shared: the hotword list (熱詞 / 字典), the smart picks (智慧選字) and the history (one file per computer,
history.py). Hotwords and smart picks are whole files: the last computer to save wins, and the others reload them
when the file changes (checked at each key press), which is enough for one person on one computer at a time.
"""
import logging
import shutil
from pathlib import Path

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

HOTWORDS = "hotword-list.json"
PICKS = "smart-picks.json"
SHARED = (HOTWORDS, PICKS)


def folder(sync_dir: str) -> Path:
    if sync_dir:
        p = Path(sync_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except OSError:
            log.exception("sync folder %s is not usable, using the data folder", sync_dir)
    return DATA_DIR


def move(old: Path, new: Path) -> list[str]:
    """The sync folder changed: copy what the new folder lacks from the old one (a second computer joining keeps
    what is already there). Returns the names copied."""
    copied = []
    if old == new:
        return copied
    new.mkdir(parents=True, exist_ok=True)
    names = list(SHARED) + [p.name for p in old.glob("history-*.jsonl")]
    for name in names:
        src, dst = old / name, new / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                copied.append(name)
            except OSError:
                log.exception("copying %s to the sync folder failed", name)
    return copied


def mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
