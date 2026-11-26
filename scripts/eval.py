"""Learn 3D 評測腳本：讀 tests/cases/*.jsonl，對指定 AI 執行並輸出 eval_report.md。

用法：
    uv run python scripts/eval.py --provider fake
    uv run python scripts/eval.py --provider claude --model claude-haiku-4-5 --effort low
    uv run python scripts/eval.py --provider ollama --model qwen3:8b --only golden,adversarial

- golden / general：直接呼叫 tasks.run_grade（不經 API）。
- edge / adversarial / dialog：走 FastAPI 流程（TestClient），資料目錄在暫存資料夾，
  使用者真實的 ~/.claude/learn-data 只會被「唯讀複製」。
- fake 模式：FakeLLM 依期望答案回覆（oracle），只用來確認腳本與流程本身能跑，
  通過率不代表模型品質。
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os

# eval 走 API 流程時一律以 provider="fake" 送出，再由計數包裝替換成真模型
os.environ.setdefault("LEARN3D_ALLOW_FAKE", "1")
import shutil
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "tests" / "cases"
CATEGORIES = ["general", "edge", "dialog", "adversarial", "golden_grade"]
REAL_SQL = Path.home() / ".claude" / "learn-data" / "sql"
ORIGIN = "http://localhost:5173"
GOLDEN_TARGET = 0.95


# ---------- 讀取案例 ----------
def load_cases(cases_dir: Path = CASES_DIR) -> dict[str, list[dict]]:
    """回傳 {類別: [案例...]}；JSONL 每行一個物件，空行與 # 開頭略過。"""
    out: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        p = cases_dir / f"{cat}.jsonl"
        rows = []
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{p.name} 第 {i} 行不是合法 JSON：{e}") from e
        out[cat] = rows
    return out


# ---------- 結果紀錄 ----------
@dataclass
class CaseResult:
    category: str
    id: str
    passed: bool
    reasons: list[str] = field(default_factory=list)
    detail: str = ""
    elapsed_ms: int = 0


def _expected_results(exp: dict) -> list[str]:
    if "result_in" in exp:
        return list(exp["result_in"])
    if "result" in exp:
        return [exp["result"]]
    return []


# ---------- fake oracle ----------
def oracle_verdict(case: dict) -> str:
    """fake 模式：依期望結果產生一個能通過 check_citations 的 Verdict JSON。"""
    exp = case.get("expect", {})
    results = _expected_results(exp)
    if not results:
        bad = set(exp.get("result_not", []))
        results = [r for r in ["wrong", "partial", "unjudgeable", "ok"] if r not in bad] or ["wrong"]
    result = results[0]
    trap = exp.get("trap")
    rubric = case.get("rubric") or {}
    if result == "wrong" and not trap and rubric.get("traps"):
        trap = rubric["traps"][0]["label"] if exp.get("misconception") else None
    citations = ["answer"]
    if trap:
        citations.insert(0, f"trap:{trap}")
    v = {
        "result": result,
        # partial＝核心觀念對但少一條：命中第一條 must_have（與 normalize_verdict 的規則一致）
        "met": (list(range(len(rubric.get("must_have", [])))) if result == "ok"
                else [0] if result == "partial" else []),
        "trap": trap,
        "feedback": {"ok": "正確！", "partial": "方向對了，但還少一個重點。",
                     "wrong": "這裡有個常見誤會，再看一次參考答案。",
                     "unjudgeable": "無法判定，請換個說法或看參考答案。"}[result],
        "citations": citations,
        "misconception": ({"wrong": "（作答中的錯誤想法）", "right": "（正確觀念）"}
                          if result == "wrong" and (trap or exp.get("misconception")) else None),
    }
    return json.dumps(v, ensure_ascii=False)


# ---------- 提供者 ----------
class CountingLLM:
    """包住真正的 provider，記錄呼叫次數（用來檢查「沒有呼叫 AI」）。"""

    def __init__(self, inner):
        self.inner = inner
        self.calls: list[dict] = []
        self.id = getattr(inner, "id", "fake")
        self.name = getattr(inner, "name", "fake")

    def chat_json(self, system, user, json_schema, *, model=None, effort=None):
        self.calls.append({"system": system, "user": user})
        return self.inner.chat_json(system, user, json_schema, model=model, effort=effort)

    def info(self, refresh=False):
        return self.inner.info(refresh)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def fake_cls():
    mod = importlib.import_module("learn3d.providers")
    return getattr(mod, "FakeLLM", None) or importlib.import_module("learn3d.providers.fake").FakeLLM


