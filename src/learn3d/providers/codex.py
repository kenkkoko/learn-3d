"""Codex CLI 提供者：`codex exec --sandbox read-only --skip-git-repo-check`。

- 使用使用者本機 Codex CLI 的登入（不讀、不設定任何 API key）。
- 唯讀沙箱：Codex 不能寫檔、不能改電腦；工作目錄設在暫存資料夾。
- 題目由 stdin 傳入（避免命令列長度限制與引號問題）；輸出取最後一個 JSON 物件。
- 預設模型讀 ~/.codex/config.toml 的 model；effort 用 `-c model_reasoning_effort=<low|medium|high>`。
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..schemas import Effort, ProviderInfo
from .base import LLMProvider, ProviderError, check_model_name, extract_json, json_instruction, run_cmd

CODEX_EFFORTS: list[Effort] = ["low", "medium", "high"]
INFO_TTL = 300.0
CHAT_TIMEOUT = 900.0
_EFFORT_MAP: dict[str, str] = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
_LOGIN_RE = re.compile(r"login|log in|auth|401|unauthori", re.I)


def read_codex_default_model() -> str | None:
    """讀 ~/.codex/config.toml 頂層的 model = "..."（不解析整個 TOML，只抓這一行）。"""
    cfg = Path.home() / ".codex" / "config.toml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    # 只看第一個 [table] 之前的頂層設定
    top = re.split(r"^\s*\[", text, maxsplit=1, flags=re.M)[0]
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"', top, re.M)
    return m.group(1) if m else None


class CodexProvider(LLMProvider):
    id = "codex"
    name = "Codex CLI"

    def __init__(self) -> None:
        self._info: ProviderInfo | None = None
        self._info_at = 0.0
        self._lock = threading.Lock()
        self._workdir = os.path.join(tempfile.gettempdir(), "learn3d-codex")

    def _exe(self) -> str | None:
        return shutil.which("codex")

    def info(self, refresh: bool = False) -> ProviderInfo:
        with self._lock:
            if self._info is not None and not refresh and time.time() - self._info_at < INFO_TTL:
                return self._info
            self._info = self._detect()
            self._info_at = time.time()
            return self._info

    def _detect(self) -> ProviderInfo:
        base = dict(id="codex", name=self.name)
        exe = self._exe()
        if not exe:
            return ProviderInfo(**base, available=False, reason="沒有找到 Codex CLI（請先安裝並執行 codex login）")
        code, out, err = run_cmd([exe, "--version"], timeout=10)
        if code != 0:
            return ProviderInfo(**base, available=False,
                                reason=f"Codex CLI 無法執行：{(err or out).strip()[:200] or '未知錯誤'}")
        configured = read_codex_default_model()
        return ProviderInfo(**base, available=True, models=[configured] if configured else [],
                            efforts=CODEX_EFFORTS)

    def _build_args(self, exe: str, model: str | None, effort: Effort | None,
                    last_msg_file: str | None) -> list[str]:
        args = [exe, "exec", "--sandbox", "read-only", "--skip-git-repo-check"]
        if model:
            args += ["-m", model]
        if effort:
            args += ["-c", f"model_reasoning_effort={_EFFORT_MAP[effort]}"]
        if last_msg_file:
            args += ["--output-last-message", last_msg_file]
        args.append("-")  # 從 stdin 讀題目
        return args

    def chat_json(self, system: str, user: str, json_schema: dict[str, Any], *,
                  model: str | None = None, effort: Effort | None = None) -> str:
        exe = self._exe()
        if not exe:
            raise ProviderError("沒有找到 Codex CLI", retryable=False)
        check_model_name(model)
        if effort is not None and effort not in _EFFORT_MAP:
            effort = None
        os.makedirs(self._workdir, exist_ok=True)
        prompt = f"{system}{json_instruction(json_schema)}\n\n---\n\n{user}"

        fd, last_file = tempfile.mkstemp(prefix="learn3d-codex-", suffix=".txt")
        os.close(fd)
        try:
            code, out, err = run_cmd(self._build_args(exe, model, effort, last_file),
                                     timeout=CHAT_TIMEOUT, input_text=prompt, cwd=self._workdir)
            if code != 0 and "output-last-message" in err:
                # 舊版 codex 不支援 --output-last-message：改從 stdout 取
                code, out, err = run_cmd(self._build_args(exe, model, effort, None),
                                         timeout=CHAT_TIMEOUT, input_text=prompt, cwd=self._workdir)
            try:
                last = Path(last_file).read_text(encoding="utf-8").strip()
            except OSError:
                last = ""
        finally:
            try:
                os.remove(last_file)
            except OSError:
                pass

        if code != 0:
            detail = (err or out).strip()[-400:] or f"codex 結束碼 {code}"
            if _LOGIN_RE.search(detail):
                raise ProviderError(f"Codex 的登入過期或尚未登入（{detail}）。請在終端機執行 codex login。",
                                    retryable=False)
            raise ProviderError(f"Codex 呼叫失敗：{detail}")
        return extract_json(last or out)
