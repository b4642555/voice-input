"""Discord quiet while recording (the user, 2026-10-15: 錄音時 Discord 收不到我說話). Muting other apps' microphone
streams in Windows muted our own too on the user's PC, so Discord is asked to mute itself with its own shortcut:
the user's choice is Discord's 切換拒聽 Ctrl+Shift+D, tapped when the recording starts and again when it stops.
(Reading the shortcut from Discord's storage was tried: it is snappy-compressed LevelDB most of the time.)
"""
import logging

from .keys import combo_vks

log = logging.getLogger("voiceinput")


class DiscordMute:
    def __init__(self):
        self._pressed = None      # the keys tapped at the start, tapped again at the end

    def start(self, combo: str):
        if self._pressed is not None:
            return
        keys = combo_vks(combo)
        if not keys:
            return
        from .output import press_chord
        press_chord(*keys)
        self._pressed = keys
        log.info("discord: %s pressed at the start of the recording", combo)

    def stop(self):
        if self._pressed is None:
            return
        from .output import press_chord
        keys, self._pressed = self._pressed, None
        press_chord(*keys)
        log.info("discord: pressed again at the end of the recording")
