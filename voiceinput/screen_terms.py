"""Chinese terms from the screen (2026-10-13, the user's request: catch the names and topic words on screen without
an LLM). The text of the whole foreground window is read while the user speaks (selection.window_text); this module
picks out what is probably a name or a topic word, and after recognition puts a same-sounding miss back
(張玉薇 -> 張雨薇 when 張雨薇 is on screen).

What counts as a term, after splitting the text into dictionary words (longest match):
- a run of 2-4 characters that make no dictionary word together (張雨薇: 張 / 雨 / 薇), without function characters
  (的 了 是 …), seen at least twice or in the window title: names, product names;
- a rare dictionary word (frequency below candidates.COMMON) of 2-4 characters seen at least twice: topic words;
- two neighbouring two-character words together that are not a dictionary word, seen at least twice
  (薔薇計畫, 信義專案): the names of projects and things the conversation is about.
Common words are never terms, and are never replaced (城市 stays 城市 even with 程式 on screen).

Privacy (the user's condition): the window text is never stored or logged. It is dropped as soon as the terms are
picked, and the terms live only on the one press's context, emptied when its dictation is done. The log records
how many terms were used and that a fix happened, never the words.
"""
import difflib
import math
import re
from collections import Counter

from pypinyin import Style, pinyin

from . import candidates, screen_en
from .textfmt import to_simplified, to_traditional

_RUN = re.compile(r"[㐀-䶿一-鿿]{2,}")
# characters that are almost never part of a name: a run with one is grammar, not a term
_FUNCTION = set("的了是在我你他她它們这這那和與与或也都就要會会有不沒没很個个着著吧嗎吗呢啊喔哦嗯把被讓让給给到說说"
                "對对從从而但又還还再才已之於于以及其為为如果因所可能得地上下中大小多少一二三四五六七八九十")
_MAX_TERMS = 30
_MAX_TEXT = 60000


def _segments(run: str) -> list[str]:
    """The split into dictionary words (up to 4 characters, unknown characters alone) with the fewest pieces, ties
    to the commonest words: 先打開設定頁 is 先 / 打開 / 設定 / 頁, not 先打 / 開設 / 定 / 頁."""
    n = len(run)
    best: list[tuple] = [(0, 0.0, [])] + [None] * n      # (pieces, -sum log frequency, words)
    for i in range(n):
        if best[i] is None:
            continue
        pieces, cost, words = best[i]
        for size in (1, 2, 3, 4):
            if i + size > n:
                break
            w = run[i:i + size]
            if size > 1 and not candidates.is_word(w):
                continue
            cand = (pieces + 1, cost - math.log(candidates.frequency(w) + 1), words + [w])
            if best[i + size] is None or cand[:2] < best[i + size][:2]:
                best[i + size] = cand
    return best[n][2]


def _name_like(run: str) -> bool:
    """Characters that make no word in any split (開設定 holds 設定: a mis-split, not a name) and no stutter (先先)."""
    if len(set(run)) < len(run):
        return False
    return not any(candidates.is_word(run[i:j]) for i in range(len(run)) for j in range(i + 2, len(run) + 1))


def terms(text: str, title: str = "") -> list[str]:
    """Probable names and topic words in text, most frequent first. Empty while the dictionary is loading."""
    if candidates.is_word("中文") is None:
        return []
    counts: Counter = Counter()
    for source, weight in ((text[:_MAX_TEXT], 1), (title, 2)):
        for m in _RUN.finditer(source):
            segs = _segments(m.group(0))
            for a, b in zip(segs, segs[1:]):
                pair = a + b
                if len(a) == len(b) == 2 and len(set(pair)) == 4 and not candidates.is_word(pair)                         and not (set(pair) & _FUNCTION):
                    counts[pair] += weight
            singles: list[str] = []
            for seg in segs + [""]:          # "" flushes the last run of single characters
                if len(seg) == 1 and seg not in _FUNCTION:
                    singles.append(seg)
                    continue
                if 2 <= len(singles) <= 4 and _name_like("".join(singles)):
                    counts["".join(singles)] += weight
                singles = []
                if 2 <= len(seg) <= 4 and candidates.frequency(seg) < candidates.COMMON:
                    counts[seg] += weight
    return [w for w, n in counts.most_common() if n >= 2][:_MAX_TERMS]


