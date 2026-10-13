"""Term packs (詞包, 2026-10-13): curated word lists shipped with the app (voiceinput/packs/<id>.txt), ticked on the
詞庫 tab. Every word of a ticked pack biases X-ASR at PCT, the same percentage scale as the user's list (hotwords.py);
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
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("voiceinput")

DIR = Path(__file__).resolve().parent / "packs"
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


def available() -> list[Pack]:
    out = []
    rank = {pack_id: i for i, pack_id in enumerate(ORDER)}
    for path in sorted(DIR.glob("*.txt"), key=lambda p: (rank.get(p.stem, len(ORDER)), p.stem)):
        try:
            out.append(_load(path))
        except (OSError, UnicodeDecodeError):
            log.exception("term pack %s", path.name)
    return out


def words_for(ids) -> list[str]:
    """The words of the ticked packs."""
    ids = set(ids)
    return list(dict.fromkeys(w for p in available() if p.id in ids for w in p.words))
