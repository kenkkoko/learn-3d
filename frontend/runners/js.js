// JS runner：每次建立一個 sandbox="allow-scripts" 的 iframe（沒有同源權限），
// 透過 MessageChannel 回傳 console 輸出；逾時就移除 iframe。
const RUN_TIMEOUT = 5000;

const SANDBOX_SRC = `<!doctype html><meta charset="utf-8"><script>
(() => {
  const fmt = (v) => {
    if (typeof v === "string") return v;
    if (v === undefined) return "undefined";
    if (typeof v === "function") return "[Function " + (v.name || "anonymous") + "]";
    if (v instanceof Error) return v.name + ": " + v.message;
    try { return JSON.stringify(v, (k, x) => (typeof x === "bigint" ? x + "n" : x)); } catch { return String(v); }
  };
  addEventListener("message", async (ev) => {
    if (ev.source !== parent || !ev.ports[0] || ev.data !== "learn3d-run") return;
    const port = ev.ports[0];
    port.onmessage = async (m) => {
      const out = [];
      const push = (lvl) => (...a) => out.push((lvl === "error" ? "錯誤：" : lvl === "warn" ? "警告：" : "") + a.map(fmt).join(" "));
      console.log = push("log"); console.info = push("log"); console.warn = push("warn"); console.error = push("error");
      try {
        const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
        await new AsyncFunction(m.data.code)();
        port.postMessage({ ok: true, output: out.join("\\n") });
      } catch (e) {
        port.postMessage({ ok: false, output: out.join("\\n"), error: (e && e.name ? e.name + ": " : "") + (e && e.message ? e.message : String(e)) });
      }
    };
  }, { once: true });
})();
<\/script>`;

/** @returns {Promise<{ok:boolean, output:string, error?:string}>} */
export function runJs(code) {
  return new Promise((resolve) => {
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("aria-hidden", "true");
    frame.tabIndex = -1;
    frame.style.cssText = "position:absolute;width:0;height:0;border:0;visibility:hidden";
    frame.srcdoc = SANDBOX_SRC;
    let done = false;
    const finish = (r) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      frame.remove();
      resolve(r);
    };
    const timer = setTimeout(() => finish({ ok: false, output: "", error: `執行超過 ${RUN_TIMEOUT / 1000} 秒，已中止。程式可能有無窮迴圈。` }), RUN_TIMEOUT);
    frame.addEventListener("load", () => {
      const ch = new MessageChannel();
      ch.port1.onmessage = (m) => finish({ ok: !!m.data.ok, output: m.data.output || "", error: m.data.error });
      // 沙箱 iframe 的 origin 是不透明的 "null"，只能用 "*"；資料只走專屬的 MessagePort
      frame.contentWindow.postMessage("learn3d-run", "*", [ch.port2]);
      ch.port1.postMessage({ code });
    }, { once: true });
    document.body.appendChild(frame);
  });
}
