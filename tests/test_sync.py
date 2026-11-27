"""跨裝置同步（本機端）：使用者資料夾分開、匯出匯入、作答紀錄合併、舊資料歸屬。"""

from __future__ import annotations

import json

from conftest import lesson_dict
from test_api import AI

from learn3d import store

U1 = "11111111-1111-4111-8111-111111111111"
U2 = "22222222-2222-4222-8222-222222222222"


def H(uid):
    return {"X-Learn3D-User": uid}


def make_topic(client, data_dir, uid, name="sql", src=None):
    """把 sql 範例主題複製到某個使用者的資料夾。"""
    import shutil
    dst = data_dir / "users" / uid / name
    shutil.copytree(src, dst)
    return dst


def test_users_are_separated(client, sql_topic, data_dir):
    # 沒帶 header：看到舊位置的 sql
    assert [t["topic"] for t in client.get("/topics").json()["topics"]] == ["sql"]
    # 帶了 header：自己的資料夾是空的，看不到別人的
    assert client.get("/topics", headers=H(U1)).json()["topics"] == []
    make_topic(client, data_dir, U2, src=sql_topic.parent)
    assert client.get("/topics", headers=H(U1)).json()["topics"] == []
    assert [t["topic"] for t in client.get("/topics", headers=H(U2)).json()["topics"]] == ["sql"]


def test_bad_user_header_falls_back_to_legacy(client, sql_topic):
    r = client.get("/topics", headers={"X-Learn3D-User": "../../etc"})
    assert [t["topic"] for t in r.json()["topics"]] == ["sql"]


def test_cors_allows_user_header(client):
    r = client.options("/topics", headers={"Access-Control-Request-Method": "GET",
                                           "Access-Control-Request-Headers": "x-learn3d-user"})
    assert "x-learn3d-user" in r.headers["access-control-allow-headers"]
    assert client.get("/health").json()["sync"] == 1


def test_claim_moves_legacy_topics(client, sql_topic, data_dir):
    m = client.get("/sync/manifest", headers=H(U1)).json()
    assert m["legacy"] == 1 and m["topics"] == []
    r = client.post("/sync/claim", headers=H(U1))
    assert r.json()["moved"] == ["sql"]
    assert not (data_dir / "sql").exists()
    assert (data_dir / "users" / U1 / "sql" / "progress.json").is_file()
    assert client.get("/sync/manifest", headers=H(U1)).json()["legacy"] == 0
    # 沒登入不能認領
    assert client.post("/sync/claim").status_code == 400


