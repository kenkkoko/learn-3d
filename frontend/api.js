// 與本機橋接溝通（SPEC §3、docs/interfaces.md §5）
// - 頁面由 127.0.0.1:8766 提供 → 同源；否則 → http://127.0.0.1:8766
// - ?mock=1 → 改讀 mock/*.json（不需要橋接）
// - 所有 AI 請求只送往本機橋接，這個檔案不含任何雲端 AI 服務網址

const BRIDGE_PORT = "8766";
const DEFAULT_BRIDGE = `http://127.0.0.1:${BRIDGE_PORT}`;

const params = new URLSearchParams(location.search);
const forcedMock = params.get("mock") === "1";

function sameOriginIsBridge() {
  // 從本機打開時，頁面就是橋接提供的（任何埠，例如 LEARN3D_PORT 改過）；
  // 只有部署在公開網址時才跨來源連 127.0.0.1:8766。
  const h = location.hostname;
  return h === "127.0.0.1" || h === "localhost" || h === "[::1]";
}

export const BRIDGE_BASE = sameOriginIsBridge() ? "" : DEFAULT_BRIDGE;
const crossOrigin = BRIDGE_BASE !== "";

/** mode：'bridge' 已連上橋接；'mock' 示範資料（?mock=1 或偵測不到橋接時瀏覽用） */
let mode = forcedMock ? "mock" : "bridge";
export const isForcedMock = () => forcedMock;
export const getMode = () => mode;
export function useMock() { mode = "mock"; }
export function useBridge() { if (!forcedMock) mode = "bridge"; }

export class ApiError extends Error {
  constructor(message, { status = 0, needsConfirm = false, body = null } = {}) {
    super(message);
    this.status = status;
    this.needsConfirm = needsConfirm;
    this.body = body;
  }
}

function errorText(status, body) {
  if (body) {
    if (typeof body.error === "string") return body.error;
    const d = body.detail;
    if (d && typeof d === "object" && typeof d.error === "string") return d.error;
    if (typeof d === "string") return d;
    if (Array.isArray(d) && d[0]?.msg) return `資料格式不符：${d[0].msg}`;
  }
  if (status === 429) return "呼叫太頻繁了，請稍等一分鐘再試";
  if (status === 403) return "橋接拒絕了這個網頁的來源（Origin 不在白名單）";
  if (status === 503) return "這題暫時無法產生／判定，請再試一次或換一個節點";
  return `橋接回應錯誤（HTTP ${status}）`;
}

function needsConfirmOf(body) {
  return !!(body && (body.needs_confirm || body.detail?.needs_confirm));
}

async function bridgeFetch(path, { method = "GET", body, timeout = 30000, signal } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(new DOMException("timeout", "TimeoutError")), timeout);
  const onAbort = () => ctrl.abort(signal.reason);
  signal?.addEventListener("abort", onAbort, { once: true });
  const init = {
    method,
    mode: "cors",
    signal: ctrl.signal,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  };
  // Local Network Access：公開網站連到 127.0.0.1 時先宣告目標是 loopback（不支援的瀏覽器會忽略）
  if (crossOrigin) init.targetAddressSpace = "loopback";
  let res;
  try {
    res = await fetch(BRIDGE_BASE + path, init);
  } catch (e) {
    if (e?.name === "TimeoutError" || ctrl.signal.reason?.name === "TimeoutError") {
      throw new ApiError("橋接太久沒有回應，請再試一次", { status: 0 });
    }
    throw new ApiError("連不到本機橋接（127.0.0.1:8766），請確認已啟動", { status: 0 });
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
  let data = null;
  try { data = await res.json(); } catch { data = null; }
  if (!res.ok) {
    throw new ApiError(errorText(res.status, data), { status: res.status, needsConfirm: needsConfirmOf(data), body: data });
  }
  return data;
}

