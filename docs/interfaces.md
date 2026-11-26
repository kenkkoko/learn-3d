# 模組介面合約

資料模型一律在 `src/learn3d/schemas.py`（不可擅自改欄位；需要改時回報統整者）。
套件：`src/learn3d/`，Python 3.12，以 `uv run` 執行。全部繁體中文註解與訊息。

## 0. 共用約定
- 資料根目錄：環境變數 `LEARN3D_DATA`，預設 `~/.claude/learn-data`。測試一律把它指到 tmp。
- 日期：台灣時間 `date`，以 `scoring.today()` 取得（測試可傳入固定日期）。
- 進度檔 `progress.json` 是 dict（v1 格式，見下方 §6），欄位名稱沿用 skill 的 camelCase。
- 不得出現任何雲端 LLM API 端點或 API key（SPEC P0-13）。

## 1. providers/（負責：子代理 A）
```python
# providers/base.py
class ProviderError(RuntimeError): ...
class LLMProvider(ABC):
    id: ProviderId
    name: str
    @abstractmethod
    def chat_json(self, system: str, user: str, json_schema: dict, *,
                  model: str | None, effort: Effort | None) -> str: ...   # 回傳原始 JSON 字串
    @abstractmethod
    def info(self, refresh: bool = False) -> ProviderInfo: ...

# providers/__init__.py
def detect_providers(refresh: bool = False) -> list[ProviderInfo]   # claude, codex, ollama（不含 fake）
def get_provider(provider_id: ProviderId) -> LLMProvider              # 未知或不可用 → ProviderError
```
- `ClaudeProvider`：`claude-agent-sdk`，`allowed_tools=[]`、不載入任何 MCP/設定的工具、`max_turns=1`；用使用者本機登入。
- `CodexProvider`：`codex exec --sandbox read-only --skip-git-repo-check`，模型讀 `~/.codex/config.toml`，effort 對應 `-c model_reasoning_effort=...`。
- `OllamaProvider`：`OLLAMA_HOST`（預設 `http://127.0.0.1:11434`），`/api/chat` 帶 `format=json_schema`；只允許 127.0.0.1 / localhost 主機。
- `FakeLLM(scripted: list[str] | None = None)`：依序回傳 scripted 字串；用完時回傳依 schema 產生的最小合法實例。`calls: list[dict]` 記錄每次的 system/user（測試用來斷言「沒有呼叫 AI」）。

## 2. structured.py（負責：子代理 A）
```python
def call_structured(llm: LLMProvider, model_cls: type[T], system: str, user: str, *,
                    task: str, ai: AiChoice, max_retries: int = 3,
                    extra_check: Callable[[T], str | None] | None = None) -> T | None
```
- 驗證失敗把錯誤摘要附在下一次 user 提示重試；全部失敗回 `None`。
- 每次嘗試寫一行到 `<LEARN3D_DATA>/.logs/llm.jsonl`（task、provider、attempt、ok、error、elapsed_ms、輸入輸出字元數，**不寫全文**）。

## 3. prompts.py / tasks.py（負責：子代理 A）
```python
# tasks.py —— 都只回傳模型或 None，不寫檔
def run_intake(llm, ai, topic: str) -> Intake | None
def run_plan(llm, ai, req: PlanRequest) -> LearningMap | None      # extra_check = validate_map
def validate_map(m: LearningMap) -> str | None                     # id 唯一、先修存在且層數較低、無循環、first 存在、env_seed 可建 SQLite
def run_lesson(llm, ai, ctx: LessonContext) -> Lesson | None
def run_grade(llm, ai, ctx: GradeContext) -> Verdict | None         # extra_check = guardrails.check_citations

@dataclass
class LessonContext:
    domain: Domain; runner: Runner | None
    node: dict                 # progress 裡的節點 dict（name, summary, pitfalls, history）
    learner: dict              # goal, level, context, minutes, notes
    env_note: str | None
    recent_types: list[QuestionType]
    want_type: QuestionType    # scoring.choose_qtype 決定
    need_lecture: bool         # 有快取或 history 非空時為 False
    review: dict | None        # 複習時的迷思 dict（label, wrong, right, scenarios）

@dataclass
class GradeContext:
    question: str; qtype: QuestionType; rubric: Rubric
    answer: str                # 已遮蔽個資
    exec: ExecResult | None
    learner_notes: str | None
```
- 提示：system 固定開頭（利於 prompt cache）；使用者作答以 `guardrails.wrap_untrusted()` 包起來。
- Lesson 提示必須要求：新名詞先白話解釋、`example_explain` 逐段翻譯、SQL 題填 `show_tables`、需要時給 `visuals`、runner 主題給 `ref_code`。

