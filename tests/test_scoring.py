"""scoring.py：把握度矩陣、狀態門檻、選題、迷思與間隔複習（純函式）。"""

from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from learn3d import scoring

from conftest import FIXED_DAY

D = FIXED_DAY


def node(nid, *, layer=1, prereqs=(), status="unseen", score=0, history=None):
    return {"id": nid, "name": nid, "layer": layer, "prereqs": list(prereqs),
            "summary": "s", "pitfalls": ["a", "b"], "status": status, "score": score,
            "lastSeen": None, "history": list(history or [])}


def hist(qtype="predict", result="ok", confidence="mid", day="2026-11-25"):
    return {"date": day, "type": qtype, "confidence": confidence, "result": result}


def prog(nodes, current=None, misconceptions=None):
    return {"version": 1, "topic": "t", "title": "T", "domain": "code", "runner": "sql",
            "created": "2026-11-01", "learner": {}, "envNote": None,
            "current": current or nodes[0]["id"], "nodes": nodes,
            "misconceptions": misconceptions or [], "lastQuestion": None,
            "stats": {"answered": 0, "correct": 0, "streak": 0}}


def get_node(p, nid):
    return next(n for n in p["nodes"] if n["id"] == nid)


# ---------- 把握度矩陣 ----------
MATRIX = [
    ("high", "ok", 2), ("high", "partial", 0), ("high", "wrong", -2),
    ("mid", "ok", 1), ("mid", "partial", 0), ("mid", "wrong", -1),
    ("low", "ok", 0), ("low", "partial", 0), ("low", "wrong", -1),
]


@pytest.mark.parametrize("conf,result,delta", MATRIX)
def test_matrix(conf, result, delta):
    p = prog([node("a", status="shaky", score=3)])
    new, out = scoring.apply_grade(p, "a", "explain", conf, result, D)
    assert out.delta == delta
    assert out.score == 3 + delta
    assert get_node(new, "a")["score"] == 3 + delta
    assert out.danger == (conf == "high" and result == "wrong")


@pytest.mark.parametrize("conf", ["high", "mid", "low"])
def test_unjudgeable_keeps_score(conf):
    p = prog([node("a", status="shaky", score=3)])
    new, out = scoring.apply_grade(p, "a", "explain", conf, "unjudgeable", D)
    assert out.delta == 0 and out.score == 3 and not out.danger
    assert get_node(new, "a")["score"] == 3


# ---------- 夾 0–6 ----------
def test_clamp_low():
    p = prog([node("a", status="weak", score=1)])
    new, out = scoring.apply_grade(p, "a", "explain", "high", "wrong", D)
    assert out.score == 0 and get_node(new, "a")["score"] == 0


def test_clamp_high():
    h = [hist("predict"), hist("explain"), hist("produce")]
    p = prog([node("a", status="mastered", score=6, history=h)])
    new, out = scoring.apply_grade(p, "a", "debug", "high", "ok", D)
    assert out.score == 6 and get_node(new, "a")["score"] == 6


# ---------- 狀態門檻 ----------
@pytest.mark.parametrize("start,expected", [(0, "weak"), (1, "shaky"), (3, "shaky"), (4, "mastered")])
def test_status_thresholds(start, expected):
    # 前兩筆不同題型都答對，再以 mid ok（+1）結算 → 最近 3 次有 ≥2 種題型答對
    h = [hist("predict"), hist("explain")]
    p = prog([node("a", score=start, history=h)])
    _, out = scoring.apply_grade(p, "a", "produce", "mid", "ok", D)
    assert out.score == start + 1
    assert out.status == expected


def test_weak_at_one():
    p = prog([node("a", score=2, history=[hist()])])
    _, out = scoring.apply_grade(p, "a", "predict", "mid", "wrong", D)
    assert out.score == 1 and out.status == "weak"


def test_mastered_needs_two_qtypes():
    # 分數到 5，但最近 3 次都是同一題型 → 不能 mastered
    h = [hist("predict"), hist("predict")]
    p = prog([node("a", score=4, history=h)])
    new, out = scoring.apply_grade(p, "a", "predict", "mid", "ok", D)
    assert out.score == 5
    assert out.status == "shaky"
    assert get_node(new, "a")["status"] == "shaky"


def test_mastered_only_counts_last_three():
    # 不同題型的答對在第 4 筆之前，最近 3 次只有一種題型答對 → 不 mastered
    h = [hist("explain"), hist("predict", "wrong"), hist("predict")]
    p = prog([node("a", score=4, history=h)])
    _, out = scoring.apply_grade(p, "a", "predict", "mid", "ok", D)
    assert out.score == 5 and out.status == "shaky"


def test_mastered_ignores_wrong_types():
    # 最近 3 次有兩種題型，但其中一種是答錯 → 不算
    h = [hist("predict"), hist("explain", "wrong")]
    p = prog([node("a", score=4, history=h)])
    _, out = scoring.apply_grade(p, "a", "predict", "high", "ok", D)
    assert out.score == 6 and out.status == "shaky"


