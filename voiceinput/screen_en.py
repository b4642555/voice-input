"""English names on screen (2026-10-13, the user's goal: the screen should fix English as well as Chinese).

X-ASR hears names as ordinary words: cloud code for Claude Code, get hub for GitHub, voice input for VoiceInput,
open ai for OpenAI. After recognition a run of English words is replaced by a name on screen when:
- it spells the same letters (voice input / VoiceInput, chat GPT / ChatGPT, GPT 4o / GPT-4o, Claude code /
  Claude Code); for a name of one plain capitalized word (Anthropic) only when the word heard is not ordinary
  English (the cloud must not become the Claude);
- or it has as many words as the name has parts (Git|Hub, Claude|Code), every part sounds alike (Soundex), one
  part at least is spelled the same, and the letters are mostly the same: cloud code -> Claude Code,
  get hub -> GitHub. A single word is never changed by sound alone unless it is long and one letter off.
An OCR misread on screen (lnstagram for Instagram) never overrides what was heard.

Names: a word with a capital inside (GitHub, iPhone, OpenAI), with a digit (GPT-4o), an acronym (LLM), a
capitalized word that is not ordinary English (Claude, Kubernetes), and runs of 2-3 capitalized words (Claude
Code, Visual Studio Code). Dotted names (file.py, urls) are skipped.
"""
import re

from . import spell

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+[a-z]*|[A-Z]+")
_MAX_NAMES = 60

# Ordinary English: never a name by itself, and never replaced by a one-word name.
COMMON = set("""
a about above after again against all almost also always am an and another any anyone anything are around as ask
at away back bad be because been before being below best better between big both bring build but buy by call came
can cannot car case change check clean clear click close cloud code come could day did different do does doing done
door down draw drive during each early easy else end enough even ever every example eye face fact far fast feel few
file find fine first fix follow for found free from full game gave get give go goes going good got great group had
hand happen hard has have he head hear help her here high him his hold home hot how however i idea if image important
in into is issue it its just keep kind know large last late later learn leave left let life light like line link
list little live long look lot love made main make man many may me mean might mind model more most move much must my
name need never new next nice night no not note nothing now number of off often old on once one only open or order
other our out over own page part people place plan play point power pretty problem pull push put question quick
quite read ready real really right room run said same save say see seem send set she should show side simple since
small so some something sometimes soon sound start state still stop story studio such sure system take talk team
tell test than thank that the their them then there these they thing think this those though through time to today
together too took top tool true try turn two under until up upload us use used very visual wait walk want was watch
water way we well went were what when where which while who why will window with without word work world would write
year yes yet you your app apps apple data email error home icon menu mode note pass run screen search setting
settings share start stop tab text type user value video view voice input output agent phone air hub
""".split())
_FUNCTION_START = set("the a an we i it this that you he she they our my your to of in on at for and or is are".split())


def _distinctive(word: str) -> bool:
    """A single word that is a name by its shape: GitHub, iPhone, GPT-4o, LLM."""
    letters = word.replace("-", "")
    return (any(c.isupper() for c in letters[1:]) or any(c.isdigit() for c in letters)
            or (len(letters) >= 2 and letters.isupper()))


def _add_run(out: dict, caps: list[str]):
    """A whole run of 2-3 capitalized words is a name (Visual Studio Code); its parts are not (Studio Code)."""
    if 2 <= len(caps) <= 3:
        out.setdefault(" ".join(caps), None)


def names(text: str) -> list[str]:
    """English names on screen, from the bottom up: the newest lines of a chat or a page matter most, and a long
    window would fill the list before reaching them."""
    out: dict[str, None] = {}
    for line in reversed(text.splitlines()):
        caps: list[str] = []
        for m in _TOKEN.finditer(line):
            w = m.group(0)
            end = m.end()
            if end + 1 < len(line) and line[end] in "._@/\\" and line[end + 1].isalnum():
                _add_run(out, caps)
                caps = []
                continue            # file.py, a url, an address
            if _distinctive(w) or (w[0].isupper() and w.lower() not in COMMON and len(w) >= 3):
                out.setdefault(w, None)
            if w[0].isupper() and w.lower() not in _FUNCTION_START:
                gap = line[prev_end:m.start()] if caps else ""
                if caps and gap.strip():
                    _add_run(out, caps)
                    caps = []
                caps.append(w)
            else:
                _add_run(out, caps)
                caps = []
            prev_end = end
        _add_run(out, caps)
        if len(out) >= _MAX_NAMES:
            break
    return list(out)[:_MAX_NAMES]


