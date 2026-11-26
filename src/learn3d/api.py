"""本機 HTTP 橋接（FastAPI）：瀏覽器 ⇄ 進度檔 / 本機 AI。

安全設定（沿用 SmartDCA ai-bridge 的做法）：
- 只監聽 127.0.0.1。
- Origin 白名單；沒有 Origin 的請求（curl 等本機工具）放行；不在白名單 → 403。
- Host 標頭只接受本機名稱（防 DNS rebinding 讀進度）。
- 回應帶 CORS 與 `Access-Control-Allow-Private-Network: true`（Chrome 的 Private Network Access）。
- AI 路由（/intake、/plan、lesson、grade）每分鐘最多 30 次 → 429。
- 錯誤一律回 ErrorResponse（中文訊息）。

注意：別的模組一律用模組參照呼叫（`providers.get_provider(...)`），測試才能替換。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from collections import deque
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, guardrails, providers, scoring, store, tasks
from .config import HOST, Settings, get_settings
from .schemas import (
    AiChoice,
    ConfirmRequest,
    ErrorResponse,
    GradeRequest,
    GradeResponse,
    Intake,
    IntakeRequest,
    JournalResponse,
    JumpRequest,
    LessonRequest,
    NodeState,
    PlanRequest,
    ProvidersResponse,
    PublicLesson,
    TopicsResponse,
    Verdict,
)

log = logging.getLogger("learn3d.api")

LESSON_FAIL = "這題暫時無法產生，請再試一次或換一個節點"
GRADE_FAIL = "這題暫時無法判定，請再試一次或換個說法"
PLAN_FAIL = "學習路徑暫時無法產生，請再試一次或換一個 AI 模型"
INTAKE_FAIL = "暫時無法產生問題，請再試一次或換一個 AI 模型"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}  # testserver = FastAPI TestClient


# ---------- 錯誤 ----------
class ApiError(Exception):
    def __init__(self, status: int, error: str, needs_confirm: bool = False):
        super().__init__(error)
        self.status = status
        self.error = error
        self.needs_confirm = needs_confirm


def _err(status: int, error: str, needs_confirm: bool = False) -> JSONResponse:
    body = ErrorResponse(error=error, needs_confirm=needs_confirm).model_dump()
    return JSONResponse(body, status_code=status)


# ---------- 頻率限制 ----------
class RateLimiter:
    """單一程序的滑動視窗計數（橋接只服務本機一位使用者，全域計數即可）。"""

    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def hit(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] >= self.window:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# ---------- 每個主題一把鎖（避免同時寫同一份進度檔）----------
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def topic_lock(topic: str) -> Iterator[None]:
    with _locks_guard:
        lock = _locks.setdefault(topic, threading.Lock())
    with lock:
        yield


# ---------- 共用小工具 ----------
def _check_topic(t: str) -> str:
    try:
        return store.check_topic_name(t)
    except store.StoreError as e:
        raise ApiError(400, str(e)) from e


def _load(t: str) -> dict:
    _check_topic(t)
    if not store.exists(t):
        raise ApiError(404, f"找不到主題「{t}」")
    return store.load(t)


def _find_node(progress: dict, node_id: str) -> dict:
    for n in progress.get("nodes", []):
        if n.get("id") == node_id:
            return n
    raise ApiError(404, f"找不到節點「{node_id}」")


def _fail(msg: str) -> str:
    """「暫時無法產生」加上 AI 最後一次的失敗原因：同一段話在別人電腦上失敗時，才看得出是哪個 AI、哪裡不合格。"""
    from .structured import last_failure
    why = last_failure()
    return f"{msg}（原因：{why}）" if why else msg


def _call_ai(fn: Callable[[], Any]) -> Any:
    """包住 provider / task 呼叫：ProviderError → 503（訊息直接給使用者看）。"""
    try:
        return fn()
    except providers.ProviderError as e:
        raise ApiError(503, str(e) or "AI 暫時無法使用，請確認本機的 Claude Code / Codex / Ollama") from e


def _limit(request: Request, ai: AiChoice | None = None) -> None:
    # 假模型只給測試用：正式環境若接受，假內容會被寫進使用者的進度檔
    if ai is not None and ai.provider == "fake" and not request.app.state.settings.allow_fake:
        raise ApiError(400, "這個 AI 選項只供測試使用，請改選 Claude、Codex 或 Ollama")
    if not request.app.state.rate_limiter.hit():
        raise ApiError(429, "AI 呼叫太頻繁了（每分鐘最多 30 次），請稍等一下再試")


def _host_name(host: str) -> str:
    """去掉 Host 標頭的埠號（支援 [::1]:8766）。"""
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0]


def _node_state(progress: dict, node_id: str) -> NodeState:
    n = _find_node(progress, node_id)
    return NodeState(id=node_id, status=n.get("status", "unseen"), score=int(n.get("score", 0)))


# ---------- App ----------
def _journal(t: str, progress: dict, lq: dict, req: GradeRequest, verdict: Verdict, danger: bool,
             delta: int, mis_label: str | None, reference: str) -> None:
    """判定完成後寫一筆作答日誌。寫入失敗只記 log，不影響判定結果（進度已經存好了）。"""
    node_id = lq.get("node") or ""
    node = next((n for n in progress.get("nodes", []) if n.get("id") == node_id), {})
    cut = lambda x, n: (x[:n] + "…") if x and len(x) > n else x  # noqa: E731
    entry = {
        "id": uuid.uuid4().hex[:12],
        "at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "node": node_id,
        "node_name": str(node.get("name") or node_id),
        "kind": "review" if lq.get("kind") == "review" else "new",
        "type": lq.get("type") or "explain",
        "question": str(lq.get("question") or ""),
        "answer": guardrails.mask_pii(req.answer),
        "confidence": req.confidence,
        "result": verdict.result,
        "danger": bool(danger),
        "delta": int(delta),
        "feedback": verdict.feedback,
        "reference_answer": reference,
        "misconception_label": mis_label,
        "exec_user": cut(req.exec.user_output, 1500) if req.exec else None,
        "exec_ref": cut(req.exec.ref_output, 1500) if req.exec else None,
        "source": "web",
    }
    try:
        store.journal_append(t, entry)
    except OSError as e:
        log.warning("作答日誌寫入失敗：%s", e)


def _journal_model(row: dict):
    """把日誌的一行轉成模型；skill 寫的舊格式或壞資料略過，不讓整頁打不開。"""
    from .schemas import JournalEntry
    try:
        return JournalEntry.model_validate({**{"source": "web", "danger": False, "delta": 0, "kind": "new",
                                               "reference_answer": "", "misconception_label": None}, **row})
    except Exception:  # noqa: BLE001
        return None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="learn3d-bridge", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter(settings.rate_limit, settings.rate_window)
    patterns = settings.origin_patterns

    # ----- Origin / Host / CORS / 預檢 / 500 -----
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if _host_name(request.headers.get("host") or "") not in ALLOWED_HOSTS:
            return _err(403, "只接受本機位址（127.0.0.1 / localhost）的請求")
        origin = request.headers.get("origin")
        cors: dict[str, str] = {}
        if origin is not None:
            if not any(p.fullmatch(origin) for p in patterns):
                return _err(403, "這個網站沒有被允許使用本機學習橋接")
            cors = {
                "Access-Control-Allow-Origin": origin,
                "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "content-type",
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "600",
            }
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001 —— 讓 500 也帶 CORS 與 ErrorResponse 格式
            log.exception("未預期的錯誤：%s %s", request.method, request.url.path)
            response = _err(500, "橋接發生未預期的錯誤，請看橋接視窗的訊息")
        response.headers.update(cors)
        return response

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return _err(exc.status, exc.error, exc.needs_confirm)

    @app.exception_handler(store.TopicNotFound)
    async def _not_found(_req: Request, exc: store.TopicNotFound):
        return _err(404, str(exc))

    @app.exception_handler(store.StoreError)
    async def _store_error(_req: Request, exc: store.StoreError):
        return _err(400, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        errs = exc.errors()
        detail = ""
        if errs:
            e = errs[0]
            loc = ".".join(str(x) for x in e.get("loc", []) if x != "body")
            detail = f"（{loc}：{e.get('msg', '')}）" if loc else f"（{e.get('msg', '')}）"
        return _err(422, f"請求格式不正確{detail}")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_req: Request, exc: StarletteHTTPException):
        msg = {404: "找不到這個路徑", 405: "不支援這個請求方法"}.get(exc.status_code, str(exc.detail))
        return _err(exc.status_code, msg)

    # ----- 基本 -----
    @app.get("/health")
    def health():
        from .version import build_id
        return {"ok": True, "name": "learn3d-bridge", "version": __version__, "build": build_id()}

    @app.get("/usage")
    def get_usage():
        """Claude / Codex 訂閱額度（5 小時、每週）的最新狀態，網站左下角即時顯示。"""
        from . import usage
        return usage.snapshot()

    @app.get("/providers", response_model=ProvidersResponse)
    def get_providers(refresh: str | None = None):
        return ProvidersResponse(providers=providers.detect_providers(refresh=bool(refresh) and refresh != "0"))

    @app.get("/topics", response_model=TopicsResponse)
    def get_topics():
        return TopicsResponse(topics=store.list_topics(scoring.today()))

    @app.get("/topics/{t}")
    def get_topic(t: str):
        return store.public_progress(_load(t))

    @app.get("/topics/{t}/env")
    def get_env(t: str):
        p = _load(t)
        return {"runner": p.get("runner"), "sql": store.read_env(t)}

    # ----- AI：intake / plan -----
    @app.post("/intake", response_model=Intake)
    def post_intake(req: IntakeRequest, request: Request):
        problem = guardrails.check_topic(req.topic)
        if problem:
            raise ApiError(400, problem)
        _limit(request, req.ai)
        result = _call_ai(lambda: tasks.run_intake(providers.get_provider(req.ai.provider), req.ai, req.topic))
        if result is None:
            raise ApiError(503, _fail(INTAKE_FAIL))
        return result

    @app.post("/plan")
    def post_plan(req: PlanRequest, request: Request):
        problem = guardrails.check_topic(req.topic)
        if problem:
            raise ApiError(400, problem)
        t = _check_topic(req.slug)
        existed = store.exists(t)
        if existed and not req.confirm:
            raise ApiError(409, f"主題「{t}」已經有學習路徑，要覆寫請先確認（保留已學狀態或完全重來）", needs_confirm=True)
        _limit(request, req.ai)
        m = _call_ai(lambda: tasks.run_plan(providers.get_provider(req.ai.provider), req.ai, req))
        if m is None:
            raise ApiError(503, _fail(PLAN_FAIL))
        with topic_lock(t):
            keep_from = None
            if store.exists(t):
                old = store.load(t)
                store.backup(t)
                if req.mode == "replan_keep":
                    keep_from = old
            progress = store.create_topic(req, m, keep_from=keep_from)
            if existed:
                if keep_from is None:
                    store.lecture_cache_clear(t)
                else:
                    store.lecture_cache_clear(t, keep={n["id"] for n in progress["nodes"]})
        return {"topic": t, "progress": store.public_progress(progress)}

    # ----- AI：出題 -----
    @app.post("/topics/{t}/lesson", response_model=PublicLesson)
    def post_lesson(t: str, req: LessonRequest, request: Request):
        _check_topic(t)
        with topic_lock(t):
            p = _load(t)
            day = scoring.today()
            review = None
            if req.review:
                due = scoring.due_misconceptions(p, day)
                if not due:
                    raise ApiError(409, "目前沒有到期要複習的迷思")
                review = due[0]
                node_id = review["node"]
            elif req.node:
                node_id = req.node
            else:
                node_id = scoring.pick_next_node(p)
            node = _find_node(p, node_id)
            kind = "review" if review else "new"

            cache = None if review else store.lecture_cache_get(t, node_id)
            history = node.get("history") or []
            need_lecture = review is None and cache is None and not history
            ctx = tasks.LessonContext(
                domain=p.get("domain") or "concept",
                runner=p.get("runner"),
                node=node,
                learner=p.get("learner") or {},
                env_note=p.get("envNote"),
                recent_types=[h.get("type") for h in history[-3:] if h.get("type")],
                want_type=scoring.choose_qtype(history, p.get("domain") or "concept"),
                need_lecture=need_lecture,
                review=review,
            )
            _limit(request, req.ai)
            lesson = _call_ai(lambda: tasks.run_lesson(providers.get_provider(req.ai.provider), req.ai, ctx))
            if lesson is None:
                raise ApiError(503, _fail(LESSON_FAIL))

            lecture, example, example_explain = lesson.lecture, lesson.example, list(lesson.example_explain)
            cached = False
            if cache:
                lecture = cache.get("lecture")
                example = cache.get("example")
                example_explain = list(cache.get("example_explain") or [])
                cached = True
            elif need_lecture and lesson.lecture:
                store.lecture_cache_put(
                    t, node_id,
                    {"lecture": lesson.lecture, "example": lesson.example,
                     "example_explain": list(lesson.example_explain), "created": day.isoformat()},
                )

            public = PublicLesson(
                question_id=uuid.uuid4().hex[:12],
                node=node_id,
                node_name=node.get("name") or node_id,
                kind=kind,
                type=lesson.type,
                lecture=lecture,
                example=example,
                example_explain=example_explain,
                question=lesson.question,
                hint=lesson.hint,
                show_tables=list(lesson.show_tables),
                visuals=list(lesson.visuals),
                ref_code=lesson.ref_code,
                runner=p.get("runner"),
                cached_lecture=cached,
            )
            # 存檔前重讀，避免覆蓋 AI 呼叫期間別的工具（例如 /learn-coach）寫入的內容
            p = store.load(t)
            if kind == "new":
                p["current"] = node_id
            p["lastQuestion"] = {
                "id": public.question_id,
                "node": node_id,
                "type": lesson.type,
                "kind": kind,
                "misconceptionId": review.get("id") if review else None,
                "question": lesson.question,
                "askedAt": day.isoformat(),
                "rubric": store.rubric_to_file(lesson.rubric),
                "lesson": public.model_dump(mode="json"),
            }
            store.save(t, p)
        return public

    # ----- AI：判定 -----
    @app.post("/topics/{t}/grade", response_model=GradeResponse)
    def post_grade(t: str, req: GradeRequest, request: Request):
        _check_topic(t)
        with topic_lock(t):
            p = _load(t)
            lq = p.get("lastQuestion")
            if not isinstance(lq, dict):
                raise ApiError(409, "目前沒有待回答的題目，請先出題")
            if lq.get("id") != req.question_id:
                raise ApiError(409, "題目已過期，請重新出題")
            node_id = lq.get("node")
            _find_node(p, node_id)
            question = str(lq.get("question") or "")
            qtype = lq.get("type") or "explain"

            problem = guardrails.check_answer(req.answer)
            if problem:
                raise ApiError(400, problem)
            _limit(request, req.ai)

            # 有執行結果時，作答是程式碼，關鍵詞比對不適用 → 只對文字作答做離題檢查
            if req.exec is None and guardrails.is_offtopic(req.answer, question):
                return GradeResponse(
                    result="unjudgeable",
                    feedback=f"這題先回答題目喔：{question}",
                    danger=False,
                    delta=0,
                    node=_node_state(p, node_id),
                    reference_answer="",
                    offtopic=True,
                )

            rubric = store.rubric_from_file(lq.get("rubric") or {})
            if req.exec is not None and req.exec.equal:
                verdict = Verdict(
                    result="ok",
                    met=list(range(len(rubric.must_have))),
                    feedback="執行結果和參考答案一致，答對了！",
                    citations=["exec"],
                )
            else:
                ctx = tasks.GradeContext(
                    question=question,
                    qtype=qtype,
                    rubric=rubric,
                    answer=guardrails.mask_pii(req.answer),
                    exec=req.exec,
                    learner_notes=(p.get("learner") or {}).get("notes") or None,
                )
                verdict = _call_ai(lambda: tasks.run_grade(providers.get_provider(req.ai.provider), req.ai, ctx))
                if verdict is None:
                    return GradeResponse(
                        result="unjudgeable",
                        feedback=GRADE_FAIL,
                        danger=False,
                        delta=0,
                        node=_node_state(p, node_id),
                        reference_answer="",
                        fallback=True,
                    )

            day = scoring.today()
            p = store.load(t)  # 重讀（AI 呼叫期間檔案可能被別的工具改過）
            if not isinstance(p.get("lastQuestion"), dict) or p["lastQuestion"].get("id") != req.question_id:
                raise ApiError(409, "題目已過期，請重新出題")
            before_stats = dict(p.get("stats") or {})
            old_score = int(_find_node(p, node_id).get("score") or 0)
            danger = req.confidence == "high" and verdict.result == "wrong"

            mis_label = None
            review_id = lq.get("misconceptionId") if lq.get("kind") == "review" else None
            reviewed = None
            if review_id and any(m.get("id") == review_id for m in p.get("misconceptions", [])):
                # 複習題：只跑 apply_review（答錯扣節點 1 分），不跑 apply_grade，避免扣兩次
                new_p = scoring.apply_review(p, review_id, verdict.result, day)
                reviewed = next((m for m in new_p.get("misconceptions", []) if m.get("id") == review_id), None)
            else:
                # 新題（或複習的迷思已被刪除）：照一般判定計分
                new_p, outcome = scoring.apply_grade(p, node_id, qtype, req.confidence, verdict.result, day)
                danger = bool(outcome.danger)

            if verdict.result in ("wrong", "partial"):
                if verdict.misconception is not None:
                    label = (verdict.trap or verdict.misconception.wrong)[:40]
                    if reviewed is not None and label == reviewed.get("label"):
                        mis_label = label  # 複習題又犯同一個迷思：apply_review 已處理
                    else:
                        new_p, _mid = scoring.record_misconception(
                            new_p, node_id, label,
                            verdict.misconception.wrong, verdict.misconception.right,
                            question[:60], day,
                        )
                        mis_label = label
                elif reviewed is not None:
                    mis_label = reviewed.get("label")

            # stats：scoring 沒更新時由這裡累加（unjudgeable 不算）
            if verdict.result != "unjudgeable" and (new_p.get("stats") or {}) == before_stats:
                s = dict(before_stats)
                s["answered"] = int(s.get("answered", 0)) + 1
                if verdict.result == "ok":
                    s["correct"] = int(s.get("correct", 0)) + 1
                    s["streak"] = int(s.get("streak", 0)) + 1
                else:
                    s["streak"] = 0
                new_p["stats"] = s

            new_p["lastQuestion"] = None
            store.save(t, new_p)
            # delta / 節點狀態一律以存檔前後該節點 score 差計算（複習題與新題相同）
            final = _node_state(new_p, node_id)
            _journal(t, new_p, lq, req, verdict, danger, final.score - old_score, mis_label, rubric.answer)

        return GradeResponse(
            result=verdict.result,
            feedback=verdict.feedback,
            danger=danger,
            delta=final.score - old_score,
            node=final,
            misconception_label=mis_label,
            reference_answer=rubric.answer,
        )

    # ----- 作答日誌 -----
    @app.get("/topics/{t}/journal", response_model=JournalResponse)
    def get_journal(t: str, limit: int = 30, before: str | None = None, node: str | None = None):
        _check_topic(t)
        if not store.exists(t):
            raise store.TopicNotFound(t)
        rows, more = store.journal_read(t, limit=limit, before=before, node=node)
        return JournalResponse(entries=[e for e in (_journal_model(r) for r in rows) if e is not None], has_more=more)

    # ----- 跳轉 / 重設 / 刪除 -----
    @app.post("/topics/{t}/jump")
    def post_jump(t: str, req: JumpRequest):
        _check_topic(t)
        with topic_lock(t):
            p = _load(t)
            _find_node(p, req.node)
            p["current"] = req.node
            store.save(t, p)
        return {"current": req.node}

    @app.post("/topics/{t}/reset")
    def post_reset(t: str, req: ConfirmRequest):
        _check_topic(t)
        if req.confirm != "reset":
            raise ApiError(400, "要重設進度，請輸入 reset 確認")
        with topic_lock(t):
            _load(t)
            p = store.reset(t)
        return {"progress": store.public_progress(p)}

    @app.delete("/topics/{t}")
    def delete_topic(t: str, req: ConfirmRequest):
        _check_topic(t)
        if req.confirm != t:
            raise ApiError(400, f"要刪除主題，請輸入主題名稱「{t}」確認")
        with topic_lock(t):
            _load(t)
            store.trash(t)
        return {"ok": True}

    # ----- 前端靜態檔（API 路由優先，最後才掛）-----
    front = settings.frontend_dir
    if front.is_dir():
        from fastapi.staticfiles import StaticFiles

        class _NoCacheStatic(StaticFiles):
            """本機提供的前端每次都向橋接確認版本，更新後不會卡到瀏覽器快取的舊模組。"""

            async def get_response(self, path, scope):
                resp = await super().get_response(path, scope)
                resp.headers["Cache-Control"] = "no-cache"
                return resp

        app.mount("/", _NoCacheStatic(directory=str(front), html=True), name="frontend")

    return app


app = create_app()  # 供 `uvicorn learn3d.api:app` 與測試直接使用


def run() -> None:
    import uvicorn

    settings = get_settings()
    app = create_app(settings)
    url = f"http://{HOST}:{settings.port}/"
    print(f"Learn 3D 學習橋接 v{__version__}")
    print(f"  資料目錄：{settings.data_root}")
    print(f"  橋接網址：{url}")
    if settings.frontend_dir.is_dir():
        print(f"  直接開啟：{url}")
    print("  線上版：https://learn.hellokai07.com（關掉這個視窗就會停止橋接）")
    try:
        uvicorn.run(app, host=HOST, port=settings.port, log_level="info")
    except OSError as e:
        print(f"無法啟動：{e}（{settings.port} 埠可能已被占用，是不是已經開了一個橋接？）")
        raise SystemExit(1) from e
