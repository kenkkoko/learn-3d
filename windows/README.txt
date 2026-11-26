Learn 3D 本機橋接
=================

讓 https://learn.hellokai07.com 的出題與判定使用這台電腦上的 AI：
  - Claude Code（需已安裝並登入 claude）
  - Codex CLI（需已安裝並登入 codex，實驗性）
  - Ollama（需執行 ollama 並下載模型）

安裝（Windows，只要一次）
  1. 把 learn3d-bridge.zip 解壓縮（右鍵 → 全部解壓縮）
  2. 雙擊資料夾裡的 Install-Learn-Bridge.bat
     - 沒有 uv 會詢問是否自動安裝（uv 是 Python 套件管理工具）
     - 檔案會放到 %LOCALAPPDATA%\Learn3D\bridge，第一次會下載約 400 MB
     - 會註冊 learn3d-bridge:// 連結，讓網站可以一鍵啟動橋接
     - 安裝完會自動啟動
  3. 之後每次使用：在 learn.hellokai07.com 按「啟動橋接」
     （瀏覽器第一次會問要不要開啟連結，請按允許；Chrome 若詢問存取本機網路也請允許）

不想安裝、只想試一下
  直接雙擊 Start-Learn-Bridge.bat（需要先裝好 uv）

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
  刪除 %LOCALAPPDATA%\Learn3D，並刪除登錄機碼 HKEY_CURRENT_USER\Software\Classes\learn3d-bridge
  若也要清除學習進度，刪除 %USERPROFILE%\.claude\learn-data
