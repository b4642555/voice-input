"""Windows, focus, sounds, permissions: win/system.py or mac/system.py, the same functions on both."""
import sys

if sys.platform == "darwin":
    from .mac.system import *  # noqa: F401,F403
else:
    from .win.system import *  # noqa: F401,F403
