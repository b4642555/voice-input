"""macOS: windows, focus, permissions, sounds and the like (system.py picks this module or win/system.py).

Qt widgets are NSViews; their NSWindow is reached through pyobjc to make the indicator, the glow and the 選字 menu
behave like their Windows counterparts: never activating the app, above full-screen apps, on every Space."""
import logging
import subprocess
import threading

import objc
from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSPanel, NSRunningApplication,
                    NSSound, NSStatusWindowLevel, NSWorkspace)
from PySide6.QtCore import QPoint

from .keyboard import MAC_TO_VK

log = logging.getLogger("voiceinput")

_NONACTIVATING_PANEL = 1 << 7
# NSWindowCollectionBehaviorCanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary
_EVERYWHERE = (1 << 0) | (1 << 4) | (1 << 6) | (1 << 8)
_widgets: dict[int, object] = {}   # window_handle -> widget, for point_on on the tap's thread
_sounds: list = []                 # the NSSound playing (kept referenced until the next one)

_PANES = {"accessibility": "Privacy_Accessibility", "input": "Privacy_ListenEvent", "microphone": "Privacy_Microphone"}


def setup(qapp) -> None:
    """No Dock icon (the app lives in the menu bar), like LSUIElement in the bundle's Info.plist."""
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def open_privacy(pane: str) -> None:
    subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{_PANES[pane]}"])


def permissions(notify) -> None:
    """Ask for what macOS needs the user to allow, once at launch: the microphone, Accessibility (reading the
    selection, typing, the event tap) and Input Monitoring (keys in the event tap). macOS shows its own dialogs
    for the first and the last; Accessibility only has a prompt that leads to System Settings. The event tap is
    tried again until allowed (mac/hotkey.py), so no restart is needed for it."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
        if status == 0:     # not determined
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda ok: None)
        elif status in (1, 2):   # restricted / denied
            notify("麥克風沒有開放：請在「系統設定 › 隱私權與安全性 › 麥克風」打開 VoiceInput")
            open_privacy("microphone")
    except Exception:
        log.exception("microphone permission check failed")
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        trusted = AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception:
        log.exception("accessibility check failed")
        trusted = True
    try:
        import Quartz as Q
        if not Q.CGPreflightListenEventAccess():
            Q.CGRequestListenEventAccess()
            trusted = False
    except Exception:
        log.exception("input monitoring check failed")
    if not trusted:
        notify("請在「系統設定 › 隱私權與安全性」的「輔助使用」和「輸入監控」打開 VoiceInput，"
               "打開後幾秒內就能用滑鼠中鍵開始聽寫")
        threading.Timer(1.0, open_privacy, ("accessibility",)).start()


def notify(text: str) -> bool:
    """A notification. Qt's needs a bundle of its own, which the Python behind the launcher is not; osascript
    shows it as Script Editor's."""
    subprocess.Popen(["osascript", "-e", f"display notification {_quote(text)} with title \"VoiceInput\""])
    return True


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def foreground() -> int:
    """The app in front (its process id): typing in another app ends what we know about the text."""
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app is not None else 0


def window_handle(widget) -> int:
    handle = int(widget.winId())
    _widgets[handle] = widget
    return handle


def foreground_is(handle: int) -> bool:
    """Keys typed now go to one of our windows (the 選字 menu's input box): our app is active."""
    return bool(NSRunningApplication.currentApplication().isActive())


def point_on(handle: int, x: int, y: int) -> bool:
    """The screen point (Qt's logical coordinates) is on the window `handle` (the tap's thread)."""
    w = _widgets.get(handle)
    return w is not None and w.isVisible() and w.frameGeometry().contains(QPoint(x, y))


def _window(widget):
    try:
        view = objc.objc_object(c_void_p=int(widget.winId()))
        return view.window()
    except Exception:
        log.exception("no NSWindow for %s", widget)
        return None


def _float(win) -> None:
    win.setHidesOnDeactivate_(False)
    win.setLevel_(NSStatusWindowLevel)
    win.setCollectionBehavior_(_EVERYWHERE)


def click_through(widget) -> None:
    """The indicator and the glow: clicks pass through, above other windows and full-screen apps."""
    win = _window(widget)
    if win is not None:
        _float(win)
        win.setIgnoresMouseEvents_(True)


def no_activate(widget) -> None:
    """The 選字 menu: clicking it does not take focus from the app it was opened in (a nonactivating panel)."""
    win = _window(widget)
    if win is None:
        return
    _float(win)
    if isinstance(win, NSPanel):
        win.setStyleMask_(win.styleMask() | _NONACTIVATING_PANEL)
        win.setBecomesKeyOnlyIfNeeded_(True)


def take_focus(widget):
    """The 選字 menu's input box was clicked: our app and the menu take focus so an input method works. Returns
    the app to give focus back to."""
    prev = NSWorkspace.sharedWorkspace().frontmostApplication()
    NSApp.activateIgnoringOtherApps_(True)
    win = _window(widget)
    if win is not None:
        win.makeKeyAndOrderFront_(None)
    return prev


def give_focus(prev) -> None:
    if prev is not None and prev.processIdentifier() != NSRunningApplication.currentApplication().processIdentifier():
        prev.activateWithOptions_(1 << 1)   # NSApplicationActivateIgnoringOtherApps


def bring_to_front(widget) -> None:
    """The settings window, opened from the menu bar: an accessory app has to activate itself."""
    NSApp.activateIgnoringOtherApps_(True)
    widget.raise_()
    widget.activateWindow()


def to_logical(x, y) -> QPoint:
    """Accessibility reports points with the origin at the top left of the main screen, as Qt does."""
    return QPoint(int(x), int(y))


def dark_menu_bar() -> bool:
    """Unused for the color: the menu bar icon is a template image macOS colors itself."""
    return False


def play_sound(path: str) -> None:
    """Asynchronous; a new sound cuts the one playing."""
    for s in _sounds:
        s.stop()
    _sounds.clear()
    sound = NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
    if sound is not None:
        _sounds.append(sound)
        sound.play()


def key_of(event) -> int:
    """A Qt key event's key as a Windows virtual-key code (keys.py); 0 when it has none."""
    return MAC_TO_VK.get(event.nativeVirtualKey(), 0)
