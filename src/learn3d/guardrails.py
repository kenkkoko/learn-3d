"""規則層 Guardrails（SPEC §5）。

完全不呼叫任何模型，只用 regex 與詞表：
- mask_pii：email、台灣手機／市話、身分證字號、信用卡號（Luhn）、IPv4、API key 樣式 → [已遮蔽]
- detect_injection：疑似提示注入（中英文；全形、空白、零寬字元正規化後比對）
- wrap_untrusted：把使用者文字包成資料區塊，先移除文字內偽造的區塊標記
- is_offtopic：作答是否明顯與題目無關（寬鬆：寧可漏抓也不誤傷）
- check_topic / check_answer：輸入長度與內容檢查，回傳中文錯誤訊息或 None
- check_citations：判定（Verdict）的來源必須真的存在於該題評分標準

詞表（data/injection_patterns.txt、data/offtopic_topics.txt）在模組載入時讀一次。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from learn3d.schemas import Rubric, Verdict

__all__ = [
    "ANSWER_CLOSE",
    "ANSWER_OPEN",
    "MAX_ANSWER_LEN",
    "MAX_CODE_LEN",
    "MAX_TOPIC_LEN",
    "PII_MASK",
    "check_answer",
    "check_citations",
    "check_topic",
    "detect_injection",
    "is_offtopic",
    "looks_like_code",
    "mask_pii",
    "wrap_untrusted",
]

PII_MASK = "[已遮蔽]"
MAX_TOPIC_LEN = 60
MAX_ANSWER_LEN = 2000
MAX_CODE_LEN = 5000

ANSWER_OPEN = "<<<作答開始>>>"
ANSWER_CLOSE = "<<<作答結束>>>"

_DATA_DIR = Path(__file__).resolve().parent / "data"

# ---------------------------------------------------------------------------
# 正規化工具
# ---------------------------------------------------------------------------

# 零寬字元、BOM、軟連字號等「看不見」的字元：攻擊者常用來拆開關鍵字
_INVISIBLE_RE = re.compile("[­᠎​-‏‪-‮⁠-⁤﻿]")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """NFKC（全形→半形）+ 移除零寬字元 + 小寫。"""
    return _INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def _squash(text: str) -> str:
    """正規化並去掉所有空白。"""
    return _WS_RE.sub("", _normalize(text))


def _read_patterns(filename: str) -> tuple[re.Pattern[str], ...]:
    text = (_DATA_DIR / filename).read_text(encoding="utf-8")
    pats: list[re.Pattern[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            pats.append(re.compile(line, re.IGNORECASE))
    return tuple(pats)


INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = _read_patterns("injection_patterns.txt")
OFFTOPIC_PATTERNS: tuple[re.Pattern[str], ...] = _read_patterns("offtopic_topics.txt")

# ---------------------------------------------------------------------------
# 個資遮蔽
# ---------------------------------------------------------------------------

# 全形 ASCII（！～）→ 半形、全形空白 → 半形空白。一對一字元轉換，長度不變，
# 所以在轉換後的字串找到的位置可以直接套回原文，未遮蔽的部分保持原樣（不會把「，」改成「,」）。
_HALFWIDTH = {cp: cp - 0xFEE0 for cp in range(0xFF01, 0xFF5F)}
_HALFWIDTH[0x3000] = 0x20

_SEP = r"[ \-‐‑–—]?"
_NOT_ALNUM_BEFORE = r"(?<![A-Za-z0-9])"
_NOT_ALNUM_AFTER = r"(?![A-Za-z0-9])"

# 依序套用；先抓長的、特徵明確的（API key、email），再抓數字類。
_PII_PATTERNS: tuple[re.Pattern[str], ...] = (
    # API key / token 樣式
    re.compile(r"(?<![A-Za-z0-9_\-])sk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(rf"{_NOT_ALNUM_BEFORE}(?:AKIA|ASIA)[0-9A-Z]{{16}}{_NOT_ALNUM_AFTER}"),
    re.compile(r"(?<![A-Za-z0-9])xox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9_\-])AIza[0-9A-Za-z_\-]{35}"),
    # Email
    re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"),
    # 身分證字號 / 新式居留證：A123456789、A812345678
    re.compile(rf"{_NOT_ALNUM_BEFORE}[A-Za-z][1289]\d{{8}}{_NOT_ALNUM_AFTER}"),
    # 手機：0912-345-678、0912 345 678、0912345678、+886 912 345 678、+886-912345678
    re.compile(rf"(?<![\d+])(?:\+?886{_SEP}|0)9\d{{2}}{_SEP}\d{{3}}{_SEP}\d{{3}}(?!\d)"),
    # 市話：(02)2345-6789、(037)123-4567、(02) 2345 6789
    re.compile(r"\(0\d{1,3}\)\s?\d{3,4}[\s\-]?\d{4}(?!\d)"),
    # 市話：02-2345-6789、037-123456、+886-2-2345-6789（區碼一定以 0 開頭並帶連字號，避免誤遮日期）
    re.compile(r"(?<![\d\-])(?:\+886-?|0)[1-8]\d{0,2}-\d{3,4}-?\d{3,4}(?![\d\-])"),
)

# 信用卡：連續 13–19 位數字，或 4-4-4-x（含 4-4-4-4）、4-6-5（AMEX）分組；且必須通過 Luhn 檢查。
# 日期（2026-11-26）、數量（1000）等分組長度不符，不會被抓。
_CARD_RE = re.compile(
    r"(?<![\d.\-])(?:\d{13,19}|\d{4}([ \-])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ \-])\d{6}\2\d{5})(?![\d\-]|\.\d)"
)

# IPv4：每段 0–255，前後不能接數字或「.數字」（避免吃到版本號的一部分）
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"(?<![\d.])(?:{_OCTET}\.){{3}}{_OCTET}(?!\.?\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _find_spans(view: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for p in _PII_PATTERNS:
        spans.extend(m.span() for m in p.finditer(view))
    for m in _CARD_RE.finditer(view):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            spans.append(m.span())
    spans.extend(m.span() for m in _IPV4_RE.finditer(view))
    return spans


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def mask_pii(text: str) -> str:
    """把 email、手機、市話、身分證字號、信用卡號、IPv4、API key 換成 [已遮蔽]。

    全形數字也抓得到；沒被遮蔽的文字一字不改（包含全形標點）。
    SQL 裡的普通數字（qty 1000、日期 2026-11-26、金額 12.5）不會被遮。
    """
    if not text:
        return text
    view = text.translate(_HALFWIDTH)
    spans = _merge(_find_spans(view))
    if not spans:
        return text
    out: list[str] = []
    pos = 0
    for s, e in spans:
        out.append(text[pos:s])
        out.append(PII_MASK)
        pos = e
    out.append(text[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# 提示注入
# ---------------------------------------------------------------------------


def detect_injection(text: str) -> bool:
    """疑似提示注入：同時比對正規化文字與去空白版本，任一命中即為 True。

    命中不代表拒絕作答：作答仍照常判定，只是以資料區塊包起來（分數由程式算，注入無法改分）。
    主題名稱命中則由 check_topic 拒絕。
    """
    if not text:
        return False
    norm = _normalize(text)
    squashed = _WS_RE.sub("", norm)
    return any(p.search(norm) or p.search(squashed) for p in INJECTION_PATTERNS)


# ---------------------------------------------------------------------------
# 資料區塊
# ---------------------------------------------------------------------------

# 偽造的區塊標記：容許全形角括號、空白、零寬字元穿插，例如「＜＜＜作答 結束＞＞＞」
_GAP = "[\\s­​-‏⁠﻿]*"
_LT = "[<＜‹❮〈《]"
_GT = "[>＞›❯〉》]"
_FAKE_MARKER_RE = re.compile(
    _LT + _GAP + _LT + _GAP + _LT + _GAP + "作" + _GAP + "答" + _GAP + "(?:開" + _GAP + "始|結" + _GAP + "束)"
    + _GAP + _GT + _GAP + _GT + _GAP + _GT
)


def wrap_untrusted(text: str) -> str:
    """把使用者文字包成資料區塊。

    先反覆移除文字內偽造的「<<<作答開始>>>／<<<作答結束>>>」（含全形、空白、零寬字元變體），
    直到沒有為止（避免「<<<作答<<<作答結束>>>結束>>>」移除一層後又拼出新標記）。
    其他內容（包含 Python 的 >>> 提示字元、SQL 的 <、>）保持原樣。
    """
    body = text or ""
    while True:
        cleaned = _FAKE_MARKER_RE.sub("", body)
        if cleaned == body:
            break
        body = cleaned
    return f"{ANSWER_OPEN}\n{body}\n{ANSWER_CLOSE}"


# ---------------------------------------------------------------------------
# 程式判斷、離題
# ---------------------------------------------------------------------------

_CODE_HINT_RE = re.compile(
    r"(\bselect\b.+\bfrom\b|\binsert\s+into\b|\bupdate\b.+\bset\b|\bdelete\s+from\b|\bcreate\s+(table|view|index)\b"
    r"|\b(inner|left|right|full|cross)?\s*join\b.+\bon\b|\bgroup\s+by\b|\border\s+by\b|\bwhere\b.+[=<>]"
    r"|\bdef\s+\w+\s*\(|\bclass\s+\w+|\bimport\s+\w+|\bfrom\s+\w+\s+import\b|\bprint\s*\(|^\s*return\b"
    r"|\b(const|let|var|function)\s+\w+|=>|console\.log|\bfor\s*\(|\bwhile\s*\(|\bif\s*\(.*\)\s*\{"
    r"|[{};]\s*$|^\s*(#|//|--)\s)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def looks_like_code(text: str) -> bool:
    """粗略判斷是不是程式碼（SQL／Python／JS），用於放寬長度上限與離題判斷。"""
    if not text:
        return False
    if "```" in text:
        return True
    return bool(_CODE_HINT_RE.search(text))


_CJK_RUN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
_WORD_RE = re.compile(r"[a-z0-9_]{2,}")

# 太常見、不能代表「和題目有關」的詞
_STOP_BIGRAMS = frozenset(
    "什麼 如何 怎麼 怎樣 為什 甚麼 這個 那個 一個 一下 可以 我們 你們 他們 請問 是否 還是 或是 因為 所以 "
    "如果 但是 然後 就是 這樣 那樣 這些 那些 哪些 哪個 多少 幾個 會不 不會 有沒 沒有 知道 覺得 現在 今天 "
    "說明 回答 答案 以下 下列 下面 上面 問題 題目 請你 你的 我的 自己 一些 時候 的話 嗎你 了嗎".split()
)
_STOP_WORDS = frozenset(
    "the a an is are was were be to of and or in on at for with it this that what how why which do does "
    "you your me my i we can will would should please answer question".split()
)


def _tokens(text: str) -> set[str]:
    norm = _normalize(text)
    toks: set[str] = set()
    for w in _WORD_RE.findall(norm):
        if w not in _STOP_WORDS:
            toks.add(w)
    for run in _CJK_RUN_RE.findall(norm):
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if bg not in _STOP_BIGRAMS:
                toks.add(bg)
    return toks


def _hits_offtopic(text: str) -> bool:
    norm = _normalize(text)
    squashed = _WS_RE.sub("", norm)
    return any(p.search(squashed) or p.search(norm) for p in OFFTOPIC_PATTERNS)


def is_offtopic(answer: str, question: str) -> bool:
    """作答是否「明顯」與題目無關（純規則，不呼叫 LLM）。

    寬鬆：只有同時符合以下三點才算離題——
    1. 命中 offtopic_topics.txt 的明顯無關請求（天氣、情書、推薦股票…）；
    2. 不像程式碼；
    3. 和題目沒有任何詞彙重疊（英文詞、中文二字詞，排除常見虛詞）。
    所以「B」「3 列」「不確定，選 B」、SQL 程式、和題目有詞彙重疊的回答都不算離題。
    """
    text = (answer or "").strip()
    if not text:
        return False
    if not _hits_offtopic(text):
        return False
    if looks_like_code(text):
        return False
    if _tokens(text) & _tokens(question or ""):
        return False
    return True


# ---------------------------------------------------------------------------
# 輸入檢查
# ---------------------------------------------------------------------------


def check_topic(topic: str) -> str | None:
    """主題名稱檢查：空白、超過 60 字、注入、明顯不是學習主題 → 回中文錯誤訊息；通過回 None。"""
    t = (topic or "").strip()
    if not t or not any(ch.isalnum() for ch in t):
        return "請輸入想學的主題，例如「SQL」「日文 N4」「樂理」。"
    if len(t) > MAX_TOPIC_LEN:
        return f"主題名稱太長了（{len(t)} 字），請精簡到 {MAX_TOPIC_LEN} 字以內。"
    if detect_injection(t):
        return "主題名稱看起來像是給 AI 的指令，請只輸入想學的主題，例如「Python」「簡報技巧」。"
    if _hits_offtopic(t):
        return "這看起來不是學習主題喔。請輸入想學的東西，例如「SQL」「日文 N4」「樂理」「簡報技巧」。"
    return None


def check_answer(answer: str) -> str | None:
    """作答檢查：空白 → 錯誤；超過 2,000 字（看起來是程式時上限 5,000 字）→ 錯誤；通過回 None。"""
    a = (answer or "").strip()
    if not a:
        return "請先寫下你的答案再送出（不確定也可以寫「不確定」加上你的猜測）。"
    is_code = looks_like_code(a)
    limit = MAX_CODE_LEN if is_code else MAX_ANSWER_LEN
    if len(a) > limit:
        kind = "程式" if is_code else "作答"
        return f"{kind}太長了（{len(a)} 字），上限 {limit} 字，請精簡後再送出。"
    return None


# ---------------------------------------------------------------------------
# 判定來源檢查
# ---------------------------------------------------------------------------

_MUST_HAVE_RE = re.compile(r"^must_have\[(\d+)\]$")


def _label_key(s: str) -> str:
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", s)).strip().casefold()


def check_citations(v: Verdict, rubric: Rubric, has_exec: bool) -> str | None:
    """判定的來源必須真的存在於該題評分標準（SPEC §4.5、§5 輸出端 2）。

    - citations 每一項只能是：must_have[i]（i 在範圍內）、trap:<rubric 裡存在的 label>、answer、
      exec（has_exec 為真才行）
    - met 索引不可越界
    - v.trap 若有值，須是 rubric 的 label，或 v.misconception 不為 None（新發現的迷思）
    不合格回錯誤字串（給 call_structured 附在重試提示裡），合格回 None。
    """
    errors: list[str] = []
    n = len(rubric.must_have)
    labels = {_label_key(t.label) for t in rubric.traps}

    if not v.citations:
        errors.append("citations 不可為空")
    for raw in v.citations:
        c = (raw or "").strip()
        m = _MUST_HAVE_RE.match(c)
        if m:
            i = int(m.group(1))
            if i >= n:
                errors.append(f"citations「{c}」越界：must_have 只有 {n} 項（索引 0–{n - 1}）")
            continue
        if c.startswith("trap:"):
            label = c[len("trap:") :]
            if _label_key(label) not in labels:
                errors.append(f"citations「{c}」指向不存在的 trap label")
            continue
        if c == "answer":
            continue
        if c == "exec":
            if not has_exec:
                errors.append("citations 含「exec」但這題沒有執行結果")
            continue
        errors.append(f"citations「{c}」格式不符，只能是 must_have[i]、trap:<label>、answer、exec")

    for i in v.met:
        if i < 0 or i >= n:
            errors.append(f"met 索引 {i} 越界：must_have 只有 {n} 項（索引 0–{n - 1}）")

    if v.trap is not None and v.trap.strip():
        if _label_key(v.trap) not in labels and v.misconception is None:
            errors.append(f"trap「{v.trap}」不是評分標準裡的 label；若是新迷思必須同時填 misconception")

    return "；".join(errors) if errors else None
