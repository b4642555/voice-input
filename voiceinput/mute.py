"""mute: win/mute.py or mac/mute.py."""
import sys

if sys.platform == "darwin":
    from .mac.mute import *  # noqa: F401,F403
else:
    from .win.mute import *  # noqa: F401,F403
