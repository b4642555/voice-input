"""autostart: win/autostart.py or mac/autostart.py."""
import sys

if sys.platform == "darwin":
    from .mac.autostart import *  # noqa: F401,F403
else:
    from .win.autostart import *  # noqa: F401,F403