def _readings(text: str) -> list[set[str]]:
    """Every toneless reading of each character (柏 is bai and bo: 陳伯翰 must match 陳柏翰)."""
    return [set(r) for r in pinyin(text, style=Style.NORMAL, heteronym=True)]


_MAX_FIX = 3000
_SCREEN_WINS = 100     # a heard word under this frequency gives way to a same-sounding name on screen (一瓶 is 61)
_RARE_NAME = 80    # a dictionary word of 3+ characters under this frequency is a name (輕井澤 is 45, 會議室 102)
_RECENT = 30          # a roster of names (17 in the user's LINE, 2026-10-14) fits
# pieces split only at grammar: numerals and 上下中大小 stay inside names (文心南二店 was cut at 二, 2026-10-13)
# grammar characters that are (almost) never in a name; 又 / 之 / 以 / 其 / 為 … are left out: 林又宸 was cut at 又
_GRAMMAR = set("的了是在我你他她它們这這那和與与或也都就要會会有不沒没很個个着著吧嗎吗呢啊喔哦嗯把被讓让給给到說说"
               "對对從从跟")
_SPLIT = re.compile("[" + "".join(sorted(_GRAMMAR)) + "]+")


def _pieces(text: str) -> list[str]:
    """Runs between grammar characters standing alone, in screen order: 的會議室 splits at 的 but not inside 會議室
    (會 alone is grammar: 我會; cut there, 議室 became a name and 一石二鳥 turned into 議室二鳥)."""
    out = []
    for m in _RUN.finditer(text[-_MAX_TEXT:]):
        piece = ""
        for seg in _segments(m.group(0)):
            if len(seg) == 1 and seg in _GRAMMAR:
                if piece:
                    out.append(piece)
                piece = ""
            else:
                piece += seg
        if piece:
            out.append(piece)
    return out


def _chunks(text: str) -> list[str]:
    """Pieces of 2-6 characters that are not a dictionary word as a whole (平丁天花板, 包樑) or are a rare one of 3+
    (輕井澤 is listed, as a place), in screen order."""
    out = []
    for p in _pieces(text):
        if set(p) & _GRAMMAR:
            continue           # 明天的, 上爆紅的四個: grammar inside, not a name
        if 2 <= len(p) <= 6 and len(set(p)) > 1 \
                and (not candidates.is_word(p) or len(p) >= 3 and candidates.frequency(p) < _RARE_NAME):
            out.append(p)
        elif len(p) > 3:       # 下週去輕井澤玩: the rare word inside
            out += [w for w in _segments(p) if len(w) >= 3 and candidates.frequency(w) < _RARE_NAME]
    return out


def _windows(text: str) -> list[str]:
    """4-6 character windows of longer pieces: OCR runs a line together (輕井澤鍋物文心南二店向心里, 2026-10-13),
    and a long piece would match nothing."""
    out = []
    for p in _pieces(text):
        if len(p) > 6:
            out += [p[i:i + n] for n in (6, 5, 4) for i in range(len(p) - n + 1)]
    return out


# common Taiwanese surnames: a person's name of three characters is also said by its given name alone (雨薇)
_SURNAMES = set("陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余盧梁趙顏柯翁"
                "魏孫戴范方宋鄧杜傅侯曹薛丁卓阮馬董温唐藍石蔣古紀姚連馮歐程湯黄田康姜白汪鄒尤巫鐘黎涂龔嚴韓袁金童陸夏柳凃邵")


# characters that end a name when growing one around a corrected character (又 is not here: 林又宸)
_NAME_STOP = set("的了是在我你他她它們这這那和與与或也都就要會会有不沒没很個个着著吧嗎吗呢啊喔哦嗯把被讓让給给到"
                 "說说跟同對对從从而但還还再才已之於于以及其為为如果因所可能得地")


def expand_around(text: str, start: int, end: int, limit: int = 8) -> str:
    """The run a corrected span sits in (貓米 -> 包你 inside 包你發娛樂城): Chinese characters around it up to a
    punctuation mark, a stop character or `limit` characters in all. "" when nothing is added."""
    lo, hi = start, end
    while lo > 0 and _RUN.match(text[lo - 1] * 2) and text[lo - 1] not in _NAME_STOP and hi - lo < limit:
        lo -= 1
    while hi < len(text) and _RUN.match(text[hi] * 2) and text[hi] not in _NAME_STOP and hi - lo < limit:
        hi += 1
    return text[lo:hi] if (lo, hi) != (start, end) else ""


