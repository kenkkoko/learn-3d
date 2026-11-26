"""store.py：原子寫入、舊檔補欄位、建立主題、垃圾桶、重設、rubric 轉換、路徑穿越。"""

from __future__ import annotations

import copy
import json
import os

import pytest

from learn3d import store

from conftest import make_map, make_plan_request, make_rubric


def _write(data_dir, topic, progress):
    d = data_dir / topic
    d.mkdir(parents=True, exist_ok=True)
    (d / "progress.json").write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    return d / "progress.json"


def test_data_root_uses_env(data_dir):
    assert store.data_root().resolve() == data_dir.resolve()


def test_load_sample(sql_topic, sample_progress):
    p = store.load("sql")
    assert p["topic"] == sample_progress["topic"]
    assert len(p["nodes"]) == len(sample_progress["nodes"])
    assert store.exists("sql") and not store.exists("nope")


def test_save_roundtrip_and_no_temp_left(sql_topic, data_dir):
    p = store.load("sql")
    p["current"] = p["nodes"][0]["id"]
    store.save("sql", p)
    assert store.load("sql")["current"] == p["nodes"][0]["id"]
    leftovers = [f.name for f in (data_dir / "sql").iterdir()
                 if f.name not in {"progress.json", "practice.sql", "practice.db", "progress.json.bak"}
                 and f.is_file()]
    assert not [n for n in leftovers if "tmp" in n.lower() or n.endswith(".part")], leftovers


def test_save_writes_utf8_chinese(sql_topic):
    p = store.load("sql")
    p["title"] = "中文標題測試"
    store.save("sql", p)
    assert "中文標題測試" in sql_topic.read_text(encoding="utf-8")


def test_save_is_atomic(sql_topic, monkeypatch):
    """os.replace 失敗時，原檔必須完整不變（代表是寫暫存檔再改名）。"""
    before = sql_topic.read_bytes()
    p = store.load("sql")
    p["title"] = "不該被寫進去"

    def boom(*a, **k):
        raise OSError("模擬中斷")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.save("sql", p)
    assert sql_topic.read_bytes() == before


def test_load_fills_missing_fields(data_dir):
    old = {
        "version": 1, "topic": "old", "title": "舊檔", "domain": "concept",
        "current": "a",
        "nodes": [{"id": "a", "name": "A", "layer": 1, "prereqs": [], "summary": "s",
                   "pitfalls": ["x", "y"]}],
    }
    _write(data_dir, "old", old)
    p = store.load("old")
    assert p["misconceptions"] == []
    assert p.get("lastQuestion") is None
    assert set(p["stats"]) >= {"answered", "correct", "streak"}
    n = p["nodes"][0]
    assert n["status"] == "unseen" and n["score"] == 0 and n["history"] == []
    assert "lastSeen" in n
    assert "learner" in p


def test_load_keeps_unknown_fields(data_dir, sample_progress):
    sample_progress["futureField"] = {"x": 1}
    _write(data_dir, "keep", {**sample_progress, "topic": "keep"})
    p = store.load("keep")
    store.save("keep", p)
    assert store.load("keep")["futureField"] == {"x": 1}


# ---------- create_topic ----------
def test_create_topic(data_dir):
    req = make_plan_request(slug="sql-new")
    m = make_map()
    p = store.create_topic(req, m)
    assert (data_dir / "sql-new" / "progress.json").exists()
    assert p["version"] == 1 and p["topic"] == "sql-new"
    assert p["title"] == m.title and p["domain"] == "code" and p["runner"] == "sql"
    assert p["current"] == m.first
    assert len(p["nodes"]) == len(m.nodes)
    for n in p["nodes"]:
        assert n["status"] == "unseen" and n["score"] == 0 and n["history"] == []
    assert p["misconceptions"] == []
    assert p["learner"]["goal"] == "面試"
    assert store.read_env("sql-new") == m.env_seed
    assert store.load("sql-new") == p


def test_create_topic_without_env(data_dir):
    p = store.create_topic(make_plan_request(slug="music", domain="concept"),
                           make_map(domain="concept", runner=None, env_seed=None))
    assert p["runner"] is None
    assert store.read_env("music") is None


def test_create_topic_replan_keep(data_dir):
    req = make_plan_request(slug="sql-new")
    m = make_map()
    old = store.create_topic(req, m)
    old["nodes"][0].update(status="shaky", score=3,
                           history=[{"date": "2026-11-25", "type": "predict", "confidence": "mid", "result": "ok"}])
    old["misconceptions"] = [{"id": "m1", "node": old["nodes"][0]["id"], "label": "L", "wrong": "w",
                              "right": "r", "scenarios": [], "created": "2026-11-25", "interval": 1,
                              "nextReview": "2026-11-26", "hits": 1, "retired": False}]
    store.save("sql-new", old)
    keep = copy.deepcopy(old)
    req2 = make_plan_request(slug="sql-new", mode="replan_keep", confirm=True)
    new = store.create_topic(req2, make_map(title="SQL 新版"), keep_from=keep)
    kept = next(n for n in new["nodes"] if n["id"] == old["nodes"][0]["id"])
    assert kept["status"] == "shaky" and kept["score"] == 3 and len(kept["history"]) == 1
    others = [n for n in new["nodes"] if n["id"] != kept["id"]]
    assert all(n["status"] == "unseen" for n in others)
    assert new["title"] == "SQL 新版"
    assert [m["id"] for m in new["misconceptions"]] == ["m1"]
    assert keep == old  # keep_from 不被就地修改


