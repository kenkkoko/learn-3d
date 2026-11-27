"""進度檔讀寫：~/.claude/learn-data/<topic>/（與 /learn-plan、/learn-coach skill 共用 v1 格式）。

- 所有寫入都在 learn-data 底下，且一律原子寫入（同目錄暫存檔 → os.replace）。
- topic 名稱一律驗證 `^[a-z0-9-]{2,30}$`，節點 id 驗證 `^[a-z0-9-]{2,40}$`，防止路徑穿越。
- 只處理檔案，不呼叫 AI、不計分。
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import get_settings
from .schemas import LearningMap, PlanRequest, Rubric, TopicSummary, Trap

TOPIC_RE = re.compile(r"^[a-z0-9-]{2,30}$")
NODE_RE = re.compile(r"^[a-z0-9-]{2,40}$")
TW = timezone(timedelta(hours=8))  # 台灣時間（不依賴系統 tzdata）

PROGRESS = "progress.json"
ENV_SQL = "practice.sql"
ENV_DB = "practice.db"
LESSONS = "lessons"
TRASH = ".trash"


class StoreError(ValueError):
    """名稱不合法等使用者可理解的錯誤（訊息為中文）。"""


class TopicNotFound(StoreError):
    pass


# ---------- 路徑 ----------
# 登入的使用者（網站在每個請求帶 X-Learn3D-User）各自一個資料夾：<learn-data>/users/<uid>/
# 沒帶（舊網站、Claude Code skill）就用 <learn-data>/ 本身，跟以前一樣。
USER_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
USERS = "users"
_current_user: ContextVar[str | None] = ContextVar("learn3d_user", default=None)


def set_user(uid: str | None):
    """設定這個請求的使用者；回傳 token 給 reset_user。uid 格式不對就當作沒有。"""
    return _current_user.set(uid if uid and USER_RE.fullmatch(uid) else None)


def reset_user(token) -> None:
    _current_user.reset(token)


def current_user() -> str | None:
    return _current_user.get()


def base_root() -> Path:
    return get_settings().data_root


def data_root() -> Path:
    uid = _current_user.get()
    return base_root() / USERS / uid if uid else base_root()


def check_topic_name(topic: str) -> str:
    if not isinstance(topic, str) or not TOPIC_RE.fullmatch(topic):
        raise StoreError("主題名稱只能是 2–30 個小寫英數字或 -")
    return topic


def check_node_id(node: str) -> str:
    if not isinstance(node, str) or not NODE_RE.fullmatch(node):
        raise StoreError("節點 id 只能是 2–40 個小寫英數字或 -")
    return node


def topic_dir(topic: str) -> Path:
    return data_root() / check_topic_name(topic)


def _today() -> date:
    return datetime.now(TW).date()


# ---------- 原子寫入 ----------
def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


# ---------- 讀取與補預設 ----------
def _normalize(p: dict, topic: str) -> dict:
    """舊檔缺欄位時補預設值（只加不改）。"""
    p.setdefault("version", 1)
    p.setdefault("topic", topic)
    p.setdefault("title", topic)
    p.setdefault("domain", "concept")
    p.setdefault("runner", None)
    p.setdefault("created", _today().isoformat())
    learner = p.get("learner")
    if not isinstance(learner, dict):
        learner = {}
        p["learner"] = learner
    for k, v in (("goal", ""), ("level", ""), ("context", ""), ("minutes", 30), ("notes", "")):
        learner.setdefault(k, v)
    p.setdefault("envNote", None)
    nodes = p.get("nodes")
    if not isinstance(nodes, list):
        nodes = []
        p["nodes"] = nodes
    for n in nodes:
        n.setdefault("prereqs", [])
        n.setdefault("summary", "")
        n.setdefault("pitfalls", [])
        n.setdefault("layer", 1)
        n.setdefault("status", "unseen")
        n.setdefault("score", 0)
        n.setdefault("lastSeen", None)
        n.setdefault("history", [])
    if not p.get("current") and nodes:
        p["current"] = _first_node(nodes)
    p.setdefault("current", None)
    mis = p.get("misconceptions")
    if not isinstance(mis, list):
        mis = []
        p["misconceptions"] = mis
    for m in mis:
        m.setdefault("scenarios", [])
        m.setdefault("created", p["created"])
        m.setdefault("interval", 1)
        m.setdefault("nextReview", m["created"])
        m.setdefault("hits", 1)
        m.setdefault("retired", False)
    p.setdefault("lastQuestion", None)
    stats = p.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        p["stats"] = stats
    for k in ("answered", "correct", "streak"):
        stats.setdefault(k, 0)
    return p


def _first_node(nodes: list[dict]) -> str | None:
    if not nodes:
        return None
    return min(nodes, key=lambda n: n.get("layer", 1))["id"]


def exists(topic: str) -> bool:
    try:
        return (topic_dir(topic) / PROGRESS).is_file()
    except StoreError:
        return False


def load(topic: str) -> dict:
    path = topic_dir(topic) / PROGRESS
    if not path.is_file():
        raise TopicNotFound(f"找不到主題「{topic}」")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise StoreError(f"主題「{topic}」的進度檔格式不正確")
    return _normalize(data, topic)


def save(topic: str, progress: dict) -> None:
    _atomic_write_text(topic_dir(topic) / PROGRESS, _dump(progress))


def list_topics(day: date | None = None) -> list[TopicSummary]:
    """列出所有主題（略過 .trash 與壞掉的檔案），依最後修改時間新到舊。"""
    root = data_root()
    if not root.is_dir():
        return []
    today = (day or _today()).isoformat()
    out: list[TopicSummary] = []
    for d in root.iterdir():
        if not d.is_dir() or not TOPIC_RE.fullmatch(d.name):
            continue
        path = d / PROGRESS
        if not path.is_file():
            continue
        try:
            p = load(d.name)
        except (OSError, ValueError):
            continue
        domain = p.get("domain")
        out.append(
            TopicSummary(
                topic=d.name,
                title=str(p.get("title") or d.name),
                domain=domain if domain in ("code", "language", "concept", "skill") else None,
                nodes=len(p["nodes"]),
                mastered=sum(1 for n in p["nodes"] if n.get("status") == "mastered"),
                due=sum(
                    1
                    for m in p["misconceptions"]
                    if not m.get("retired") and str(m.get("nextReview") or "") <= today
                ),
                updated=path.stat().st_mtime,
            )
        )
    out.sort(key=lambda s: s.updated, reverse=True)
    return out


# ---------- 建立主題 ----------
def _parse_minutes(text: str) -> int:
    m = re.search(r"\d+", text or "")
    return int(m.group()) if m else 30


def create_topic(req: PlanRequest, m: LearningMap, keep_from: dict | None = None) -> dict:
    """依 PlanRequest + LearningMap 產生 v1 進度並寫檔（含 practice.sql / practice.db）。

    keep_from：重新規劃且保留時傳入舊進度；同 id 節點保留 status/score/lastSeen/history，
    misconceptions 只保留節點仍存在者，stats 沿用。
    """
    topic = check_topic_name(req.slug)
    today = _today().isoformat()
    old_nodes = {n["id"]: n for n in (keep_from or {}).get("nodes", []) if isinstance(n, dict)}
    nodes = []
    for mn in m.nodes:
        n = {
            "id": mn.id,
            "name": mn.name,
            "layer": mn.layer,
            "prereqs": list(mn.prereqs),
            "summary": mn.summary,
            "pitfalls": list(mn.pitfalls),
            "status": mn.status,
            "score": 0,
            "lastSeen": None,
            "history": [],
        }
        old = old_nodes.get(mn.id)
        if old is not None:
            for k in ("status", "score", "lastSeen", "history"):
                if k in old:
                    n[k] = copy.deepcopy(old[k])
        nodes.append(n)
    ids = {n["id"] for n in nodes}
    misconceptions: list[dict] = []
    stats = {"answered": 0, "correct": 0, "streak": 0}
    created = today
    if keep_from:
        misconceptions = [
            copy.deepcopy(x) for x in keep_from.get("misconceptions", []) if x.get("node") in ids
        ]
        stats.update({k: v for k, v in (keep_from.get("stats") or {}).items() if k in stats})
        created = keep_from.get("created") or today

    current = m.first if m.first in ids else _first_node(nodes)
    if keep_from and keep_from.get("current") in ids:
        current = keep_from["current"]

    progress = {
        "version": 1,
        "topic": topic,
        "title": m.title,
        "domain": m.domain,
        "runner": m.runner,
        "created": created,
        "learner": {
            "goal": req.answers.goal,
            "level": req.answers.level,
            "context": req.answers.context,
            "minutes": _parse_minutes(req.answers.minutes),
            "notes": req.notes or "",
        },
        "envNote": m.env_note,
        "current": current,
        "nodes": nodes,
        "misconceptions": misconceptions,
        "lastQuestion": None,
        "stats": stats,
    }

    d = topic_dir(topic)
    d.mkdir(parents=True, exist_ok=True)
    if m.env_seed:
        _atomic_write_text(d / ENV_SQL, m.env_seed)
        _build_db(d, m.env_seed)
    else:
        for name in (ENV_SQL, ENV_DB):
            try:
                (d / name).unlink()
            except FileNotFoundError:
                pass
    save(topic, progress)
    return progress


def _build_db(d: Path, seed: str) -> None:
    """依 practice.sql 建 practice.db（給 /learn-coach 的 learn_tool.py 用）；建不起來就略過。"""
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".practice.", suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(tmp)
        try:
            con.executescript(seed)
            con.commit()
        finally:
            con.close()
        os.replace(tmp, d / ENV_DB)
    except (sqlite3.Error, OSError):
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_env(topic: str) -> str | None:
    path = topic_dir(topic) / ENV_SQL
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


# ---------- 短講快取 ----------
def _lesson_path(topic: str, node: str) -> Path:
    return topic_dir(topic) / LESSONS / f"{check_node_id(node)}.json"


def lecture_cache_get(topic: str, node: str) -> dict | None:
    path = _lesson_path(topic, node)
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def lecture_cache_put(topic: str, node: str, data: dict) -> None:
    _atomic_write_text(_lesson_path(topic, node), _dump(data))


def lecture_cache_clear(topic: str, keep: set[str] | None = None) -> None:
    """清掉短講快取；keep 內的節點保留（重新規劃時用）。"""
    d = topic_dir(topic) / LESSONS
    if not d.is_dir():
        return
    for f in d.glob("*.json"):
        if keep is None or f.stem not in keep:
            f.unlink(missing_ok=True)


# ---------- 刪除 / 重設 / 備份 ----------
# ---------- 作答日誌（journal.jsonl：一行一筆，只附加不改寫） ----------
def _journal_path(topic: str) -> Path:
    return topic_dir(topic) / "journal.jsonl"


def journal_append(topic: str, entry: dict) -> None:
    """附加一筆日誌。單行 JSON + 換行，一次 write，不會和既有內容交錯。"""
    path = _journal_path(topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)


JOURNAL_FILTERS = {
    "wrong": lambda r: r.get("result") in ("wrong", "partial"),
    "unsure": lambda r: r.get("confidence") in ("low", "mid"),
    "danger": lambda r: bool(r.get("danger")),
}


def journal_read(topic: str, limit: int = 30, before: str | None = None,
                 node: str | None = None, only: str | None = None) -> tuple[list[dict], bool]:
    """最新的在前。before＝上一頁最後一筆的 id（往更早翻）；node＝只看某節點。

    壞掉的行（例如被手動編輯）直接略過，不讓整個紀錄打不開。
    """
    path = _journal_path(topic)
    if not path.exists():
        return [], False
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("id"):
            rows.append(d)
    rows.reverse()
    if node:
        rows = [r for r in rows if r.get("node") == node]
    if only in JOURNAL_FILTERS:
        rows = [r for r in rows if JOURNAL_FILTERS[only](r)]
    if before:
        idx = next((i for i, r in enumerate(rows) if r.get("id") == before), None)
        rows = rows[idx + 1:] if idx is not None else []
    limit = max(1, min(limit, 100))
    return rows[:limit], len(rows) > limit


def _stamp() -> str:
    return datetime.now(TW).strftime("%Y%m%d%H%M%S")


def trash(topic: str) -> None:
    src = topic_dir(topic)
    if not src.is_dir():
        raise TopicNotFound(f"找不到主題「{topic}」")
    dest_dir = data_root() / TRASH
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{topic}-{_stamp()}"
    i = 1
    while dest.exists():
        dest = dest_dir / f"{topic}-{_stamp()}-{i}"
        i += 1
    shutil.move(str(src), str(dest))


def backup(topic: str) -> None:
    """把目前的 progress.json 複製成 progress.json.bak（原子寫入）。"""
    d = topic_dir(topic)
    src = d / PROGRESS
    if src.is_file():
        _atomic_write_text(d / (PROGRESS + ".bak"), src.read_text(encoding="utf-8"))


def reset(topic: str) -> dict:
    """清空學習紀錄（保留地圖、學習者資料與練習環境），先備份 progress.json.bak。"""
    p = load(topic)
    backup(topic)
    for n in p["nodes"]:
        n["status"] = "unseen"
        n["score"] = 0
        n["lastSeen"] = None
        n["history"] = []
    p["misconceptions"] = []
    p["lastQuestion"] = None
    p["stats"] = {"answered": 0, "correct": 0, "streak": 0}
    p["current"] = _first_node(p["nodes"])
    save(topic, p)
    return p


# ---------- 評分標準格式轉換 ----------
def rubric_to_file(r: Rubric) -> dict:
    return {
        "answer": r.answer,
        "accept": r.accept,
        "mustHave": list(r.must_have),
        "traps": [{"if": t.when, "label": t.label} for t in r.traps],
    }


def rubric_from_file(d: dict) -> Rubric:
    """skill 格式 → Rubric。skill 手寫的資料可能超長或缺欄位，這裡截斷並補值以通過驗證。"""
    d = d or {}
    must = [str(x)[:300] for x in (d.get("mustHave") or d.get("must_have") or []) if str(x).strip()]
    traps = []
    for t in (d.get("traps") or [])[:3]:
        if not isinstance(t, dict):
            continue
        when = str(t.get("if") or t.get("when") or "").strip()[:120]
        label = str(t.get("label") or "").strip()[:40]
        if when and label:
            traps.append(Trap(when=when, label=label))
    answer = str(d.get("answer") or "").strip()[:800] or "（沒有參考答案）"
    return Rubric(
        answer=answer,
        accept=str(d.get("accept") or "")[:300],
        must_have=must[:3] or ["觀念與參考答案一致"],
        traps=traps,
    )


def public_progress(p: dict) -> dict:
    out = copy.deepcopy(p)
    lq = out.get("lastQuestion")
    if isinstance(lq, dict):
        lq.pop("rubric", None)
    return out
