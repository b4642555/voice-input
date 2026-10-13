"""Known English terms written the way they are listed, after recognition (2026-10-13, the user's choice).

X-ASR often hears an English term right but spells it its own way: Deep Seek, Notebook LM, fine tune, C N S. A run
of English words whose letters and digits, ignoring case, spaces and hyphens, are exactly a known term's is
rewritten as that term (user list, ticked term packs, terms on screen). A difference in case alone is fixed only
when it adds capitals inside the term (deepseek -> DeepSeek, claude code -> Claude Code, llm -> LLM), so an ordinary
word is left as it was said (cursor stays cursor, Open Source at a sentence start stays).

Learned mishearings (aliases) are rewritten always: a misheard English spelling the user replaced in 選字
(Dipsick -> DeepSeek) is stored with the term (hotwords.Hotword.aliases) and matched the same way (Dip sick too;
deep sick has other letters and needs its own pick). This reverses, for English only, the 2026-10-11 decision to
leave words to biasing alone: raising the score above +4 broke sentences and negative scores for the misheard
spellings made it worse (TTS, 2026-10-13: Deep Seek fell back to Deepsec).
"""
import re

_RUN = re.compile(r"[A-Za-z0-9]+(?:[ -]?[A-Za-z0-9]+)*")
_PIECE = re.compile(r"[A-Za-z0-9]+")
_MAX_PIECES = 5


def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _adds_capitals(span: str, term: str) -> bool:
    """span and term differ only in case: true when term only adds capitals, one past its first letter."""
    return (all(t.isupper() or not s.isupper() for s, t in zip(span, term))
            and any(c.isupper() for c in term[1:]))


def table(terms, aliases) -> dict[str, tuple[str, bool]]:
    """key -> (term, is_alias); the first of terms with the same key wins, aliases before terms."""
    out: dict[str, tuple[str, bool]] = {}
    for alias, term in aliases:
        k = key(alias)
        if len(k) > 1:
            out.setdefault(k, (term, True))
    for term in terms:
        k = key(term)
        if len(k) > 1:
            out.setdefault(k, (term, False))
    return out


def fix(text: str, known: dict[str, tuple[str, bool]]) -> tuple[str, list[tuple[str, str]]]:
    """text with known terms spelled as listed, and the (before, after) changes."""
    if not known:
        return text, []
    changes = []

    def run(m):
        s = m.group()
        pieces = list(_PIECE.finditer(s))
        out, pos, i = [], 0, 0
        while i < len(pieces):
            for n in range(min(_MAX_PIECES, len(pieces) - i), 0, -1):
                a, b = pieces[i].start(), pieces[i + n - 1].end()
                span = s[a:b]
                hit = known.get(key(span))
                if hit is None:
                    continue
                term, alias = hit
                if span == term:   # already right: its pieces are not tried as shorter terms
                    i += n
                    break
                if not alias and span.lower() == term.lower() and not _adds_capitals(span, term):
                    continue
                out.append(s[pos:a] + term)
                changes.append((span, term))
                pos, i = b, i + n
                break
            else:
                i += 1
        return "".join(out) + s[pos:]

    return _RUN.sub(run, text), changes
