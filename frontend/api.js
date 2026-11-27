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

/**
 * mode：'bridge' 已連上橋接；'cloud' 沒有橋接時唯讀看雲端資料（手機複習）；
 *       'mock' 示範資料（只在 ?mock=1）
 */
let mode = forcedMock ? "mock" : "bridge";
export const isForcedMock = () => forcedMock;
export const getMode = () => mode;
export function useMock() { mode = "mock"; }
export function useCloud() { if (!forcedMock) mode = "cloud"; }
export function useBridge() { if (!forcedMock) mode = "bridge"; }

/** 登入的使用者：每個橋接請求都帶 X-Learn3D-User，橋接把資料存到這個帳號自己的資料夾 */
let userId = null;
export function setUser(id) { userId = id || null; }

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
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(userId && path !== "/health" ? { "X-Learn3D-User": userId } : {}),
    },
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

/** 示範模式只有一個主題：英文文法範例（mock/*-en.json） */
const mockName = (base) => `${base}-en`;

async function mockRoute(method, path, body) {
  await wait(120);
  if (path === "/health") return { ok: true, name: "learn3d-bridge", version: "mock" };
  if (path.startsWith("/providers")) return mockJson("providers");
  if (path === "/usage") {
    const now = Math.floor(Date.now() / 1000);
    return { now, providers: { claude: { updated: now - 120, windows: [
      { type: "five_hour", label: "5 小時", status: "allowed", used_percent: 34, resets_at: now + 2 * 3600 + 780 },
      { type: "seven_day", label: "每週", status: "allowed", used_percent: 12, resets_at: now + 4 * 86400 } ] } } };
  }
  if (path === "/topics" && method === "GET") return mockJson("topics");
  if (path === "/intake") {
    await wait(600);
    const it = await mockJson("intake");
    // 示範：一律產生英文文法範例；輸入 english-demo 可以看到「已存在 → 覆寫確認」流程
    const slug = String(body.topic || "").trim().toLowerCase();
    if (slug === "english-demo") it.topic_slug = slug;
    return it;
  }
  if (path === "/plan") {
    const topics = await mockJson("topics");
    if (topics.topics.some((t) => t.topic === body.slug) && !body.confirm) {
      throw new ApiError("這個主題已經有學習路徑了，要覆寫嗎？", { status: 409, needsConfirm: true });
    }
    await wait(4000);
    const progress = await mockJson("progress-en");
    return { topic: "english-demo", progress };
  }
  if (/^\/topics\/[a-z0-9-]+\/journal(\?|$)/.test(path)) {
    const q = new URLSearchParams(path.split("?")[1] || "");
    const all = (await mockJson(mockName("journal", path.split("/")[2]))).entries;
    let rows = q.get("node") ? all.filter((e) => e.node === q.get("node")) : all;
    if (q.get("before")) rows = rows.slice(rows.findIndex((e) => e.id === q.get("before")) + 1);
    const limit = Number(q.get("limit") || 30);
    return { entries: rows.slice(0, limit), has_more: rows.length > limit };
  }
  const m = path.match(/^\/topics\/([a-z0-9-]+)(\/[a-z]+)?$/);
  if (m) {
    const [, t, sub] = m;
    if (!sub && method === "GET") return mockJson(mockName("progress", t));
    if (!sub && method === "DELETE") return { ok: true };
    if (sub === "/env") return { runner: null, sql: null };
    if (sub === "/lesson") {
      await wait(2500);                       // 示範也讓人看得到出題進度
      const l = await mockJson(mockName("lesson", t));
      if (body?.node) l.node = body.node;
      if (body?.review) l.kind = "review";
      return l;
    }
    if (sub === "/followup") {
      return { answer: "（示範模式）連上本機橋接後，這裡會由你的 AI 針對這一題回答追問：例如為什麼要這樣寫、換個情況會怎樣、能不能再舉一個例子。" };
    }
    if (sub === "/grade") {
      await wait(1500);
      const g = await mockJson(mockName("grade", t));
      if (body.answer.trim().length < 3) {
        return { ...g.wrong, result: "unjudgeable", feedback: "答案太短，看不出你的想法。請用自己的話多寫一點。", danger: false, delta: 0, misconception_label: null, offtopic: true };
      }
      if (body.exec?.equal) return g.ok;
      if (g.ok_pattern && new RegExp(g.ok_pattern, "i").test(body.answer)) return g.ok;   // 沒有程式可執行的主題（英文）
      const r = { ...g.wrong };
      r.danger = body.confidence === "high";
      r.delta = body.confidence === "high" ? -2 : -1;
      return r;
    }
    if (sub === "/jump") return { current: body.node };
    if (sub === "/reset") {
      const p = await mockJson(mockName("progress", t));
      p.nodes.forEach((n) => { n.status = "unseen"; n.score = 0; n.history = []; });
      p.misconceptions = [];
      return { progress: p };
    }
  }
  throw new ApiError(`示範模式不支援 ${method} ${path}`, { status: 404 });
}

// ---------- 雲端唯讀（沒有橋接：手機或電腦沒開橋接時看紀錄） ----------
export const NEEDS_BRIDGE = "這需要電腦開著本機橋接（AI 在你的電腦上）。手機上可以看主題和作答紀錄。";