## 4. guardrails.py / scoring.py（負責：子代理 B）
```python
# guardrails.py
def mask_pii(text: str) -> str
def detect_injection(text: str) -> bool
def wrap_untrusted(text: str) -> str            # 包成資料區塊並移除可偽造的區塊標記
def is_offtopic(answer: str, question: str) -> bool
def check_topic(topic: str) -> str | None       # 不是學習主題 / 太長 / 注入 → 回錯誤訊息
def check_answer(answer: str) -> str | None     # 空白、超長（> 2000 字；程式 > 5000）
def check_citations(v: Verdict, rubric: Rubric, has_exec: bool) -> str | None

# scoring.py —— 純函式，輸入 progress dict，回傳新的 dict（不就地修改），不碰檔案
def today() -> date
def choose_qtype(history: list[dict], domain: Domain) -> QuestionType
def pick_next_node(progress: dict) -> str
def due_misconceptions(progress: dict, day: date) -> list[dict]
def apply_grade(progress: dict, node_id: str, qtype: QuestionType, confidence: Confidence,
                result: Result, day: date) -> tuple[dict, GradeOutcome]
def record_misconception(progress: dict, node_id: str, label: str, wrong: str, right: str,
                         scenario: str, day: date) -> tuple[dict, str]       # 回傳 (新 progress, 迷思 id)
def apply_review(progress: dict, mis_id: str, result: Result, day: date) -> dict

@dataclass
class GradeOutcome:
    delta: int; score: int; status: NodeStatus; danger: bool
```
規則照 SPEC §4.6（分數 0–6；weak ≤1、shaky 2–4、mastered ≥5 且最近 3 次至少 2 種題型答對；unjudgeable 不改分；間隔 1→3→7→14，14 天答對退役，答錯回 1 天並扣節點 1 分）。

## 5. store.py / api.py（負責：子代理 C）
```python
# store.py
def data_root() -> Path
def list_topics() -> list[TopicSummary]
def exists(topic: str) -> bool
def load(topic: str) -> dict
def save(topic: str, progress: dict) -> None            # 原子寫入（暫存檔 → os.replace）
def create_topic(req: PlanRequest, m: LearningMap, keep_from: dict | None = None) -> dict
def read_env(topic: str) -> str | None                  # practice.sql
def lecture_cache_get(topic: str, node: str) -> dict | None
def lecture_cache_put(topic: str, node: str, data: dict) -> None
def trash(topic: str) -> None                           # 移到 .trash/<topic>-<時間戳>
def reset(topic: str) -> dict                           # 先備份 progress.json.bak
def rubric_to_file(r: Rubric) -> dict                   # → {"answer","accept","mustHave","traps":[{"if","label"}]}
def rubric_from_file(d: dict) -> Rubric
def public_progress(p: dict) -> dict                    # 移除 lastQuestion.rubric
def journal_append(topic: str, entry: dict) -> None     # journal.jsonl 附加一行
def journal_read(topic, limit=30, before=None, node=None) -> tuple[list[dict], bool]
```
`api.py`（FastAPI）路由：

| 方法 | 路徑 | 請求 | 回應 |
|---|---|---|---|
| GET | `/health` | — | `{ok, name:"learn3d-bridge", version}` |
| GET | `/providers?refresh=1` | — | `ProvidersResponse` |
| GET | `/topics` | — | `TopicsResponse` |
| GET | `/topics/{t}` | — | `public_progress(progress)` |
| GET | `/topics/{t}/env` | — | `{runner, sql}`（practice.sql 原文；無則 null）|
| POST | `/intake` | `IntakeRequest` | `Intake` |
| POST | `/plan` | `PlanRequest` | `{topic, progress}`；已存在且 `confirm=false` → 409 `ErrorResponse(needs_confirm=true)` |
| POST | `/topics/{t}/lesson` | `LessonRequest` | `PublicLesson` |
| POST | `/topics/{t}/grade` | `GradeRequest` | `GradeResponse` |
| GET | `/topics/{t}/journal?limit=30&before=<id>&node=<id>` | — | `JournalResponse`（最新在前；`before` 往更早翻頁）|
| POST | `/topics/{t}/jump` | `JumpRequest` | `{current}` |
| POST | `/topics/{t}/reset` | `ConfirmRequest(confirm="reset")` | `{progress}` |
| DELETE | `/topics/{t}` | `ConfirmRequest(confirm=<t>)` | `{ok}` |

