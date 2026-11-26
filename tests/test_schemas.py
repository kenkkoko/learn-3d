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
    d["questions"][0]["options"][0]["label"] = "長" * 31
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
