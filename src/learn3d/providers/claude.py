"""Claude Code 提供者：透過 Python 版 Claude Agent SDK 呼叫使用者本機的 Claude Code。

- 使用使用者自己的 Claude Code 登入（不讀、不設定任何 API key）。
- 不給任何工具：tools=[]、allowed_tools=[]；setting_sources=[]（不載入使用者的
  settings / hooks / plugins / CLAUDE.md）；mcp_servers={} + strict_mcp_config=True
  （不載入使用者設定的 MCP 伺服器）；max_turns=1；工作目錄設在暫存資料夾。
- SDK 是 async，這裡用 asyncio 包成同步介面（FastAPI 的同步路由會在執行緒池中呼叫）。
- effort 直接對應 SDK 的 ClaudeAgentOptions.effort（CLI 的 --effort）。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Any

from ..schemas import Effort, ProviderInfo
from .base import LLMProvider, ProviderError, check_model_name, extract_json, json_instruction

ALL_EFFORTS: list[Effort] = ["low", "medium", "high", "xhigh", "max"]
FALLBACK_MODELS = ["opus", "sonnet", "haiku"]
INFO_TTL = 300.0  # 偵測結果快取秒數
DETECT_TIMEOUT = 25.0
CHAT_TIMEOUT = 600.0  # 規劃（強模型、長輸出）可能要好幾分鐘

_LOGIN_RE = re.compile(r"OAuth|authenticat|log ?in|401|invalid api key|credential", re.I)
_LIMIT_RE = re.compile(r"rate limit|usage limit|429|quota", re.I)


def _run_sync(coro_factory, timeout: float):
    """在同步程式中執行 coroutine；若目前執行緒已有事件迴圈，改在新執行緒跑。"""

    async def _with_timeout():
        return await asyncio.wait_for(coro_factory(), timeout)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_with_timeout())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, _with_timeout()).result()


def _sdk():
    try:
        import claude_agent_sdk  # noqa: PLC0415 — 延遲載入，沒裝也不影響其他提供者
    except ImportError:
        return None
    return claude_agent_sdk


def _cli_present() -> bool:
    """SDK 內建的 claude 執行檔，或 PATH 上的 claude。"""
    sdk = _sdk()
    if sdk is not None:
        bundled = os.path.join(os.path.dirname(sdk.__file__), "_bundled")
        if os.path.isfile(os.path.join(bundled, "claude.exe")) or os.path.isfile(
            os.path.join(bundled, "claude")
        ):
            return True
    return shutil.which("claude") is not None


def logged_in() -> bool | None:
    """Claude Code 有沒有登入：True / False；看不出來（macOS 存在鑰匙圈）回 None。

    依官方文件：Windows / Linux 的登入存在 ~/.claude/.credentials.json（CLAUDE_CONFIG_DIR 可改位置）；
    也可以用 ANTHROPIC_API_KEY、ANTHROPIC_AUTH_TOKEN、CLAUDE_CODE_OAUTH_TOKEN 環境變數。
    只檢查檔案在不在，不讀內容。
    """
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")):
        return True
    cfg = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    if os.path.isfile(os.path.join(cfg, ".credentials.json")):
        return True
    if sys.platform == "darwin":
        return None
    return False


def _record_usage(info) -> None:
    try:
        from .. import usage
        usage.record_claude(info)
    except Exception:  # noqa: BLE001 — 用量只是顯示用，記錄失敗不影響出題
        pass


def _note_call(result=None) -> None:
    try:
        from .. import usage
        usage.note_claude_call(getattr(result, "usage", None), getattr(result, "total_cost_usd", None))
    except Exception:  # noqa: BLE001
        pass


def friendly_error(msg: str) -> str:
    if _LOGIN_RE.search(msg):
        return f"Claude Code 的登入過期或尚未登入（{msg}）。請在終端機執行 claude，輸入 /login 登入後再試。"
    if _LIMIT_RE.search(msg):
        return f"Claude 的用量額度暫時用完了（{msg}）。請稍後再試，或改用其他模型。"
    return f"Claude Code 呼叫失敗：{msg}"


class ClaudeProvider(LLMProvider):
    id = "claude"
    name = "Claude Code"

    def __init__(self) -> None:
        self._info: ProviderInfo | None = None
        self._info_at = 0.0
        self._lock = threading.Lock()
        self._workdir = os.path.join(tempfile.gettempdir(), "learn3d-claude")

    # ---------------- 共用設定 ----------------
    def _options(self, system: str | None = None, model: str | None = None,
                 effort: Effort | None = None):
        sdk = _sdk()
        assert sdk is not None
        os.makedirs(self._workdir, exist_ok=True)
        kwargs: dict[str, Any] = dict(
            tools=[],  # 不提供任何內建工具（CLI：--tools ""）
            allowed_tools=[],
            setting_sources=[],  # 不載入 user / project / local 設定（含 hooks、plugins、CLAUDE.md）
            mcp_servers={},
            strict_mcp_config=True,  # 只用上面的空 MCP 設定，忽略使用者設定的 MCP 伺服器
            plugins=[],
            max_turns=1,
            cwd=self._workdir,
        )
        if system is not None:
            kwargs["system_prompt"] = system
        if model:
            kwargs["model"] = model
        if effort:
            kwargs["effort"] = effort
        return sdk.ClaudeAgentOptions(**kwargs)

    # ---------------- 偵測 ----------------
    def info(self, refresh: bool = False) -> ProviderInfo:
        with self._lock:
            if self._info is not None and not refresh and time.time() - self._info_at < INFO_TTL:
                return self._info
            self._info = self._detect()
            self._info_at = time.time()
            return self._info

    def _detect(self) -> ProviderInfo:
        base = dict(id="claude", name=self.name)
        sdk = _sdk()
        if sdk is None:
            return ProviderInfo(**base, available=False,
                                reason="橋接沒有安裝 claude-agent-sdk 套件（請執行 uv sync）")
        if not _cli_present():
            return ProviderInfo(**base, available=False,
                                reason="沒有找到 Claude Code（請先安裝 Claude Code 並在終端機執行 claude 登入）")
        if logged_in() is False:
            return ProviderInfo(**base, available=False, needs_login=True, models=FALLBACK_MODELS,
                                efforts=ALL_EFFORTS,
                                reason="已安裝，但還沒登入：按「登入 Claude Code」，用你自己的 Claude Pro / Max 帳號登入")

        async def _probe() -> list[dict[str, Any]]:
            # 只連線取得初始化資訊（模型清單），不送出任何提示，不花 token
            async with sdk.ClaudeSDKClient(options=self._options()) as client:
                init = await client.get_server_info() or {}
                return list(init.get("models") or [])

        try:
            raw_models = _run_sync(_probe, DETECT_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            msg = str(e) or type(e).__name__
            return ProviderInfo(**base, available=False, models=FALLBACK_MODELS,
                                efforts=ALL_EFFORTS,
                                reason=f"Claude Code 無法啟動：{msg}。請先在終端機執行 claude 並登入。")
        models: list[str] = []
        efforts: set[str] = set()
        for m in raw_models:
            v = m.get("value")
            if isinstance(v, str) and v and v not in models:
                models.append(v)
            if m.get("supportsEffort"):
                efforts.update(m.get("supportedEffortLevels") or ALL_EFFORTS)
        if not models:
            models = FALLBACK_MODELS
            efforts = set(ALL_EFFORTS)
        return ProviderInfo(**base, available=True, models=models,
                            efforts=[e for e in ALL_EFFORTS if e in efforts])

    # ---------------- 呼叫 ----------------
    def chat_json(self, system: str, user: str, json_schema: dict[str, Any], *,
                  model: str | None = None, effort: Effort | None = None) -> str:
        sdk = _sdk()
        if sdk is None:
            raise ProviderError("橋接沒有安裝 claude-agent-sdk 套件", retryable=False)
        check_model_name(model)
        if effort is not None and effort not in ALL_EFFORTS:
            effort = None
        options = self._options(system + json_instruction(json_schema), model, effort)

        async def _ask() -> str:
            texts: list[str] = []
            final: str | None = None
            failure: str | None = None
            result_msg = None
            # 不能在 async for 裡直接 raise：SDK 的產生器還在執行，會變成
            # 「aclose(): asynchronous generator is already running」，真正的原因（例如沒登入）就被蓋掉了。
            # 先記下來，讓產生器正常結束後再丟出。
            async for msg in sdk.query(prompt=user, options=options):
                rle = getattr(sdk, "RateLimitEvent", None)
                if rle is not None and isinstance(msg, rle):
                    _record_usage(msg.rate_limit_info)
                    continue
                if isinstance(msg, sdk.AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, sdk.TextBlock):
                            texts.append(block.text)
                elif isinstance(msg, sdk.ResultMessage):
                    if msg.is_error or msg.subtype != "success":
                        failure = str("; ".join(getattr(msg, "errors", None) or []) or msg.result or msg.subtype)
                    else:
                        final = msg.result
                        result_msg = msg
            if failure is not None:
                raise ProviderError(friendly_error(failure), retryable=not _LOGIN_RE.search(failure))
            _note_call(result_msg)
            return final if final else "".join(texts)

        try:
            text = _run_sync(_ask, CHAT_TIMEOUT)
        except ProviderError:
            raise
        except TimeoutError as e:
            raise ProviderError(f"Claude Code 回應逾時（{int(CHAT_TIMEOUT)} 秒）") from e
        except Exception as e:  # noqa: BLE001 — SDK 的連線 / 程序錯誤都轉成 ProviderError
            msg = str(e) or type(e).__name__
            not_found = type(e).__name__ == "CLINotFoundError"
            raise ProviderError(friendly_error(msg), retryable=not (not_found or _LOGIN_RE.search(msg))) from e
        return extract_json(text)