def test_export_import_roundtrip_and_manifest(client, sql_topic, data_dir, llm_box):
    make_topic(client, data_dir, U1, src=sql_topic.parent)
    m = client.get("/sync/manifest", headers=H(U1)).json()["topics"]
    assert m[0]["topic"] == "sql" and m[0]["hash"] and m[0]["base_hash"] is None
    b = client.get("/sync/topics/sql", headers=H(U1)).json()
    assert b["hash"] == m[0]["hash"] and b["progress"]["nodes"] and b["env_sql"]

    # 上傳後記下同步點
    assert client.post("/sync/topics/sql/mark", headers=H(U1), json={"cloud_at": "2026-11-27T00:00:00Z", "hash": b["hash"]}).status_code == 200
    m = client.get("/sync/manifest", headers=H(U1)).json()["topics"][0]
    assert m["base_hash"] == m["hash"] and m["base_cloud_at"] == "2026-11-27T00:00:00Z"

    # 另一個帳號（模擬另一台電腦）匯入同一份
    r = client.post("/sync/topics/sql/import", headers=H(U2), json={"bundle": b, "cloud_at": "2026-11-27T00:00:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["hash"] == b["hash"]
    m2 = client.get("/sync/manifest", headers=H(U2)).json()["topics"][0]
    assert m2["hash"] == m2["base_hash"] == b["hash"]
    assert (data_dir / "users" / U2 / "sql" / "practice.db").is_file()


def test_journal_merge_keeps_both_sides(client, sql_topic, data_dir):
    d = make_topic(client, data_dir, U1, src=sql_topic.parent)
    local = [{"id": "a1", "at": "2026-11-27T10:00:00+08:00", "question": "q1"},
             {"id": "a3", "at": "2026-11-27T12:00:00+08:00", "question": "q3"}]
    (d / "journal.jsonl").write_text("".join(json.dumps(e) + "\n" for e in local), encoding="utf-8")
    cloud = [{"id": "a2", "at": "2026-11-27T11:00:00+08:00", "question": "q2"},
             {"id": "a1", "at": "2026-11-27T10:00:00+08:00", "question": "q1"}]
    r = client.post("/sync/topics/sql/import", headers=H(U1),
                    json={"bundle": {"journal": cloud}, "journal_only": True})
    assert r.status_code == 200, r.text and r.json()["journal_added"] == 1
    ids = [json.loads(x)["id"] for x in (d / "journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert ids == ["a1", "a2", "a3"]


def test_import_newer_cloud_progress_keeps_local_journal(client, sql_topic, data_dir, llm_box):
    make_topic(client, data_dir, U1, src=sql_topic.parent)
    b = client.get("/sync/topics/sql", headers=H(U1)).json()
    b["progress"]["title"] = "雲端改過的標題"
    b["journal"] = [{"id": "c1", "at": "2026-11-27T09:00:00+08:00", "question": "cloud"}]
    b["hash"] = "cloudhash"
    (data_dir / "users" / U1 / "sql" / "journal.jsonl").write_text(
        json.dumps({"id": "l1", "at": "2026-11-27T10:00:00+08:00", "question": "local"}) + "\n", encoding="utf-8")
    client.post("/sync/topics/sql/import", headers=H(U1), json={"bundle": b, "cloud_at": "T1"})
    with store_user(U1):
        assert store.load("sql")["title"] == "雲端改過的標題"
        assert (store.topic_dir("sql") / "progress.json.bak").is_file()
    m = client.get("/sync/manifest", headers=H(U1)).json()["topics"][0]
    # 本機多了一筆雲端沒有的紀錄 → 指紋不同，網站會再上傳
    assert m["hash"] != m["base_hash"] == "cloudhash"


def test_drop_and_forget(client, sql_topic, data_dir):
    make_topic(client, data_dir, U1, src=sql_topic.parent)
    client.post("/sync/topics/sql/mark", headers=H(U1), json={"cloud_at": "T", "hash": "h"})
    client.post("/sync/topics/sql/drop", headers=H(U1))
    assert client.get("/topics", headers=H(U1)).json()["topics"] == []
    assert client.get("/sync/manifest", headers=H(U1)).json()["topics"] == []
    assert any((data_dir / "users" / U1 / ".trash").iterdir())


def test_deleted_locally_shows_in_manifest(client, sql_topic, data_dir):
    make_topic(client, data_dir, U1, src=sql_topic.parent)
    client.post("/sync/topics/sql/mark", headers=H(U1), json={"cloud_at": "T", "hash": "h"})
    client.request("DELETE", "/topics/sql", headers=H(U1), json={"confirm": "sql"})
    m = client.get("/sync/manifest", headers=H(U1)).json()["topics"]
    assert m == [{"topic": "sql", "hash": None, "mtime": None, "base_hash": "h", "base_cloud_at": "T"}]
    client.post("/sync/topics/sql/forget", headers=H(U1))
    assert client.get("/sync/manifest", headers=H(U1)).json()["topics"] == []


def test_import_rejects_bad_bundle(client):
    r = client.post("/sync/topics/sql/import", headers=H(U1), json={"bundle": {"progress": {"x": 1}}})
    assert r.status_code == 400
    r = client.post("/sync/topics/sql/import", headers=H(U1),
                    json={"bundle": {"progress": {"nodes": []}, "lessons": {"../x": {}}}})
    assert r.status_code == 400


def test_grade_writes_to_user_folder(client, sql_topic, data_dir, llm_box):
    """登入後作答紀錄寫進自己的資料夾，不會寫到舊位置。"""
    make_topic(client, data_dir, U1, src=sql_topic.parent)
    llm_box.set([json.dumps(lesson_dict(), ensure_ascii=False)])
    q = client.post("/topics/sql/lesson", json={"ai": AI}, headers=H(U1)).json()
    llm_box.set([])
    r = client.post("/topics/sql/grade", headers=H(U1), json={
        "question_id": q["question_id"], "confidence": "high", "ai": AI,
        "answer": "SELECT name FROM suppliers WHERE rating IS NULL;",
        "exec": {"user_output": "x", "ref_output": "x", "equal": True}})
    assert r.status_code == 200, r.text
    assert (data_dir / "users" / U1 / "sql" / "journal.jsonl").is_file()
    assert not (data_dir / "sql" / "journal.jsonl").exists()


class store_user:
    def __init__(self, uid):
        self.uid = uid

    def __enter__(self):
        self.tok = store.set_user(self.uid)

    def __exit__(self, *a):
        store.reset_user(self.tok)


def test_journal_only_filter(client, sql_topic):
    rows = [{"id": "w", "at": "1", "result": "wrong", "confidence": "high", "danger": True},
            {"id": "o", "at": "2", "result": "ok", "confidence": "low", "danger": False},
            {"id": "k", "at": "3", "result": "ok", "confidence": "high", "danger": False}]
    (sql_topic.parent / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from learn3d import store as s
    assert [r["id"] for r in s.journal_read("sql", only="wrong")[0]] == ["w"]
    assert [r["id"] for r in s.journal_read("sql", only="unsure")[0]] == ["o"]
    assert [r["id"] for r in s.journal_read("sql", only="danger")[0]] == ["w"]
    assert len(s.journal_read("sql", only="nope")[0]) == 3
