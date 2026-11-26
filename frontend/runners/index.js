// 統一的執行介面：依 runner 呼叫 sql.js / Pyodide / JS 沙箱，並組成 ExecResult
import { runSql, compareSql, needsOrder, toTextTable } from "./sql.js";

const MAX_OUT = 6000;
const clip = (s) => (s.length > MAX_OUT ? s.slice(0, MAX_OUT - 20) + "\n…（輸出過長，已截斷）" : s);
const normText = (s) => String(s ?? "").replace(/\r\n/g, "\n").split("\n").map((l) => l.trimEnd()).join("\n").trim();

/**
 * 執行一段程式
 * @returns {Promise<{ok:boolean, text:string, error?:string, sql?:object}>}
 */
export async function runCode(runner, code, { seed } = {}) {
  if (runner === "sql") {
    const r = await runSql(seed, code);
    return { ok: r.ok, text: toTextTable(r), error: r.ok ? undefined : r.error, sql: r };
  }
  if (runner === "python") {
    const { runPython } = await import("./python.js");
    const r = await runPython(code);
    const text = r.ok ? r.output : [r.output, `錯誤：${r.error}`].filter(Boolean).join("\n");
    return { ok: r.ok, text: text.trimEnd() || (r.ok ? "（沒有輸出）" : ""), error: r.error };
  }
  if (runner === "js") {
    const { runJs } = await import("./js.js");
    const r = await runJs(code);
    const text = r.ok ? r.output : [r.output, `錯誤：${r.error}`].filter(Boolean).join("\n");
    return { ok: r.ok, text: text.trimEnd() || (r.ok ? "（沒有輸出）" : ""), error: r.error };
  }
  return { ok: false, text: "", error: "這個主題沒有可執行環境" };
}

/**
 * produce / debug：執行使用者程式與 ref_code 並比較
 * predict：只執行 ref_code，user_output 放使用者的文字答案，equal=false 交給橋接判斷
 * @returns {Promise<{exec: {user_output:string, ref_output:string, equal:boolean}, user?:object, ref:object} | null>}
 */
export async function buildExec({ runner, type, answer, refCode, question, seed }) {
  if (!runner || !refCode) return null;
  const ref = await runCode(runner, refCode, { seed });
  if (!ref.ok) return null; // 參考程式自己壞了：不送 exec，讓橋接用評分標準判斷
  if (type === "predict") {
    return { exec: { user_output: clip(answer), ref_output: clip(ref.text), equal: false }, ref };
  }
  if (type !== "produce" && type !== "debug") return null;
  const user = await runCode(runner, answer, { seed });
  let equal = false;
  if (user.ok) {
    if (runner === "sql") equal = compareSql(user.sql, ref.sql, needsOrder(refCode, question));
    else equal = normText(user.text) === normText(ref.text);
  }
  return { exec: { user_output: clip(user.text), ref_output: clip(ref.text), equal }, user, ref };
}