def _key(text: str) -> str:
    return "".join(_WORD.findall(text)).lower()


def _parts(name: str) -> list[str]:
    """Spoken parts: words, and CamelCase pieces (GitHub -> git, hub; OpenAI -> open, ai)."""
    out = []
    for w in _WORD.findall(name):
        out += [p.lower() for p in _CAMEL.findall(w)] or [w.lower()]
    return out


def _soundex(word: str) -> str:
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
             "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}
    word = word.lower()
    if not word:
        return ""
    out, last = word[0], codes.get(word[0], "")
    for c in word[1:]:
        code = codes.get(c, "")
        if code and code != last:
            out += code
        if c not in "hw":
            last = code
    return (out + "000")[:4]


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _ratio(a: str, b: str) -> float:
    return 1 - _distance(a, b) / max(len(a), len(b))


_SOUND_CLASS = str.maketrans("ckqgjxzsfvpbdt", "kkkkkksssffppt")


def _same_sound(a: str, b: str) -> bool:
    """Soundex alike, first letters of the same sound (c / k, s / z)."""
    return _soundex(a)[1:] == _soundex(b)[1:] and a[0].translate(_SOUND_CLASS) == b[0].translate(_SOUND_CLASS)


def _like(key: str, nkey: str) -> bool:
    """A long unknown word that sounds like a long name: covernet -> Kubernetes (real X-ASR output)."""
    return len(nkey) >= 6 and _same_sound(key, nkey) and _ratio(key, nkey) >= 0.5


def _ocr_same(a: str, b: str) -> bool:
    """Equal once OCR's usual confusions are undone (lnstagram / Instagram, G00gle / Google)."""
    def norm(s):
        return s.lower().replace("rn", "m").translate(str.maketrans("l1|0", "iiio"))
    return norm(a) == norm(b)


def _matches(heard: list[str], name: str, strict: bool = False) -> bool:
    """heard (the words of a run) is name misheard. strict (the bundled tech list, no screen evidence): only the same
    letters (get hub = GitHub), a one-word near miss of a long word, or a run whose every word sounds like the name's
    part in the same place; numbers must be equal (Windows 10 is not Windows 11)."""
    key, nkey = "".join(heard).lower(), _key(name)
    if _ocr_same(key, nkey) and key != nkey:
        return False                 # the screen's spelling is the misread one
    if key == nkey:
        if len(heard) == 1 and " " not in name and not _distinctive(name):
            return heard[0].lower() not in COMMON           # anthropic -> Anthropic, but not cloud / code
        return True
    parts = _parts(name)
    if len(heard) != len(parts):
        if len(heard) == 1 and len(parts) == 1 and len(key) >= 6 and not _ordinary(key):
            return _distance(key, nkey) <= 1 and _soundex(key) == _soundex(nkey) or (not strict and _like(key, nkey))
        if strict:
            return False
        if len(heard) >= 2 and len(parts) == 1 and len(nkey) >= 6:
            # one long name heard as several words of plain English: cover nets -> Kubernetes (c / k alike);
            # never a run that holds a name already (Instagram before)
            return all(_ordinary(w) for w in heard) and _same_sound(key, nkey) and _ratio(key, nkey) >= 0.6
        return False
    words = [w.lower() for w in heard]
    if len(words) == 1:
        return len(key) >= 6 and not _ordinary(key) and (
            _distance(key, nkey) <= 1 and _soundex(key) == _soundex(nkey) or (not strict and _like(key, nkey)))
    if not any(w == p for w, p in zip(words, parts)):
        return False
    for w, p in zip(words, parts):
        if strict and w != p and (any(c.isdigit() for c in w + p) or _soundex(w) != _soundex(p)):
            return False             # google dream is not Google Drive; Windows 10 is not Windows 11
        if w != p and _soundex(w) != _soundex(p) and w[:2] != p[:2]:
            return False             # clock / Claude: different Soundex, same start
    return _ratio(key, nkey) >= 0.6


def _ordinary(word: str) -> bool:
    """An ordinary English word (instead, installed): never turned into a name by its sound alone."""
    return word.lower() in COMMON or spell.is_word(word)


def _english_word(tok: str) -> bool:
    return tok.isalpha()