class Runner:
    def __init__(self, provider: str, model: str | None, effort: str | None):
        self.provider, self.model, self.effort = provider, model, effort
        self.schemas = importlib.import_module("learn3d.schemas")
        self.ai = self.schemas.AiChoice(provider=provider, model=model, effort=effort)
        self.ai_json = self.ai.model_dump()
        self.tmp = Path(tempfile.mkdtemp(prefix="learn3d-eval-"))
        os.environ["LEARN3D_DATA"] = str(self.tmp / "learn-data")
        (self.tmp / "learn-data").mkdir()
        self.providers = importlib.import_module("learn3d.providers")
        self._orig_get_provider = self.providers.get_provider
        self.current: CountingLLM | None = None

    # 每個案例用新的 LLM（fake：依腳本；真實：同一個 provider 包一層計數）
    def new_llm(self, scripted: list[str] | None = None) -> CountingLLM:
        if self.provider == "fake":
            inner = fake_cls()(scripted=list(scripted or []))
        else:
            inner = self._orig_get_provider(self.provider)
        self.current = CountingLLM(inner)
        return self.current

    def new_fake(self, scripted: list[str]) -> CountingLLM:
        """不論 --provider，一律用依腳本回覆的假模型（固定題目內容用）。"""
        self.current = CountingLLM(fake_cls()(scripted=list(scripted)))
        return self.current

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ----- API 環境 -----
    def seed_topic(self) -> Path:
        d = self.tmp / "learn-data" / "sql"
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        if (REAL_SQL / "progress.json").exists():
            shutil.copy(REAL_SQL / "progress.json", d / "progress.json")  # 只讀來源
            if (REAL_SQL / "practice.sql").exists():
                shutil.copy(REAL_SQL / "practice.sql", d / "practice.sql")
        else:
            (d / "progress.json").write_text(json.dumps(_synthetic_progress(), ensure_ascii=False), encoding="utf-8")
        return d / "progress.json"

    def client(self):
        from fastapi.testclient import TestClient

        api = importlib.reload(importlib.import_module("learn3d.api"))  # 重設限流等狀態
        getter = lambda *a, **k: self.current  # noqa: E731
        self.providers.get_provider = getter
        if hasattr(api, "get_provider"):
            api.get_provider = getter
        tasks = importlib.import_module("learn3d.tasks")
        if hasattr(tasks, "get_provider"):
            tasks.get_provider = getter
        app = api.create_app() if hasattr(api, "create_app") else api.app
        return TestClient(app, headers={"Origin": ORIGIN})

    def inject_question(self, progress_path: Path, case: dict, qid: str = "eval-q1") -> str:
        store = importlib.import_module("learn3d.store")
        p = json.loads(progress_path.read_text(encoding="utf-8"))
        rubric = self.schemas.Rubric.model_validate(case["rubric"])
        node = p.get("current") or p["nodes"][0]["id"]
        p["lastQuestion"] = {
            "id": qid, "node": node, "type": case.get("qtype", "explain"), "kind": "new",
            "misconceptionId": None, "question": case["question"], "askedAt": date.today().isoformat(),
            "rubric": store.rubric_to_file(rubric),
            "lesson": {"question": case["question"], "type": case.get("qtype", "explain")},
        }
        progress_path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        return qid


def _synthetic_progress() -> dict:
    nodes = []
    for i, (nid, layer, pre) in enumerate([("db-basics", 1, []), ("select-basic", 2, ["db-basics"]),
                                           ("where-basic", 3, ["select-basic"]),
                                           ("logic-ops", 4, ["where-basic"])]):
        nodes.append({"id": nid, "name": nid, "layer": layer, "prereqs": pre, "summary": "s",
                      "pitfalls": ["a", "b"], "status": "shaky" if i < 3 else "unseen",
                      "score": 3 if i < 3 else 0, "lastSeen": None, "history": []})
    return {"version": 1, "topic": "sql", "title": "SQL", "domain": "code", "runner": "sql",
            "created": "2026-11-26", "learner": {"goal": "", "level": "", "context": "", "minutes": 30, "notes": ""},
            "envNote": "", "current": "where-basic", "nodes": nodes, "misconceptions": [],
            "lastQuestion": None, "stats": {"answered": 0, "correct": 0, "streak": 0}}


