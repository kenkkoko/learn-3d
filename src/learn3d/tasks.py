"""LLM 任務：intake / plan / lesson / grade。

都只回傳通過驗證的模型或 None（全部重試失敗），不寫任何檔案、不更新進度。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import prompts
from .providers.base import LLMProvider
from .schemas import (
    AiChoice,
    Domain,
    ExecResult,
    Intake,
    LearningMap,
    Lesson,
    PlanRequest,
    QuestionType,
    Rubric,
    Runner,
    Verdict,
)
from .structured import call_structured

# SQLite 練習庫檢查的上限
_SQL_MAX_STEPS = 2_000_000  # VM 指令數上限（防止遞迴 CTE 之類的無窮迴圈）


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------


@dataclass
class LessonContext:
    domain: Domain
    runner: Runner | None
    node: dict  # progress 裡的節點 dict（name, summary, pitfalls, history）
    learner: dict  # goal, level, context, minutes, notes
    env_note: str | None
    recent_types: list[QuestionType]
    want_type: QuestionType  # scoring.choose_qtype 決定
    need_lecture: bool  # 有快取或 history 非空時為 False
    review: dict | None = None  # 複習時的迷思 dict（label, wrong, right, scenarios）


@dataclass
class GradeContext:
    question: str
    qtype: QuestionType
    rubric: Rubric
    answer: str  # 已遮蔽個資
    exec: ExecResult | None = None
    learner_notes: str | None = None


# ---------------------------------------------------------------------------
# 結構檢查
# ---------------------------------------------------------------------------


def check_env_seed(sql: str) -> str | None:
    """在 SQLite in-memory 執行練習庫腳本；成功回 None，失敗回錯誤訊息。

    安全：禁止 ATTACH / DETACH（避免在磁碟建立檔案），並限制執行步數。
    """
    conn = sqlite3.connect(":memory:")
    try:
        def _authorizer(action, *_args):
            if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        steps = {"n": 0}

        def _progress():
            steps["n"] += 1000
            return 1 if steps["n"] > _SQL_MAX_STEPS else 0  # 非 0 = 中止

        conn.set_authorizer(_authorizer)
        conn.set_progress_handler(_progress, 1000)
        conn.executescript(sql)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if not tables:
            return "env_seed 沒有建立任何資料表"
        return None
    except sqlite3.Error as e:
        return f"env_seed 無法在 SQLite 執行：{e}"
    finally:
        conn.close()


def validate_map(m: LearningMap) -> str | None:
    """學習路徑的結構檢查；通過回 None，否則回中文錯誤訊息（會附在重試提示中）。"""
    errors: list[str] = []
    ids = [n.id for n in m.nodes]
    seen: set[str] = set()
    dup = sorted({i for i in ids if i in seen or seen.add(i)})
    if dup:
        errors.append(f"節點 id 重複：{', '.join(dup)}")
    by_id = {n.id: n for n in m.nodes}

    for n in m.nodes:
        for p in n.prereqs:
            if p == n.id:
                errors.append(f"{n.id} 的先修不能是自己")
            elif p not in by_id:
                errors.append(f"{n.id} 的先修 {p} 不存在")
            elif by_id[p].layer >= n.layer:
                errors.append(f"{n.id}（第 {n.layer} 層）的先修 {p}（第 {by_id[p].layer} 層）層數必須較低")

    # 循環偵測（DFS 三色標記）；層數規則通常已排除循環，這裡再保險一次
    state: dict[str, int] = {}

    def has_cycle(nid: str) -> bool:
        state[nid] = 1
        for p in by_id[nid].prereqs:
            if p not in by_id:
                continue
            if state.get(p) == 1 or (state.get(p) is None and has_cycle(p)):
                return True
        state[nid] = 2
        return False

    if any(state.get(nid) is None and has_cycle(nid) for nid in by_id):
        errors.append("先修關係有循環")

    if m.first not in by_id:
        errors.append(f"first（{m.first}）不是存在的節點 id")

    if m.runner == "sql":
        if not m.env_seed or not m.env_seed.strip():
            errors.append("runner 為 sql 時必須提供 env_seed（SQLite 練習庫腳本）")
    if m.env_seed and m.env_seed.strip():
        err = check_env_seed(m.env_seed)
        if err:
            errors.append(err)

    if not errors:
        return None
    more = f"（另有 {len(errors) - 8} 個問題）" if len(errors) > 8 else ""
    return "學習路徑結構檢查失敗：" + "；".join(errors[:8]) + more


def _lesson_check(ctx: LessonContext):
    def check(lesson: Lesson) -> str | None:
        errors = []
        if ctx.need_lecture and not (lesson.lecture and lesson.lecture.strip()):
            errors.append("need_lecture 為 true，lecture 不可為空")
        if ctx.runner and lesson.type in ("predict", "debug", "produce") and not (
            lesson.ref_code and lesson.ref_code.strip()
        ):
            errors.append(f"runner 為 {ctx.runner} 且題型為 {lesson.type} 時必須提供 ref_code")
        for v in lesson.visuals:
            if v.kind == "table" and any(len(r) != len(v.columns) for r in v.rows):
                errors.append(f"表格「{v.title}」每列的欄數必須與 columns 相同")
            if v.kind == "chart" and len(v.labels) != len(v.values):
                errors.append(f"圖表「{v.title}」的 labels 與 values 數量必須相同")
        return "；".join(errors) or None

    return check


def _grade_check(ctx: GradeContext):
    def check(v: Verdict) -> str | None:
        n = len(ctx.rubric.must_have)
        bad = [i for i in v.met if i < 0 or i >= n]
        if bad:
            return f"met 索引超出範圍（must_have 只有 0–{n - 1}）：{bad}"
        from . import guardrails  # 延遲匯入：guardrails 由另一個模組負責

        return guardrails.check_citations(v, ctx.rubric, ctx.exec is not None)

    return check


# ---------------------------------------------------------------------------
# 任務
# ---------------------------------------------------------------------------


def repair_intake(data: dict) -> dict:
    """修正 intake 輸出裡「意思對、格式差一點」的地方，不用為了這種小事重試。

    - topic_slug：轉小寫、非英數字換成連字號、截到 30 字；修不好就用 topic
    - questions：id 重複或不認得的丟掉，最多留 4 題；每題選項最多 4 個
    """
    import re as _re
    out = dict(data)
    slug = _re.sub(r"[^a-z0-9]+", "-", str(out.get("topic_slug") or "").lower()).strip("-")[:30].strip("-")
    out["topic_slug"] = slug if len(slug) >= 2 else "topic"
    seen: set[str] = set()
    qs = []
    for q in out.get("questions") or []:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        if qid not in ("goal", "level", "context", "minutes") or qid in seen:
            continue
        seen.add(qid)
        if isinstance(q.get("options"), list):
            q = {**q, "options": [o for o in q["options"] if isinstance(o, dict)][:4]}
        qs.append(q)
    if qs:
        out["questions"] = qs[:4]
    return out


def run_intake(llm: LLMProvider, ai: AiChoice, topic: str) -> Intake | None:
    system, user = prompts.intake_prompt(topic)
    return call_structured(llm, Intake, system, user, task="intake", ai=ai, repair=repair_intake)


def run_plan(llm: LLMProvider, ai: AiChoice, req: PlanRequest) -> LearningMap | None:
    system, user = prompts.plan_prompt(req)
    return call_structured(llm, LearningMap, system, user, task="plan", ai=ai, extra_check=validate_map)


def run_lesson(llm: LLMProvider, ai: AiChoice, ctx: LessonContext) -> Lesson | None:
    system, user = prompts.lesson_prompt(ctx)
    return call_structured(llm, Lesson, system, user, task="lesson", ai=ai, extra_check=_lesson_check(ctx))


def run_grade(llm: LLMProvider, ai: AiChoice, ctx: GradeContext) -> Verdict | None:
    if not _has_content(ctx.answer):
        # 只有標點、問號或空白：沒有可判斷的內容，不呼叫 AI
        return Verdict(result="unjudgeable", met=[], trap=None,
                       feedback="這次的作答沒有可以判斷的內容，可以先看提示，再用自己的話試著回答。",
                       citations=["answer"])
    system, user = prompts.grade_prompt(ctx)
    v = call_structured(llm, Verdict, system, user, task="grade", ai=ai, extra_check=_grade_check(ctx))
    return normalize_verdict(v, ctx) if v is not None else None


def _has_content(text: str) -> bool:
    """作答裡至少要有一個字母、數字或文字（中日韓文字都算）；只有標點符號視為沒有內容。"""
    return any(ch.isalnum() for ch in text)


def _norm_label(s: str) -> str:
    return "".join(s.split()).lower()


def normalize_verdict(v: Verdict, ctx: GradeContext) -> Verdict:
    """用程式把 AI 的判定校正成和評分規則一致（便宜模型常把錯答判成 partial、把不完整判成 ok）。

    規則（優先順序由上而下）：
    1. 程式執行出錯 → wrong。
    2. 命中評分標準裡的 trap → wrong。
    3. 判 ok 但 met 沒涵蓋全部 must_have，或執行結果和參考答案不同 → partial。
    4. 判 partial 但一條 must_have 都沒做到 → wrong。
    unjudgeable 一律保留（不替模型編造判定）。
    """
    if v.result == "unjudgeable":
        return v
    result = v.result
    ex = ctx.exec
    exec_error = ex is not None and not ex.equal and "錯誤：" in ex.user_output
    exec_differs = ex is not None and not ex.equal and ctx.qtype in ("produce", "debug")
    trap_labels = {_norm_label(t.label) for t in ctx.rubric.traps}
    hit_trap = v.trap is not None and _norm_label(v.trap) in trap_labels
    met = set(v.met)
    if exec_error or hit_trap:
        result = "wrong"
    elif result == "ok" and (len(met) < len(ctx.rubric.must_have) or exec_differs):
        result = "partial"
    elif result == "partial" and not met:
        result = "wrong"
    return v if result == v.result else v.model_copy(update={"result": result})


# ---------- 判定後追問 ----------
@dataclass
class FollowupContext:
    question: str
    qtype: str
    reference: str
    result: str
    feedback: str
    answer: str
    history: list
    message: str


def run_followup(llm: LLMProvider, ai: AiChoice, ctx: FollowupContext):
    from .schemas import FollowupReply
    system, user = prompts.followup_prompt(ctx)
    return call_structured(llm, FollowupReply, system, user, task="followup", ai=ai)
