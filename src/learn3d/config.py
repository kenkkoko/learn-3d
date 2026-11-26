"""橋接設定（環境變數 LEARN3D_*，用 pydantic-settings 讀取）。

- LEARN3D_DATA：進度資料根目錄，預設 ~/.claude/learn-data
- LEARN3D_PORT：監聽埠，預設 8766（只監聽 127.0.0.1，不可改）
- LEARN3D_ORIGINS：額外允許的 Origin，逗號分隔；可用 `http://host:*` 表示任意埠
- LEARN3D_RATE_LIMIT / LEARN3D_RATE_WINDOW：AI 路由頻率限制（預設每 60 秒 30 次）
- LEARN3D_FRONTEND：前端靜態檔目錄（預設為專案的 frontend/）
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

HOST = "127.0.0.1"  # 只監聽本機，刻意不開放設定

DEFAULT_ORIGINS = (
    "https://learn.hellokai07.com",
    "http://localhost:*",
    "http://127.0.0.1:*",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEARN3D_", extra="ignore")

    data: Path = Path("~/.claude/learn-data")
    port: int = 8766
    origins: str = ""  # 逗號分隔（用字串避免 pydantic-settings 把 list 當 JSON 解析）
    rate_limit: int = 30
    rate_window: float = 60.0
    frontend: Path | None = None
    allow_fake: bool = False  # 只在測試開啟（LEARN3D_ALLOW_FAKE=1）

    @property
    def data_root(self) -> Path:
        return self.data.expanduser()

    @property
    def origin_patterns(self) -> list[re.Pattern[str]]:
        extra = [o.strip().rstrip("/") for o in self.origins.split(",") if o.strip()]
        return [_origin_regex(o) for o in (*DEFAULT_ORIGINS, *extra)]

    @property
    def frontend_dir(self) -> Path:
        if self.frontend is not None:
            return self.frontend.expanduser()
        # src/learn3d/config.py → 專案根目錄/frontend
        return Path(__file__).resolve().parents[2] / "frontend"


def _origin_regex(origin: str) -> re.Pattern[str]:
    """`http://localhost:*` → 允許省略埠或任意數字埠；其餘完全比對。"""
    if origin.endswith(":*"):
        base = re.escape(origin[:-2])
        return re.compile(rf"^{base}(:\d{{1,5}})?$")
    return re.compile(rf"^{re.escape(origin)}$")


def get_settings() -> Settings:
    """每次重新讀環境變數（測試可直接改 os.environ）。"""
    return Settings()