def _inside_english(text, tokens, i, n, run, by_letters: bool) -> bool:
    """Ordinary words in an English sentence (We use voice input every day, I phone you later, a sided code) are
    what was said, not a name: every word of the run is ordinary English and an English word sits right next to
    it. When the letters already spell the name (Claude code), only the short common words count as ordinary."""
    ordinary = (lambda w: w.lower() in COMMON) if by_letters else _ordinary
    if not all(ordinary(t[2]) for t in run):
        return False
    before = tokens[i - 1] if i > 0 else None
    after = tokens[i + n] if i + n < len(tokens) else None
    if before and text[before[1]:run[0][0]] == " " and _english_word(before[2]):
        return True
    return bool(after and text[run[-1][1]:after[0]] == " " and _english_word(after[2]))


def _supported_hits(text: str, tok, screen_names: list[str]) -> list[str]:
    """An ordinary word that sounds like a one-word name on screen (club / Claude) is that name only when another
    screen name is in the same dictation (Anthropic 的 club 很強): the user is talking about what is on screen. The
    same rule as 一瓶 -> 一萍 in Chinese (2026-10-14)."""
    word = tok[2].lower()
    if len(word) < 3 or len(word) > 5 or word in COMMON:     # a short misheard word (club), not installed
        return []                    # cloud / code / open: everyday English stays (cloud 服務 is a cloud service)
    rest = (text[:tok[0]] + " " + text[tok[1]:]).lower()
    present = {other for other in screen_names
               if _key(other) and re.search(r"(?<![a-z0-9])" + re.escape(other.lower()) + r"(?![a-z0-9])", rest)}
    out = []
    for name in screen_names:
        nkey = _key(name)
        if " " in name or not name.isalpha() or len(nkey) < 4 or nkey == word or _ordinary(name):
            continue
        if not present - {name}:
            continue                 # the support must be another name (installed ... Instagram is no support)
        if word[:2] == nkey[:2] and abs(len(word) - len(nkey)) <= 2 and _ratio(word, nkey) >= 0.5:
            out.append(name)         # club / Claude; not chat / ChatGPT, installed / Instagram
    return out


def fix(text: str, screen_names: list[str], rank: dict | None = None, strict: bool = False) -> tuple[str, int]:
    """Replace runs of English words that are a screen name misheard. Returns the text and how many changed. rank (name
    -> number): when two names fit one run, the one with the higher number wins if it is higher than the other's."""
    if not screen_names:
        return text, 0
    tokens = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text)]
    chosen, i = [], 0
    while i < len(tokens):
        done = False
        for n in (4, 3, 2, 1):
            run = tokens[i:i + n]
            if len(run) < n:
                continue
            if any(text[a[1]:b[0]] not in (" ", "-") for a, b in zip(run, run[1:])):
                continue             # a run is words joined by single spaces or hyphens only
            if run[0][0] > 0 and (text[run[0][0] - 1].isalnum()):
                continue
            surface = text[run[0][0]:run[-1][1]]
            heard = [t[2] for t in run]
            hits = [name for name in screen_names if surface != name and _matches(heard, name, strict)
                    and not _inside_english(text, tokens, i, n, run,
                                            _key(surface) == _key(name) and not (" " not in name and _ordinary(name)))]
            hits = list(dict.fromkeys(hits))
            if not hits and n == 1 and not strict:
                hits = _supported_hits(text, run[0], screen_names)
            if rank and len(hits) > 1:
                top = max(rank.get(h, 0) for h in hits)
                best = [h for h in hits if rank.get(h, 0) == top]
                hits = best if len(best) == 1 else hits
            if len(hits) == 1:
                chosen.append((run[0][0], run[-1][1], hits[0]))
                i += n
                done = True
                break
            if surface in screen_names:
                i += n               # already written as on screen
                done = True
                break
        if not done:
            i += 1
    for a, b, name in reversed(chosen):
        text = text[:a] + name + text[b:]
    return text, len(chosen)


