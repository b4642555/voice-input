"""macOS: mute the speakers while listening (setting "錄音時暫時靜音電腦音效"), with AppleScript's volume settings.
Speakers the user had muted themselves are left alone: only a mute we set is undone. osascript takes ~0.1s, so it
runs on a thread of its own, in order."""
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)
_runner = ThreadPoolExecutor(max_workers=1)


def _osascript(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5).stdout.strip()


class Muter:
    def __init__(self):
        self._ours = False   # asked to mute and not restored yet
        self._did = False    # the runner thread muted the speakers (it alone reads and writes this)

    @property
    def active(self) -> bool:
        return self._ours

    def mute(self):
        if self._ours:
            return
        self._ours = True
        _runner.submit(self._mute)

    def restore(self):
        if not self._ours:
            return
        self._ours = False
        _runner.submit(self._unmute)

    def _mute(self):
        try:
            if _osascript("output muted of (get volume settings)") != "true":   # muted by the user: left alone
                _osascript("set volume with output muted")
                self._did = True
        except Exception:
            log.exception("mute failed")

    def _unmute(self):
        if not self._did:
            return
        self._did = False
        try:
            _osascript("set volume without output muted")
        except Exception:
            log.exception("unmute failed")
