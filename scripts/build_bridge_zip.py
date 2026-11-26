"""把本機橋接打包成 frontend/downloads/learn3d-bridge.zip（網站上的「下載橋接」）。

- 只放執行橋接需要的檔案：pyproject、uv.lock、src/learn3d、Windows 啟動檔與說明
- 固定時間戳與檔案順序：內容沒變時壓縮檔位元組也不變（tests/test_bridge_zip.py 用來確認已重新打包）
- .bat / .txt 轉成 CRLF，避免 Windows 的 cmd 與記事本讀錯

用法：uv run python scripts/build_bridge_zip.py [輸出路徑]
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "downloads" / "learn3d-bridge.zip"
FOLDER = "learn3d-bridge/"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def files() -> list[tuple[str, Path]]:
    """回傳 (壓縮檔內路徑, 來源檔) 清單，順序固定。"""
    out: list[tuple[str, Path]] = [
        ("pyproject.toml", ROOT / "pyproject.toml"),
        ("uv.lock", ROOT / "uv.lock"),
        (".python-version", ROOT / ".python-version"),
        ("README.md", ROOT / "README.md"),  # pyproject 的 readme 欄位需要
        ("README.txt", ROOT / "windows" / "README.txt"),
        ("Install-Learn-Bridge.bat", ROOT / "windows" / "Install-Learn-Bridge.bat"),
        ("Start-Learn-Bridge.bat", ROOT / "windows" / "Start-Learn-Bridge.bat"),
    ]
    pkg = ROOT / "src" / "learn3d"
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".txt", ".typed"}:
            out.append((p.relative_to(ROOT).as_posix(), p))
    return out


def build(out: Path = OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, src in files():
            # 換行一律統一（git 在 Windows 取出時可能轉成 CRLF），打包結果才不因電腦而異
            data = src.read_bytes().replace(b"\r\n", b"\n")
            if name.endswith((".bat", "README.txt")):
                data = data.replace(b"\n", b"\r\n")
            info = zipfile.ZipInfo(FOLDER + name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    tmp.replace(out)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    p = build(target)
    print(f"{p}  {p.stat().st_size / 1024:.0f} KB")
