"""Structured Output 呼叫：LLM → 取出 JSON → Pydantic 驗證 → 語意檢查 → 失敗重試。

流程：
1. 把 Pydantic model 轉成 JSON Schema 交給提供者（Ollama 走原生 format；Claude / Codex 寫進提示）
2. 從輸出取出 JSON 物件，用 Pydantic 驗證
3. extra_check（例如 validate_map、check_citations）回傳錯誤字串代表不通過
4. 失敗就把錯誤摘要附在下一次 user 提示中重試，最多 max_retries 次；全部失敗回傳 None
5. 每次嘗試寫一行到 <LEARN3D_DATA>/.logs/llm.jsonl（只記長度與錯誤摘要，不記提示與輸出全文）
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .providers.base import LLMProvider, ProviderError, extract_json
from .schemas import AiChoice

T = TypeVar("T", bound=BaseModel)

_log_lock = threading.Lock()
_MAX_ERR = 300


def log_path() -> Path:
    root = os.environ.get("LEARN3D_DATA") or "~/.claude/learn-data"
    return Path(root).expanduser() / ".logs" / "llm.jsonl"


def _log(record: dict) -> None:
    """寫一行紀錄；紀錄失敗絕不影響主流程。"""
    try:
        p = log_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _log_lock, p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def summarize_validation_error(e: ValidationError, limit: int = 6) -> str:
    """把 Pydantic 錯誤整理成模型看得懂的短摘要（欄位路徑：原因）。"""
    parts = []
    for err in e.errors()[:limit]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "(根)"
        parts.append(f"{loc}：{err.get('msg', '')}")
    more = len(e.errors()) - limit
    if more > 0:
        parts.append(f"另有 {more} 個錯誤")
    return "；".join(parts)


def _retry_prompt(user: str, feedback: str) -> str:
    return (
        f"{user}\n\n【上一次的輸出不合格】{feedback}\n"
        "請修正上述問題，重新輸出完整的 JSON 物件（只輸出 JSON）。"
    )


def call_structured(
    llm: LLMProvider,
    model_cls: type[T],
    system: str,
    user: str,
    *,
    task: str,
    ai: AiChoice,
    max_retries: int = 3,
    extra_check: Callable[[T], str | None] | None = None,
) -> T | None:
    """呼叫 LLM 並回傳通過驗證（含 extra_check）的 model；全部失敗回傳 None。"""
    schema = model_cls.model_json_schema()
    feedback = ""
    for attempt in range(1, max_retries + 1):
        prompt = user if not feedback else _retry_prompt(user, feedback)
        t0 = time.perf_counter()
        raw = ""
        error: str | None = None
        result: T | None = None
        give_up = False
        try:
            raw = llm.chat_json(system, prompt, schema, model=ai.model, effort=ai.effort)
            result = model_cls.model_validate_json(extract_json(raw))
            if extra_check is not None:
                error = extra_check(result)
                if error:
                    result = None
        except ValidationError as e:
            error = f"格式驗證失敗：{summarize_validation_error(e)}"
        except ProviderError as e:
            error = str(e)
            give_up = not e.retryable
        except Exception as e:  # noqa: BLE001 — 任何例外都要記錄並重試
            error = f"{type(e).__name__}: {e}"

        _log({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": task,
            "provider": getattr(llm, "id", ai.provider),
            "model": ai.model,
            "effort": ai.effort,
            "attempt": attempt,
            "max_retries": max_retries,
            "ok": result is not None,
            "error": (error or None) and error[:_MAX_ERR],
            "elapsed_ms": round((time.perf_counter() - t0) * 1000),
            "in_chars": len(system) + len(prompt),
            "out_chars": len(raw or ""),
        })
        if result is not None:
            return result
        if give_up:
            break
        feedback = (error or "未知錯誤")[:_MAX_ERR * 2]
    return None
