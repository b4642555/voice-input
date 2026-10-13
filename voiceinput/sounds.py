"""English words that sound like a selection, for 選字 (2026-10-13): clock code -> Claude Code, lanchin ->
LangChain, Deepsec -> DeepSeek, Laura -> LoRA. Only known terms are offered (the user's list, ticked term packs,
English terms on screen): a misheard word cannot be turned into one nobody listed.

A rough consonant skeleton (key) is compared by edit distance, and the plain letters by difflib; the better of the
two counts. The key forgives what X-ASR mixes up with a Taiwanese accent: voicing (b/p, d/t, g/k, v/f, z/s), ng/n,
vowels, a doubled letter. A short acronym is read as letter names first (CNS -> "si en es", near Seedance). Tested
on the TTS mishearings of the AI pack (dev experiment 2026-10-13): 20 of 22 targets first, Claude for clock and
Gemini for Jimmy second; ordinary words (email, meeting) get look-alikes scoring up to ~0.67.
"""
import re
from difflib import SequenceMatcher

THRESHOLD = 0.6

_RULES = [(r"ph", "f"), (r"gh", ""), (r"ck", "k"), (r"sch", "sk"), (r"[cs]h", "x"), (r"th", "t"), (r"wh", "w"),
          (r"qu", "kw"), (r"q", "k"), (r"x", "ks"), (r"c(?=[eiy])", "s"), (r"c", "k"), (r"dg", "j"),
          (r"g(?=[eiy])", "j"), (r"ng", "n"), (r"b", "p"), (r"d", "t"), (r"g", "k"), (r"v", "f"), (r"z", "s"),
          (r"j", "x"), (r"h", ""), (r"(?<=.)[aeiouy]+", ""), (r"^[aeiouy]+", "a"), (r"w(?![aeiouy])", ""),
          (r"(.)\1+", r"\1")]
_RULES = [(re.compile(a), b) for a, b in _RULES]
_LETTERS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    "ei bi si di i ef ji eich ai jei kei el em en ou pi kiu ar es ti iu vi dabliu eks wai zi".split()))
_ACRONYM = re.compile(r"\b[A-Z]{2,4}\b")


def _spoken(text: str) -> str:
    """CNS -> si en es: a short all-capital word is what X-ASR writes for letter names."""
    return _ACRONYM.sub(lambda m: " ".join(_LETTERS[c] for c in m.group()), text)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def key(text: str) -> str:
    s = _letters(text)
    for pattern, repl in _RULES:
        s = pattern.sub(repl, s)
    return s


def _distance(a: str, b: str) -> float:
    """Edit distance; a different last sound costs half (clock / Claude: klk / klt)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            sub = 0 if ca == cb else 0.5 if i == len(a) and j == len(b) else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + sub))
        prev = cur
    return prev[-1]


def similarity(heard: str, term: str) -> float:
    best = 0.0
    for h in dict.fromkeys((heard, _spoken(heard))):
        ka, kb = key(h), key(term)
        by_key = 1 - _distance(ka, kb) / max(len(ka), len(kb), 1)
        by_letters = SequenceMatcher(None, _letters(h), _letters(term)).ratio()
        best = max(best, by_key, by_letters)
    return best


def alike(heard: str, terms, limit: int = 4) -> list[str]:
    """Known terms that sound like heard, best first (ties keep the order of terms); never heard itself."""
    scored = []
    for i, term in enumerate(dict.fromkeys(terms)):
        if term == heard.strip():
            continue
        s = similarity(heard, term)
        if s >= THRESHOLD:
            scored.append((-s, i, term))
    return [t for _, _, t in sorted(scored)[:limit]]
