"""schemas.py：各模型合法 / 非法範例。"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from learn3d.schemas import (
    ConfirmRequest,
    ExecResult,
    GradeRequest,
    Intake,
    LearningMap,
    Lesson,
    PlanRequest,
    PublicLesson,
    Rubric,
    Verdict,
    Visual,
)

from conftest import lesson_dict, make_lesson, make_map, make_rubric, map_dict, verdict_dict


def intake_dict(**over):
    q = lambda qid: {"id": qid, "question": "你的目標？",
                     "options": [{"label": "面試", "description": "準備面試"},
                                 {"label": "工作", "description": "工作要用"}]}
    d = {"topic_slug": "sql", "domain": "code", "questions": [q("goal"), q("level"), q("context")]}
    d.update(over)
    return d


# ---------- Intake ----------
def test_intake_ok():
    assert Intake.model_validate(intake_dict()).domain == "code"


@pytest.mark.parametrize("slug", ["S", "SQL", "a", "有中文", "x" * 31, "a_b"])
def test_intake_bad_slug(slug):
    with pytest.raises(ValidationError):
        Intake.model_validate(intake_dict(topic_slug=slug))


def test_intake_question_count():
    d = intake_dict()
    d["questions"] = d["questions"][:2]
    with pytest.raises(ValidationError):
        Intake.model_validate(d)


def test_intake_option_label_too_long():
    d = intake_dict()
    d["questions"][0]["options"][0]["label"] = "長" * 41
    with pytest.raises(ValidationError):
        Intake.model_validate(d)


# ---------- LearningMap ----------
def test_map_ok():
    m = make_map()
    assert len(m.nodes) == 15 and m.nodes[0].status == "unseen"


@pytest.mark.parametrize("n", [14, 26])
def test_map_node_count(n):
    with pytest.raises(ValidationError):
        LearningMap.model_validate(map_dict(n=n))


def test_map_node_limits():
    d = map_dict()
    d["nodes"][0]["layer"] = 8
    with pytest.raises(ValidationError):
        LearningMap.model_validate(d)
    d = map_dict()
    d["nodes"][0]["pitfalls"] = ["只有一個"]
    with pytest.raises(ValidationError):
        LearningMap.model_validate(d)
    d = map_dict()
    d["nodes"][0]["summary"] = "長" * 81
    with pytest.raises(ValidationError):
        LearningMap.model_validate(d)


def test_map_extra_field_forbidden():
    d = map_dict()
    d["hacker"] = 1
    with pytest.raises(ValidationError):
        LearningMap.model_validate(d)


# ---------- Rubric / Lesson ----------
def test_rubric_needs_must_have():
    with pytest.raises(ValidationError):
        make_rubric(must_have=[])
    with pytest.raises(ValidationError):
        make_rubric(must_have=["a", "b", "c", "d"])


def test_rubric_answer_length():
    make_rubric(answer="x" * 800)
    with pytest.raises(ValidationError):
        make_rubric(answer="x" * 801)


def test_trap_uses_when_field():
    with pytest.raises(ValidationError):
        make_rubric(traps=[{"if_": "x", "label": "y"}])


def test_lesson_ok_and_limits():
    assert make_lesson().type == "produce"
    with pytest.raises(ValidationError):
        make_lesson(question="x" * 401)
    with pytest.raises(ValidationError):
        make_lesson(lecture="x" * 601)
    with pytest.raises(ValidationError):
        make_lesson(type="choice")
    with pytest.raises(ValidationError):
        make_lesson(show_tables=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        make_lesson(example_explain=["x"] * 7)


def test_lesson_requires_rubric():
    d = lesson_dict()
    del d["rubric"]
    with pytest.raises(ValidationError):
        Lesson.model_validate(d)


# ---------- Visual discriminator ----------
def test_visual_discriminator():
    ta = TypeAdapter(Visual)
    t = ta.validate_python({"kind": "table", "title": "表", "columns": ["a"], "rows": [["1"]]})
    assert type(t).__name__ == "TableVisual"
    d = ta.validate_python({"kind": "diagram", "title": "圖", "mermaid": "graph TD; A-->B"})
    assert type(d).__name__ == "DiagramVisual"
    c = ta.validate_python({"kind": "chart", "title": "圖", "chart": "bar", "labels": ["a"], "values": [1]})
    assert type(c).__name__ == "ChartVisual"


@pytest.mark.parametrize("bad", [
    {"kind": "pie", "title": "x"},
    {"kind": "table", "title": "x", "mermaid": "graph TD"},          # 欄位對錯 kind
    {"kind": "table", "title": "x", "columns": ["c"] * 9, "rows": []},
    {"kind": "table", "title": "x", "columns": ["c"], "rows": [["1"]] * 13},
    {"kind": "chart", "title": "x", "chart": "pie", "labels": [], "values": []},
    {"title": "沒有 kind", "columns": ["a"], "rows": []},
])
def test_visual_invalid(bad):
    with pytest.raises(ValidationError):
        TypeAdapter(Visual).validate_python(bad)


def test_lesson_visuals_max_two():
    v = {"kind": "diagram", "title": "圖", "mermaid": "graph TD; A-->B"}
    make_lesson(visuals=[v, v])
    with pytest.raises(ValidationError):
        make_lesson(visuals=[v, v, v])


# ---------- Verdict ----------
def test_verdict_ok():
    v = Verdict.model_validate(verdict_dict())
    assert v.result == "wrong" and v.misconception.right


def test_verdict_needs_citation():
    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict_dict(citations=[]))
    d = verdict_dict()
    del d["citations"]
    with pytest.raises(ValidationError):
        Verdict.model_validate(d)


def test_verdict_limits():
    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict_dict(result="maybe"))
    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict_dict(feedback="x" * 301))
    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict_dict(misconception={"wrong": "x" * 81, "right": "y"}))


# ---------- API 模型 ----------
def test_public_lesson_has_no_rubric():
    assert "rubric" not in PublicLesson.model_fields


def test_grade_request_answer_limits():
    base = {"question_id": "q1", "confidence": "high", "ai": {"provider": "fake"}}
    GradeRequest.model_validate({**base, "answer": "x" * 5000})
    with pytest.raises(ValidationError):
        GradeRequest.model_validate({**base, "answer": ""})
    with pytest.raises(ValidationError):
        GradeRequest.model_validate({**base, "answer": "x" * 5001})
    with pytest.raises(ValidationError):
        GradeRequest.model_validate({**base, "answer": "a", "confidence": "super"})


def test_exec_result_limits():
    ExecResult(user_output="1", ref_output="1", equal=True)
    with pytest.raises(ValidationError):
        ExecResult(user_output="x" * 6001, ref_output="", equal=False)


def test_plan_request_slug_and_mode():
    base = {"topic": "SQL", "slug": "sql", "domain": "code",
            "answers": {"goal": "", "level": "", "context": "", "minutes": "30"},
            "ai": {"provider": "fake"}}
    p = PlanRequest.model_validate(base)
    assert p.mode == "new" and p.confirm is False
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({**base, "slug": "../etc"})
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({**base, "topic": "x" * 2001})
    with pytest.raises(ValidationError):
        PlanRequest.model_validate({**base, "mode": "overwrite"})


def test_confirm_request():
    assert ConfirmRequest(confirm="reset").confirm == "reset"


def test_provider_error_is_surfaced_not_swallowed():
    """AI 工具本身用不了（沒登入、額度用完）時要把原因交給 API 層，不是回 None 變成看不出原因的「暫時無法產生」。"""
    import pytest
    from learn3d.providers.base import LLMProvider, ProviderError
    from learn3d.schemas import AiChoice, Intake
    from learn3d.structured import call_structured

    class NoLogin(LLMProvider):
        id = "fake"
        name = "x"
        calls = 0

        def chat_json(self, *a, **k):
            NoLogin.calls += 1
            raise ProviderError("Claude Code 的登入過期或尚未登入", retryable=False)

        def info(self, refresh=False):  # pragma: no cover
            raise NotImplementedError

    with pytest.raises(ProviderError, match="登入"):
        call_structured(NoLogin(), Intake, "s", "u", task="intake", ai=AiChoice(provider="fake"))
    assert NoLogin.calls == 1  # 不可重試的錯誤不重試


def test_intake_slightly_too_long_is_trimmed_not_failed():
    """便宜模型多寫幾個字、slug 有大寫或空白：修剪後通過，不要重試三次後失敗。"""
    import json
    from learn3d.providers.fake import FakeLLM
    from learn3d.schemas import AiChoice
    from learn3d.tasks import run_intake

    opt = {"label": "多益" * 30, "description": "說明" * 100}
    raw = {"topic_slug": "English Grammar & Speaking!!", "domain": "language", "questions": [
        {"id": qid, "question": "問題" * 60, "options": [opt, opt, opt, opt, opt]}
        for qid in ("goal", "level", "context", "minutes", "goal")]}
    llm = FakeLLM(scripted=[json.dumps(raw, ensure_ascii=False)])
    it = run_intake(llm, AiChoice(provider="fake"), "英文")
    assert it is not None and len(llm.calls) == 1
    assert it.topic_slug == "english-grammar-speaking"
    assert len(it.questions) == 4 and all(len(q.options) == 4 for q in it.questions)


def test_failure_reason_is_recorded():
    """全部重試都失敗時記下原因，API 會附在「暫時無法產生」後面。"""
    from learn3d.providers.fake import FakeLLM
    from learn3d.schemas import AiChoice
    from learn3d.structured import last_failure
    from learn3d.tasks import run_intake

    llm = FakeLLM(scripted=["不是 JSON"] * 3)
    assert run_intake(llm, AiChoice(provider="fake", model="m1"), "英文") is None
    assert "m1" in last_failure() and last_failure()


def test_claude_login_detection(tmp_path, monkeypatch):
    """沒有登入檔也沒有金鑰環境變數 → 未登入；有 .credentials.json → 已登入。"""
    import sys
    from learn3d.providers import claude
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert claude.logged_in() is False
    (tmp_path / ".credentials.json").write_text("{}")
    assert claude.logged_in() is True
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "x")
    (tmp_path / ".credentials.json").unlink()
    assert claude.logged_in() is True


def test_usage_records_claude_and_reads_codex(tmp_path, monkeypatch):
    """Claude 的 RateLimitEvent 會被記下；Codex 從 sessions 紀錄讀 rate_limits；重置時間過了視為已重置。"""
    import json
    import time
    from learn3d import usage
    monkeypatch.setenv("LEARN3D_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(usage, "_state", None)
    future = int(time.time()) + 3600
    usage.record_claude({"status": "allowed_warning", "rate_limit_type": "five_hour", "utilization": 0.82, "resets_at": future})
    usage.record_claude({"status": "allowed", "rate_limit_type": "seven_day", "utilization": 0.3, "resets_at": int(time.time()) - 5})
    d = tmp_path / "codex" / "sessions" / "2026" / "11" / "27"
    d.mkdir(parents=True)
    line = {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": {"used_percent": 41.0, "window_minutes": 300, "resets_in_seconds": 1200},
        "secondary": {"used_percent": 12.5, "window_minutes": 10080, "resets_in_seconds": 90000}}}}
    (d / "rollout-x.jsonl").write_text("{}\n" + json.dumps(line) + "\n", encoding="utf-8")
    snap = usage.snapshot()["providers"]
    cw = {w["type"]: w for w in snap["claude"]["windows"]}
    assert cw["five_hour"]["used_percent"] == 82.0 and cw["five_hour"]["status"] == "allowed_warning"
    assert cw["seven_day"]["reset"] is True and cw["seven_day"]["used_percent"] == 0.0
    labels = [w["label"] for w in snap["codex"]["windows"]]
    assert labels == ["5 小時", "每週"] and snap["codex"]["windows"][0]["used_percent"] == 41.0
    monkeypatch.setattr(usage, "_state", None)
    assert "claude" in usage.snapshot()["providers"]  # 存在檔案裡，重開也讀得到
