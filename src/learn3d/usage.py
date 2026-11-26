"""AI 訂閱的用量（Claude / Codex 的 5 小時、每週額度）：給網站左下角即時顯示。

資料來源（都不額外花 token、不打未公開的 API）：
- Claude Code：每次呼叫時 Claude Agent SDK 會送出 RateLimitEvent（狀態、哪個時間窗、重置時間、用了多少比例）。
  橋接把最新一筆記下來。所以數字是「上一次出題／判定當下」的狀態，不是秒級即時。
- Codex CLI：Codex 會把每次對話寫進 ~/.codex/sessions/**/rollout-*.jsonl，裡面的 token_count 事件帶有
  rate_limits（primary / secondary 的 used_percent、window_minutes、重置時間）。橋接讀最新一筆（實驗性）。
- Ollama：本機模型沒有額度。

記錄存在 <資料目錄>/.usage.json，橋接重開也看得到上次的狀態。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] | None = None

WINDOW_LABEL = {
    "five_hour": "5 小時",
    "seven_day": "每週",
    "seven_day_opus": "每週（Opus）",
    "seven_day_sonnet": "每週（Sonnet）",
    "overage": "額外用量",
}


def _path() -> Path:
    root = os.environ.get("LEARN3D_DATA") or "~/.claude/learn-data"
    return Path(root).expanduser() / ".usage.json"


def _load() -> dict[str, Any]:
    global _state
    if _state is None:
        try:
            _state = json.loads(_path().read_text(encoding="utf-8"))
            if not isinstance(_state, dict):
                _state = {}
        except (OSError, ValueError):
            _state = {}
    return _state


def _save() -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass  # 記不住也不影響出題


def record_claude(info: Any) -> None:
    """記下一筆 Claude 的 RateLimitInfo（SDK 物件或 dict）。"""
    get = (lambda k: info.get(k)) if isinstance(info, dict) else (lambda k: getattr(info, k, None))
    wtype = get("rate_limit_type") or "five_hour"
    util = get("utilization")
    win = {
        "type": wtype,
        "label": WINDOW_LABEL.get(wtype, wtype),
        "status": get("status") or "allowed",
        "used_percent": round(float(util) * 100, 1) if isinstance(util, (int, float)) else None,
        "resets_at": int(get("resets_at")) if isinstance(get("resets_at"), (int, float)) else None,
        "seen_at": int(time.time()),
    }
    with _lock:
        st = _load()
        c = st.setdefault("claude", {"windows": {}})
        c["windows"][wtype] = win
        c["updated"] = int(time.time())
        _save()


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # 台灣時間的日期


def note_claude_call(tokens: dict | None = None, cost_usd: float | None = None) -> None:
    """每次成功呼叫都記：今天呼叫幾次、用了多少 token。

    Claude 不一定每次都送額度事件（通常只在狀態改變時送），所以另外記「今天的用量」，
    左下角至少看得到這個；有額度事件時再顯示 5 小時／每週的比例。
    """
    t = tokens or {}
    n = sum(int(t.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    with _lock:
        st = _load()
        c = st.setdefault("claude", {"windows": {}})
        c["updated"] = int(time.time())
        day = c.get("today") if isinstance(c.get("today"), dict) else {}
        if day.get("date") != _today():
            day = {"date": _today(), "calls": 0, "tokens": 0, "cost_usd": 0.0}
        day["calls"] += 1
        day["tokens"] += n
        if isinstance(cost_usd, (int, float)):
            day["cost_usd"] = round(day["cost_usd"] + float(cost_usd), 4)
        c["today"] = day
        _save()


# ---------------------------------------------------------------------------
# Codex：讀 ~/.codex/sessions 最新的 rate_limits
# ---------------------------------------------------------------------------

def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _newest_rollouts(limit: int = 3) -> list[Path]:
    """依「年/月/日」資料夾由新到舊找最新的幾個 rollout 檔，不掃整棵樹。"""
    base = _codex_home() / "sessions"
    out: list[Path] = []
    try:
        for y in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True):
            for m in sorted((d for d in y.iterdir() if d.is_dir()), reverse=True):
                for d in sorted((x for x in m.iterdir() if x.is_dir()), reverse=True):
                    files = sorted(d.glob("rollout-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
                    out.extend(files)
                    if len(out) >= limit:
                        return out[:limit]
    except OSError:
        pass
    return out[:limit]


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _codex_window(w: Any, now_ref: float) -> dict[str, Any] | None:
    if not isinstance(w, dict):
        return None
    used = w.get("used_percent")
    mins = w.get("window_minutes")
    resets = w.get("resets_at")
    if resets is None and isinstance(w.get("resets_in_seconds"), (int, float)):
        resets = int(now_ref + w["resets_in_seconds"])
    label = "5 小時" if mins == 300 else "每週" if mins == 10080 else (f"{round(mins / 60)} 小時" if mins else "額度")
    return {
        "type": f"window_{mins}" if mins else "window",
        "label": label,
        "status": "rejected" if isinstance(used, (int, float)) and used >= 100 else "allowed",
        "used_percent": round(float(used), 1) if isinstance(used, (int, float)) else None,
        "resets_at": int(resets) if isinstance(resets, (int, float)) else None,
        "seen_at": int(now_ref),
    }


def read_codex() -> dict[str, Any] | None:
    for f in _newest_rollouts():
        try:
            with f.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 512_000))
                lines = fh.read().decode("utf-8", "replace").splitlines()
            mtime = f.stat().st_mtime
        except OSError:
            continue
        for line in reversed(lines):
            if "rate_limits" not in line:
                continue
            try:
                rl = _find_key(json.loads(line), "rate_limits")
            except ValueError:
                continue
            if not isinstance(rl, dict):
                continue
            wins = [w for w in (_codex_window(rl.get("primary"), mtime), _codex_window(rl.get("secondary"), mtime)) if w]
            if wins:
                return {"windows": {w["type"]: w for w in wins}, "updated": int(mtime), "source": "codex-sessions"}
    return None


def snapshot() -> dict[str, Any]:
    """給 GET /usage：每個 AI 的時間窗清單（重置時間已過的視為已重置）。"""
    now = time.time()
    with _lock:
        st = json.loads(json.dumps(_load()))
    codex = read_codex()
    if codex:
        st["codex"] = codex
    out: dict[str, Any] = {}
    for pid, data in st.items():
        wins = []
        for w in (data.get("windows") or {}).values():
            w = dict(w)
            if w.get("resets_at") and w["resets_at"] <= now:
                w.update(status="allowed", used_percent=0.0 if w.get("used_percent") is not None else None,
                         resets_at=None, reset=True)
            wins.append(w)
        order = {"five_hour": 0, "window_300": 0, "seven_day": 1, "window_10080": 1}
        wins.sort(key=lambda w: order.get(w["type"], 2))
        today = data.get("today") if isinstance(data.get("today"), dict) and data["today"].get("date") == _today() else None
        out[pid] = {"windows": wins, "updated": data.get("updated"), "today": today}
    return {"providers": out, "now": int(now)}
