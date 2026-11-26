"""作答日誌：判定後寫入 journal.jsonl，可依節點篩選、分頁；離題與判定失敗不記。"""

from __future__ import annotations

import json

from conftest import verdict_dict
from test_api import AI, BAD_OUTPUTS, grade, start_lesson

from learn3d import store


def journal_lines(sql_topic):
    path = sql_topic.parent / "journal.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_grade_appends_journal_entry(client, sql_topic, llm_box):
    q = start_lesson(client, llm_box)
    llm_box.set([])
    r = grade(client, q["question_id"], "SELECT name FROM suppliers WHERE rating IS NULL;", "high",
              {"user_output": "聯茂電子", "ref_output": "聯茂電子", "equal": True})
    assert r.status_code == 200, r.text
    rows = journal_lines(sql_topic)
    assert len(rows) == 1
    e = rows[0]
    assert e["question"] == q["question"]
    assert e["answer"].startswith("SELECT name")
    assert e["confidence"] == "high" and e["result"] == "ok" and e["delta"] == r.json()["delta"]
    assert e["reference_answer"] and e["feedback"] and e["node"] == q["node"]
    assert e["exec_user"] == "聯茂電子" and e["source"] == "web"


def test_journal_masks_pii(client, sql_topic, llm_box):
    q = start_lesson(client, llm_box)
    llm_box.set([json.dumps(verdict_dict(result="partial", met=[0]), ensure_ascii=False)] * 3)
    grade(client, q["question_id"], "我的信箱是 someone@example.com，答案是 IS NULL")
    e = journal_lines(sql_topic)[-1]
    assert "someone@example.com" not in e["answer"] and "[已遮蔽]" in e["answer"]


def test_offtopic_and_fallback_not_journaled(client, sql_topic, llm_box):
    q = start_lesson(client, llm_box)
    r = grade(client, q["question_id"], "今天天氣如何")
    assert r.json()["offtopic"] is True
    llm_box.set(BAD_OUTPUTS)
    r = grade(client, q["question_id"], "我覺得是 IS NULL 因為空白不能比較")
    assert r.json()["fallback"] is True
    assert journal_lines(sql_topic) == []


def test_journal_endpoint_newest_first_and_paging(client, sql_topic, llm_box):
    for i in range(3):
        q = start_lesson(client, llm_box)
        llm_box.set([])
        grade(client, q["question_id"], f"SELECT {i}", "mid", {"user_output": "x", "ref_output": "x", "equal": True})
    r = client.get("/topics/sql/journal?limit=2")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [e["answer"] for e in b["entries"]] == ["SELECT 2", "SELECT 1"] and b["has_more"] is True
    r2 = client.get(f"/topics/sql/journal?limit=2&before={b['entries'][-1]['id']}").json()
    assert [e["answer"] for e in r2["entries"]] == ["SELECT 0"] and r2["has_more"] is False


def test_journal_filter_by_node_and_skips_bad_lines(client, sql_topic, data_dir):
    path = sql_topic.parent / "journal.jsonl"
    good = {"id": "a1", "at": "2026-11-26T10:00:00+08:00", "node": "where-basic", "node_name": "WHERE",
            "type": "produce", "question": "q", "answer": "a", "confidence": "mid", "result": "ok",
            "feedback": "f", "kind": "new", "delta": 1, "danger": False, "reference_answer": "r"}
    other = {**good, "id": "b2", "node": "select-basic"}
    path.write_text(json.dumps(good, ensure_ascii=False) + "\n這行壞掉了\n" + json.dumps(other) + "\n", encoding="utf-8")
    b = client.get("/topics/sql/journal?node=where-basic").json()
    assert [e["id"] for e in b["entries"]] == ["a1"]
    assert len(client.get("/topics/sql/journal").json()["entries"]) == 2


def test_journal_empty_and_unknown_topic(client, sql_topic):
    assert client.get("/topics/sql/journal").json() == {"entries": [], "has_more": False}
    assert client.get("/topics/no-such/journal").status_code == 404


def test_store_journal_roundtrip(sql_topic):
    store.journal_append("sql", {"id": "x1", "answer": "中文 OK"})
    rows, more = store.journal_read("sql")
    assert rows[0]["answer"] == "中文 OK" and more is False
