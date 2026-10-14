"""What the focused app shows around the caret, in platform-independent terms: the selection, the caret's line and
the English terms on screen. Read by selection.py through win/selection.py (UI Automation) or mac/selection.py
(Accessibility)."""
import re
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class Selection:
    text: str
    before: str       # rest of the line before the selection
    after: str        # rest of the line after the selection
    app: str          # process name (chrome.exe) or bundle id (com.google.Chrome)
    rect: tuple | None = None   # selection bounds (x, y, w, h) in screen coordinates (where menus open; see
    #                             system.to_logical)
    term: tuple | None = None   # terminal: where the selection is, for selection.move_cursor_after


@dataclass
class Context:
    selection: Selection | None = None
    terms: list[str] = field(default_factory=list)
    caret: tuple[str, str] | None = None   # without a selection: the line's text before and after the caret


# English terms on screen bias the recognizer toward them (worktree, LLM, sherpa-onnx in a terminal).
# Common words are left out: boosting them only risks inserting them.
_TERM = re.compile(r"(?<![A-Za-z0-9_.])[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9_]|\.[A-Za-z])")
_COMMON = set("""the and for you that this with from have are was were will would could should there their
what when where which while about after before again because being between both each into just like make
more most much must only other over same some such than then them they these those through under until very
your yours also been does done doing here how its itself our out own down off once all any can did don few
has had her him his not now nor too yes who whom why let may might new old use used using file files line
lines code text true false null none return print import class function def else elif self error value
name type data list open close save edit view help tools window select click press enter""".split())
_MAX_TERMS = 40


def terms_in(text: str) -> list[str]:
    """Distinctive English terms in text, most frequent first: acronyms, CamelCase and words with digits
    always; other words of 5+ letters only when they appear at least twice. Dotted names (ranges.GetText) are
    skipped: nobody says them aloud."""
    counts = Counter(m.group(0) for m in _TERM.finditer(text))
    out = []
    for w, n in counts.most_common():
        if len(w) < 3 or w.casefold() in _COMMON:
            continue
        strong = any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w)
        if strong or (len(w) >= 5 and n >= 2):
            out.append(w)
        if len(out) == _MAX_TERMS:
            break
    return out