- 只監聽 `127.0.0.1:8766`（`LEARN3D_PORT`）。Origin 白名單：`https://learn.hellokai07.com`、`http://localhost:*`、`http://127.0.0.1:*`（`LEARN3D_ORIGINS` 可加）；不在白名單 → 403。回應帶 CORS 與 `Access-Control-Allow-Private-Network: true`。
- AI 呼叫路由每分鐘最多 30 次（429）。
- `GET /` 及靜態檔：若 `frontend/` 存在則掛在 `/`，讓使用者也能直接開 `http://127.0.0.1:8766/`。
- grade 流程：`check_answer` → `is_offtopic`（是則回 `offtopic=true`，不改進度）→ `exec.equal` 為真則直接 `Verdict(result="ok", citations=["exec"])` **不呼叫 AI** → 否則 `mask_pii` + `run_grade` → `None` 則 `fallback=true` 不改進度 → 新題跑 `scoring.apply_grade`；**複習題（kind=review）只跑 `scoring.apply_review`，不跑 apply_grade**（避免答錯扣兩次分）→ 有迷思則 `record_misconception` → `save`。
- lesson 流程：決定節點（`node` / `review` / `pick_next_node`）→ 讀短講快取 → `run_lesson` → `None` 回 503 預設訊息 → 存 `lastQuestion`（含 `question_id`、rubric 用 `rubric_to_file`）與短講快取 → 回 `PublicLesson`。
- `learn3d.main()`：啟動 uvicorn；Windows 另附 `windows/Start-Learn-Bridge.bat`。

## 6. 進度檔 v1（與 skill 相容）
```json
{ "version": 1, "topic": "sql", "title": "...", "domain": "code", "runner": "sql",
  "created": "YYYY-MM-DD",
  "learner": { "goal": "", "level": "", "context": "", "minutes": 30, "notes": "" },
  "envNote": "...", "current": "<node id>",
  "nodes": [ { "id","name","layer","prereqs","summary","pitfalls",
               "status","score","lastSeen","history":[{"date","type","confidence","result"}] } ],
  "misconceptions": [ { "id","node","label","wrong","right","scenarios","created",
                        "interval","nextReview","hits","retired" } ],
  "lastQuestion": { "id", "node","type","kind","misconceptionId","question","askedAt",
                    "rubric": {"answer","accept","mustHave","traps":[{"if","label"}]},
                    "lesson": { ...PublicLesson 的顯示欄位，供重新整理後還原... } } | null,
  "stats": { "answered": 0, "correct": 0, "streak": 0 } }
```
新欄位只加不改；讀到缺欄位的舊檔要補預設值。現成範例：`~/.claude/learn-data/sql/progress.json`（使用者真實進度，**只能讀，不可寫**；測試要複製到 tmp）。

## 7. frontend/（負責：子代理 E）
靜態、免建置：`index.html`、`app.css`、`app.js`、`scene.js`、`api.js`、`runners/`、`mock/`。
- importmap 指定固定版本（three@0.170.0 等）；sql.js、Pyodide、Mermaid 用到時才載入。
- `api.js`：橋接位址＝同源（若頁面由橋接提供）否則 `http://127.0.0.1:8766`；`?mock=1` 時改讀 `mock/*.json`（不需橋接即可開發）。
- 版面：左＝主題房間清單；中＝路徑 + 3D 星雲（另有 2D 清單切換）；右＝可收合問答面板。手機版左右改為抽屜。
- 題目畫面：短講 → 範例（runner 主題實際執行並顯示輸出，失敗就隱藏）→ `example_explain` → `show_tables`（sql.js 從 `/topics/{t}/env` 建 DB 後 `SELECT *` 顯示）→ `visuals`（表格 / Mermaid strict / 簡單長條或折線 SVG）→ 題目 → 提示（可展開）→ 把握度三鍵 → 作答框（code 題用等寬字型）→ 送出。
- `produce` / `debug`（runner 主題）：送出前在瀏覽器執行使用者程式與 `ref_code`，比較結果組成 `ExecResult`（SQL：欄位與列集合比較，題目沒要求排序時不比順序）。
- 判定畫面：✅🟡❌、🚨 危險迷思提示、回饋、參考答案（可展開）、迷思筆記、下一題按鈕。
