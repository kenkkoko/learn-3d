"""橋接的程式指紋：判斷使用者安裝的橋接是不是最新版。

打包下載檔時（scripts/build_bridge_zip.py）把同一個指紋寫進網站的 frontend/bridge-version.json；
橋接在 /health 回報自己的指紋。兩者不同 → 網站提示「橋接有新版本」。
不用手動改版本號：只要 src/learn3d 的程式內容變了，指紋就會變。
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

PKG = Path(__file__).resolve().parent


def source_hash(pkg: Path = PKG) -> str:
    """src/learn3d 底下 .py 與 data/*.txt 的內容雜湊（換行統一成 LF，不受 git 在 Windows 轉 CRLF 影響）。"""
    h = hashlib.sha256()
    for p in sorted(pkg.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts or p.suffix not in {".py", ".txt"}:
            continue
        h.update(p.relative_to(pkg).as_posix().encode("utf-8") + b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()[:16]


@lru_cache(maxsize=1)
def build_id() -> str:
    """橋接啟動後只算一次。"""
    return source_hash()
