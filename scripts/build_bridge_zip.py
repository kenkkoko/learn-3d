"""把本機橋接打包成 frontend/downloads/learn3d-bridge.zip（網站上的「下載橋接」）。

- 只放執行橋接需要的檔案：pyproject、uv.lock、src/learn3d、Windows 啟動檔與說明
- 固定時間戳與檔案順序：內容沒變時壓縮檔位元組也不變（tests/test_bridge_zip.py 用來確認已重新打包）
- .bat / .txt 轉成 CRLF，避免 Windows 的 cmd 與記事本讀錯

用法：uv run python scripts/build_bridge_zip.py [輸出路徑]
"""

from __future__ import annotations

import json
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
    # 網頁本身也放進去：橋接會在 http://127.0.0.1:8766/ 提供（瀏覽器擋住公開網站連本機時的備援）
    front = ROOT / "frontend"
    for p in sorted(front.rglob("*")):
        rel = p.relative_to(front)
        if p.is_file() and rel.parts[0] != "downloads":
            out.append(("frontend/" + rel.as_posix(), p))
    pkg = ROOT / "src" / "learn3d"
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".txt", ".typed"}:
            out.append((p.relative_to(ROOT).as_posix(), p))
    return out


BINARY = {".png", ".ico", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".woff", ".woff2"}

VERSION_FILE = ROOT / "frontend" / "bridge-version.json"


def write_version_file() -> str:
    """網站上的「最新橋接指紋」：網站拿它和使用者橋接 /health 回報的 build 比對。"""
    sys.path.insert(0, str(ROOT / "src"))
    from learn3d.version import source_hash
    h = source_hash(ROOT / "src" / "learn3d")
    VERSION_FILE.write_bytes((json.dumps({"build": h}) + "\n").encode("utf-8"))
    return h


def build(out: Path = OUT) -> Path:
    if out == OUT:
        write_version_file()  # 先寫指紋（它也在 frontend/ 裡，會一起打包）
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, src in files():
            # 換行一律統一（git 在 Windows 取出時可能轉成 CRLF），打包結果才不因電腦而異
            data = src.read_bytes()
            if src.suffix.lower() not in BINARY:  # 圖片等二進位檔不能動（PNG 檔頭本身就含 \r\n）
                data = data.replace(b"\r\n", b"\n")
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