def has_stop_char(text: str) -> bool:
    return bool(set(text) & _NAME_STOP)


def name_around(text: str, start: int, end: int) -> str:
    """The name a corrected character belongs to (林又成跟曾冠維, 成 -> 宸: 林又宸; the user had selected and retyped
    all three characters, but only one changed). The single characters around it that make no word, up to a stop
    character, four at most, from a surname when there is one on the left. "" when the character is part of a
    dictionary word (程式寫東西 is not a name) or stands alone."""
    if not (0 <= start < end <= len(text)) or not _RUN.match(text[start] * 2) or text[start] in _NAME_STOP:
        return ""
    lo = start
    while lo > 0 and _RUN.match(text[lo - 1] * 2):
        lo -= 1
    hi = end
    while hi < len(text) and _RUN.match(text[hi] * 2):
        hi += 1
    segs, at, k = _segments(text[lo:hi]), lo, None
    spans = []
    for seg in segs:
        spans.append((at, at + len(seg)))
        if at <= start < at + len(seg):
            k = len(spans) - 1
        at += len(seg)
    if k is None or spans[k][1] - spans[k][0] > 1:
        return ""
    single = [b - a == 1 and text[a] not in _NAME_STOP for a, b in spans]
    i = j = k
    while i > 0 and single[i - 1] and k - i < 3:
        i -= 1
    while j + 1 < len(spans) and single[j + 1] and j - i < 3:
        j += 1
    span = text[spans[i][0]:spans[j][1]]
    off = start - spans[i][0]
    sur = next((n for n in range(off + 1) if span[n] in _SURNAMES), None)
    if sur is None:
        return ""        # no surname on the left: two stray characters (機案 in 老虎機案) are not a name (2026-10-15)
    span = span[sur:sur + 3]
    return span if 2 <= len(span) <= 4 and not candidates.is_word(span) else ""


def given_names(names: list[str]) -> list[str]:
    """The given names of three-character person names (張雨薇 -> 雨薇) that are not words themselves."""
    return [n[1:] for n in names if len(n) == 3 and n[0] in _SURNAMES and not candidates.is_word(n[1:])
            and not (set(n[1:]) & _GRAMMAR)]


_NOT_TEXT = re.compile(r"[\s，,。.、!！?？:：;；|·]+")


def without_draft(text: str, draft: str) -> str:
    """Screen text without the lines of the message being written (OCR reads the input box too)."""
    key = _NOT_TEXT.sub("", draft)
    if len(key) < 2:
        return text
    keep = []
    for line in text.splitlines():
        k = _NOT_TEXT.sub("", line)
        # the draft itself, or a long piece of it (OCR wraps a long draft); a name in the list that the draft
        # also holds (雅婷) stays
        if len(k) >= 2 and (key in k or (k in key and len(k) >= 6 and 2 * len(k) >= len(key))):
            continue
        # OCR misreads the draft too (宋家雅婷 read as 餐家雅婷, which then "fixed" 宋家 into 餐家): mostly the same
        # characters in order, most of the draft's length
        if len(k) >= 4 and 2 * len(k) >= len(key) and difflib.SequenceMatcher(None, k, key).ratio() >= 0.7:
            continue           # (陸寶寶 read as 陸寶瓦 kept the draft's 曾冠偉 "on screen")
        keep.append(line)
    return "\n".join(keep)


def normalize_screen(text: str) -> str:
    """Screen text in the form dictations are typed in: a simplified line becomes Traditional (a simplified page
    must not turn 張雨薇 into 张雨薇); a Traditional line is left as it is (s2tw would turn 向心里 into 向心裡 and 包樑
    into 包梁) but for 臺 -> 台 (the user's spelling). Decided for the whole screen, a line only when it is clearly
    simplified itself: one character decided per line turned the name 佳怜 in a Traditional roster into 佳憐."""
    lines = text.splitlines()
    counts = []
    for line in lines:
        trad, simp = to_traditional(line), to_simplified(line)
        counts.append((trad, sum(1 for a, b in zip(line, trad) if a != b and a in simp),
                       sum(1 for a, b in zip(line, simp) if a != b)))
    s_all, t_all = sum(c[1] for c in counts), sum(c[2] for c in counts)
    page_simplified = s_all >= 3 and s_all > 2 * t_all
    out = []
    for line, (trad, s_line, t_line) in zip(lines, counts):
        simplified = page_simplified or (s_line >= 4 and t_line == 0)
        out.append(trad if simplified else line.replace("臺", "台"))
    return "\n".join(out)


