"""模型提供者的抽象介面與共用工具。

所有提供者都只在使用者電腦上執行（Claude Code、Codex CLI、127.0.0.1 的 Ollama），
不呼叫任何雲端 LLM API、不讀寫任何 API key（SPEC P0-13）。
"""

from __future__ import annotations

import json
import re
import subprocess
from abc import ABC, abstractmethod
from typing import Any

from ..schemas import Effort, ProviderId, ProviderInfo

# 模型名稱會進到命令列（Codex 在 Windows 經由 shell 啟動），只允許單純的名稱
MODEL_NAME_RE = re.compile(r"^[\w.:/-]{1,80}$")


class ProviderError(RuntimeError):
    """模型服務無法使用或回傳錯誤。

    retryable=False 代表重試也沒用（例如沒安裝、沒登入），structured 會直接放棄。
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(ABC):
    id: ProviderId
    name: str

    @abstractmethod
    def chat_json(
        self,
        system: str,
        user: str,
        json_schema: dict[str, Any],
        *,
        model: str | None = None,
        effort: Effort | None = None,
    ) -> str:
        """送出一次對話，要求模型依 JSON Schema 回傳；回傳原始 JSON 字串。

        驗證與重試由 structured.py 負責，這裡只負責「拿到文字」。
        """

    @abstractmethod
    def info(self, refresh: bool = False) -> ProviderInfo:
        """回報是否可用、可選模型與推理強度。"""


# ---------------------------------------------------------------------------
# 共用工具
# ---------------------------------------------------------------------------


def check_model_name(model: str | None) -> None:
    if model and not MODEL_NAME_RE.match(model):
        raise ProviderError("模型名稱只能包含英數字與 . : / - _", retryable=False)


def json_instruction(json_schema: dict[str, Any]) -> str:
    """給沒有原生 Structured Output 的提供者（Claude / Codex）附在 system 後面的輸出規定。"""
    schema = json.dumps(json_schema, ensure_ascii=False, separators=(",", ":"))
    return (
        "\n\n【輸出格式】只輸出一個 JSON 物件，必須符合下列 JSON Schema；"
        "不要 markdown 程式碼框、不要任何前後說明文字。\n"
        f"JSON Schema：{schema}"
    )


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> str:
    """從模型輸出中取出 JSON 物件字串。

    依序嘗試：整段就是 JSON → 最後一個 ```json 區塊 → 從後往前找最後一個完整的 {...}。
    都找不到就原樣回傳（讓 Pydantic 回報錯誤、觸發重試）。
    """
    s = (text or "").strip()
    if not s:
        return s
    try:
        json.loads(s)
        return s
    except ValueError:
        pass
    fences = _FENCE_RE.findall(s)
    for block in reversed(fences):
        b = block.strip()
        try:
            json.loads(b)
            return b
        except ValueError:
            continue
    # 由左往右找能完整解析的最外層物件，保留「結束位置最後」的那一個（模型常把答案放最後）
    decoder = json.JSONDecoder()
    best: str | None = None
    best_end = -1
    i = s.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(s, i)
        except ValueError:
            i = s.find("{", i + 1)
            continue
        if isinstance(obj, dict) and end > best_end:
            best, best_end = s[i:end], end
        i = s.find("{", end)  # 跳過已解析的物件內部
    return best if best is not None else s


def run_cmd(args: list[str], *, timeout: float = 8.0, input_text: str | None = None,
            cwd: str | None = None) -> tuple[int, str, str]:
    """執行短指令，回傳 (結束碼, stdout, stderr)；找不到程式或逾時回傳結束碼 -1，不丟例外。"""
    try:
        p = subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)
    return p.returncode, p.stdout or "", p.stderr or ""