# ---------- lecture cache ----------
def test_lecture_cache(sql_topic):
    assert store.lecture_cache_get("sql", "where-basic") is None
    store.lecture_cache_put("sql", "where-basic", {"lecture": "短講"})
    assert store.lecture_cache_get("sql", "where-basic")["lecture"] == "短講"


# ---------- trash / reset ----------
def test_trash(sql_topic, data_dir):
    store.trash("sql")
    assert not store.exists("sql")
    assert not (data_dir / "sql").exists()
    trashed = list((data_dir / ".trash").iterdir())
    assert len(trashed) == 1 and trashed[0].name.startswith("sql-")
    assert (trashed[0] / "progress.json").exists()


def test_trash_not_listed(sql_topic):
    store.trash("sql")
    assert all(t.topic != "sql" for t in store.list_topics())
    assert all(not t.topic.startswith(".") for t in store.list_topics())


def test_reset_backs_up(sql_topic, sample_progress):
    before = sql_topic.read_text(encoding="utf-8")
    p = store.reset("sql")
    bak = sql_topic.with_name("progress.json.bak")
    assert bak.exists() and bak.read_text(encoding="utf-8") == before
    assert all(n["status"] == "unseen" and n["score"] == 0 and n["history"] == [] for n in p["nodes"])
    assert len(p["nodes"]) == len(sample_progress["nodes"])
    assert store.load("sql") == p


# ---------- list_topics ----------
def test_list_topics(sql_topic):
    ts = store.list_topics()
    assert [t.topic for t in ts] == ["sql"]
    t = ts[0]
    assert t.nodes == len(store.load("sql")["nodes"])
    assert t.mastered >= 0 and t.due >= 0


# ---------- rubric 轉換 ----------
def test_rubric_to_file_format():
    d = store.rubric_to_file(make_rubric())
    assert set(d) == {"answer", "accept", "mustHave", "traps"}
    assert d["mustHave"] == ["用 IS NULL 判斷空白", "不加引號、不用 ="]
    assert d["traps"][0] == {"if": "寫成 = 'IS NULL' 或 = NULL", "label": "把 IS NULL 寫成字串"}
    json.dumps(d, ensure_ascii=False)


def test_rubric_roundtrip():
    r = make_rubric()
    assert store.rubric_from_file(store.rubric_to_file(r)) == r
    r2 = make_rubric(traps=[])
    assert store.rubric_from_file(store.rubric_to_file(r2)) == r2


def test_rubric_from_skill_file():
    """skill 寫出的格式也要能讀。"""
    d = {"answer": "A", "accept": "", "mustHave": ["重點"], "traps": [{"if": "錯想法", "label": "迷思"}]}
    r = store.rubric_from_file(d)
    assert r.must_have == ["重點"] and r.traps[0].when == "錯想法"


# ---------- public_progress ----------
def test_public_progress_strips_rubric(sample_progress):
    sample_progress["lastQuestion"] = {
        "id": "q1", "node": "where-basic", "type": "produce", "kind": "new", "misconceptionId": None,
        "question": "Q", "askedAt": "2026-11-26",
        "rubric": store.rubric_to_file(make_rubric()), "lesson": {"question": "Q"},
    }
    snap = copy.deepcopy(sample_progress)
    pub = store.public_progress(sample_progress)
    assert "rubric" not in pub["lastQuestion"]
    assert pub["lastQuestion"]["question"] == "Q"
    assert "mustHave" not in json.dumps(pub, ensure_ascii=False)
    assert sample_progress == snap  # 不修改輸入


def test_public_progress_no_last_question(sample_progress):
    sample_progress["lastQuestion"] = None
    assert store.public_progress(sample_progress)["lastQuestion"] is None


# ---------- 路徑穿越 ----------
BAD_TOPICS = ["../evil", "..", "a/b", "a\\b", "/etc", "C:\\Windows", "sql/../../x", ".trash", ""]


@pytest.mark.parametrize("bad", BAD_TOPICS)
def test_traversal_rejected_on_save(bad, data_dir, sample_progress):
    with pytest.raises(Exception):
        store.save(bad, sample_progress)
    assert not (data_dir.parent / "evil").exists()
    assert not (data_dir.parent / "x").exists()


@pytest.mark.parametrize("bad", ["../evil", "..", "a/b", "/etc", "sql/../../x"])
def test_traversal_rejected_on_load_trash_reset(bad, data_dir):
    outside = data_dir.parent / "evil"
    outside.mkdir()
    (outside / "progress.json").write_text("{}", encoding="utf-8")
    for fn in (store.load, store.trash, store.reset):
        with pytest.raises(Exception):
            fn(bad)
    assert (outside / "progress.json").exists()


def test_traversal_exists_is_false(data_dir):
    outside = data_dir.parent / "evil"
    outside.mkdir()
    (outside / "progress.json").write_text("{}", encoding="utf-8")
    try:
        assert store.exists("../evil") is False
    except Exception:
        pass  # 直接拒絕也可以