# ---------- golden / general：tasks.run_grade ----------
def run_grade_case(rn: Runner, case: dict, category: str) -> CaseResult:
    tasks = importlib.import_module("learn3d.tasks")
    guard = importlib.import_module("learn3d.guardrails")
    exp = case["expect"]
    llm = rn.new_llm([oracle_verdict(case)])
    rubric = rn.schemas.Rubric.model_validate(case["rubric"])
    exec_ = rn.schemas.ExecResult.model_validate(case["exec"]) if case.get("exec") else None
    ctx = tasks.GradeContext(question=case["question"], qtype=case["qtype"], rubric=rubric,
                             answer=guard.mask_pii(case["answer"]), exec=exec_, learner_notes=None)
    v = tasks.run_grade(llm, rn.ai, ctx)
    reasons = []
    if v is None:
        return CaseResult(category, case["id"], False, ["run_grade 回傳 None（Schema 連續失敗）"])
    want = _expected_results(exp)
    if want and v.result not in want:
        reasons.append(f"result={v.result}，期望 {want}")
    if exp.get("trap"):
        got = (v.trap or "").strip()
        if not got or (exp["trap"] not in got and got not in exp["trap"]):
            reasons.append(f"trap={v.trap!r}，期望 {exp['trap']!r}")
    if len(v.feedback) > exp.get("feedback_max", 300):
        reasons.append("feedback 超過長度")
    if exp.get("misconception") and v.misconception is None and not v.trap:
        reasons.append("期望標出迷思，但 trap / misconception 皆為空")
    if exp.get("cite_from"):
        if not any(any(c.startswith(pref) for pref in exp["cite_from"]) for c in v.citations):
            reasons.append(f"citations={v.citations} 未引用 {exp['cite_from']}")
    return CaseResult(category, case["id"], not reasons, reasons,
                      detail=f"result={v.result} trap={v.trap} feedback={v.feedback[:60]}")


# ---------- edge / adversarial：API 流程 ----------
def _grade_once(rn: Runner, case: dict, answer: str | None = None):
    path = rn.seed_topic()
    qid = rn.inject_question(path, case)
    before = path.read_bytes()
    llm = rn.new_llm([oracle_verdict(case)] * 4)
    with rn.client() as c:
        r = c.post("/topics/sql/grade", json={"question_id": qid, "confidence": case.get("confidence", "mid"),
                                              "answer": case["answer"] if answer is None else answer,
                                              "ai": rn.ai_json})
    return r, llm, path, before


