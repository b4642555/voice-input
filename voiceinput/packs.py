"""Term packs (詞包, 2026-10-13): curated word lists shipped with the app (voiceinput/packs/<id>.txt), ticked on the
詞庫 tab. Every word of a ticked pack biases X-ASR at PCT, the same percentage scale as the user's list (hotwords.py);
a word is listed there only once the user picks it, has it replaced or moves it, and from then on the user's
percentage wins.

PCT measured with Windows TTS (2026-10-13, the AI pack, 18 sentences with a pack term, 28 without): 130% hit 11/18,
150% and 170% 12/18, 200% 14/18, but at 200% 我把檔案存到 cloud 上面 became Claude; nothing else changed at any
strength, and the 178 words cost no time. The TTS voice says English badly, so real hit rates should be higher.

File format: the first line is "# <name>", other lines starting with # are comments, one term per line.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("voiceinput")

DIR = Path(__file__).resolve().parent / "packs"
PCT = 170


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


def available() -> list[Pack]:
    out = []
    for path in sorted(DIR.glob("*.txt")):
        try:
            out.append(_load(path))
        except (OSError, UnicodeDecodeError):
            log.exception("term pack %s", path.name)
    return out


def words_for(ids) -> list[str]:
    """The words of the ticked packs."""
    ids = set(ids)
    return list(dict.fromkeys(w for p in available() if p.id in ids for w in p.words))
