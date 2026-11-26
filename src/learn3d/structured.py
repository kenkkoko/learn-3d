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
_last_failure = threading.local()


def last_failure() -> str:
    """這個執行緒上一次 call_structured 全部失敗時的原因摘要（給使用者看的錯誤訊息用）。"""
    return getattr(_last_failure, "text", "") or ""
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


def _resolve(node: dict, root: dict) -> dict:
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        return root.get("$defs", {}).get(ref.split("/")[-1], node)
    return node


def fit_to_schema(data, node: dict, root: dict | None = None):
    """把 AI 輸出「剪」進 Schema 的長度限制：字串超過 maxLength 就截斷、陣列超過 maxItems 就只留前面。

    便宜模型最常見的失敗是「多寫了幾個字」（例如選項說明 85 字、上限 80），整份輸出其實是好的。
    以前這種情況會重試 3 次後整個失敗（使用者看到「暫時無法產生問題」）；現在先修剪再驗證。
    只處理長度，其他錯誤（缺欄位、型別不對、pattern）照樣交給 Pydantic 判斷並重試。
    """
    root = root or node
    node = _resolve(node, root)
    if isinstance(data, str):
        mx = node.get("maxLength")
        if isinstance(mx, int) and len(data) > mx:
            return data[: max(1, mx - 1)].rstrip() + "…" if mx > 1 else data[:mx]
        return data
    if isinstance(data, list):
        items = node.get("items")
        if items is None:
            for alt in node.get("anyOf", []) or []:
                alt = _resolve(alt, root)
                if alt.get("type") == "array":
                    node, items = alt, alt.get("items")
                    break
        mx = node.get("maxItems")
        if isinstance(mx, int) and len(data) > mx:
            data = data[:mx]
        if isinstance(items, dict):
            return [fit_to_schema(x, items, root) for x in data]
        return data
    if isinstance(data, dict):
        props = node.get("properties")
        if props is None:
            alts = node.get("anyOf") or node.get("oneOf") or []
            disc = (node.get("discriminator") or {}).get("propertyName")
            for alt in alts:
                alt = _resolve(alt, root)
                if alt.get("type") != "object" and "properties" not in alt:
                    continue
                if disc:
                    want = alt.get("properties", {}).get(disc, {}).get("const")
                    if want is not None and data.get(disc) != want:
                        continue
                props = alt.get("properties")
                break
        if not props:
            return data
        return {k: fit_to_schema(v, props[k], root) if k in props else v for k, v in data.items()}
    return data


def _parse_fitted(model_cls: type[T], raw: str, schema: dict, repair: Callable[[dict], dict] | None) -> T:
    text = extract_json(raw)
    try:
        data = json.loads(text)
    except ValueError:
        return model_cls.model_validate_json(text)  # 讓 Pydantic 回報 JSON 格式錯誤
    if isinstance(data, dict):
        if repair is not None:
            data = repair(data)
        data = fit_to_schema(data, schema)
    return model_cls.model_validate(data)


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
    repair: Callable[[dict], dict] | None = None,
) -> T | None:
    """呼叫 LLM 並回傳通過驗證（含 extra_check）的 model；全部失敗回傳 None。"""
    schema = model_cls.model_json_schema()
    feedback = ""
    _last_failure.text = ""
    provider_error: ProviderError | None = None  # 最後一次失敗若是 AI 工具本身的問題（沒登入、額度用完…）
    for attempt in range(1, max_retries + 1):
        provider_error = None
        prompt = user if not feedback else _retry_prompt(user, feedback)
        t0 = time.perf_counter()
        raw = ""
        error: str | None = None
        result: T | None = None
        give_up = False
        try:
            raw = llm.chat_json(system, prompt, schema, model=ai.model, effort=ai.effort)
            result = _parse_fitted(model_cls, raw, schema, repair)
            if extra_check is not None:
                error = extra_check(result)
                if error:
                    result = None
        except ValidationError as e:
            error = f"格式驗證失敗：{summarize_validation_error(e)}"
        except ProviderError as e:
            error = str(e)
            give_up = not e.retryable
            provider_error = e
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
    _last_failure.text = f"{getattr(llm, 'name', ai.provider)}{f' / {ai.model}' if ai.model else ''}：{(feedback or '')[:200]}"
    if provider_error is not None:
        # 不是 AI 輸出格式的問題，而是 AI 工具用不了：把原因（例如「請執行 claude 登入」）交給 API 層顯示，
        # 不要變成看不出原因的「暫時無法產生」。
        raise provider_error
    return None