# ---------- history ----------
def test_history_appended_and_capped():
    h = [hist("predict", day=f"2026-11-{i:02d}") for i in range(1, 7)]
    p = prog([node("a", score=3, history=h)])
    new, _ = scoring.apply_grade(p, "a", "debug", "low", "partial", D)
    nh = get_node(new, "a")["history"]
    assert len(nh) == 6
    assert nh[-1]["type"] == "debug" and nh[-1]["result"] == "partial"
    assert nh[-1]["confidence"] == "low" and nh[-1]["date"] == D.isoformat()
    assert nh[0]["date"] == "2026-11-02"  # 最舊的那筆被丟掉


def test_apply_grade_sets_last_seen():
    p = prog([node("a", score=0)])
    new, _ = scoring.apply_grade(p, "a", "predict", "mid", "ok", D)
    assert get_node(new, "a")["lastSeen"] == D.isoformat()


# ---------- 不就地修改 ----------
def test_inputs_not_mutated():
    mis = [{"id": "m1", "node": "a", "label": "L", "wrong": "w", "right": "r", "scenarios": ["s"],
            "created": "2026-11-20", "interval": 1, "nextReview": "2026-11-21", "hits": 1,
            "retired": False}]
    p = prog([node("a", score=3, history=[hist()]), node("b", layer=2, prereqs=["a"])],
             misconceptions=mis)
    snap = copy.deepcopy(p)
    scoring.apply_grade(p, "a", "predict", "high", "wrong", D)
    scoring.record_misconception(p, "a", "新迷思", "w", "r", "情境", D)
    scoring.record_misconception(p, "a", "L", "w", "r", "情境二", D)
    scoring.apply_review(p, "m1", "ok", D)
    scoring.apply_review(p, "m1", "wrong", D)
    scoring.pick_next_node(p)
    scoring.due_misconceptions(p, D)
    scoring.choose_qtype(p["nodes"][0]["history"], "code")
    assert p == snap


# ---------- choose_qtype ----------
QTYPES = {"predict", "debug", "explain", "produce"}


@pytest.mark.parametrize("domain", ["code", "language", "concept", "skill"])
def test_choose_qtype_valid(domain):
    assert scoring.choose_qtype([], domain) in QTYPES


@pytest.mark.parametrize("domain", ["code", "language", "concept", "skill"])
def test_choose_qtype_rotates(domain):
    history: list[dict] = []
    picked = []
    for _ in range(4):
        q = scoring.choose_qtype(history, domain)
        assert q in QTYPES
        if picked:
            assert q != picked[-1], f"{domain} 連續出同一題型 {q}"
        picked.append(q)
        history.append(hist(q))
    assert len(set(picked)) >= 2


def test_choose_qtype_avoids_last():
    for last in QTYPES:
        assert scoring.choose_qtype([hist("predict"), hist(last)], "code") != last


# ---------- pick_next_node ----------
def test_pick_current_when_not_mastered():
    p = prog([node("a", status="mastered", score=6), node("b", layer=2, prereqs=["a"], status="shaky", score=3)],
             current="b")
    assert scoring.pick_next_node(p) == "b"


def test_pick_moves_on_when_current_mastered():
    p = prog([node("a", status="mastered", score=6), node("b", layer=2, prereqs=["a"])], current="a")
    assert scoring.pick_next_node(p) == "b"


def test_pick_skips_node_with_unmet_prereq():
    nodes = [
        node("a", status="mastered", score=6),
        node("w", layer=1, status="weak", score=1),          # 紅燈
        node("x", layer=2, prereqs=["w"]),                   # 先修紅燈 → 不可選
        node("y", layer=2, prereqs=["a"]),                   # 先修綠燈 → 可選
    ]
    p = prog(nodes, current="a")
    assert scoring.pick_next_node(p) in {"w", "y"}
    assert scoring.pick_next_node(p) != "x"


def test_pick_yellow_prereq_is_enough():
    nodes = [
        node("a", status="mastered", score=6),
        node("s", layer=1, status="shaky", score=2, history=[hist()]),
        node("u", layer=1, status="unseen"),
        node("x", layer=2, prereqs=["u"]),   # 先修未學 → 不選
        node("y", layer=2, prereqs=["s"]),   # 先修黃燈 → 可選
    ]
    p = prog(nodes, current="a")
    assert scoring.pick_next_node(p) != "x"
    # 只剩 y 與先修本身可選時，y 應可被選
    nodes2 = [node("a", status="mastered", score=6),
              node("s", layer=1, status="shaky", score=2, history=[hist()]),
              node("x", layer=2, prereqs=["s"])]
    p2 = prog(nodes2, current="a")
    assert scoring.pick_next_node(p2) in {"s", "x"}


def test_pick_only_locked_candidates_not_chosen():
    nodes = [node("a", status="mastered", score=6),
             node("w", layer=1, status="weak", score=0, history=[hist(result="wrong")]),
             node("x", layer=2, prereqs=["w"])]
    p = prog(nodes, current="a")
    assert scoring.pick_next_node(p) == "w"


