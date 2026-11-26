"""tests/cases/*.jsonl 與 scripts/eval.py：案例可讀、結構正確、數量足夠。"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

from learn3d.schemas import Lesson, Rubric, Verdict

ROOT = Path(__file__).resolve().parents[1]


def _load_eval():
    spec = importlib.util.spec_from_file_location("learn3d_eval", ROOT / "scripts" / "eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 需要模組已註冊
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ev():
    return _load_eval()


@pytest.fixture(scope="module")
def cases(ev):
    return ev.load_cases()


def test_all_categories_have_five(cases):
    for cat in ["general", "edge", "dialog", "adversarial", "golden_grade"]:
        assert len(cases[cat]) >= 5, cat
    assert len(cases["golden_grade"]) >= 20


def test_ids_unique(cases):
    ids = [c["id"] for rows in cases.values() for c in rows]
    assert len(ids) == len(set(ids))


def test_golden_structure(cases):
    for c in cases["golden_grade"]:
        Rubric.model_validate(c["rubric"])
        assert c["qtype"] in {"predict", "debug", "explain", "produce"}
        assert c["confidence"] in {"high", "mid", "low"}
        assert c["question"] and c["answer"]
        exp = c["expect"]
        results = exp.get("result_in") or [exp["result"]]
        assert set(results) <= {"ok", "partial", "wrong", "unjudgeable"}
        if exp.get("trap"):
            labels = [t["label"] for t in c["rubric"]["traps"]]
            assert exp["trap"] in labels, f"{c['id']} 期望的 trap 不在 rubric 裡"


def test_golden_coverage(cases):
    g = cases["golden_grade"]
    results = Counter(c["expect"].get("result") for c in g)
    for r in ["ok", "partial", "wrong", "unjudgeable"]:
        assert results[r] >= 1, f"黃金樣本缺少 {r}"
    domains = Counter(c["domain"] for c in g)
    assert {"sql", "nl", "concept"} <= set(domains)
    texts = " ".join(c["answer"] for c in g)
    assert "= 'IS NULL'" in texts and " AND " in texts  # 真實作答情境


def test_general_subjects(cases):
    subjects = {c["subject"] for c in cases["general"]}
    assert {"sql", "english", "music"} <= subjects
    for c in cases["general"]:
        Rubric.model_validate(c["rubric"])


def test_edge_kinds(cases):
    kinds = Counter(c["kind"] for c in cases["edge"])
    for k in ["blank", "too_long", "typo", "no_data"]:
        assert kinds[k] >= 1
    long_case = next(c for c in cases["edge"] if c["kind"] == "too_long")
    assert len(long_case["answer"]) > 2000


def test_adversarial_kinds(cases):
    attacks = Counter(c["attack"] for c in cases["adversarial"])
    for a in ["ignore", "forged_marker", "leak_rubric", "exec_command", "offtopic"]:
        assert attacks[a] >= 1
    assert any("<<<作答結束>>>" in c.get("answer", "") for c in cases["adversarial"])


def test_dialog_steps_valid(cases):
    for c in cases["dialog"]:
        assert len(c["steps"]) >= 2
        for st in c["steps"]:
            assert st["do"] in {"lesson", "grade", "jump", "set_due"}
            if st["do"] == "lesson" and st.get("fake"):
                Lesson.model_validate(st["fake"])
            if st["do"] == "grade" and st.get("fake"):
                Verdict.model_validate(st["fake"])


def test_oracle_verdicts_valid(ev, cases):
    """fake 模式的 oracle 回覆本身要通過 Verdict Schema。"""
    for cat in ["golden_grade", "general", "edge", "adversarial"]:
        for c in cases[cat]:
            if "rubric" in c:
                Verdict.model_validate_json(ev.oracle_verdict(c))


def test_eval_fake_end_to_end(ev, tmp_path, monkeypatch):
    """fake 模式應能跑完並產出報告（需要 src 各模組已實作）。"""
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "eval_report.md"
    rc = ev.main(["--provider", "fake", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert "通過率" in text and "golden_grade" in text
    assert "例外：" not in text, text
    assert rc == 0, text  # fake 模式（oracle 回覆）下流程應全部通過
