"""Microphone devices (listen.Listener records)."""
import sounddevice as sd


def list_input_devices() -> list[str]:
    """Input device names from the platform's default host API (avoids MME/WASAPI duplicates)."""
    try:
        default_api = sd.default.hostapi
        devices = sd.query_devices()
    except Exception:
        return []
    names = []
    for d in devices:
        if (d["max_input_channels"] > 0 and d["hostapi"] == default_api
                and "Sound Mapper" not in d["name"] and d["name"] not in names):
            names.append(d["name"])
    return names


def resolve_device(name: str):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    return None