# ---------- record_misconception ----------
def test_record_new_misconception():
    p = prog([node("a", score=3)])
    new, mid = scoring.record_misconception(p, "a", "AND OR 搞混", "city='台中' AND 'B'",
                                            "要用 OR 且每個條件寫完整", "供應商城市或評等", D)
    assert len(new["misconceptions"]) == 1
    m = new["misconceptions"][0]
    assert m["id"] == mid
    assert m["node"] == "a" and m["label"] == "AND OR 搞混"
    assert m["wrong"] and m["right"]
    assert m["scenarios"] == ["供應商城市或評等"]
    assert m["interval"] == 1
    assert m["nextReview"] == (D + timedelta(days=1)).isoformat()
    assert m["hits"] == 1 and m["retired"] is False
    assert m["created"] == D.isoformat()


def test_record_new_misconception_unique_id():
    p = prog([node("a")])
    p, id1 = scoring.record_misconception(p, "a", "L1", "w", "r", "s", D)
    p, id2 = scoring.record_misconception(p, "a", "L2", "w", "r", "s", D)
    assert id1 != id2 and len(p["misconceptions"]) == 2


def test_record_repeat_misconception():
    mis = [{"id": "m1", "node": "a", "label": "以為 NULL 會被 <> 選到", "wrong": "w", "right": "r",
            "scenarios": ["評等 <> 'A'"], "created": "2026-11-10", "interval": 7,
            "nextReview": "2026-12-01", "hits": 1, "retired": False}]
    p = prog([node("a", score=3)], misconceptions=mis)
    new, mid = scoring.record_misconception(p, "a", "以為 NULL 會被 <> 選到", "w2", "r2", "城市 <> '台中'", D)
    assert mid == "m1"
    assert len(new["misconceptions"]) == 1
    m = new["misconceptions"][0]
    assert m["hits"] == 2
    assert "城市 <> '台中'" in m["scenarios"] and "評等 <> 'A'" in m["scenarios"]
    assert m["interval"] == 1
    assert m["nextReview"] == (D + timedelta(days=1)).isoformat()


# ---------- due_misconceptions ----------
def test_due_misconceptions():
    def mm(i, nr, retired=False):
        return {"id": i, "node": "a", "label": i, "wrong": "w", "right": "r", "scenarios": [],
                "created": "2026-11-01", "interval": 1, "nextReview": nr, "hits": 1, "retired": retired}
    p = prog([node("a")], misconceptions=[mm("past", "2026-11-20"), mm("today", D.isoformat()),
                                          mm("future", "2026-12-30"), mm("old", "2026-11-01", True)])
    ids = {m["id"] for m in scoring.due_misconceptions(p, D)}
    assert ids == {"past", "today"}


# ---------- apply_review ----------
def _mis(interval, nr="2026-11-26"):
    return {"id": "m1", "node": "a", "label": "L", "wrong": "w", "right": "r", "scenarios": ["s"],
            "created": "2026-11-01", "interval": interval, "nextReview": nr, "hits": 1, "retired": False}


@pytest.mark.parametrize("before,after", [(1, 3), (3, 7), (7, 14)])
def test_review_ok_steps(before, after):
    p = prog([node("a", score=3)], misconceptions=[_mis(before)])
    new = scoring.apply_review(p, "m1", "ok", D)
    m = new["misconceptions"][0]
    assert m["interval"] == after
    assert m["nextReview"] == (D + timedelta(days=after)).isoformat()
    assert m["retired"] is False


def test_review_full_cycle_retires():
    p = prog([node("a", score=3)], misconceptions=[_mis(1)])
    day = D
    seen = []
    for _ in range(4):
        p = scoring.apply_review(p, "m1", "ok", day)
        m = p["misconceptions"][0]
        seen.append(m["interval"])
        if m["retired"]:
            break
        day = date.fromisoformat(m["nextReview"])
    assert p["misconceptions"][0]["retired"] is True
    assert seen[:3] == [3, 7, 14]


def test_review_14_ok_retires():
    p = prog([node("a", score=3)], misconceptions=[_mis(14)])
    new = scoring.apply_review(p, "m1", "ok", D)
    assert new["misconceptions"][0]["retired"] is True


@pytest.mark.parametrize("interval", [3, 7, 14])
def test_review_wrong_resets_and_penalizes(interval):
    p = prog([node("a", status="shaky", score=3)], misconceptions=[_mis(interval)])
    new = scoring.apply_review(p, "m1", "wrong", D)
    m = new["misconceptions"][0]
    assert m["interval"] == 1
    assert m["nextReview"] == (D + timedelta(days=1)).isoformat()
    assert m["retired"] is False
    assert get_node(new, "a")["score"] == 2


def test_review_wrong_penalty_clamped():
    p = prog([node("a", status="weak", score=0, history=[hist(result="wrong")])], misconceptions=[_mis(3)])
    new = scoring.apply_review(p, "m1", "wrong", D)
    assert get_node(new, "a")["score"] == 0


def test_today_is_date():
    assert isinstance(scoring.today(), date)
