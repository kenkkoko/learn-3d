// Python runner：Pyodide（固定版本，用到時才在 module worker 裡載入；執行逾時 10 秒中止）
import { WorkerRunner } from "./worker-runner.js";

const runner = new WorkerRunner(new URL("./py-worker.js", import.meta.url), { type: "module" });
let loading = null;
const LOAD_TIMEOUT = 90000;
const RUN_TIMEOUT = 10000;

async function ensureLoaded() {
  if (!runner.worker) loading = null;
  if (!loading) {
    loading = runner.run({ op: "load" }, LOAD_TIMEOUT).then((r) => {
      if (!r.ok) throw new Error(r.error || "Pyodide 載入失敗");
    });
    loading.catch(() => { loading = null; });
  }
  return loading;
}

/** @returns {Promise<{ok:boolean, output:string, error?:string}>} */
export async function runPython(code) {
  try {
    await ensureLoaded();
    const r = await runner.run({ op: "exec", code }, RUN_TIMEOUT);
    return { ok: r.ok, output: r.output || "", error: r.error };
  } catch (e) {
    return { ok: false, output: "", error: e?.message || String(e) };
  }
}