def english_names(text: str) -> list[str]:
    return screen_en.names(text)


def fix_english(text: str, names: list[str]) -> tuple[str, int]:
    """English names on screen: misheard English words, then Chinese transliterations (克勞德 -> Claude)."""
    text, n = screen_en.fix(text, names)
    text, m = screen_en.fix_transliterated(text, names, _has_common_word)
    return text, n + m


def _has_common_word(span: str) -> bool:
    return any(candidates.frequency(span[k:k + size]) >= candidates.COMMON
               for size in (2, 3, 4) for k in range(len(span) - size + 1))


def recent(text: str, n: int = _RECENT) -> list[str]:
    """The last n distinct name-like pieces on screen, newest first: the bottom of a chat or a page is what the
    user is talking about now. Seen once is enough (2026-10-13: 平丁天花板 under one LINE message was lost once the
    input box no longer repeated it). These bias the recognizer, so they are few."""
    out: dict[str, None] = {}
    for piece in reversed(_chunks(text)):
        out.setdefault(piece, None)
        if len(out) >= n:
            break
    return list(out)


def fix_candidates(text: str) -> list[str]:
    """Every two-character piece (see _chunks) and every span of 3-4 characters on screen that is not a dictionary word and has no function character, seen
    once is enough (平偉本色 under one Reel). Used only to put back a same-sounding miss, never to bias the
    recognizer: a wrong candidate can only change a span that sounds exactly like it and is not a word itself."""
    out: dict[str, None] = dict.fromkeys(p for p in _chunks(text) if len(p) == 2)   # 包樑: whole pieces only
    for w in _windows(text):
        if not candidates.is_word(w):
            out.setdefault(w, None)
    for m in _RUN.finditer(text[:_MAX_TEXT]):
        run = m.group(0)
        for size in (4, 3):
            for i in range(len(run) - size + 1):
                span = run[i:i + size]
                if span in out or set(span) & _FUNCTION or candidates.is_word(span):
                    continue
                out[span] = None
                if len(out) >= _MAX_FIX:
                    return list(out)
    return list(out)


def _fuzzy(reading: str) -> str:
    """Taiwanese-accent and hearing confusions: zh/z, ch/c, sh/s, and the nasal ending (xin / xing / xi)."""
    r = reading.replace("zh", "z").replace("ch", "c").replace("sh", "s")
    if r.endswith("ng"):
        r = r[:-1]
    if r.endswith("n") and len(r) > 1:
        r = r[:-1]
    return r


def _shared(mine: list[set[str]], theirs: list[set[str]]) -> int:
    """Sounds two spans share in order (longest common subsequence, exact toneless readings: with the fuzzy ones
    靜 / 機 / 今 all matched, and 平靜天花板 became 平主機版)."""
    a, b = mine, theirs
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x & y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _supported(text: str, i: int, size: int, term: str, words) -> bool:
    """Another screen name (2+ characters, not this one) is in the same dictation, outside the span: the user is
    talking about what is on screen (一瓶的平丁天花板 -> 一萍), not just saying a word (我買了一瓶水 stays)."""
    rest = text[:i] + "　" + text[i + size:]
    # a time or a number on screen is not a name (下午三點 made 跟客戶開會 into 跟客戶凱慧)
    return any(len(w) >= 2 and w != term and w in rest and not (set(w) & _NUMERALS) for w in words)


_NUMERALS = set("零〇一二兩三四五六七八九十百千萬億點半0123456789")


def _holds_word(span: str, name: str) -> bool:
    """span contains a common word of two characters that the name does not (上面爆紅 against 上爆紅: the extra
    character belongs to 上面, it was said)."""
    return any(candidates.frequency(span[k:k + 2]) >= candidates.COMMON and span[k:k + 2] not in name
               for k in range(len(span) - 1))


def _splits_word(text: str, i: int, size: int) -> bool:
    """The span's first or last character makes a common word with its neighbour outside the span."""
    for pair in (text[i - 1:i + 1] if i > 0 else "", text[i + size - 1:i + size + 1]):
        if len(pair) == 2 and _RUN.fullmatch(pair) and candidates.frequency(pair) >= candidates.COMMON:
            return True
    return False


