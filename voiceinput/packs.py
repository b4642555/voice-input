"""Term packs (詞包, 2026-10-13): curated word lists shipped with the app (voiceinput/packs/<id>.txt), ticked on the
詞庫 tab. Since 2026-10-14 the user edits them (the user's request): every launch copies the shipped packs missing from
DATA_DIR/packs there (the installer's job, once there is one), and only those copies are read. An app update merges
(the user's choice): the version last shipped is kept in DATA_DIR/packs/shipped, and terms a new version adds are
appended to the user's file under a comment; what the user added stays, what the user deleted is not brought back
(unless a new version adds it again), and terms a new version drops stay in the user's file. Each pack is its own file; the 詞庫 tab opens it, and an
edit applies at the next dictation (`stamp`). The first terms of a file are shown on the tab, so the best known come
first. Every word of a ticked pack biases X-ASR at PCT, the same percentage scale as the user's list (hotwords.py);
a word is listed there only once the user picks it, has it replaced or moves it, and from then on the user's
percentage wins.

PCT measured with Windows TTS (2026-10-13). The AI pack alone: 130% hit 11/18, 150-170% 12/18, 200% 14/18 but
我把檔案存到 cloud 上面 became Claude. All seven packs (723 words, 38 sentences with a term, 37 without; with
spelling.py, which alone gets 18/38): 120% 24/38, 130% 25/38 but 開 task manager lost 開, 150% 28/38 but Gira 的
ticket became Key the ticket, 170% also Kimi task manager: many short English words take over a sentence's start.
No time cost. Those runs had a bug: app._bias dropped a word another one starts with, so Gemini (Gemini CLI),
Claude (Claude Code), token (tokenizer) ... were never boosted. Fixed (Chinese only), the user's real recordings
(14 times Gemini): 120% 8, 150% 10, 170% 12 but the user's Cloud became Claude, 200% 13 with more damage; TTS 150%
28/38, nothing correct broken except 開 task manager with all seven packs (fine with the AI pack alone). So 150%;
picks raise a word from there (Gemini picked once: 180%).

File format: the first line is "# <name>", other lines starting with # are comments, one term per line.
"""
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import DATA_DIR

log = logging.getLogger("voiceinput")

SHIPPED = Path(__file__).resolve().parent / "packs"
DIR = DATA_DIR / "packs"
_BASE = DIR / "shipped"   # each pack as last shipped, to tell a new version's terms from the user's deletions
PCT = 150
ORDER = ["ai", "office", "dev", "cloud", "design", "marketing", "hardware"]   # as shown on the 詞庫 tab


@dataclass
class Pack:
    id: str
    name: str
    words: list[str]


def _load(path: Path) -> Pack:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    name = lines[0].lstrip("#").strip() if lines and lines[0].startswith("#") else path.stem
    words = list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))
    return Pack(path.stem, name, words)


def install():
    """Copy the shipped packs missing from DIR there (a pack the user deleted comes back as shipped) and merge new
    terms of a newer shipped version into the user's copy."""
    try:
        _BASE.mkdir(parents=True, exist_ok=True)
        for src in SHIPPED.glob("*.txt"):
            dst, base = DIR / src.name, _BASE / src.name
            if not dst.exists():
                shutil.copyfile(src, dst)
                log.info("term pack installed: %s", src.name)
            elif base.exists() and base.read_bytes() != src.read_bytes():
                _merge(src, base, dst)
            if not base.exists() or base.read_bytes() != src.read_bytes():
                shutil.copyfile(src, base)
    except OSError:
        log.exception("installing term packs")


def _merge(src: Path, base: Path, dst: Path):
    """Append to dst the terms src has that base (the version shipped before) had not."""
    known = set(_load(base).words) | set(_load(dst).words)
    new = [w for w in _load(src).words if w not in known]
    if not new:
        return
    text = dst.read_text(encoding="utf-8")
    text += ("" if text.endswith("\n") or not text else "\n") + f"\n# added by an app update, {time.strftime('%Y-%m-%d')}\n"
    dst.write_text(text + "\n".join(new) + "\n", encoding="utf-8")
    log.info("term pack %s: %d new terms merged", src.name, len(new))


def path(pack_id: str) -> Path:
    return DIR / f"{pack_id}.txt"


def _files() -> list[Path]:
    rank = {pack_id: i for i, pack_id in enumerate(ORDER)}
    files = list(DIR.glob("*.txt")) or list(SHIPPED.glob("*.txt"))   # the shipped ones if the copy failed
    return sorted(files, key=lambda p: (rank.get(p.stem, len(ORDER)), p.stem))


def stamp() -> tuple:
    """Changes when a pack file is edited, added or removed."""
    out = []
    for p in _files():
        try:
            out.append((p.name, p.stat().st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


def available() -> list[Pack]:
    out = []
    for path in _files():
        try:
            out.append(_load(path))
        except (OSError, UnicodeDecodeError):
            log.exception("term pack %s", path.name)
    return out


def words_for(ids) -> list[str]:
    """The words of the ticked packs."""
    ids = set(ids)
    return list(dict.fromkeys(w for p in available() if p.id in ids for w in p.words))