// ---------- mock ----------
const mockCache = new Map();
async function mockJson(name) {
  if (!mockCache.has(name)) {
    mockCache.set(name, fetch(new URL(`./mock/${name}.json`, import.meta.url)).then((r) => {
      if (!r.ok) throw new ApiError(`找不到示範資料 ${name}.json`, { status: r.status });
      return r.json();
    }));
  }
  return structuredClone(await mockCache.get(name));
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function mockRoute(method, path, body) {
  await wait(120);
  if (path === "/health") return { ok: true, name: "learn3d-bridge", version: "mock" };
  if (path.startsWith("/providers")) return mockJson("providers");
  if (path === "/topics" && method === "GET") return mockJson("topics");
  if (path === "/intake") {
    await wait(600);
    const it = await mockJson("intake");
    // 示範：英文主題直接當代號（輸入 SQL 可以看到「已存在 → 覆寫確認」流程）
    const slug = String(body.topic || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    if (/^[a-z0-9-]{2,30}$/.test(slug)) it.topic_slug = slug;
    return it;
  }
  if (path === "/plan") {
    const topics = await mockJson("topics");
    if (topics.topics.some((t) => t.topic === body.slug) && !body.confirm) {
      throw new ApiError("這個主題已經有學習路徑了，要覆寫嗎？", { status: 409, needsConfirm: true });
    }
    await wait(1800);
    const progress = await mockJson("progress");
    return { topic: "sql", progress };
  }
  if (/^\/topics\/[a-z0-9-]+\/journal(\?|$)/.test(path)) {
    const q = new URLSearchParams(path.split("?")[1] || "");
    const all = (await mockJson("journal")).entries;
    let rows = q.get("node") ? all.filter((e) => e.node === q.get("node")) : all;
    if (q.get("before")) rows = rows.slice(rows.findIndex((e) => e.id === q.get("before")) + 1);
    const limit = Number(q.get("limit") || 30);
    return { entries: rows.slice(0, limit), has_more: rows.length > limit };
  }
  const m = path.match(/^\/topics\/([a-z0-9-]+)(\/[a-z]+)?$/);
  if (m) {
    const [, , sub] = m;
    if (!sub && method === "GET") return mockJson("progress");
    if (!sub && method === "DELETE") return { ok: true };
    if (sub === "/env") return mockJson("env");
    if (sub === "/lesson") {
      await wait(700);
      const l = await mockJson("lesson");
      if (body?.node) l.node = body.node;
      if (body?.review) l.kind = "review";
      return l;
    }
    if (sub === "/grade") {
      await wait(700);
      const g = await mockJson("grade");
      if (body.answer.trim().length < 3) {
        return { ...g.wrong, result: "unjudgeable", feedback: "答案太短，看不出你的想法。請用自己的話多寫一點。", danger: false, delta: 0, misconception_label: null, offtopic: true };
      }
      if (body.exec?.equal) return g.ok;
      const r = { ...g.wrong };
      r.danger = body.confidence === "high";
      r.delta = body.confidence === "high" ? -2 : -1;
      return r;
    }
    if (sub === "/jump") return { current: body.node };
    if (sub === "/reset") {
      const p = await mockJson("progress");
      p.nodes.forEach((n) => { n.status = "unseen"; n.score = 0; n.history = []; });
      p.misconceptions = [];
      return { progress: p };
    }
  }
  throw new ApiError(`示範模式不支援 ${method} ${path}`, { status: 404 });
}

async function call(method, path, body, opts = {}) {
  if (mode === "mock") return mockRoute(method, path, body);
  return bridgeFetch(path, { method, body, ...opts });
}

// ---------- 偵測 ----------
/**
 * 查 loopback-network 權限（Local Network Access）。不支援時回 'unsupported'。
 * 不查舊的 'local-network-access' 名稱（舊版 Chrome 會當掉）。
 */
export async function loopbackPermission() {
  if (!crossOrigin) return "granted"; // 同源（頁面由橋接提供）不需要權限
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") return "granted"; // 本機開發頁面不觸發 LNA
  if (!navigator.permissions?.query) return "unsupported";
  try {
    const s = await navigator.permissions.query({ name: "loopback-network" });
    return s.state;
  } catch {
    return "unsupported";
  }
}

/** 探測橋接是否在線（不受 mock 模式影響） */
export async function probeBridge() {
  if (forcedMock) return { ok: true, mock: true };
  try {
    const h = await bridgeFetch("/health", { timeout: 2500 });
    return { ok: !!h?.ok, info: h };
  } catch (e) {
    return { ok: false, error: e };
  }
}

// ---------- 路由 ----------
export const api = {
  health: () => call("GET", "/health"),
  providers: (refresh = false) => call("GET", `/providers${refresh ? "?refresh=1" : ""}`, undefined, { timeout: 20000 }),
  topics: () => call("GET", "/topics"),
  topic: (t) => call("GET", `/topics/${encodeURIComponent(t)}`),
  env: (t) => call("GET", `/topics/${encodeURIComponent(t)}/env`),
  intake: (topic, ai) => call("POST", "/intake", { topic, ai }, { timeout: 120000 }),
  plan: (req) => call("POST", "/plan", req, { timeout: 600000 }),
  lesson: (t, req) => call("POST", `/topics/${encodeURIComponent(t)}/lesson`, req, { timeout: 180000 }),
  grade: (t, req) => call("POST", `/topics/${encodeURIComponent(t)}/grade`, req, { timeout: 180000 }),
  jump: (t, node) => call("POST", `/topics/${encodeURIComponent(t)}/jump`, { node }),
  reset: (t) => call("POST", `/topics/${encodeURIComponent(t)}/reset`, { confirm: "reset" }),
  journal: (t, { limit = 30, before, node } = {}) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (before) q.set("before", before);
    if (node) q.set("node", node);
    return call("GET", `/topics/${encodeURIComponent(t)}/journal?${q}`);
  },
  remove: (t) => call("DELETE", `/topics/${encodeURIComponent(t)}`, { confirm: t }),
};
