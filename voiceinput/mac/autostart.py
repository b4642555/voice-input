"""macOS: launch at login with a LaunchAgent in ~/Library/LaunchAgents. It opens the app bundle the installer made
(its launcher tells us where it is), or, run from source, run.pyw with this Python."""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from ..paths import APP_DIR

LABEL = "com.voiceinput.app"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _program() -> list[str]:
    app = os.environ.get("VOICEINPUT_APP")   # set by packaging/mac/launcher.c
    if app:
        return ["/usr/bin/open", "-a", app]
    return [sys.executable, str(APP_DIR / "run.pyw")]


def set_enabled(enabled: bool) -> None:
    if enabled:
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": _program(), "RunAtLoad": True,
                                          "ProcessType": "Interactive"}))
    else:
        if PLIST.exists():
            subprocess.run(["launchctl", "unload", str(PLIST)], capture_output=True)
            PLIST.unlink()
