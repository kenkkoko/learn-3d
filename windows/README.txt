Learn 3D 本機橋接
=================

讓 https://learn.hellokai07.com 的出題與判定使用這台電腦上的 AI：
  - Claude Code（需已安裝並登入 claude）
  - Codex CLI（需已安裝並登入 codex，實驗性）
  - Ollama（需執行 ollama 並下載模型）

安裝與啟動（Windows）
  1. 安裝 uv（只要一次）：開啟 PowerShell，執行
       powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  2. 把 learn3d-bridge.zip 解壓縮到你想放的資料夾（例如「文件」）
  3. 雙擊資料夾裡的 Start-Learn-Bridge.bat
     - 第一次會下載 Python 與 Claude Agent SDK（約 400 MB），需要幾分鐘
     - 啟動後會縮小成一個視窗在背景執行，並自動開啟 Learn 3D 網站
  4. 網站上按「重新偵測」；Chrome 若詢問「允許存取本機網路」請按允許

停止
  關掉工作列上縮小的「Learn 3D Bridge」視窗

macOS / Linux
  在這個資料夾執行：
    uv run learn3d-bridge
  然後保持終端機開著。

你的資料放在哪裡
  學習進度：%USERPROFILE%\.claude\learn-data\<主題>\progress.json
  （和 Claude Code 的 /learn-coach skill 共用；刪除這個資料夾就會清除所有進度）

安全
  - 只監聽 127.0.0.1，只接受 learn.hellokai07.com 與 localhost 網頁的請求
  - AI 不能使用任何工具（不能讀寫檔案、不能執行指令），只回答文字
  - 不需要也不保存任何 API key；出題與判定會計入你自己的 Claude / OpenAI 用量
  - 執行紀錄：這個資料夾的 bridge.log

移除
  刪除這個資料夾；若也要清除學習進度，刪除 %USERPROFILE%\.claude\learn-data
