"""Is this an English word? Windows' own spell checker (ISpellChecker, en-US), offline and already installed.

Used by screen_en so that a real word is never "fixed" by sound into a name on screen (instead / installed were
turned into Instagram before, 2026-10-13). Answers are cached; when the checker is unavailable nothing is a word.
"""
import ctypes
import functools
import logging
import threading

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
from ctypes import POINTER, c_int, c_ulong, c_wchar_p

log = logging.getLogger("voiceinput")


class ISpellingError(IUnknown):
    _iid_ = GUID("{B7C82D61-FBE8-4B47-9B27-6C0D2E0DE0A3}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_StartIndex", (["out", "retval"], POINTER(c_ulong), "value")),
        COMMETHOD([], HRESULT, "get_Length", (["out", "retval"], POINTER(c_ulong), "value")),
        COMMETHOD([], HRESULT, "get_CorrectiveAction", (["out", "retval"], POINTER(c_int), "value")),
        COMMETHOD([], HRESULT, "get_Replacement", (["out", "retval"], POINTER(c_wchar_p), "value")),
    ]


class IEnumSpellingError(IUnknown):
    _iid_ = GUID("{803E3BD4-2828-4410-8290-418D1D73C762}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["out"], POINTER(POINTER(ISpellingError)), "value")),
    ]


class ISpellChecker(IUnknown):
    _iid_ = GUID("{B6FD0B71-E2BC-4653-8D05-F197E412770B}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_LanguageTag", (["out", "retval"], POINTER(c_wchar_p), "value")),
        COMMETHOD([], HRESULT, "Check", (["in"], c_wchar_p, "text"),
                  (["out", "retval"], POINTER(POINTER(IEnumSpellingError)), "value")),
    ]


class ISpellCheckerFactory(IUnknown):
    _iid_ = GUID("{8E018A9D-2415-4677-BF08-794EA61F94BB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "get_SupportedLanguages", (["out", "retval"], POINTER(POINTER(IUnknown)), "value")),
        COMMETHOD([], HRESULT, "IsSupported", (["in"], c_wchar_p, "languageTag"),
                  (["out", "retval"], POINTER(c_int), "value")),
        COMMETHOD([], HRESULT, "CreateSpellChecker", (["in"], c_wchar_p, "languageTag"),
                  (["out", "retval"], POINTER(POINTER(ISpellChecker)), "value")),
    ]


_CLSID = GUID("{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}")
_local = threading.local()


def _checker():
    if not hasattr(_local, "checker"):
        _local.checker = None
        try:
            comtypes.CoInitialize()
            factory = comtypes.CoCreateInstance(_CLSID, interface=ISpellCheckerFactory)
            if factory.IsSupported("en-US"):
                _local.checker = factory.CreateSpellChecker("en-US")
        except Exception:
            log.exception("the Windows spell checker is unavailable")
    return _local.checker


@functools.lru_cache(maxsize=4096)
def is_word(word: str) -> bool:
    """word (letters only) is an ordinary English word: correctly spelled in lower case (Claude is only a name)."""
    if not word.isalpha():
        return False
    checker = _checker()
    if checker is None:
        return False
    try:
        errors = checker.Check(word.lower())
        error = POINTER(ISpellingError)()
        hr = errors._IEnumSpellingError__com_Next(ctypes.byref(error))
        return hr == 1 or not error      # S_FALSE: no spelling error
    except Exception:
        log.exception("spell check failed")
        return False
