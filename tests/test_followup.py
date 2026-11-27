"""判定後追問：對或錯都能針對同一題繼續問；出下一題後結束。"""

import json

from test_api import AI, grade, start_lesson


def _ask(client, qid, msg, history=None):
    return client.post("/topics/sql/followup", json={"question_id": qid, "message": msg, "history": history or [], "ai": AI})


def test_followup_after_grade_then_closed_by_next_lesson(client, sql_topic, llm_box):
    q = start_lesson(client, llm_box)
    qid = q["question_id"]
    assert _ask(client, qid, "為什麼？").status_code == 409  # 判定前不能追問
    llm_box.set([])
    r = grade(client, qid, "select name from suppliers where rating is null", "mid",
              {"user_output": "乙", "ref_output": "乙", "equal": True})
    assert r.status_code == 200, r.text
    llm = llm_box.set([json.dumps({"answer": "因為 NULL 不是一個值，要用 IS NULL 判斷。"}, ensure_ascii=False)])
    r = _ask(client, qid, "那如果寫 = NULL 會怎樣？", [{"role": "me", "text": "為什麼？"}, {"role": "ai", "text": "因為……"}])
    assert r.status_code == 200, r.text
    assert "IS NULL" in r.json()["answer"]
    prompt = llm.calls[0]["user"]
    assert "那如果寫 = NULL 會怎樣？" in prompt and "參考答案" in prompt and "先前的追問對話" in prompt
    start_lesson(client, llm_box)  # 出下一題後，上一題的追問結束
    assert _ask(client, qid, "再問一次").status_code == 409


def test_followup_empty_message_rejected(client, sql_topic, llm_box):
    q = start_lesson(client, llm_box)
    llm_box.set([])
    grade(client, q["question_id"], "select 1", "high", {"user_output": "1", "ref_output": "1", "equal": True})
    assert _ask(client, q["question_id"], "   ").status_code == 400
