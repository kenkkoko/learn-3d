"""計分與排程（SPEC §4.6）。純函式：不碰檔案、不呼叫 AI。

所有接受 progress dict 的函式都先深拷貝，回傳新的 dict，絕不就地修改輸入。
進度檔欄位沿用 v1 的 camelCase（見 docs/interfaces.md §6）；舊檔缺欄位時以預設值處理。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from learn3d.schemas import Confidence, Domain, NodeStatus, QuestionType, Result

__all__ = [
    "GradeOutcome",
    "HISTORY_KEEP",
    "MAX_SCORE",
    "QTYPE_ROTATION",
    "REVIEW_INTERVALS",
    "apply_grade",
    "apply_review",
    "choose_qtype",
    "compute_status",
    "due_misconceptions",
    "pick_next_node",
    "record_misconception",
    "score_delta",
    "today",
]

TW_TZ = timezone(timedelta(hours=8))
MIN_SCORE = 0
MAX_SCORE = 6
HISTORY_KEEP = 6
QTYPE_ROTATION: tuple[QuestionType, ...] = ("predict", "debug", "explain", "produce")
REVIEW_INTERVALS: tuple[int, ...] = (1, 3, 7, 14)

# 把握度 × 結果 → 分數變化（SPEC §4.6）
_DELTA: dict[str, dict[str, int]] = {
    "high": {"ok": 2, "partial": 0, "wrong": -2},
    "mid": {"ok": 1, "partial": 0, "wrong": -1},
    "low": {"ok": 0, "partial": 0, "wrong": -1},
}


@dataclass
class GradeOutcome:
    delta: int  # 實際套用到 score 的變化（夾在 0–6 之後）
    score: int
    status: NodeStatus
    danger: bool  # 🟢 有把握卻答錯


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def today() -> date:
    """台灣時間（UTC+8）的今天。"""
    return datetime.now(TW_TZ).date()


def _clamp(score: int) -> int:
    return max(MIN_SCORE, min(MAX_SCORE, score))


def _parse_day(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _find_node(progress: dict, node_id: str) -> dict:
    for n in progress.get("nodes") or []:
        if n.get("id") == node_id:
            return n
    raise KeyError(f"找不到節點：{node_id}")


def _find_mis(progress: dict, mis_id: str) -> dict:
    for m in progress.get("misconceptions") or []:
        if m.get("id") == mis_id:
            return m
    raise KeyError(f"找不到迷思：{mis_id}")


def score_delta(confidence: Confidence, result: Result) -> int:
    """把握度矩陣的分數變化（未夾值）；unjudgeable → 0。"""
    return _DELTA.get(confidence, {}).get(result, 0)


def compute_status(node: dict) -> NodeStatus:
    """依 score 與 history 算節點狀態。

    - 從沒答過（history 空且原本是 unseen）→ unseen
    - score ≤ 1 → weak；2–4 → shaky
    - score ≥ 5 且最近 3 筆 history 中 result=ok 的至少涵蓋 2 種題型 → mastered，否則 shaky
    """
    history = node.get("history") or []
    score = int(node.get("score") or 0)
    if not history and node.get("status", "unseen") == "unseen":
        return "unseen"
    if score <= 1:
        return "weak"
    if score <= 4:
        return "shaky"
    ok_types = {h.get("type") for h in history[-3:] if h.get("result") == "ok"}
    return "mastered" if len(ok_types) >= 2 else "shaky"


# ---------------------------------------------------------------------------
# 出題
# ---------------------------------------------------------------------------


def choose_qtype(history: list[dict], domain: Domain) -> QuestionType:
    """依最近使用的題型輪替 predict → debug → explain → produce，挑最久沒用的。

    - 沒用過的題型最優先；多個沒用過時，從「最近一次題型的下一個」開始照輪替順序挑。
    - 都用過時挑最後一次出現位置最早的。
    - domain 目前不影響選擇（四種題型對所有領域都適用；非程式領域的 debug = 找錯／改錯）。
    """
    del domain  # 保留參數以符合介面；之後若要依領域調整再使用
    last_seen: dict[str, int] = {}
    for i, h in enumerate(history or []):
        t = h.get("type")
        if t in QTYPE_ROTATION:
            last_seen[t] = i
    if not last_seen:
        return QTYPE_ROTATION[0]
    most_recent = max(last_seen, key=lambda t: last_seen[t])
    start = (QTYPE_ROTATION.index(most_recent) + 1) % len(QTYPE_ROTATION)
    order = QTYPE_ROTATION[start:] + QTYPE_ROTATION[:start]
    return min(order, key=lambda t: (last_seen.get(t, -1), order.index(t)))


def pick_next_node(progress: dict) -> str:
    """挑下一個要學的節點 id。

    1. current 存在且不是 mastered → current
    2. 否則：先修全部 ≥ shaky（shaky / mastered）、自己不是 mastered 的節點中，層數最低者（同層照原順序）
       （不存在的先修 id 視為已滿足）
    3. 全部 mastered → current
    4. （地圖不合法時的保險）沒有可解鎖的節點 → 層數最低的非 mastered 節點
    """
    nodes = progress.get("nodes") or []
    current = progress.get("current")
    by_id = {n.get("id"): n for n in nodes}

    cur = by_id.get(current)
    if cur is not None and cur.get("status") != "mastered":
        return current

    ok_prereq = {"shaky", "mastered"}
    candidates: list[tuple[int, int, str]] = []
    fallback: list[tuple[int, int, str]] = []
    for idx, n in enumerate(nodes):
        if n.get("status") == "mastered":
            continue
        key = (int(n.get("layer") or 0), idx, n.get("id"))
        fallback.append(key)
        prereqs = n.get("prereqs") or []
        if all(by_id[p].get("status") in ok_prereq for p in prereqs if p in by_id):
            candidates.append(key)

    if candidates:
        return min(candidates)[2]
    if fallback:
        return min(fallback)[2]
    if current:
        return current
    return nodes[0].get("id") if nodes else ""


# ---------------------------------------------------------------------------
# 判定後計分
# ---------------------------------------------------------------------------


def apply_grade(
    progress: dict,
    node_id: str,
    qtype: QuestionType,
    confidence: Confidence,
    result: Result,
    day: date,
) -> tuple[dict, GradeOutcome]:
    """套用一次判定，回傳 (新 progress, GradeOutcome)。

    - 分數依把握度矩陣變化並夾在 0–6；狀態重算
    - 更新 node.lastSeen、history（只留最近 6 筆 {date,type,confidence,result}）
    - 更新 stats：answered +1；ok → correct +1、streak +1；partial / wrong → streak 歸零
    - unjudgeable：不改分、不記 history、不動 stats 與 lastSeen（progress 原樣複製回傳）
    - 找不到節點 → KeyError
    """
    p = copy.deepcopy(progress)
    node = _find_node(p, node_id)
    old_score = int(node.get("score") or 0)

    if result == "unjudgeable":
        status = node.get("status") or "unseen"
        return p, GradeOutcome(delta=0, score=old_score, status=status, danger=False)

    new_score = _clamp(old_score + score_delta(confidence, result))
    history = list(node.get("history") or [])
    history.append({"date": day.isoformat(), "type": qtype, "confidence": confidence, "result": result})
    node["history"] = history[-HISTORY_KEEP:]
    node["score"] = new_score
    node["lastSeen"] = day.isoformat()
    node["status"] = compute_status(node)

    stats = p.setdefault("stats", {})
    stats["answered"] = int(stats.get("answered") or 0) + 1
    stats.setdefault("correct", 0)
    stats.setdefault("streak", 0)
    if result == "ok":
        stats["correct"] = int(stats["correct"] or 0) + 1
        stats["streak"] = int(stats["streak"] or 0) + 1
    else:
        stats["streak"] = 0

    outcome = GradeOutcome(
        delta=new_score - old_score,
        score=new_score,
        status=node["status"],
        danger=(confidence == "high" and result == "wrong"),
    )
    return p, outcome


# ---------------------------------------------------------------------------
# 迷思與間隔複習
# ---------------------------------------------------------------------------


def due_misconceptions(progress: dict, day: date) -> list[dict]:
    """未退役且 nextReview ≤ day 的迷思（深拷貝），依 nextReview、原順序排序。

    nextReview 缺漏或格式錯誤 → 視為到期（寧可多複習也不要永遠漏掉）。
    """
    due: list[tuple[date, int, dict]] = []
    for idx, m in enumerate(progress.get("misconceptions") or []):
        if m.get("retired"):
            continue
        nr = _parse_day(m.get("nextReview")) or date.min
        if nr <= day:
            due.append((nr, idx, m))
    due.sort(key=lambda x: (x[0], x[1]))
    return [copy.deepcopy(m) for _, _, m in due]


_MIS_ID_RE = re.compile(r"^m(\d+)$")


def _norm_label(s: str) -> str:
    return " ".join((s or "").split()).casefold()


def record_misconception(
    progress: dict,
    node_id: str,
    label: str,
    wrong: str,
    right: str,
    scenario: str,
    day: date,
) -> tuple[dict, str]:
    """記錄一個迷思，回傳 (新 progress, 迷思 id)。

    - 同節點且同 label（忽略前後空白、大小寫、連續空白）已存在 → hits+1、interval=1、
      nextReview=day+1、retired=False（重新啟用）、scenario 不重複地加入 scenarios；wrong/right 保留舊的
    - 否則新增 id m<n>（n = 現有 m<數字> 的最大值 + 1；沒有則 m1）
    """
    p = copy.deepcopy(progress)
    mis = p.setdefault("misconceptions", [])
    next_day = (day + timedelta(days=1)).isoformat()
    key = _norm_label(label)
    scenario = (scenario or "").strip()

    for m in mis:
        if m.get("node") == node_id and _norm_label(m.get("label", "")) == key:
            m["hits"] = int(m.get("hits") or 0) + 1
            m["interval"] = 1
            m["nextReview"] = next_day
            m["retired"] = False
            scenarios = list(m.get("scenarios") or [])
            if scenario and scenario not in scenarios:
                scenarios.append(scenario)
            m["scenarios"] = scenarios
            return p, m["id"]

    nums = [int(mm.group(1)) for m in mis if (mm := _MIS_ID_RE.match(str(m.get("id", ""))))]
    new_id = f"m{max(nums, default=0) + 1}"
    mis.append(
        {
            "id": new_id,
            "node": node_id,
            "label": label,
            "wrong": wrong,
            "right": right,
            "scenarios": [scenario] if scenario else [],
            "created": day.isoformat(),
            "interval": 1,
            "nextReview": next_day,
            "hits": 1,
            "retired": False,
        }
    )
    return p, new_id


def _next_interval(current: int) -> int:
    for iv in REVIEW_INTERVALS:
        if iv > current:
            return iv
    return REVIEW_INTERVALS[-1]


def apply_review(progress: dict, mis_id: str, result: Result, day: date) -> dict:
    """套用一次迷思複習的結果，回傳新 progress。

    - ok：interval 1 → 3 → 7 → 14 前進並推 nextReview = day + 新 interval；
          在 interval ≥ 14 那次答對 → retired=True（interval、nextReview 不再變動）
    - partial / wrong：interval=1、nextReview=day+1，該迷思所屬節點 score −1（夾 0）並重算 status
      （節點已不存在就只更新迷思）
    - unjudgeable：不變
    - 找不到迷思 → KeyError
    """
    p = copy.deepcopy(progress)
    m = _find_mis(p, mis_id)
    if result == "unjudgeable":
        return p

    interval = int(m.get("interval") or 1)
    if result == "ok":
        if interval >= REVIEW_INTERVALS[-1]:
            m["retired"] = True
        else:
            new_iv = _next_interval(interval)
            m["interval"] = new_iv
            m["nextReview"] = (day + timedelta(days=new_iv)).isoformat()
        return p

    m["interval"] = 1
    m["nextReview"] = (day + timedelta(days=1)).isoformat()
    try:
        node = _find_node(p, m.get("node"))
    except KeyError:
        return p
    node["score"] = _clamp(int(node.get("score") or 0) - 1)
    node["status"] = compute_status(node)
    return p