function summaryOf(topic, p, updatedAt) {
  const nodes = p?.nodes || [];
  const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
  return {
    topic, title: p?.title || topic, domain: p?.domain || null, nodes: nodes.length,
    mastered: nodes.filter((n) => n.status === "mastered").length,
    due: (p?.misconceptions || []).filter((m) => !m.retired && String(m.nextReview || "") <= today).length,
    updated: Date.parse(updatedAt || 0) / 1000 || 0,
  };
}

async function cloudRoute(method, path) {
  const cloud = await import("./cloud.js");
  if (path === "/health") return { ok: true, name: "learn3d-cloud", version: "cloud" };
  if (path.startsWith("/providers")) return { providers: [] };
  if (path === "/usage") return { now: Math.floor(Date.now() / 1000), providers: {} };
  if (path === "/topics" && method === "GET") {
    const rows = await cloud.cloudTopics();
    return { topics: rows.map((r) => summaryOf(r.topic, r.progress, r.updated_at)) };
  }
  const jm = path.match(/^\/topics\/([a-z0-9-]+)\/journal(?:\?(.*))?$/);
  if (jm && method === "GET") {
    const q = new URLSearchParams(jm[2] || "");
    return cloud.cloudJournal(jm[1], { limit: Number(q.get("limit") || 30), before: q.get("before") || undefined,
      node: q.get("node") || undefined, only: q.get("only") || undefined });
  }
  const m = path.match(/^\/topics\/([a-z0-9-]+)(\/env)?$/);
  if (m && method === "GET") {
    const r = await cloud.cloudTopic(m[1]);
    if (!r?.data?.progress) throw new ApiError(`找不到主題「${m[1]}」`, { status: 404 });
    const p = structuredClone(r.data.progress);
    if (m[2]) return { runner: p.runner || null, sql: r.data.env_sql || null };
    if (p.lastQuestion && typeof p.lastQuestion === "object") delete p.lastQuestion.rubric;
    return p;
  }
  throw new ApiError(NEEDS_BRIDGE, { status: 0, body: { needs_bridge: true } });
}

async function call(method, path, body, opts = {}) {
  if (mode === "mock") return mockRoute(method, path, body);
  if (mode === "cloud") return cloudRoute(method, path, body);
  const res = await bridgeFetch(path, { method, body, ...opts });
  if (method !== "GET") notifyChange(method, path, res);
  return res;
}

/** 會改到本機主題檔案的請求成功後，發出 learn3d:changed（網站收到就排程同步到雲端） */
function notifyChange(method, path, res) {
  let topic = null;
  const m = path.match(/^\/topics\/([a-z0-9-]+)(?:\/(lesson|grade|jump|reset))?$/);
  if (m && (m[2] || method === "DELETE")) topic = m[1];
  else if (path === "/plan") topic = res?.topic || null;
  if (topic) dispatchEvent(new CustomEvent("learn3d:changed", { detail: { topic } }));
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
  usage: () => call("GET", "/usage", undefined, { timeout: 8000 }),
  topic: (t) => call("GET", `/topics/${encodeURIComponent(t)}`),
  env: (t) => call("GET", `/topics/${encodeURIComponent(t)}/env`),
  intake: (topic, ai) => call("POST", "/intake", { topic, ai }, { timeout: 120000 }),
  plan: (req) => call("POST", "/plan", req, { timeout: 600000 }),
  lesson: (t, req) => call("POST", `/topics/${encodeURIComponent(t)}/lesson`, req, { timeout: 180000 }),
  grade: (t, req) => call("POST", `/topics/${encodeURIComponent(t)}/grade`, req, { timeout: 180000 }),
  followup: (t, req) => call("POST", `/topics/${encodeURIComponent(t)}/followup`, req, { timeout: 180000 }),
  jump: (t, node) => call("POST", `/topics/${encodeURIComponent(t)}/jump`, { node }),
  reset: (t) => call("POST", `/topics/${encodeURIComponent(t)}/reset`, { confirm: "reset" }),
  journal: (t, { limit = 30, before, node, only } = {}) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (before) q.set("before", before);
    if (node) q.set("node", node);
    if (only) q.set("only", only);
    return call("GET", `/topics/${encodeURIComponent(t)}/journal?${q}`);
  },
  remove: (t) => call("DELETE", `/topics/${encodeURIComponent(t)}`, { confirm: t }),
  // 同步（一律直接找橋接，不走 mock / cloud）
  syncManifest: () => bridgeFetch("/sync/manifest"),
  syncExport: (t) => bridgeFetch(`/sync/topics/${encodeURIComponent(t)}`, { timeout: 60000 }),
  syncImport: (t, body) => bridgeFetch(`/sync/topics/${encodeURIComponent(t)}/import`, { method: "POST", body, timeout: 60000 }),
  syncMark: (t, body) => bridgeFetch(`/sync/topics/${encodeURIComponent(t)}/mark`, { method: "POST", body }),
  syncForget: (t) => bridgeFetch(`/sync/topics/${encodeURIComponent(t)}/forget`, { method: "POST", body: {} }),
  syncDrop: (t) => bridgeFetch(`/sync/topics/${encodeURIComponent(t)}/drop`, { method: "POST", body: {} }),
  syncClaim: () => bridgeFetch("/sync/claim", { method: "POST", body: {} }),
};
