"""Fake 提供者：測試與離線開發用，不需要任何 AI。

- scripted：依序回傳的原始字串（用來模擬壞 JSON、觸發重試、指定回應）。
- 用完後：依 JSON Schema 產生最小合法實例。對本專案的四個輸出模型
  （Intake / LearningMap / Lesson / Verdict）回傳手寫的合法樣本，
  確保也能通過語意檢查（validate_map、check_citations）。
- calls：記錄每次呼叫的 system / user / model / effort（測試用來斷言「沒有呼叫 AI」）。
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from ..schemas import Effort, ProviderInfo
from .base import LLMProvider


class FakeLLM(LLMProvider):
    id = "fake"
    name = "測試用 AI"

    def __init__(self, scripted: list[str] | None = None):
        self.scripted: deque[str] = deque(scripted or [])
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, system: str, user: str, json_schema: dict[str, Any], *,
                  model: str | None = None, effort: Effort | None = None) -> str:
        self.calls.append({"system": system, "user": user, "model": model, "effort": effort,
                           "schema": json_schema.get("title")})
        if self.scripted:
            return self.scripted.popleft()
        sample = _SAMPLES.get(str(json_schema.get("title")))
        data = sample() if sample else _minimal_instance(json_schema, json_schema)
        return json.dumps(data, ensure_ascii=False)

    def info(self, refresh: bool = False) -> ProviderInfo:
        return ProviderInfo(id="fake", name=self.name, available=True, models=["fake"],
                            efforts=["low", "medium", "high"])


# ---------------------------------------------------------------------------
# 本專案輸出模型的合法樣本
# ---------------------------------------------------------------------------


def _sample_intake() -> dict[str, Any]:
    def q(qid: str, text: str) -> dict[str, Any]:
        return {"id": qid, "question": text, "options": [
            {"label": "選項一", "description": "測試用選項"},
            {"label": "選項二", "description": "測試用選項"},
        ]}

    return {"topic_slug": "fake-topic", "domain": "concept", "questions": [
        q("goal", "你學這個想達成什麼？"), q("level", "你現在的程度？"),
        q("context", "你會在哪裡用到？"), q("minutes", "每次想練多久？"),
    ]}


def _sample_map() -> dict[str, Any]:
    nodes = []
    for i in range(15):
        layer = 1 + i // 3  # 5 層，每層 3 個節點
        prereqs = [] if layer == 1 else [f"node-{(layer - 2) * 3:02d}"]
        nodes.append({"id": f"node-{i:02d}", "name": f"節點 {i}", "layer": layer,
                      "prereqs": prereqs, "summary": f"能說明第 {i} 個觀念",
                      "pitfalls": ["常見誤會甲", "常見誤會乙"]})
    return {"title": "測試學習路徑", "domain": "concept", "runner": None, "env_seed": None,
            "env_note": None, "nodes": nodes, "first": "node-00"}


def _sample_lesson() -> dict[str, Any]:
    return {
        "lecture": "這是測試用的短講：新名詞先用白話解釋。",
        "example": "SELECT 1;",
        "example_explain": ["SELECT 1：叫資料庫回傳數字 1"],
        "type": "explain",
        "question": "請用自己的話說明這個觀念。",
        "hint": "想想生活中的例子",
        "show_tables": [],
        "visuals": [],
        "ref_code": "SELECT 1;",
        "rubric": {"answer": "測試用參考答案", "accept": "意思相同即可",
                   "must_have": ["說出重點"], "traps": [{"when": "答非所問", "label": "離題"}]},
    }


def _sample_verdict() -> dict[str, Any]:
    return {"result": "ok", "met": [0], "trap": None, "feedback": "測試用回饋：答得不錯。",
            "citations": ["must_have[0]"], "misconception": None}


_SAMPLES = {
    "Intake": _sample_intake,
    "LearningMap": _sample_map,
    "Lesson": _sample_lesson,
    "Verdict": _sample_verdict,
}


# ---------------------------------------------------------------------------
# 通用：依 JSON Schema 產生最小實例
# ---------------------------------------------------------------------------


def _minimal_instance(schema: dict[str, Any], root: dict[str, Any]) -> Any:
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        return _minimal_instance(root.get("$defs", {})[name], root)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = schema[key]
            # 優先選非 null 的分支以外的最簡單分支：null 可用就用 null
            for opt in options:
                if opt.get("type") == "null":
                    return None
            return _minimal_instance(options[0], root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    t = schema.get("type")
    if t == "object":
        return {
            k: _minimal_instance(v, root)
            for k, v in schema.get("properties", {}).items()
            if k in schema.get("required", [])
        }
    if t == "array":
        n = schema.get("minItems", 0)
        return [_minimal_instance(schema.get("items", {}), root) for _ in range(n)]
    if t == "string":
        if "pattern" in schema:
            return "ab"
        return "測" * max(1, schema.get("minLength", 1))
    if t == "integer":
        return int(schema.get("minimum", 0))
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return True
    return None
