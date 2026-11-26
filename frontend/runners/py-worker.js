// Pyodide 執行緒（module worker）：用到時才載入，逾時由主執行緒 terminate
const PYODIDE_BASE = "https://cdn.jsdelivr.net/npm/pyodide@0.26.4/";
let pyodide = null;

async function ready() {
  if (!pyodide) {
    const { loadPyodide } = await import(PYODIDE_BASE + "pyodide.mjs");
    pyodide = await loadPyodide({ indexURL: PYODIDE_BASE });
  }
  return pyodide;
}

self.onmessage = async (ev) => {
  const { id, op, code } = ev.data || {};
  try {
    const py = await ready();
    if (op === "load") {
      self.postMessage({ id, ok: true });
      return;
    }
    let out = "";
    py.setStdout({ batched: (s) => { out += s + "\n"; } });
    py.setStderr({ batched: (s) => { out += s + "\n"; } });
    // 每次用乾淨的命名空間，避免上一段程式的變數殘留
    const ns = py.globals.get("dict")();
    try {
      await py.runPythonAsync(code, { globals: ns });
      self.postMessage({ id, ok: true, output: out });
    } catch (e) {
      const msg = String(e?.message || e);
      // 只留最後幾行（Traceback 前面是 Pyodide 內部堆疊）
      const lines = msg.trim().split("\n").filter((l) => !/^\s*[\^~]+\s*$/.test(l));
      const tail = lines.slice(-2).join("\n");
      self.postMessage({ id, ok: false, output: out, error: tail });
    } finally {
      ns.destroy();
    }
  } catch (e) {
    self.postMessage({ id, ok: false, output: "", error: String(e?.message || e) });
  }
};
