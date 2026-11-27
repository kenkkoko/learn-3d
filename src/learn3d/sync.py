"""跨裝置同步的本機端（雲端由網站用 Supabase 處理，橋接不碰帳號密鑰）。

網站的流程：
1. GET /sync/manifest：拿每個主題的內容指紋（hash）與「上次同步時」的指紋、雲端時間。
2. 跟雲端的 topics 表比對：
   - 只有本機改過 → 匯出（export_bundle）上傳，再 mark 記下新的同步點；
   - 只有雲端改過 → 下載後匯入（import_bundle）；
   - 兩邊都改過 → 比較新的那份為準，作答紀錄兩邊合併（只會新增、不會改）。
3. 本機刪掉的主題：manifest 裡 hash 為 None，網站把雲端也標成刪除後呼叫 forget。

同步點存在 <使用者資料夾>/.sync.json：{topic: {"hash": ..., "cloud_at": ...}}。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import store

STATE = ".sync.json"
MAX_JOURNAL = 5000


# ---------- 同步點 ----------
def _state_path() -> Path:
    return store.data_root() / STATE


def _load_state() -> dict[str, dict]:
    try:
        d = json.loads(_state_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, dict]) -> None:
    store._atomic_write_text(_state_path(), json.dumps(state, ensure_ascii=False, indent=1) + "\n")


# ---------- 讀本機內容 ----------
def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _lessons(topic: str) -> dict[str, Any]:
    d = store.topic_dir(topic) / store.LESSONS
    out: dict[str, Any] = {}
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            if not store.NODE_RE.fullmatch(f.stem):
                continue
            try:
                out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
    return out


def _journal_all(topic: str) -> list[dict]:
    # journal_read 一次最多 100 筆：這裡直接讀檔拿全部（舊到新）
    path = store.topic_dir(topic) / "journal.jsonl"
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"]:
            out.append(d)
    return out


def _canon(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(progress: dict, lessons: dict, env_sql: str | None, journal_ids: list[str]) -> str:
    """內容指紋：同樣的內容在任何電腦上算出來都一樣（不含檔案時間）。"""
    h = hashlib.sha256()
    for part in (_canon(progress), _canon(lessons), env_sql or "", "\n".join(sorted(set(journal_ids)))):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def export_bundle(topic: str) -> dict:
    """主題的完整內容（給網站上傳）。"""
    progress = store.load(topic)
    lessons = _lessons(topic)
    env_sql = store.read_env(topic)
    journal = _journal_all(topic)
    return {
        "topic": topic,
        "hash": content_hash(progress, lessons, env_sql, [e["id"] for e in journal]),
        "progress": progress,
        "lessons": lessons,
        "env_sql": env_sql,
        "journal": journal,
    }


def _local_hash(topic: str) -> str | None:
    if not store.exists(topic):
        return None
    try:
        b = export_bundle(topic)
    except (OSError, ValueError):
        return None
    return b["hash"]


def _mtime(topic: str) -> str | None:
    try:
        ts = (store.topic_dir(topic) / store.PROGRESS).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def manifest() -> dict:
    state = _load_state()
    root = store.data_root()
    names: set[str] = set(state)
    if root.is_dir():
        names |= {d.name for d in root.iterdir()
                  if d.is_dir() and store.TOPIC_RE.fullmatch(d.name) and (d / store.PROGRESS).is_file()}
    topics = []
    for t in sorted(names):
        if not store.TOPIC_RE.fullmatch(t):
            continue
        base = state.get(t) or {}
        topics.append({
            "topic": t,
            "hash": _local_hash(t),
            "mtime": _mtime(t),
            "base_hash": base.get("hash"),
            "base_cloud_at": base.get("cloud_at"),
        })
    return {"topics": topics, "legacy": len(legacy_topics()) if store.current_user() else 0}


# ---------- 寫入 ----------
def _check_bundle(topic: str, bundle: dict) -> tuple[dict, dict, str | None, list[dict]]:
    if not isinstance(bundle, dict):
        raise store.StoreError("同步資料格式不正確")
    progress = bundle.get("progress")
    if not isinstance(progress, dict) or not isinstance(progress.get("nodes"), list):
        raise store.StoreError("同步資料缺少進度")
    progress = store._normalize(progress, topic)
    lessons = bundle.get("lessons") or {}
    if not isinstance(lessons, dict):
        raise store.StoreError("同步資料的講義格式不正確")
    for k in lessons:
        store.check_node_id(k)
    env_sql = bundle.get("env_sql")
    if env_sql is not None and not isinstance(env_sql, str):
        raise store.StoreError("同步資料的練習環境格式不正確")
    journal = [e for e in (bundle.get("journal") or [])
               if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]][:MAX_JOURNAL]
    return progress, lessons, env_sql, journal


def merge_journal(topic: str, entries: list[dict]) -> int:
    """把本機沒有的紀錄併進 journal.jsonl（依時間排序重寫）；回傳新增幾筆。"""
    have = _journal_all(topic)
    ids = {e["id"] for e in have}
    new = [e for e in entries if e["id"] not in ids]
    if not new:
        return 0
    rows = sorted(have + new, key=lambda e: str(e.get("at") or ""))
    text = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in rows)
    store._atomic_write_text(store.topic_dir(topic) / "journal.jsonl", text)
    return len(new)


def import_bundle(topic: str, bundle: dict, cloud_at: str | None, journal_only: bool = False) -> dict:
    """用雲端內容覆蓋本機主題（作答紀錄合併，不會少）；journal_only＝只合併作答紀錄。"""
    store.check_topic_name(topic)
    progress, lessons, env_sql, journal = _check_bundle(topic, bundle) if not journal_only else (None, None, None, [
        e for e in (bundle.get("journal") or []) if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"]])
    d = store.topic_dir(topic)
    if not journal_only:
        if store.exists(topic):
            store.backup(topic)
        d.mkdir(parents=True, exist_ok=True)
        store.save(topic, progress)
        ld = d / store.LESSONS
        if ld.is_dir():
            for f in ld.glob("*.json"):
                if f.stem not in lessons:
                    f.unlink(missing_ok=True)
        for node, data in lessons.items():
            store._atomic_write_text(ld / f"{node}.json", store._dump(data))
        if env_sql != store.read_env(topic):
            if env_sql:
                store._atomic_write_text(d / store.ENV_SQL, env_sql)
                store._build_db(d, env_sql)
            else:
                for name in (store.ENV_SQL, store.ENV_DB):
                    (d / name).unlink(missing_ok=True)
    elif not store.exists(topic):
        raise store.TopicNotFound(f"找不到主題「{topic}」")
    added = merge_journal(topic, journal)
    h = _local_hash(topic)
    if not journal_only and cloud_at:
        # 合併後內容若跟雲端一樣（沒有新增本機紀錄），記成已同步；否則留給網站再上傳一次
        state = _load_state()
        state[topic] = {"hash": h if bundle.get("hash") in (None, h) else bundle.get("hash"), "cloud_at": cloud_at}
        _save_state(state)
    return {"topic": topic, "hash": h, "journal_added": added}


def mark(topic: str, cloud_at: str, hash_: str) -> None:
    """上傳成功後記下同步點。"""
    store.check_topic_name(topic)
    state = _load_state()
    state[topic] = {"hash": hash_, "cloud_at": cloud_at}
    _save_state(state)


def forget(topic: str) -> None:
    store.check_topic_name(topic)
    state = _load_state()
    if state.pop(topic, None) is not None:
        _save_state(state)


def drop(topic: str) -> None:
    """雲端已刪除：本機也移到 .trash（可救回），並忘掉同步點。"""
    store.check_topic_name(topic)
    if store.topic_dir(topic).is_dir():
        store.trash(topic)
    forget(topic)


# ---------- 舊資料歸屬 ----------
def legacy_topics() -> list[str]:
    """登入前就存在 <learn-data>/ 底下的主題（還沒歸到任何帳號）。"""
    root = store.base_root()
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir()
                  if d.is_dir() and d.name != store.USERS and store.TOPIC_RE.fullmatch(d.name)
                  and (d / store.PROGRESS).is_file())


def claim() -> dict:
    """把舊主題搬進目前登入的帳號資料夾；同名的已存在就保留兩份（舊的加 -old）。"""
    if not store.current_user():
        raise store.StoreError("要先登入才能把主題歸到帳號")
    src_root = store.base_root()
    dst_root = store.data_root()
    dst_root.mkdir(parents=True, exist_ok=True)
    moved = []
    for t in legacy_topics():
        dest = dst_root / t
        name = t
        if dest.exists():
            for i in range(2, 100):
                name = f"{t[:25]}-old{i if i > 2 else ''}"
                if store.TOPIC_RE.fullmatch(name) and not (dst_root / name).exists():
                    break
            dest = dst_root / name
        shutil.move(str(src_root / t), str(dest))
        moved.append(name)
    return {"moved": moved}
