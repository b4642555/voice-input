"""While recording, other apps get no microphone (the user, 2026-10-15: 按下錄音鍵時，Discord 等其他軟體自動關掉，
只有這套軟體收得到音). Every capture session of every active microphone that belongs to another process is muted
(Core Audio ISimpleAudioVolume, the same per-app mute as the volume mixer), and unmuted when the dictation is done.
A session the app or the user had muted already is left alone. The device itself is never muted (that would silence
our own recording too). comtypes, no extra package.
"""
import ctypes
import logging
import os
from ctypes import HRESULT, POINTER, c_float, c_int, c_uint, c_void_p
from ctypes.wintypes import BOOL, DWORD, LPWSTR

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown

from .mute import IMMDevice, _CLSCTX_ALL, _CLSID_MMDeviceEnumerator

log = logging.getLogger("voiceinput")

_eCapture, _DEVICE_STATE_ACTIVE = 1, 1


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint))),
        COMMETHOD([], HRESULT, "Item", (["in"], c_uint), (["out"], POINTER(POINTER(IMMDevice)))),
    ]


class _Enumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], DWORD), (["in"], DWORD),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)))),
    ]


class ISimpleAudioVolume(IUnknown):
    _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float))),
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL))),
    ]


class IAudioSessionControl2(IUnknown):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = [
        # IAudioSessionControl
        COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int))),
        COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "SetDisplayName", (["in"], LPWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "SetIconPath", (["in"], LPWSTR), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID)), (["in"], POINTER(GUID))),
        COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p)),
        COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p)),
        # IAudioSessionControl2
        COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(LPWSTR))),
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD))),
        COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], BOOL)),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int))),
        COMMETHOD([], HRESULT, "GetSession", (["in"], c_int), (["out"], POINTER(POINTER(IUnknown)))),
    ]


class IAudioSessionManager2(IUnknown):
    _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID)), (["in"], DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID)), (["in"], DWORD),
                  (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetSessionEnumerator", (["out"], POINTER(POINTER(IAudioSessionEnumerator)))),
    ]


def _process_name(pid: int) -> str:
    """The program's file name (Discord.exe), for the log."""
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return str(pid)
        try:
            buf, size = ctypes.create_unicode_buffer(520), DWORD(520)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value)
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass
    return str(pid)


def other_capture_sessions():
    """(process id, its ISimpleAudioVolume) for every microphone session of another process, on all active
    microphones."""
    try:
        comtypes.CoInitialize()
    except OSError:
        pass
    me = os.getpid()
    enum = comtypes.CoCreateInstance(_CLSID_MMDeviceEnumerator, _Enumerator, _CLSCTX_ALL)
    devices = enum.EnumAudioEndpoints(_eCapture, _DEVICE_STATE_ACTIVE)
    out = []
    for i in range(devices.GetCount()):
        try:
            dev = devices.Item(i)
            mgr = dev.Activate(ctypes.byref(IAudioSessionManager2._iid_), _CLSCTX_ALL, None) \
                .QueryInterface(IAudioSessionManager2)
            sessions = mgr.GetSessionEnumerator()
            for k in range(sessions.GetCount()):
                ctl = sessions.GetSession(k).QueryInterface(IAudioSessionControl2)
                pid = ctl.GetProcessId()
                if pid and pid != me:
                    out.append((pid, ctl.QueryInterface(ISimpleAudioVolume)))
        except Exception:
            log.debug("a microphone's sessions could not be read", exc_info=True)
    return out


class MicGuard:
    """mute() while recording, restore() when it stops. The sessions are looked up again on restore (by process,
    on the calling thread): holding the COM objects from mute() and unmuting through them failed silently in the app
    and left Steam, Discord and LINE muted (2026-10-15). What we muted is also written to a file, so a crash in
    between is undone at the next start (recover())."""

    def __init__(self, state_file=None):
        from .paths import DATA_DIR
        self._file = state_file or (DATA_DIR / "mic-muted.json")
        self._muted: set[int] = set()     # process ids whose microphone we muted

    def _save(self):
        import json
        try:
            if self._muted:
                self._file.write_text(json.dumps(sorted(self._muted)), encoding="utf-8")
            else:
                self._file.unlink(missing_ok=True)
        except OSError:
            log.debug("could not record the muted microphones", exc_info=True)

    def mute(self):
        if self._muted:
            return
        try:
            names, skipped = [], []
            for pid, vol in other_capture_sessions():
                if vol.GetMute():
                    skipped.append(_process_name(pid))
                    continue          # muted already (by the app or the user): not ours to undo
                vol.SetMute(True, None)
                self._muted.add(pid)
                names.append(_process_name(pid))
            self._save()
            log.info("microphone muted for other apps: %s%s", ", ".join(sorted(set(names))) or "none",
                     f" (already muted: {', '.join(sorted(set(skipped)))})" if skipped else "")
        except Exception:
            log.exception("muting other apps' microphones failed")

    def restore(self):
        if not self._muted:
            return
        pids, self._muted = self._muted, set()
        self._unmute(pids)
        self._save()

    @staticmethod
    def _unmute(pids):
        left = []
        try:
            for pid, vol in other_capture_sessions():
                if pid in pids and vol.GetMute():
                    vol.SetMute(False, None)
                    if vol.GetMute():
                        left.append(_process_name(pid))
        except Exception:
            log.exception("unmuting other apps' microphones failed")
            return
        if left:
            log.warning("microphone still muted for: %s", ", ".join(left))

    def recover(self):
        """At start: our own microphone session unmuted (Windows keeps a session's mute for the program), and any
        app a crashed run left muted."""
        import json
        pids = set()
        try:
            pids = set(json.loads(self._file.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        try:
            me = os.getpid()
            enum = comtypes.CoCreateInstance(_CLSID_MMDeviceEnumerator, _Enumerator, _CLSCTX_ALL)
            devices = enum.EnumAudioEndpoints(_eCapture, _DEVICE_STATE_ACTIVE)
            for i in range(devices.GetCount()):
                mgr = devices.Item(i).Activate(ctypes.byref(IAudioSessionManager2._iid_), _CLSCTX_ALL, None) \
                    .QueryInterface(IAudioSessionManager2)
                sessions = mgr.GetSessionEnumerator()
                for k in range(sessions.GetCount()):
                    ctl = sessions.GetSession(k).QueryInterface(IAudioSessionControl2)
                    pid = ctl.GetProcessId()
                    if pid == me or pid in pids:
                        vol = ctl.QueryInterface(ISimpleAudioVolume)
                        if vol.GetMute():
                            vol.SetMute(False, None)
                            log.info("microphone unmuted at start: %s", _process_name(pid))
        except Exception:
            log.exception("recovering muted microphones failed")
        try:
            self._file.unlink(missing_ok=True)
        except OSError:
            pass
