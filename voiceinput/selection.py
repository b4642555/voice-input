"""The focused app's selection, caret line and on-screen terms: Windows UI Automation (win/selection.py) or macOS
Accessibility (mac/selection.py), behind the same functions. Read at key-down on a background thread
(ContextProbe) so a slow app does not delay listening."""
import sys
import threading

from .context import Context, Selection, terms_in  # noqa: F401 (re-exported)

if sys.platform == "darwin":
    from .mac.selection import move_cursor_after, read_caret, read_selection, term_watcher, visible_text
else:
    from .win.selection import move_cursor_after, read_caret, read_selection, term_watcher, visible_text

__all__ = ["Context", "ContextProbe", "Selection", "move_cursor_after", "read_caret", "read_context",
           "read_selection", "term_watcher", "terms_in", "visible_text"]


def read_context() -> Context:
    """The focused app's selection (see read_selection), else the text around its caret, and the English terms
    visible in it."""
    ctx = Context()
    ctx.selection = read_selection()
    if ctx.selection is None:
        ctx.caret = read_caret()
    ctx.terms = terms_in(visible_text())
    return ctx


class ContextProbe:
    """Reads the selection and on-screen terms on a background thread so a slow app does not delay the
    recording start."""

    def __init__(self):
        self._result = Context()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        self._result = read_context()

    def result(self, timeout: float = 2.0) -> Context:
        self._thread.join(timeout)
        return self._result
