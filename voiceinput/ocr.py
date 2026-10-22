"""Read the foreground window with Windows' built-in OCR (2026-10-13, the user's request: names on screen in any
app, also ones like LINE that draw their messages themselves and show UI Automation nothing).

The window is drawn (PrintWindow) into memory and recognized by Windows.Media.Ocr
(Traditional Chinese, else the user's languages): offline, nothing is saved or sent. The pixels and the text are
dropped as soon as screen_terms has picked its terms (the user's condition: no records).
"""
import asyncio
import ctypes
import ctypes.wintypes as wt
import logging
import re
import threading

log = logging.getLogger("voiceinput")

_MAX_SIDE = 4000          # larger windows are skipped (OCR time grows with the area)
_engine = None
_one_at_a_time = threading.Lock()   # the engine refuses a second read while one runs (the window and the monitors)
_SPACED = re.compile(r"(?<=[㐀-䶿一-鿿，。、！？：；「」（）])\s+(?=[㐀-䶿一-鿿，。、！？：；「」（）])")


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD), ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD)]


_gdi_ready = False


def _gdi():
    """user32 / gdi32 with 64-bit handle types: ctypes' default int cut handles above 2**31 and crashed now and
    then (2026-10-14)."""
    global _gdi_ready
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    if not _gdi_ready:
        H = ctypes.c_void_p
        for fn, res, args in (
                (user32.GetDC, H, [H]), (user32.ReleaseDC, ctypes.c_int, [H, H]),
                (user32.PrintWindow, wt.BOOL, [H, H, wt.UINT]),
                (gdi32.CreateCompatibleDC, H, [H]), (gdi32.CreateCompatibleBitmap, H, [H, ctypes.c_int, ctypes.c_int]),
                (gdi32.SelectObject, H, [H, H]), (gdi32.DeleteObject, wt.BOOL, [H]), (gdi32.DeleteDC, wt.BOOL, [H]),
                (gdi32.BitBlt, wt.BOOL, [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H,
                                         ctypes.c_int, ctypes.c_int, wt.DWORD]),
                (gdi32.StretchBlt, wt.BOOL, [H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H,
                                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.DWORD]),
                (gdi32.SetStretchBltMode, ctypes.c_int, [H, ctypes.c_int]),
                (gdi32.SetBrushOrgEx, wt.BOOL, [H, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]),
                (gdi32.GetDIBits, ctypes.c_int, [H, H, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                                 wt.UINT])):
            fn.restype, fn.argtypes = res, args
        _gdi_ready = True
    return user32, gdi32


_SCALE = 1.5   # LINE roster, 17 names: 12 read as is, 15 at 1.5x, 14 at 2x; the same 0.45s (2026-10-14)
_PASSES = (1.0, 2.0)   # one read missed a bold name that the next read of the same window found (曾冠維 in LINE's
                       # chat list): two reads, joined; ~1s in the background while the user speaks


def _capture(hwnd: int | None, scale: float = None, rect: tuple | None = None) -> tuple[int, int, bytes] | None:
    """The window's pixels (or, with rect = (left, top, right, bottom), that part of the screen as it is seen),
    BGRA top-down, 1.5 times larger (small chat text is read much better), unless that would pass OCR's size
    limit."""
    user32, gdi32 = _gdi()
    r = wt.RECT()
    if rect is not None:
        r.left, r.top, r.right, r.bottom = rect
    else:
        ctypes.windll.user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))     # PrintWindow draws the whole rect
    w, h = r.right - r.left, r.bottom - r.top
    if w < 40 or h < 40 or w > _MAX_SIDE or h > _MAX_SIDE:
        return None
    scale = _SCALE if scale is None else scale
    nw, nh = int(w * scale), int(h * scale)
    if max(nw, nh) > 9000:
        nw, nh = w, h
    screen = user32.GetDC(None)
    mem, big = gdi32.CreateCompatibleDC(screen), gdi32.CreateCompatibleDC(screen)
    bmp, bmp2 = gdi32.CreateCompatibleBitmap(screen, w, h), gdi32.CreateCompatibleBitmap(screen, nw, nh)
    old, old2 = gdi32.SelectObject(mem, bmp), gdi32.SelectObject(big, bmp2)
    try:
        # the window draws itself (PW_RENDERFULLCONTENT: also when partly covered); else copy the screen
        if rect is not None or not user32.PrintWindow(hwnd, mem, 2):
            gdi32.BitBlt(mem, 0, 0, w, h, screen, r.left, r.top, 0x00CC0020)   # SRCCOPY
        gdi32.SetStretchBltMode(big, 4)                                     # HALFTONE: smooth
        gdi32.SetBrushOrgEx(big, 0, 0, None)
        gdi32.StretchBlt(big, 0, 0, nw, nh, mem, 0, 0, w, h, 0x00CC0020)
        info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), nw, -nh, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(nw * nh * 4)
        gdi32.SelectObject(big, old2)          # a bitmap must not be selected while GetDIBits reads it
        if not gdi32.GetDIBits(big, bmp2, 0, nh, buf, ctypes.byref(info), 0):
            return None
        return nw, nh, buf.raw
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteObject(bmp2)
        gdi32.DeleteDC(mem)
        gdi32.DeleteDC(big)
        user32.ReleaseDC(None, screen)


def _get_engine():
    global _engine
    if _engine is None:
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
        _engine = OcrEngine.try_create_from_language(Language("zh-Hant-TW"))
        if _engine is None:
            _engine = OcrEngine.try_create_from_user_profile_languages()
    return _engine


async def _recognize(w: int, h: int, pixels: bytes) -> str:
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter
    writer = DataWriter()
    writer.write_bytes(pixels)
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
    with _one_at_a_time:
        result = await _get_engine().recognize_async(bitmap)
    return "\n".join(_SPACED.sub("", line.text) for line in result.lines)


def _monitors() -> list[tuple]:
    """Every monitor's rectangle in virtual-screen pixels."""
    out = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wt.RECT), ctypes.c_void_p)

    def cb(_m, _dc, r, _d):
        out.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, proto(cb), None)
    return out


def screens_text() -> str:
    """The text OCR reads on every monitor as the user sees it (the user, 2026-10-22: he may look at the window on
    the left and speak into the box on the right, so the foreground window is not all the screen). One read per
    monitor; pixels and text are dropped by the caller as soon as the terms are picked."""
    try:
        if _get_engine() is None:
            return ""
        out = []
        for rect in _monitors():
            shot = _capture(None, 1.0, rect)
            if shot is None:
                continue
            out.append(asyncio.run(_recognize(*shot)))
            del shot
        return "\n".join(out)
    except Exception:
        log.exception("OCR of the screens failed")
        return ""


def window_text(hwnd: int | None = None) -> str:
    """The text OCR reads in the foreground window (two reads at different sizes, joined), "" when it cannot (no
    OCR language, window too large)."""
    try:
        hwnd = hwnd or ctypes.windll.user32.GetForegroundWindow()
        if not hwnd or _get_engine() is None:
            return ""
        out = []
        for scale in _PASSES:
            shot = _capture(hwnd, scale)
            if shot is None:
                continue
            out.append(asyncio.run(_recognize(*shot)))
            del shot
        return "\n".join(out)
    except Exception:
        log.exception("OCR failed")
        return ""
