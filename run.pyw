import sys

if sys.platform not in ("win32", "darwin"):
    sys.exit("VoiceInput runs on Windows and macOS only.")

from voiceinput.app import main

sys.exit(main())