# --- names heard as a Chinese transliteration (克勞德 for Claude, 吉特哈布 for GitHub) ---
# Characters used to transliterate foreign names: a span made only of these, with no grammar or common word in it,
# may stand for an English name on screen whose consonants it follows.
_TRANSLIT = set("阿埃艾安奧澳巴拜邦保貝本比彼畢波伯博布查達戴丹道德迪蒂丁東杜多俄厄恩法范菲費芬佛弗福夫蓋甘岡高戈"
                "格哥古圭哈海漢豪赫黑亨胡華惠霍基吉加賈傑金卡凱坎康柯科克肯庫奎拉萊蘭朗勞勒雷黎李里利麗林琳隆盧路"
                "倫羅洛馬麥曼梅門米密敏莫墨穆納奈南內尼妮諾歐帕潘佩彭皮普奇齊喬切欽丘瑞薩塞桑瑟森沙莎尚舍什史斯松"
                "蘇索塔泰坦湯唐特提圖托瓦威韋維溫沃烏伍西希謝辛休許雅亞楊伊易英尤約贊扎詹澤朱茲卓優布谷魯因姆頓爾")
_CJK_RUN = re.compile(r"[㐀-䶿一-鿿]{2,6}")


def _skeleton_en(word: str) -> str:
    """The consonants of an English word by sound class: Claude -> KLT, GitHub -> KTP."""
    w = word.lower()
    for a, b in (("ph", "f"), ("th", "t"), ("sh", "s"), ("ch", "s"), ("ck", "k"), ("qu", "k"), ("x", "ks")):
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w)
    classes = {**dict.fromkeys("gkcqj", "K"), **dict.fromkeys("dt", "T"), **dict.fromkeys("bp", "P"),
               **dict.fromkeys("lr", "L"), **dict.fromkeys("szx", "S"), **dict.fromkeys("fv", "F"),
               "m": "M", "n": "N"}
    out = ""
    for c in w:
        k = classes.get(c, "")
        if k and not out.endswith(k):
            out += k
    return out


def _skeleton_zh(span: str) -> str:
    """The consonants of a transliteration by its pinyin: 克勞德 -> KLT, 吉特哈布 -> KTP."""
    from pypinyin import Style, lazy_pinyin
    initials = {**dict.fromkeys(["g", "k", "j", "q"], "K"), **dict.fromkeys(["d", "t"], "T"),
                **dict.fromkeys(["b", "p"], "P"), **dict.fromkeys(["l", "r"], "L"),
                **dict.fromkeys(["s", "z", "c", "x", "sh", "zh", "ch"], "S"), "f": "F", "m": "M", "n": "N"}
    out = ""
    for syl in lazy_pinyin(span, style=Style.NORMAL):
        ini = next((i for i in ("zh", "ch", "sh") if syl.startswith(i)), syl[:1] if syl[:1] not in "aeiouvwy" else "")
        for k in (initials.get(ini, ""), "N" if syl.endswith("n") else ""):
            if k and not out.endswith(k):
                out += k
    return out


def fix_transliterated(text: str, screen_names: list[str], is_common=lambda w: False) -> tuple[str, int]:
    """Replace a transliteration of an English name on screen by the name (克勞德 -> Claude). Only one-word names of
    4+ letters that are not ordinary English, three consonants at least, and only when exactly one name fits."""
    targets = {}
    for name in screen_names:
        if " " not in name and len(name) >= 4 and name.isalpha() and not _ordinary(name):
            sk = _skeleton_en(name)
            if len(sk) >= 3:
                targets.setdefault(sk, set()).add(name)
    if not targets:
        return text, 0
    out, n, last = [], 0, 0
    for m in _CJK_RUN.finditer(text):
        run, start = m.group(0), m.start()
        i = 0
        while i < len(run):
            done = False
            for size in range(min(6, len(run) - i), 1, -1):
                span = run[i:i + size]
                if not set(span) <= _TRANSLIT or is_common(span):
                    continue
                sk = _skeleton_zh(span)
                names = targets.get(sk) or (
                    {t for k, ts in targets.items() if len(k) >= 5 and _distance(k, sk) <= 1 for t in ts}
                    if len(sk) >= 4 else set())    # 庫伯內提斯 drops the r of Kubernetes, 安索羅皮克 has s for th
                if names and len(names) == 1:
                    before = text[last:start + i]
                    out.append(before)
                    lead = " " if (before or text[:start + i])[-1:].strip() and start + i > 0 else ""
                    after = text[start + i + size:start + i + size + 1]
                    trail = " " if after and _CJK_RUN.match(after + after) else ""
                    out.append(lead + next(iter(names)) + trail)
                    last = start + i + size
                    n += 1
                    i += size
                    done = True
                    break
            if not done:
                i += 1
    out.append(text[last:])
    return "".join(out), n
