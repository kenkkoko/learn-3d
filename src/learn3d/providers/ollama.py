"""Ollama 提供者：只連本機（127.0.0.1 / localhost）的 Ollama。

- 主機由環境變數 OLLAMA_HOST 指定，預設 http://127.0.0.1:11434；
  主機不是 127.0.0.1 / localhost / ::1 一律拒絕（SPEC P0-13：AI 來源只限本機）。
- POST /api/chat，format 帶入 JSON Schema（原生 Structured Output）。
- GET /api/tags 列出已下載的模型。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..schemas import Effort, ProviderInfo
from .base import LLMProvider, ProviderError, check_model_name

DEFAULT_HOST = "http://127.0.0.1:11434"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
INFO_TTL = 60.0
CHAT_TIMEOUT = 900.0
_STRONG = {"high", "xhigh", "max"}


def ollama_base_url() -> tuple[str | None, str | None]:
    """回傳 (base_url, 錯誤訊息)。主機不在本機時 base_url 為 None。"""
    raw = (os.environ.get("OLLAMA_HOST") or DEFAULT_HOST).strip()
    if "://" not in raw:  # Ollama 也接受 "127.0.0.1:11434" 這種寫法
        raw = "http://" + raw
    try:
        u = urlparse(raw)
        host = (u.hostname or "").lower()
        port = u.port or 11434
    except ValueError:
        return None, f"OLLAMA_HOST 格式不正確：{raw}"
    if u.scheme not in ("http", "https") or host not in LOCAL_HOSTS:
        return None, f"為了安全只允許本機的 Ollama（127.0.0.1 / localhost），目前 OLLAMA_HOST={raw}"
    netloc = f"[{host}]:{port}" if host == "::1" else f"{host}:{port}"
    return f"{u.scheme}://{netloc}", None


def num_ctx_for(system: str, user: str) -> int:
    """依提示長度設定 context 視窗（中文約 1 字 ≈ 0.5–1 token，另留輸出空間）。

    Ollama 預設只讀 2048–4096 tokens，超過會默默丟掉開頭（也就是 system prompt）。
    """
    need = (len(system) + len(user)) // 2 + 6144
    return min(32768, max(8192, -(-need // 1024) * 1024))


class OllamaProvider(LLMProvider):
    id = "ollama"
    name = "Ollama（本機模型）"

    def __init__(self) -> None:
        self._info: ProviderInfo | None = None
        self._info_at = 0.0
        self._sizes: dict[str, int] = {}
        self._lock = threading.Lock()

    def _tags(self, base: str) -> list[dict[str, Any]]:
        r = httpx.get(f"{base}/api/tags", timeout=3)
        r.raise_for_status()
        return list(r.json().get("models") or [])

    def info(self, refresh: bool = False) -> ProviderInfo:
        with self._lock:
            if self._info is not None and not refresh and time.time() - self._info_at < INFO_TTL:
                return self._info
            self._info = self._detect()
            self._info_at = time.time()
            return self._info

    def _detect(self) -> ProviderInfo:
        base_info = dict(id="ollama", name=self.name)
        base, err = ollama_base_url()
        if base is None:
            return ProviderInfo(**base_info, available=False, reason=err)
        try:
            tags = self._tags(base)
        except (httpx.HTTPError, ValueError):
            return ProviderInfo(**base_info, available=False,
                                reason=f"沒有偵測到 Ollama（{base}）：請確認 Ollama 有在執行")
        models = [m["name"] for m in tags if isinstance(m.get("name"), str)]
        self._sizes = {m["name"]: int(m.get("size") or 0) for m in tags if isinstance(m.get("name"), str)}
        if not models:
            return ProviderInfo(**base_info, available=False,
                                reason="Ollama 沒有已下載的模型（請先 ollama pull 一個模型）")
        return ProviderInfo(**base_info, available=True, models=models, efforts=[])

    def _default_model(self, effort: Effort | None) -> str:
        info = self.info()
        if not info.models:
            raise ProviderError(info.reason or "Ollama 沒有可用模型", retryable=False)
        if effort in _STRONG and self._sizes:
            # 強推理需求（例如規劃）：用最大的模型
            return max(info.models, key=lambda n: self._sizes.get(n, 0))
        return info.models[0]

    def chat_json(self, system: str, user: str, json_schema: dict[str, Any], *,
                  model: str | None = None, effort: Effort | None = None) -> str:
        base, err = ollama_base_url()
        if base is None:
            raise ProviderError(err or "OLLAMA_HOST 不允許", retryable=False)
        check_model_name(model)
        use_model = model or self._default_model(effort)
        body = {
            "model": use_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": json_schema,
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": num_ctx_for(system, user)},
        }
        try:
            r = httpx.post(f"{base}/api/chat", json=body, timeout=CHAT_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except httpx.ConnectError as e:
            raise ProviderError("連不到 Ollama：請確認 Ollama 有在執行", retryable=False) from e
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"Ollama 呼叫失敗：{e}") from e
        if data.get("error"):
            raise ProviderError(f"Ollama 回報錯誤：{data['error']}")
        return str((data.get("message") or {}).get("content") or "")
