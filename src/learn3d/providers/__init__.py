"""模型提供者：Claude Code / Codex CLI / Ollama（全部在使用者電腦上）＋測試用 FakeLLM。

- detect_providers()：偵測三個本機 AI 是否可用（平行偵測、結果各自快取）。
- get_provider()：取得提供者實例；未知或不可用 → ProviderError。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..schemas import ProviderId, ProviderInfo
from .base import LLMProvider, ProviderError
from .claude import ClaudeProvider
from .codex import CodexProvider
from .fake import FakeLLM
from .ollama import OllamaProvider

__all__ = [
    "LLMProvider",
    "ProviderError",
    "ClaudeProvider",
    "CodexProvider",
    "OllamaProvider",
    "FakeLLM",
    "detect_providers",
    "get_provider",
]

REAL_IDS: tuple[ProviderId, ...] = ("claude", "codex", "ollama")

_instances: dict[str, LLMProvider] = {}


def _instance(provider_id: str) -> LLMProvider:
    if provider_id not in _instances:
        cls = {"claude": ClaudeProvider, "codex": CodexProvider, "ollama": OllamaProvider,
               "fake": FakeLLM}.get(provider_id)
        if cls is None:
            raise ProviderError(f"不認得的 AI：{provider_id}", retryable=False)
        _instances[provider_id] = cls()
    return _instances[provider_id]


def detect_providers(refresh: bool = False) -> list[ProviderInfo]:
    """依序回傳 claude、codex、ollama 的偵測結果（不含 fake）。"""

    def one(pid: str) -> ProviderInfo:
        try:
            return _instance(pid).info(refresh=refresh)
        except Exception as e:  # noqa: BLE001 — 偵測失敗不能讓整個 /providers 掛掉
            return ProviderInfo(id=pid, name=pid, available=False, reason=f"偵測失敗：{e}")

    with ThreadPoolExecutor(max_workers=len(REAL_IDS)) as ex:
        return list(ex.map(one, REAL_IDS))


def get_provider(provider_id: ProviderId) -> LLMProvider:
    """取得提供者。fake 一律可用（測試 / 離線開發）；其他不可用時丟 ProviderError（附中文原因）。"""
    llm = _instance(provider_id)
    if provider_id == "fake":
        return llm
    info = llm.info()
    if not info.available:
        raise ProviderError(info.reason or f"{info.name} 目前無法使用", retryable=False)
    return llm