def _plain_words(span: str) -> bool:
    """span splits into common words of two or more characters: ordinary speech, not a misheard name."""
    segs = _segments(span)
    return all(len(w) >= 2 and candidates.frequency(w) >= candidates.COMMON for w in segs)


def _distance(mine: list[set[str]], theirs: list[set[str]], close_from: int = 4) -> tuple | None:
    """How far a span is from a term (see _close): None when too far, else (different, close) characters."""
    close = other = 0
    for x, y in zip(mine, theirs):
        if x & y:
            continue
        if {_fuzzy(a) for a in x} & {_fuzzy(b) for b in y}:
            close += 1
        else:
            other += 1
    return (other, close) if _close(mine, theirs, close_from) else None


def _close(mine: list[set[str]], theirs: list[set[str]], close_from: int = 4) -> bool:
    """Every character sounds the same (toneless). In a name of 4+ characters one may be only close (文西南二殿 for
    文心南二店), in one of 5+ one may differ entirely (清景澤波物 for 輕井澤鍋物): long spans rarely match by chance,
    short ones would."""
    close = other = 0
    edge = False
    for k, (x, y) in enumerate(zip(mine, theirs)):
        if x & y:
            continue
        if {_fuzzy(a) for a in x} & {_fuzzy(b) for b in y}:
            close += 1
        else:
            other += 1
        edge = edge or k in (0, len(mine) - 1)
    if close + other == 0:
        return True
    if close + other > 1 or (other and edge):
        return False                 # 文心南二店好 against the window 文心南二店向: a different last character only
        # means the window ran on into the next word
    return len(mine) >= (5 if other else close_from)


