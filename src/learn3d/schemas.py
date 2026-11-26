"""所有 LLM 輸出與 HTTP API 的資料模型（介面合約，各模組共用）。

- LLM 輸出模型：Intake、LearningMap、Lesson、Verdict —— 必須通過 Pydantic 驗證才能使用。
- API 模型：*Request / *Response —— 瀏覽器與本機橋接之間的 JSON。
- 進度檔（progress.json）維持既有 v1 的 dict 格式，由 store.py 讀寫，不在此定義成嚴格模型，
  以免與 /learn-coach skill 互相破壞。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["code", "language", "concept", "skill"]
Runner = Literal["sql", "python", "js"]
QuestionType = Literal["predict", "debug", "explain", "produce"]
Confidence = Literal["high", "mid", "low"]  # 🟢 🟡 🔴
Result = Literal["ok", "partial", "wrong", "unjudgeable"]
NodeStatus = Literal["unseen", "weak", "shaky", "mastered"]
ProviderId = Literal["claude", "codex", "ollama", "fake"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- LLM 輸出：Intake ----------
class IntakeOption(_Strict):
    label: str = Field(min_length=1, max_length=30)
    description: str = Field(max_length=80)


class IntakeQuestion(_Strict):
    id: Literal["goal", "level", "context", "minutes"]
    question: str = Field(min_length=1, max_length=60)
    options: list[IntakeOption] = Field(min_length=2, max_length=4)


class Intake(_Strict):
    topic_slug: str = Field(pattern=r"^[a-z0-9-]{2,30}$")
    domain: Domain
    questions: list[IntakeQuestion] = Field(min_length=3, max_length=4)


# ---------- LLM 輸出：學習路徑 ----------
class MapNode(_Strict):
    id: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    name: str = Field(min_length=1, max_length=30)
    layer: int = Field(ge=1, le=7)
    prereqs: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=80)
    pitfalls: list[str] = Field(min_length=2, max_length=3)
    status: NodeStatus = "unseen"


class LearningMap(_Strict):
    title: str = Field(min_length=1, max_length=40)
    domain: Domain
    runner: Runner | None = None
    env_seed: str | None = Field(default=None, max_length=20000)  # runner=sql 時的 practice.sql
    env_note: str | None = Field(default=None, max_length=600)
    nodes: list[MapNode] = Field(min_length=15, max_length=25)
    first: str


# ---------- LLM 輸出：一題 ----------
class Trap(_Strict):
    when: str = Field(min_length=1, max_length=120)  # 答案出現的錯誤想法
    label: str = Field(min_length=1, max_length=40)  # 迷思名稱


class Rubric(_Strict):
    answer: str = Field(min_length=1, max_length=800)
    accept: str = Field(max_length=300)
    must_have: list[str] = Field(min_length=1, max_length=3)
    traps: list[Trap] = Field(default_factory=list, max_length=3)


class TableVisual(_Strict):
    kind: Literal["table"]
    title: str = Field(max_length=40)
    columns: list[str] = Field(min_length=1, max_length=8)
    rows: list[list[str]] = Field(max_length=12)


class DiagramVisual(_Strict):
    kind: Literal["diagram"]
    title: str = Field(max_length=40)
    mermaid: str = Field(min_length=1, max_length=800)


class ChartVisual(_Strict):
    kind: Literal["chart"]
    title: str = Field(max_length=40)
    chart: Literal["bar", "line"]
    labels: list[str] = Field(max_length=12)
    values: list[float] = Field(max_length=12)


Visual = Annotated[TableVisual | DiagramVisual | ChartVisual, Field(discriminator="kind")]


class Lesson(_Strict):
    lecture: str | None = Field(default=None, max_length=600)
    example: str | None = Field(default=None, max_length=400)
    example_explain: list[str] = Field(default_factory=list, max_length=6)
    type: QuestionType
    question: str = Field(min_length=1, max_length=400)
    hint: str | None = Field(default=None, max_length=120)
    show_tables: list[str] = Field(default_factory=list, max_length=3)
    visuals: list[Visual] = Field(default_factory=list, max_length=2)
    ref_code: str | None = Field(default=None, max_length=1500)
    rubric: Rubric


# ---------- LLM 輸出：判定 ----------
class Misconception(_Strict):
    wrong: str = Field(min_length=1, max_length=80)
    right: str = Field(min_length=1, max_length=80)


class Verdict(_Strict):
    result: Result
    met: list[int] = Field(default_factory=list)
    trap: str | None = Field(default=None, max_length=40)
    feedback: str = Field(min_length=1, max_length=300)
    citations: list[str] = Field(min_length=1)  # "must_have[0]" / "trap:<label>" / "exec" / "answer"
    misconception: Misconception | None = None


# ---------- API ----------
class AiChoice(_Strict):
    provider: ProviderId
    model: str | None = None
    effort: Effort | None = None


class ProviderInfo(_Strict):
    id: ProviderId
    name: str
    available: bool
    reason: str | None = None
    models: list[str] = Field(default_factory=list)
    efforts: list[Effort] = Field(default_factory=list)


class ProvidersResponse(_Strict):
    providers: list[ProviderInfo]


class TopicSummary(_Strict):
    topic: str
    title: str
    domain: Domain | None = None
    nodes: int
    mastered: int
    due: int
    updated: float  # mtime（秒）


class TopicsResponse(_Strict):
    topics: list[TopicSummary]


class IntakeRequest(_Strict):
    topic: str = Field(min_length=1, max_length=60)
    ai: AiChoice


class IntakeAnswers(_Strict):
    goal: str = Field(max_length=200)
    level: str = Field(max_length=200)
    context: str = Field(max_length=200)
    minutes: str = Field(max_length=60)


class PlanRequest(_Strict):
    topic: str = Field(min_length=1, max_length=60)
    slug: str = Field(pattern=r"^[a-z0-9-]{2,30}$")
    domain: Domain
    answers: IntakeAnswers
    notes: str | None = Field(default=None, max_length=500)
    ai: AiChoice
    mode: Literal["new", "replan_keep", "replan_reset"] = "new"
    confirm: bool = False  # 覆寫既有主題時必須為 true


class LessonRequest(_Strict):
    node: str | None = None  # 指定節點（跳轉）；None = 依規則挑
    review: bool = False  # True = 出一題到期迷思的複習題
    ai: AiChoice


class PublicLesson(_Strict):
    """送到瀏覽器的一題：不含 rubric（評分標準只留在橋接的進度檔）。"""

    question_id: str
    node: str
    node_name: str
    kind: Literal["new", "review"]
    type: QuestionType
    lecture: str | None = None
    example: str | None = None
    example_explain: list[str] = Field(default_factory=list)
    question: str
    hint: str | None = None
    show_tables: list[str] = Field(default_factory=list)
    visuals: list[Visual] = Field(default_factory=list)
    ref_code: str | None = None
    runner: Runner | None = None
    cached_lecture: bool = False


class ExecResult(_Strict):
    user_output: str = Field(max_length=6000)
    ref_output: str = Field(max_length=6000)
    equal: bool


class GradeRequest(_Strict):
    question_id: str
    confidence: Confidence
    answer: str = Field(min_length=1, max_length=5000)
    exec: ExecResult | None = None
    ai: AiChoice


class NodeState(_Strict):
    id: str
    status: NodeStatus
    score: int


class GradeResponse(_Strict):
    result: Result
    feedback: str
    danger: bool  # 🟢 有把握卻錯
    delta: int
    node: NodeState
    misconception_label: str | None = None
    reference_answer: str
    offtopic: bool = False
    fallback: bool = False  # Schema 全部失敗、回傳預設訊息


class JumpRequest(_Strict):
    node: str


class ConfirmRequest(_Strict):
    confirm: str  # 刪除需輸入主題名稱（topic slug）；重設輸入 "reset"


class ErrorResponse(_Strict):
    error: str
    needs_confirm: bool = False
