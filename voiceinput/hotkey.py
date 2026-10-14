"""hotkey: win/hotkey.py or mac/hotkey.py."""
import sys

if sys.platform == "darwin":
    from .mac.hotkey import *  # noqa: F401,F403
else:
    from .win.hotkey import *  # noqa: F401,F403