def fix(text: str, words: list[str], loose: list[str] = (), common_wins: bool = True) -> tuple[str, int]:
    """Replace a span of text that sounds like a term (every reading of each character, toneless; see _close) but
    is written differently, unless that span is a common word or is on screen itself. words: the picked terms
    (2-6 characters); loose: fix_candidates (2-4). Returns the text and how many spans were replaced."""
    on_screen = set(words) | set(loose)          # both spellings on screen: neither is a mistake
    index: dict[tuple[int, str], list[tuple[str, list[set[str]]]]] = {}
    for term in dict.fromkeys(list(words) + list(loose)):
        if 2 <= len(term) <= 6 and _RUN.fullmatch(term):
            readings = _readings(term)
            for first in {_fuzzy(r) for r in readings[0]}:
                index.setdefault((len(term), first), []).append((term, readings))
    if not index:
        return text, 0
    names = [(t, _readings(t)) for t in dict.fromkeys(words) if 3 <= len(t) <= 6 and _RUN.fullmatch(t)]
    names_set = {t for t in words if not candidates.is_word(t) or candidates.frequency(t) < candidates.COMMON}
    given = given_names(list(words))
    for g in given:
        if g not in on_screen:
            on_screen.add(g)
            readings = _readings(g)
            for first in {_fuzzy(r) for r in readings[0]}:
                index.setdefault((2, first), []).append((g, readings))
    # every match first, then the longest and closest ones without overlap: a short match taken left to right
    # hid a longer one (一平丁天花板 became 一萍丁天花板) or ate a neighbour (清景澤郭文 for 輕井澤鍋物 left 文 out)
    found, unsure = [], []
    for i in range(len(text)):
        for size in (7, 6, 5, 4, 3, 2):
            span = text[i:i + size]
            if len(span) < size or span in on_screen or not _RUN.fullmatch(span):
                continue
            if size >= 4 and set(span) & _GRAMMAR:
                continue        # a phrase with 的 / 是 … is a sentence, not a misheard name
            if size <= 3 and _splits_word(text, i, size):
                continue        # 下雨威力: 雨威 is half of 雨 and half of 威力, not the name 雨薇 (a long match
                # is evidence enough: 你說清景澤波物 has 說清 but is 輕井澤鍋物)
            mine = _readings(span)
            freq = candidates.frequency(span)
            # any common word heard (一瓶, even 開會 at 495) gives way to a name on screen that sounds exactly the
            # same (一萍, 凱慧) when another screen name is in the same dictation: the user is reading from the
            # screen (the user's rule, 2026-10-14). 城市 never becomes 程式: only names, not dictionary words, win.
            common = freq >= candidates.COMMON
            firsts = {_fuzzy(r) for r in mine[0]}
            hits = {}
            for first in firsts:
                for t, r in index.get((size, first), []):
                    # a name on screen of three characters may have one only close in sound, like a longer one:
                    # 鄭冠維 (shu / su) -> 曾冠維, 林又成 (cheng / chen) -> 林又宸 (the user's rule, 2026-10-14);
                    # loose OCR windows need four characters
                    d = _distance(mine, r, 3 if t in names_set else 4)
                    # a common word heard (一瓶) gives way only to a name on screen that sounds exactly the same
                    # (一萍): the user's rule, 2026-10-14, 「畫面上有的字要優先套用」
                    if d is not None and (not common or (common_wins and d == (0, 0) and t in names_set
                                                         and _supported(text, i, size, t, words))):
                        hits[t] = d
            if hits:
                best = min(hits.values())
                tops = [t for t, d in hits.items() if d == best]
                if len(tops) == 1:
                    found.append((0, -size, best, i, size, tops[0]))
                else:
                    unsure.append((i, size))     # 張宇薇 is 張雨薇 or 章宇威: nothing inside it changes either
                continue
            # 2 個以上的跡象 (the user's rule, 2026-10-13): a span within one character of a name's length that shares
            # all of its sounds but one (at least two), in order, is that name whatever the rest
            # was heard as (清引影澤 -> 輕井澤). Only against names (the picked terms and recent pieces), never the
            # loose OCR windows (單一中文字 became 單澤鍋物文字 against 澤鍋物文); last in priority; never on a span with
            # a grammar character or one that is plain common words (請你選擇, 情況選擇 stay).
            if common or size < 3 or set(span) & _GRAMMAR or _plain_words(span):
                continue
            evident = {}
            for t, r in names:
                if abs(len(t) - size) <= 1:
                    # same length: the shared sounds in the same places (上面爆 shares 上 and 爆 with 上爆紅, but
                    # shifted); a character more or less: in order
                    shared = sum(1 for x, y in zip(mine, r) if x & y) if len(t) == size else _shared(mine, r)
                    # all but one (2 of 4 matched 實際設定 to 檢視地圖); a longer or shorter span also needs its first
                    # and last characters to match the name's (頭尾對上: 清引影澤 for 輕井澤)
                    # the first and last sounds match: also at the same length the different character is inside
                    # (清引澤 for 輕井澤), never at an edge (明天見 is not 明天的)
                    ends = bool(mine[0] & r[0] and mine[-1] & r[-1])
                    if _holds_word(span, t) or any(g in span for g in given):
                        continue     # 情境裡 holds 情境, 問雨薇 holds 雨薇: what was heard is right
                    if shared >= max(2, len(t) - 1) and ends:
                        evident[t] = (len(t) - shared, abs(len(t) - size))
            if evident:
                best = min(evident.values())
                tops = [t for t, d in evident.items() if d == best]
                if len(tops) == 1:
                    found.append((1, -size, best, i, size, tops[0]))
    taken = [False] * len(text)
    # a whole name already written as on screen stays (一平丁天花板 keeps 平丁天花板), unless a match holds all of it
    # (輕井澤國物 -> 輕井澤鍋物); not the loose windows: 文心南二 would block 文心南二殿 -> 文心南二店
    kept = []
    for term in set(words):
        if len(term) >= 3:
            start = text.find(term)
            while start >= 0:
                kept.append((start, start + len(term)))
                start = text.find(term, start + 1)
    for i, size in unsure:
        taken[i:i + size] = [True] * size
    chosen = []
    for _, _, _, i, size, term in sorted(found):
        # a match may overlap a name already written as on screen when it leaves those characters as they are
        # (嚴肅紀東海總 -> 鹽酥紀東海總 overlaps 東海總店 but keeps 東海總; 一平 -> 一萍 would change 平丁天花板)
        def changes_kept(a, b):
            if len(term) != size:
                return True
            return any(text[k] != term[k - i] for k in range(max(a, i), min(b, i + size)))
        if any(a < i + size and i < b and not (i <= a and b <= i + size) and changes_kept(a, b) for a, b in kept):
            continue
        if not any(taken[i:i + size]):
            taken[i:i + size] = [True] * size
            chosen.append((i, size, term))
    n = len(chosen)
    for i, size, term in sorted(chosen, reverse=True):
        text = text[:i] + term + text[i + size:]
    return text, n
