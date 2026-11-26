"""網站上的橋接下載檔：必須和目前的程式碼一致，且不夾帶測試或本機資料。"""

import importlib.util
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "frontend" / "downloads" / "learn3d-bridge.zip"


def _builder():
    spec = importlib.util.spec_from_file_location("build_bridge_zip", ROOT / "scripts" / "build_bridge_zip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_zip_is_up_to_date(tmp_path):
    """改了 src/ 或啟動檔卻沒重新打包時失敗：請執行 uv run python scripts/build_bridge_zip.py"""
    fresh = _builder().build(tmp_path / "fresh.zip")
    assert ZIP.exists(), "缺少 frontend/downloads/learn3d-bridge.zip"
    assert fresh.read_bytes() == ZIP.read_bytes(), "橋接下載檔過期：請執行 uv run python scripts/build_bridge_zip.py"


def test_zip_contents():
    names = zipfile.ZipFile(ZIP).namelist()
    assert all(n.startswith("learn3d-bridge/") for n in names)
    for must in ("pyproject.toml", "uv.lock", "Start-Learn-Bridge.bat", "README.txt",
                 "src/learn3d/api.py", "src/learn3d/data/injection_patterns.txt"):
        assert "learn3d-bridge/" + must in names, must
    assert not any("/tests/" in n or ".venv" in n or "__pycache__" in n or "progress.json" in n for n in names)


def test_bat_is_ascii_with_crlf():
    data = zipfile.ZipFile(ZIP).read("learn3d-bridge/Start-Learn-Bridge.bat")
    data.decode("ascii")  # cmd 在部分系統會讀錯 UTF-8 批次檔
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