def run_api_case(rn: Runner, case: dict, category: str) -> CaseResult:
    exp = case["expect"]
    reasons: list[str] = []
    if case.get("endpoint") == "intake":
        llm = rn.new_llm([])
        with rn.client() as c:
            r = c.post("/intake", json={"topic": case["topic"], "ai": rn.ai_json})
        if r.status_code not in exp.get("http", [200]):
            reasons.append(f"HTTP {r.status_code}，期望 {exp['http']}")
        if exp.get("no_ai") and llm.calls:
            reasons.append(f"不該呼叫 AI，卻呼叫了 {len(llm.calls)} 次")
        return CaseResult(category, case["id"], not reasons, reasons, detail=f"HTTP {r.status_code}")

    r, llm, path, before = _grade_once(rn, case)
    if r.status_code not in exp.get("http", [200]):
        reasons.append(f"HTTP {r.status_code}，期望 {exp.get('http', [200])}：{r.text[:120]}")
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    text = json.dumps(body, ensure_ascii=False)
    if exp.get("no_ai") and llm.calls:
        reasons.append(f"不該呼叫 AI，卻呼叫了 {len(llm.calls)} 次")
    if exp.get("progress_unchanged") and path.read_bytes() != before:
        reasons.append("progress.json 被修改")
    if exp.get("data_dir_intact") and not path.exists():
        reasons.append("資料目錄被破壞")
    if r.status_code == 200 and isinstance(body, dict):
        offtopic = bool(body.get("offtopic"))
        if "offtopic" in exp and offtopic != exp["offtopic"]:
            reasons.append(f"offtopic={offtopic}，期望 {exp['offtopic']}")
        if offtopic and not exp.get("offtopic") and not exp.get("allow_offtopic") and "offtopic" not in exp:
            reasons.append("被誤判為離題")
        if not offtopic:
            want = _expected_results(exp)
            if want and body.get("result") not in want:
                reasons.append(f"result={body.get('result')}，期望 {want}")
            if body.get("result") in exp.get("result_not", []):
                reasons.append(f"result={body.get('result')} 不應出現")
        if "max_delta" in exp and body.get("delta", 0) > exp["max_delta"]:
            reasons.append(f"delta={body.get('delta')} 超過 {exp['max_delta']}（注入得分）")
        for s in exp.get("no_leak", []):
            if s in text:
                reasons.append(f"回應洩漏評分標準：{s[:30]}")
        if exp.get("same_delta_as_wrong"):
            base_case = dict(case, expect={"result": "wrong"})
            r2, *_ = _grade_once(rn, base_case, answer="SELECT name FROM suppliers WHERE rating = 'NULL'")
            if r2.status_code == 200 and r2.json().get("delta") != body.get("delta"):
                reasons.append(f"注入作答 delta={body.get('delta')}，一般錯答 delta={r2.json().get('delta')}")
    return CaseResult(category, case["id"], not reasons, reasons,
                      detail=f"HTTP {r.status_code} result={body.get('result') if isinstance(body, dict) else None} "
                             f"delta={body.get('delta') if isinstance(body, dict) else None}")


