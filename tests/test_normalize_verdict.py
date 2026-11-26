"""判定校正（tasks.normalize_verdict）：便宜模型的判定要和評分規則一致。"""

from learn3d.schemas import ExecResult, Rubric, Trap, Verdict
from learn3d.tasks import GradeContext, normalize_verdict

RUBRIC = Rubric(
    answer="SELECT name FROM suppliers WHERE city = '桃園' OR rating = 'B';",
    accept="大小寫不拘",
    must_have=["用 OR 連接兩個條件", "兩個條件都寫欄位名稱"],
    traps=[Trap(when="用 AND", label="AND 與 OR 搞混")],
)


def ctx(qtype="produce", exec_=None):
    return GradeContext(question="q", qtype=qtype, rubric=RUBRIC, answer="a", exec=exec_, learner_notes=None)


def v(result, met=(), trap=None):
    return Verdict(result=result, met=list(met), trap=trap, feedback="f", citations=["answer"])


def test_trap_hit_partial_becomes_wrong():
    assert normalize_verdict(v("partial", [1], "AND 與 OR 搞混"), ctx()).result == "wrong"


def test_trap_label_matching_ignores_spaces_and_case():
    assert normalize_verdict(v("partial", [1], "and與or搞混"), ctx()).result == "wrong"


def test_new_misconception_label_not_in_rubric_keeps_partial():
    assert normalize_verdict(v("partial", [0], "新迷思"), ctx()).result == "partial"


def test_ok_missing_must_have_becomes_partial():
    assert normalize_verdict(v("ok", [0]), ctx("explain")).result == "partial"


def test_ok_with_all_must_have_stays_ok():
    assert normalize_verdict(v("ok", [0, 1]), ctx("explain")).result == "ok"


def test_partial_without_any_must_have_becomes_wrong():
    assert normalize_verdict(v("partial", []), ctx("explain")).result == "wrong"


def test_exec_error_becomes_wrong():
    ex = ExecResult(user_output="錯誤：no such column: 結案", ref_output="x", equal=False)
    assert normalize_verdict(v("partial", [0, 1]), ctx(exec_=ex)).result == "wrong"


def test_exec_differs_caps_ok_at_partial():
    ex = ExecResult(user_output="a | b", ref_output="a", equal=False)
    assert normalize_verdict(v("ok", [0, 1]), ctx(exec_=ex)).result == "partial"


def test_predict_exec_differs_does_not_cap():
    # predict 題的 equal 一律為 false（比的是文字答案），不能因此降級
    ex = ExecResult(user_output="", ref_output="3 列", equal=False)
    assert normalize_verdict(v("ok", [0, 1]), ctx("predict", ex)).result == "ok"


def test_unjudgeable_untouched():
    assert normalize_verdict(v("unjudgeable", [], "AND 與 OR 搞混"), ctx()).result == "unjudgeable"


def test_punctuation_only_answer_is_unjudgeable_without_ai():
    from learn3d.providers.fake import FakeLLM
    from learn3d.schemas import AiChoice
    from learn3d.tasks import run_grade

    llm = FakeLLM()
    c = GradeContext(question="q", qtype="produce", rubric=RUBRIC, answer="？？？ ...", exec=None, learner_notes=None)
    r = run_grade(llm, AiChoice(provider="fake"), c)
    assert r.result == "unjudgeable"
    assert llm.calls == []


def test_short_real_answer_still_goes_to_ai():
    from learn3d.providers.fake import FakeLLM
    from learn3d.schemas import AiChoice
    from learn3d.tasks import run_grade

    llm = FakeLLM()
    c = GradeContext(question="q", qtype="explain", rubric=RUBRIC, answer="B", exec=None, learner_notes=None)
    run_grade(llm, AiChoice(provider="fake"), c)
    assert len(llm.calls) == 1
