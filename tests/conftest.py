"""共用 fixture：tmp 資料目錄、範例進度、合法模型工廠、FakeLLM 注入。

重要：使用者真實進度 ~/.claude/learn-data/sql/progress.json 只能「唯讀」複製到 tmp，
任何測試都不得寫入它。session 結束時會比對雜湊，確認沒被動到。
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from learn3d.schemas import (
    AiChoice,
    IntakeAnswers,
    LearningMap,
    Lesson,
    PlanRequest,
    Rubric,
    Verdict,
)

REAL_DATA = Path.home() / ".claude" / "learn-data"
REAL_SQL = REAL_DATA / "sql"
REAL_PROGRESS = REAL_SQL / "progress.json"
FIXED_DAY = date(2026, 11, 26)
LOCAL_ORIGIN = "http://localhost:5173"


def _sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


# ---------- 保護真實進度 ----------
@pytest.fixture(scope="session", autouse=True)
def _guard_real_progress():
    """整個測試期間真實進度檔不得被修改。"""
    before = {p: _sha(p) for p in (REAL_PROGRESS, REAL_SQL / "practice.sql")}
    yield
    after = {p: _sha(p) for p in before}
    assert before == after, "測試寫入了使用者真實的 learn-data！"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch) -> Path:
    """每個測試都把 LEARN3D_DATA 指到 tmp（autouse：絕不碰真實目錄）。"""
    d = tmp_path / "learn-data"
    d.mkdir()
    monkeypatch.setenv("LEARN3D_DATA", str(d))
    monkeypatch.setenv("LEARN3D_ALLOW_FAKE", "1")
    return d


# ---------- 範例進度 ----------
def synthetic_progress() -> dict:
    """真實檔不存在時使用的合成進度（結構與真實 v1 檔相同）。"""
    ids = [
        ("db-basics", 1, [], "weak", 1),
        ("select-basic", 2, ["db-basics"], "shaky", 3),
        ("create-table", 2, ["db-basics"], "unseen", 0),
        ("where-basic", 3, ["select-basic"], "shaky", 4),
        ("order-distinct", 3, ["select-basic"], "unseen", 0),
        ("logic-ops", 4, ["where-basic"], "unseen", 0),
        ("null-handling", 4, ["where-basic"], "unseen", 0),
        ("aggregate", 4, ["where-basic"], "unseen", 0),
    ]
    nodes = []
    for i, (nid, layer, pre, st, sc) in enumerate(ids):
        nodes.append({
            "id": nid, "name": f"節點{i}", "layer": layer, "prereqs": pre,
            "summary": "能說出重點", "pitfalls": ["迷思一", "迷思二"],
            "status": st, "score": sc,
            "lastSeen": "2026-11-26" if sc else None,
            "history": ([{"date": "2026-11-26", "type": "predict", "confidence": "mid", "result": "ok"}]
                        if sc else []),
        })
    return {
        "version": 1, "topic": "sql", "title": "SQL 練習", "domain": "code", "runner": "sql",
        "created": "2026-11-26",
        "learner": {"goal": "面試", "level": "零基礎", "context": "工廠 ERP", "minutes": 30, "notes": ""},
        "envNote": "suppliers(supplier_id,name,city,rating 可為 NULL)",
        "current": "where-basic",
        "nodes": nodes,
        "misconceptions": [{
            "id": "m1", "node": "where-basic", "label": "以為 NULL 會被 <> 選到",
            "wrong": "rating <> 'A' 會選到空白", "right": "NULL 要用 IS NULL",
            "scenarios": ["供應商評等 <> 'A'"], "created": "2026-11-26",
            "interval": 1, "nextReview": "2026-11-27", "hits": 1, "retired": False,
        }],
        "lastQuestion": None,
        "stats": {"answered": 7, "correct": 6, "streak": 2},
    }


PRACTICE_SQL_FALLBACK = (
    "CREATE TABLE suppliers(supplier_id INTEGER PRIMARY KEY, name TEXT, city TEXT, rating TEXT);\n"
    "INSERT INTO suppliers VALUES (1,'甲','台中','A'),(2,'乙','新竹',NULL),(3,'丙','台中','B');\n"
)


def load_sample_progress() -> dict:
    """唯讀載入真實進度（不存在則用合成版）。回傳的是複本。"""
    if REAL_PROGRESS.exists():
        return json.loads(REAL_PROGRESS.read_text(encoding="utf-8"))
    return synthetic_progress()


@pytest.fixture
def sample_progress() -> dict:
    return copy.deepcopy(load_sample_progress())


@pytest.fixture
def sql_topic(data_dir) -> Path:
    """把範例進度（含 practice.sql）複製到 tmp/learn-data/sql/，回傳 progress.json 路徑。"""
    dst = data_dir / "sql"
    dst.mkdir()
    if REAL_PROGRESS.exists():
        # shutil.copy 只讀來源，不會寫入真實檔
        shutil.copy(REAL_PROGRESS, dst / "progress.json")
        if (REAL_SQL / "practice.sql").exists():
            shutil.copy(REAL_SQL / "practice.sql", dst / "practice.sql")
    else:
        (dst / "progress.json").write_text(
            json.dumps(synthetic_progress(), ensure_ascii=False, indent=2), encoding="utf-8")
        (dst / "practice.sql").write_text(PRACTICE_SQL_FALLBACK, encoding="utf-8")
    return dst / "progress.json"


# ---------- 合法模型工廠 ----------
def make_rubric(**over) -> Rubric:
    d = {
        "answer": "SELECT * FROM suppliers WHERE rating IS NULL;",
        "accept": "IS NULL 寫法皆可，大小寫不拘",
        "must_have": ["用 IS NULL 判斷空白", "不加引號、不用 ="],
        "traps": [
            {"when": "寫成 = 'IS NULL' 或 = NULL", "label": "把 IS NULL 寫成字串"},
            {"when": "以為 <> 會選到 NULL", "label": "以為 NULL 會被 <> 選到"},
        ],
    }
    d.update(over)
    return Rubric.model_validate(d)


def lesson_dict(**over) -> dict:
    d = {
        "lecture": "NULL 代表「沒有填」，不是空字串。",
        "example": "SELECT name FROM suppliers WHERE rating IS NULL;",
        "example_explain": ["從 suppliers 表", "挑出評等沒填的供應商"],
        "type": "produce",
        "question": "請寫 SQL：列出評等（rating）沒有填的供應商名稱。",
        "hint": "空白要用 IS NULL",
        "show_tables": ["suppliers"],
        "visuals": [],
        "ref_code": "SELECT name FROM suppliers WHERE rating IS NULL;",
        "rubric": make_rubric().model_dump(),
    }
    d.update(over)
    return d


def make_lesson(**over) -> Lesson:
    return Lesson.model_validate(lesson_dict(**over))


def verdict_dict(**over) -> dict:
    d = {
        "result": "wrong",
        "met": [],
        "trap": "把 IS NULL 寫成字串",
        "feedback": "你把 IS NULL 當成文字比對了，空白要寫 rating IS NULL。",
        "citations": ["trap:把 IS NULL 寫成字串", "must_have[0]"],
        "misconception": {"wrong": "WHERE rating = 'IS NULL'", "right": "WHERE rating IS NULL"},
    }
    d.update(over)
    return d


def make_verdict(**over) -> Verdict:
    return Verdict.model_validate(verdict_dict(**over))


def map_dict(n: int = 15, **over) -> dict:
    nodes = []
    for i in range(n):
        layer = min(1 + i // 3, 7)
        nodes.append({
            "id": f"node-{i:02d}", "name": f"節點 {i}", "layer": layer,
            "prereqs": [], "summary": f"能做到第 {i} 件事", "pitfalls": ["常見誤會甲", "常見誤會乙"],
        })
    # 先修：每個節點指向上一層第一個節點（層數一定較低、無循環）
    first_of_layer: dict[int, str] = {}
    for nd in nodes:
        first_of_layer.setdefault(nd["layer"], nd["id"])
    for nd in nodes:
        if nd["layer"] > 1:
            nd["prereqs"] = [first_of_layer[nd["layer"] - 1]]
    d = {
        "title": "SQL 入門", "domain": "code", "runner": "sql",
        "env_seed": PRACTICE_SQL_FALLBACK,
        "env_note": "suppliers(supplier_id,name,city,rating)",
        "nodes": nodes, "first": "node-00",
    }
    d.update(over)
    return d


def make_map(**over) -> LearningMap:
    return LearningMap.model_validate(map_dict(**over))


def make_plan_request(slug: str = "sql-new", **over) -> PlanRequest:
    d = {
        "topic": "SQL", "slug": slug, "domain": "code",
        "answers": IntakeAnswers(goal="面試", level="零基礎", context="工廠", minutes="30").model_dump(),
        "notes": "新名詞先白話解釋", "ai": {"provider": "fake"},
    }
    d.update(over)
    return PlanRequest.model_validate(d)


FAKE_AI = AiChoice(provider="fake")


# ---------- FakeLLM 與 API client ----------
def fake_llm_cls():
    """合約 §1：FakeLLM 在 learn3d.providers（可能在子模組 fake）。"""
    mod = importlib.import_module("learn3d.providers")
    if hasattr(mod, "FakeLLM"):
        return mod.FakeLLM
    return importlib.import_module("learn3d.providers.fake").FakeLLM


class LLMBox:
    """測試中可替換的 FakeLLM：box.set([...]) 換新腳本，box.llm.calls 斷言呼叫。"""

    def __init__(self):
        self.cls = fake_llm_cls()
        self.llm = self.cls(scripted=[])
        self.history: list = []

    def set(self, scripted: list[str]):
        self.llm = self.cls(scripted=list(scripted))
        self.history.append(self.llm)
        return self.llm


@pytest.fixture
def llm_box(monkeypatch) -> LLMBox:
    box = LLMBox()
    providers = importlib.import_module("learn3d.providers")
    monkeypatch.setattr(providers, "get_provider", lambda provider_id, *a, **k: box.llm)
    return box


def _build_app():
    api = importlib.import_module("learn3d.api")
    api = importlib.reload(api)  # 重設模組層級狀態（限流計數等）
    if hasattr(api, "create_app"):
        return api, api.create_app()
    return api, api.app


@pytest.fixture
def client(llm_box, monkeypatch, data_dir):
    from fastapi.testclient import TestClient

    api, app = _build_app()
    # 若 api 以 `from .providers import get_provider` 匯入，也一併替換
    if hasattr(api, "get_provider"):
        monkeypatch.setattr(api, "get_provider", lambda provider_id, *a, **k: llm_box.llm)
    tasks = importlib.import_module("learn3d.tasks")
    if hasattr(tasks, "get_provider"):
        monkeypatch.setattr(tasks, "get_provider", lambda provider_id, *a, **k: llm_box.llm)
    with TestClient(app, headers={"Origin": LOCAL_ORIGIN}) as c:
        yield c


def err_body(resp) -> dict:
    """ErrorResponse 可能直接回傳，也可能包在 FastAPI 的 detail 裡。"""
    body = resp.json()
    if isinstance(body, dict) and isinstance(body.get("detail"), dict):
        return body["detail"]
    return body