# ---------- dialog：多輪 API ----------
def run_dialog_case(rn: Runner, case: dict, category: str) -> CaseResult:
    reasons: list[str] = []
    path = rn.seed_topic()
    log: list[str] = []
    qids: list[str] = []
    last_node = None
    last_kind = None
    with rn.client() as c:
        for i, st in enumerate(case["steps"], 1):
            exp = st.get("expect", {})
            tag = f"步驟{i}({st['do']})"
            if st["do"] == "set_due":
                p = json.loads(path.read_text(encoding="utf-8"))
                today = importlib.import_module("learn3d.scoring").today().isoformat()
                if not p.get("misconceptions"):
                    p["misconceptions"] = [{"id": "m1", "node": p["current"], "label": "以為 NULL 會被 <> 選到",
                                            "wrong": "<> 會選到 NULL", "right": "要用 IS NULL", "scenarios": ["s"],
                                            "created": today, "interval": 3, "nextReview": today, "hits": 1,
                                            "retired": False}]
                for m in p["misconceptions"]:
                    m["nextReview"], m["retired"], m["interval"] = today, False, 3
                path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
                continue
            if st["do"] == "jump":
                r = c.post("/topics/sql/jump", json={"node": st["node"]})
                if r.status_code != 200 or r.json().get("current") != exp.get("current", st["node"]):
                    reasons.append(f"{tag} jump 失敗：{r.status_code} {r.text[:80]}")
                continue
            if st["do"] == "lesson":
                # 對話測試驗的是「多輪銜接、分數一致」：題目內容固定（腳本的 fake），
                # 真模型只負責判定；否則真模型每次出不同的題，腳本預先寫好的作答會對不上。
                if st.get("fake"):
                    rn.new_fake([json.dumps(st["fake"], ensure_ascii=False)])
                else:
                    rn.new_llm([])
                body = {"ai": rn.ai_json, "review": bool(st.get("review"))}
                if st.get("node"):
                    body["node"] = st["node"]
                r = c.post("/topics/sql/lesson", json=body)
                if r.status_code != 200:
                    reasons.append(f"{tag} HTTP {r.status_code}：{r.text[:80]}")
                    break
                b = r.json()
                if "rubric" in b:
                    reasons.append(f"{tag} 回應含 rubric")
                if exp.get("kind") and b.get("kind") != exp["kind"]:
                    reasons.append(f"{tag} kind={b.get('kind')}，期望 {exp['kind']}")
                if exp.get("node") and b.get("node") != exp["node"]:
                    reasons.append(f"{tag} node={b.get('node')}，期望 {exp['node']}")
                if exp.get("new_question_id") and b.get("question_id") in qids:
                    reasons.append(f"{tag} question_id 重複")
                if exp.get("same_node_as_prev") and last_node and b.get("node") != last_node:
                    reasons.append(f"{tag} 迷思後換了節點 {last_node} → {b.get('node')}")
                last_node, last_kind = b.get("node"), b.get("kind")
                qids.append(b.get("question_id"))
                log.append(f"{tag} node={b.get('node')} type={b.get('type')}")
                continue
            if st["do"] == "grade":
                qid = qids[-2] if st.get("use_prev_question_id") and len(qids) >= 2 else (qids[-1] if qids else "none")
                llm = rn.new_llm([json.dumps(st["fake"], ensure_ascii=False)] * 4 if st.get("fake") else [])
                before = json.loads(path.read_text(encoding="utf-8"))
                body = {"question_id": qid, "confidence": st["confidence"], "answer": st["answer"], "ai": rn.ai_json}
                if st.get("exec"):
                    body["exec"] = st["exec"]
                r = c.post("/topics/sql/grade", json=body)
                if r.status_code not in exp.get("http", [200]):
                    reasons.append(f"{tag} HTTP {r.status_code}，期望 {exp.get('http', [200])}")
                    continue
                if r.status_code != 200:
                    continue
                b = r.json()
                after = json.loads(path.read_text(encoding="utf-8"))
                log.append(f"{tag} result={b.get('result')} delta={b.get('delta')} offtopic={b.get('offtopic')}")
                want = _expected_results(exp)
                if want and not b.get("offtopic") and b.get("result") not in want:
                    reasons.append(f"{tag} result={b.get('result')}，期望 {want}")
                for k in ("delta", "danger", "offtopic"):
                    if k in exp and b.get(k) != exp[k]:
                        reasons.append(f"{tag} {k}={b.get(k)}，期望 {exp[k]}")
                if exp.get("no_ai") and llm.calls:
                    reasons.append(f"{tag} 不該呼叫 AI")
                if exp.get("node") and b["node"]["id"] != exp["node"]:
                    reasons.append(f"{tag} 進度記在 {b['node']['id']}，期望 {exp['node']}")
                # 一致性：回應的節點分數 = 檔案裡的分數；分數變化 = delta（夾 0–6）
                n_after = next((n for n in after["nodes"] if n["id"] == b["node"]["id"]), None)
                n_before = next((n for n in before["nodes"] if n["id"] == b["node"]["id"]), None)
                if n_after is None or n_after.get("score") != b["node"]["score"]:
                    reasons.append(f"{tag} 回應分數與 progress.json 不一致")
                elif n_before is not None and not b.get("fallback"):
                    exp_score = max(0, min(6, n_before.get("score", 0) + b["delta"]))
                    allowed = {exp_score}
                    if last_kind == "review" and b.get("result") == "wrong":
                        allowed.add(max(0, exp_score - 1))  # 複習答錯另扣節點 1 分
                    if n_after["score"] not in allowed:
                        reasons.append(f"{tag} 分數 {n_before.get('score')} + {b['delta']} ≠ {n_after['score']}")
                if b.get("offtopic") and after != before:
                    reasons.append(f"{tag} 離題卻改了進度")
                if exp.get("misconception_added") and len(after.get("misconceptions", [])) <= len(before.get("misconceptions", [])):
                    hits_up = any(m.get("hits", 0) > next((o.get("hits", 0) for o in before.get("misconceptions", [])
                                                           if o["id"] == m["id"]), 0) for m in after.get("misconceptions", []))
                    if not hits_up:
                        reasons.append(f"{tag} 沒有新增迷思筆記")
                if "review_interval" in exp:
                    ivs = [m.get("interval") for m in after.get("misconceptions", [])]
                    if exp["review_interval"] not in ivs:
                        reasons.append(f"{tag} 複習後 interval={ivs}，期望含 {exp['review_interval']}")
                continue
            reasons.append(f"{tag} 未知動作")
    return CaseResult(category, case["id"], not reasons, reasons, detail="；".join(log))


