// SQL runner：sql.js（固定版本，用到時才在 worker 裡載入）
import { WorkerRunner } from "./worker-runner.js";

const runner = new WorkerRunner(new URL("./sql-worker.js", import.meta.url));
let loading = null;
const LOAD_TIMEOUT = 60000;
const RUN_TIMEOUT = 10000;

async function ensureLoaded() {
  if (!runner.worker) loading = null;
  if (!loading) {
    loading = runner.run({ op: "load" }, LOAD_TIMEOUT).then((r) => {
      if (!r.ok) throw new Error(r.error || "sql.js 載入失敗");
    });
    loading.catch(() => { loading = null; });
  }
  return loading;
}

/**
 * 執行 SQL：回傳最後一個有結果的查詢
 * @returns {Promise<{ok:true, columns:string[], rows:any[][], changes:number, empty:boolean} | {ok:false, error:string}>}
 */
export async function runSql(seed, code) {
  try {
    await ensureLoaded();
    const r = await runner.run({ op: "exec", seed, code }, RUN_TIMEOUT);
    if (!r.ok) return { ok: false, error: translateError(r.error) };
    const last = r.results?.[r.results.length - 1];
    if (!last) return { ok: true, columns: [], rows: [], changes: r.changes || 0, empty: true };
    return { ok: true, columns: last.columns, rows: last.values, changes: r.changes || 0, empty: false };
  } catch (e) {
    return { ok: false, error: e?.message || String(e) };
  }
}

/** 查整張表給題目顯示（表名先驗證，避免被當成 SQL 注入） */
export async function queryTable(seed, table) {
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(table)) return { ok: false, error: `表名不合法：${table}` };
  return runSql(seed, `SELECT * FROM "${table}";`);
}

function translateError(msg = "") {
  const m = String(msg);
  const hints = [
    [/no such table: (\S+)/i, (x) => `找不到資料表 ${x[1]}（表名拼錯了嗎？）`],
    [/no such column: (\S+)/i, (x) => `找不到欄位 ${x[1]}（欄位名稱拼錯，或字串忘了加單引號？）`],
    [/syntax error/i, () => `語法錯誤：${m}（常見原因：少逗號、多逗號、引號沒成對）`],
    [/incomplete input/i, () => "SQL 還沒寫完（括號或引號沒有成對？）"],
  ];
  for (const [re, fn] of hints) {
    const x = m.match(re);
    if (x) return fn(x);
  }
  return m;
}

// ---------- 比較與文字化 ----------
function normCell(v) {
  if (v === null || v === undefined) return "NULL";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  if (v instanceof Uint8Array) return `<blob ${v.length} bytes>`;
  const s = String(v);
  // 字串型數字也統一（例如 '3.0' 與 3）
  if (/^-?\d+(\.\d+)?$/.test(s)) { const n = Number(s); return Number.isInteger(n) ? String(n) : String(Math.round(n * 1e6) / 1e6); }
  return s;
}

/** 題目或參考答案有 ORDER BY 才比較順序 */
export function needsOrder(refCode = "", question = "") {
  if (/不(用|需要?|必)排序|順序不拘/.test(question)) return false;
  return /\border\s+by\b/i.test(refCode) || /排序|由(大到小|小到大|高到低|低到高)/.test(question);
}

/** 比較兩個結果：欄位數相同、列集合相同（需要時比順序） */
export function compareSql(user, ref, ordered) {
  if (!user.ok || !ref.ok) return false;
  if (user.columns.length !== ref.columns.length) return false;
  if (user.rows.length !== ref.rows.length) return false;
  // 欄名相同只是順序不同時，先依參考答案的欄位順序重排（SELECT 欄位順序不影響答案對錯）
  let userRows = user.rows;
  const un = user.columns.map((c) => String(c).toLowerCase());
  const rn = ref.columns.map((c) => String(c).toLowerCase());
  if (new Set(un).size === un.length && rn.every((c) => un.includes(c)) && rn.some((c, i) => un[i] !== c)) {
    const idx = rn.map((c) => un.indexOf(c));
    userRows = user.rows.map((r) => idx.map((i) => r[i]));
  }
  const a = userRows.map((r) => JSON.stringify(r.map(normCell)));
  const b = ref.rows.map((r) => JSON.stringify(r.map(normCell)));
  if (ordered) return a.every((x, i) => x === b[i]);
  a.sort(); b.sort();
  return a.every((x, i) => x === b[i]);
}

function width(s) {
  let w = 0;
  for (const ch of s) w += /[ᄀ-￿]/.test(ch) ? 2 : 1;
  return w;
}
const pad = (s, n) => s + " ".repeat(Math.max(0, n - width(s)));

/** 轉成等寬文字表格（送給橋接、判定畫面對照用） */
export function toTextTable(res, maxRows = 50) {
  if (!res.ok) return `錯誤：${res.error}`;
  if (res.empty) return res.changes ? `（沒有查詢結果；異動 ${res.changes} 列）` : "（沒有查詢結果）";
  const cols = res.columns.map(String);
  const rows = res.rows.slice(0, maxRows).map((r) => r.map((v) => (v === null ? "NULL" : normCell(v))));
  const ws = cols.map((c, i) => Math.min(24, Math.max(width(c), ...rows.map((r) => width(r[i] ?? "")))));
  const line = (cells) => cells.map((c, i) => pad(c, ws[i])).join(" | ");
  const out = [line(cols), ws.map((w) => "-".repeat(w)).join("-+-"), ...rows.map(line)];
  if (res.rows.length > maxRows) out.push(`…共 ${res.rows.length} 列`);
  else out.push(`（${res.rows.length} 列）`);
  return out.join("\n");
}
