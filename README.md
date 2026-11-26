# Learn 3D｜一問一答的知識星雲

> 想學什麼都可以：程式語言、SQL、英文日文、樂理、投資觀念。系統先了解你的目標和程度，產出學習路徑，在 3D 星雲裡用顏色看見自己的熟練度；每題先自評把握度、再用自己的話回答，答錯的觀念變成迷思筆記，幾天後換個情境再考一次。

- 線上版：<https://learn.hellokai07.com>（沒啟動橋接時以示範資料瀏覽）
- AI：只用你電腦上的 Claude Code / Codex CLI / Ollama，經本機橋接呼叫，不需要也不保存任何雲端 API key

## 功能

- **學習路徑**：問 3–4 題（目標、程度、生活情境、每次時間）後，強模型一次產出 15–25 個知識節點、4–7 層的先修關係圖，每個節點附常見迷思
- **3D 知識星雲**：起點在中心、越外越進階；灰未學／紅不熟／黃半熟／綠熟練；每個節點顯示名稱；另有 2D 清單檢視
- **可調整的版面**：左側主題清單可收合；左右兩欄都能拖曳調整寬度（方向鍵也可以，雙擊恢復預設），右側問答面板可拉到畫面 75% 或一鍵加寬，寬度會記住
- **一問一答**：白話短講 → 範例逐段翻譯 → 題目用到的資料表 / 圖解 / 圖表 → 自評把握度 🟢🟡🔴 → 作答（可語音輸入）→ 判定與回饋
- **題型輪替**：預測輸出、抓錯、解釋、實作（SQL / Python / JS 在瀏覽器裡實際執行驗證）
- **作答日誌**：每題的題目、你的答案、回饋、參考答案都存在 `journal.jsonl`，右側面板「紀錄」分頁可回頭翻看
- **迷思筆記與間隔複習**：1 → 3 → 7 → 14 天，每次換情境
- **與 Claude Code 共用進度**：同一份 `~/.claude/learn-data/<主題>/progress.json`，也能用 `/learn-coach` skill 接著練

## 安裝與執行

需要：[uv](https://docs.astral.sh/uv/)，以及下列任一個 AI 工具（已登入）：Claude Code、Codex CLI、Ollama。

**一般使用者（不用 git）**：在 <https://learn.hellokai07.com> 按「下載橋接」，解壓縮後雙擊 `Start-Learn-Bridge.bat`。第一次會下載 Python 與 Claude Agent SDK（約 400 MB），之後橋接在背景執行並自動開啟網站。

**開發者**：

```powershell
git clone https://github.com/kenkkoko/learn-3d.git
cd learn-3d
uv run learn3d-bridge          # 啟動本機橋接：http://127.0.0.1:8766
```

改了 `src/` 或 `windows/` 之後要重新打包下載檔（測試會檢查）：`uv run python scripts/build_bridge_zip.py`。

環境變數：`LEARN3D_DATA`（資料目錄，預設 `~/.claude/learn-data`）、`LEARN3D_PORT`（預設 8766）、`LEARN3D_ORIGINS`（額外允許的網址，逗號分隔）、`OLLAMA_HOST`（只接受本機位址）。

## 架構

```
瀏覽器（Cloudflare Workers 靜態資產，免建置）
  index.html / app.js / scene.js（Three.js 星雲）/ runners/（sql.js、Pyodide、沙箱 iframe）
        │ fetch http://127.0.0.1:8766（Origin 白名單、Private Network Access）
        ▼
本機橋接（Python 3.12 · FastAPI）
  guardrails.py   個資遮蔽、提示注入、離題、判定引用檢查
  structured.py   JSON Schema + Pydantic 驗證，失敗重試 3 次，再失敗回預設訊息
  tasks.py        intake / plan / lesson / grade，判定後由程式校正（normalize_verdict）
  scoring.py      把握度矩陣、紅黃綠、間隔複習（純程式）
  store.py        進度檔原子寫入，相容 /learn-coach skill
  providers/      Claude Agent SDK（不給工具）/ codex exec（read-only）/ Ollama（限本機）
```

規格見 [SPEC.md](SPEC.md)，模組介面見 [docs/interfaces.md](docs/interfaces.md)。

## 設計理由

| 決策 | 理由 |
|---|---|
| **AI 只走本機橋接** | 資料不出電腦、不用 API key、用自己訂閱的額度；網頁本身不能啟動電腦裡的程式，所以需要一個只監聽 127.0.0.1 的小服務 |
| **LLM 不打分** | 分數、紅黃綠、複習間隔全部由程式計算，結果穩定可重現；AI 只回一個判定 JSON |
| **能用程式判就不呼叫 AI** | 實作題在瀏覽器執行，結果和參考答案相同就直接判對（0.16 秒、0 token）；只有標點的作答也不呼叫 AI |
| **判定後再用程式校正** | 便宜模型常把錯答判成「部分正確」。命中已知迷思或執行出錯 → 錯誤；沒做到全部要求 → 不能算正確 |
| **評分標準在出題時一起產生** | 判定時只需要「題目 + 評分標準 + 作答」，不帶對話紀錄；每次呼叫約 2,000–3,300 字元 |
| **規劃用強模型、日常用便宜模型** | 規劃一次寫好節點摘要、迷思清單；日常出題判定預設 Claude Haiku、低推理強度 |
| **短講快取** | 同一個節點的短講只生成一次 |
| **題目附資料表** | SQL 題用到的表由瀏覽器從練習資料庫直接查出來顯示，不花 token、不會抄錯 |

## 測試結果

- 單元與 API 測試：**250 項全部通過**（`uv run pytest`）
- Eval（Claude Haiku、低推理強度，`uv run python scripts/eval.py --provider claude --model haiku --effort low`）：

| 類別 | 通過 / 總數 |
|---|---|
| 通用 | 9 / 9 |
| 邊界（空白、超長、錯字、查無資料） | 11 / 11 |
| 多輪對話 | 6 / 6 |
| 對抗（注入、偽造標記、洩漏評分標準、要求執行指令） | 12 / 12 |
| 黃金樣本（回歸基準） | **30 / 30（100%，目標 ≥ 95%）** |

修正迴圈紀錄：第一輪黃金樣本 87%（Haiku 把錯答判成部分正確）→ 改判定 Prompt 並加入程式校正 → 93% → 要求命中條件必須在作答原文找得到、空作答不呼叫 AI → 100%。詳見 [eval_report.md](eval_report.md)。

## 已知限制與下一步

- **語音輸入**用瀏覽器內建辨識（Chrome / Edge），錄音會送到 Google / Microsoft 的伺服器；介面上有標示，不想傳出錄音可改用 Windows 的 Win + H。下一步：可改成本機 Whisper。
- **Claude 對話紀錄**：Claude Agent SDK 會把每次呼叫存到 `~/.claude/projects/` 底下，目前 SDK 沒有選項可關閉。
- 重新整理網頁後，不會自動還原上次沒答完的題目。
- Codex CLI 尚未實測（開發機沒有安裝）。
- 手機直式畫面上，3D 星雲中央的節點名稱會互相重疊，可用雙指縮放或改用 2D 清單。
- 本機網路存取（Local Network Access）：Chrome 從公開網址連 127.0.0.1 時可能要求授權，頁面會顯示「連線到本機橋接」按鈕；Safari、Firefox 支援不完整。
- P1：上傳自己的教材做 RAG、學習統計、匯出匯入主題。