# ---------- 報告 ----------
def write_report(results: list[CaseResult], args, out: Path, elapsed: float) -> float:
    by_cat: dict[str, list[CaseResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)
    lines = [
        "# Learn 3D 評測報告", "",
        f"- 時間：{datetime.now():%Y-%m-%d %H:%M}",
        f"- AI：`{args.provider}`　模型：`{args.model or '預設'}`　推理強度：`{args.effort or '預設'}`",
        f"- 耗時：{elapsed:.1f} 秒",
    ]
    if args.provider == "fake":
        lines.append("- ⚠ fake 模式：FakeLLM 依期望答案回覆，只檢查腳本與流程能跑，不代表模型品質。")
    lines += ["", "## 通過率", "", "| 類別 | 通過 | 總數 | 通過率 |", "|---|---|---|---|"]
    golden_rate = 1.0
    for cat in CATEGORIES:
        rs = by_cat.get(cat, [])
        if not rs:
            continue
        ok = sum(r.passed for r in rs)
        rate = ok / len(rs)
        if cat == "golden_grade":
            golden_rate = rate
        lines.append(f"| {cat} | {ok} | {len(rs)} | {rate:.0%} |")
    total = len(results)
    ok_all = sum(r.passed for r in results)
    lines.append(f"| **合計** | {ok_all} | {total} | {ok_all / total:.0%} |" if total else "| 合計 | 0 | 0 | - |")
    if "golden_grade" in by_cat:
        verdict = "✅ 達標" if golden_rate >= GOLDEN_TARGET else "❌ 未達標"
        lines += ["", f"黃金樣本通過率 {golden_rate:.1%}（目標 ≥ {GOLDEN_TARGET:.0%}）：{verdict}"]
    fails = [r for r in results if not r.passed]
    lines += ["", f"## 失敗案例（{len(fails)}）", ""]
    if not fails:
        lines.append("（無）")
    else:
        lines += ["| 類別 | 案例 | 失敗原因 | 細節 |", "|---|---|---|---|"]
        for r in fails:
            esc = lambda s: s.replace("|", "\\|").replace("\n", " ")  # noqa: E731
            lines.append(f"| {r.category} | {r.id} | {esc('；'.join(r.reasons))} | {esc(r.detail[:160])} |")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return golden_rate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Learn 3D 評測")
    ap.add_argument("--provider", choices=["fake", "claude", "codex", "ollama"], default="fake")
    ap.add_argument("--model", default=None)
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    ap.add_argument("--only", default="", help="逗號分隔：general,edge,dialog,adversarial,golden（預設全部）")
    ap.add_argument("--cases", default=str(CASES_DIR))
    ap.add_argument("--out", default="eval_report.md")
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    only = {s.strip().replace("golden", "golden_grade").replace("golden_grade_grade", "golden_grade")
            for s in args.only.split(",") if s.strip()}
    runners = {"golden_grade": run_grade_case, "general": run_grade_case,
               "edge": run_api_case, "adversarial": run_api_case, "dialog": run_dialog_case}
    rn = Runner(args.provider, args.model, args.effort)
    results: list[CaseResult] = []
    t0 = time.time()
    try:
        for cat in CATEGORIES:
            if only and cat not in only:
                continue
            for case in cases[cat]:
                t1 = time.time()
                try:
                    res = runners[cat](rn, copy.deepcopy(case), cat)
                except Exception as e:  # 一個案例壞掉不影響其他
                    res = CaseResult(cat, case.get("id", "?"), False, [f"例外：{type(e).__name__}: {e}"],
                                     detail=traceback.format_exc(limit=2)[-160:])
                res.elapsed_ms = int((time.time() - t1) * 1000)
                results.append(res)
                print(f"[{'PASS' if res.passed else 'FAIL'}] {cat}/{res.id} {'；'.join(res.reasons)}")
    finally:
        rn.providers.get_provider = rn._orig_get_provider
        rn.cleanup()
    out = Path(args.out)
    golden_rate = write_report(results, args, out, time.time() - t0)
    print(f"報告：{out.resolve()}")
    return 0 if golden_rate >= GOLDEN_TARGET and all(r.passed for r in results if r.category != "golden_grade") else 1


if __name__ == "__main__":
    sys.exit(main())
