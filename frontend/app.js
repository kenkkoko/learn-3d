// Learn 3D 前端主程式：三欄 UI、主題房間、3D 星雲、問答面板（docs/interfaces.md §7）
import { api, ApiError, getMode, useMock, useBridge, isForcedMock, probeBridge, loopbackPermission } from "./api.js";
import { runCode, buildExec } from "./runners/index.js";
import { queryTable } from "./runners/sql.js";

const MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
const SETTINGS_KEY = "learn3d.settings.v1";

const STATUS_TEXT = { unseen: "未學", weak: "不熟", shaky: "半熟", mastered: "熟練" };
const TYPE_TEXT = { predict: "預測結果", debug: "找錯並修正", explain: "用自己的話解釋", produce: "動手寫" };
const CONF_TEXT = { high: "🟢 有把握", mid: "🟡 不太確定", low: "🔴 用猜的" };
const RESULT_TEXT = { ok: "✅ 正確", partial: "🟡 部分正確", wrong: "❌ 錯誤", unjudgeable: "❔ 無法判定" };
const FALLBACK_MSG = "這題暫時無法產生／判定，請再試一次或換一個節點";

// ---------- 小工具 ----------
const $ = (sel, root = document) => root.querySelector(sel);
let uid = 0;
const nextId = (p = "id") => `${p}-${++uid}`;

/** 建立元素；文字一律用 textContent（AI 輸出不當 HTML） */
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

function todayTW() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(new Date());
}

let toastTimer = null;
function toast(msg, isErr = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, isErr ? 6000 : 3200);
}

const errMsg = (e) => (e instanceof ApiError ? e.message : e?.message || String(e));

// ---------- 設定（localStorage，失敗也能用） ----------
function loadSettings() {
  try {
    const s = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "null");
    if (s && typeof s === "object") return s;
  } catch { /* 私密視窗或被封鎖 */ }
  return {};
}
function saveSettings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* 忽略 */ }
}
const settings = Object.assign({ ai: {}, view: "3d" }, loadSettings());

// ---------- 狀態 ----------
const state = {
  bridge: "checking", // ok | off | mock | checking
  providers: [],
  topics: [],
  topic: null,
  progress: null,
  envCache: new Map(), // topic → Promise<string|null>
  selected: null,
  view: settings.view === "list" ? "list" : "3d",
  lastReq: null,
  lesson: null,
};
let nebula = null;
let sceneMod = null;
let webgl = true;

const app = $("#app");
const mqMobile = window.matchMedia("(max-width: 767px)");
const isMobile = () => mqMobile.matches;

// ======================================================================
// 橋接偵測
// ======================================================================
function setBridgeStatus(kind, text) {
  state.bridge = kind;
  const el = $("#bridgeStatus");
  el.dataset.state = kind;
  $(".txt", el).textContent = text;
  $("#mockBadge").hidden = getMode() !== "mock";
}

function showBridgeCard(variant) {
  const card = $("#bridgeCard");
  const note = $("#bridgeCardNote");
  const title = $("#bridgeCardTitle");
  const retry = $("#retryBridge");
  if (variant === "prompt") {
    title.textContent = "要連線到你電腦上的橋接嗎？";
    note.textContent = "瀏覽器會詢問是否允許這個網站連到本機（127.0.0.1）。按「連線到本機橋接」後請選允許；在那之前先用示範資料瀏覽。";
    retry.textContent = "連線到本機橋接";
  } else if (variant === "denied") {
    title.textContent = "瀏覽器封鎖了本機連線";
    note.textContent = "請到網址列左側的「網站設定」，把「本機網路存取（Loopback network）」改成允許，再按重新偵測。現在先用示範資料瀏覽。";
    retry.textContent = "重新偵測";
  } else {
    title.textContent = "還沒偵測到本機橋接";
    note.textContent = "現在先用示範資料瀏覽；啟動後按「重新偵測」。";
    retry.textContent = "重新偵測";
  }
  card.hidden = false;
}

async function detectBridge({ userGesture = false } = {}) {
  if (isForcedMock()) {
    setBridgeStatus("mock", "示範模式（?mock=1），不連橋接");
    return;
  }
  setBridgeStatus("checking", "偵測橋接中…");
  const perm = await loopbackPermission();
  if (perm === "denied") {
    useMock();
    setBridgeStatus("off", "瀏覽器封鎖了本機連線（示範資料）");
    showBridgeCard("denied");
    return;
  }
  // 權限還沒決定時，不在載入時自動連線，等使用者按按鈕（避免一進站就跳權限詢問）
  if (perm === "prompt" && !userGesture) {
    useMock();
    setBridgeStatus("off", "尚未連線到本機橋接（示範資料）");
    showBridgeCard("prompt");
    return;
  }
  const r = await probeBridge();
  if (r.ok) {
    useBridge();
    $("#bridgeCard").hidden = true;
    setBridgeStatus("ok", "橋接已連線");
  } else {
    useMock();
    setBridgeStatus("off", "未偵測到橋接（示範資料）");
    showBridgeCard("off");
  }
}

// ======================================================================
// AI 設定
// ======================================================================
async function loadProviders(refresh = false) {
  try {
    const r = await api.providers(refresh);
    state.providers = r.providers || [];
  } catch (e) {
    state.providers = [];
    if (refresh) toast(errMsg(e), true);
  }
  renderAiSettings();
  if (state.bridge === "ok") {
    const names = state.providers.filter((p) => p.available).map((p) => p.name);
    setBridgeStatus("ok", names.length ? `橋接已連線 · ${names.join("、")}` : "橋接已連線 · 沒有可用的 AI 工具");
  }
}

function defaultChoice(role) {
  const avail = state.providers.filter((p) => p.available);
  const p = avail[0] || state.providers[0];
  if (!p) return { provider: "claude", model: "", effort: "" };
  const want = role === "plan" ? ["high", "xhigh", "medium"] : ["low", "medium"];
  const effort = want.find((x) => p.efforts?.includes(x)) || "";
  // 規劃用強模型一次、日常用便宜模型（SPEC P0-9）；只在模型清單裡真的有時才指定
  const pref = p.id === "claude" ? (role === "plan" ? "opus" : "haiku") : "";
  const model = pref && p.models?.includes(pref) ? pref : "";
  return { provider: p.id, model, effort };
}

function getChoice(role) {
  const saved = settings.ai?.[role];
  const ok = saved && state.providers.some((p) => p.id === saved.provider);
  return ok ? { ...saved } : defaultChoice(role);
}

/** 送給橋接的 AiChoice */
function aiChoice(role) {
  const c = getChoice(role);
  return { provider: c.provider || "claude", model: c.model || null, effort: c.effort || null };
}

function fillSelect(sel, opts, value) {
  sel.replaceChildren(...opts.map((o) => h("option", { value: o.value, disabled: o.disabled || null, selected: o.value === value || null }, o.label)));
  sel.value = opts.some((o) => o.value === value) ? value : opts[0]?.value ?? "";
}

function renderAiSettings() {
  for (const group of document.querySelectorAll(".ai-group")) {
    const role = group.dataset.role;
    const c = getChoice(role);
    const pSel = $('[data-k="provider"]', group);
    const mSel = $('[data-k="model"]', group);
    const eSel = $('[data-k="effort"]', group);
    const provOpts = state.providers.length
      ? state.providers.map((p) => ({ value: p.id, label: p.available ? p.name : `${p.name}（不可用）`, disabled: !p.available }))
      : [{ value: "claude", label: "（偵測不到 AI 工具）" }];
    fillSelect(pSel, provOpts, c.provider);
    const prov = state.providers.find((p) => p.id === pSel.value);
    fillSelect(mSel, [{ value: "", label: "（工具預設）" }, ...(prov?.models || []).map((m) => ({ value: m, label: m }))], c.model || "");
    fillSelect(eSel, [{ value: "", label: "（工具預設）" }, ...(prov?.efforts || []).map((m) => ({ value: m, label: m }))], c.effort || "");
    const onChange = (ev) => {
      settings.ai = settings.ai || {};
      const next = { provider: pSel.value, model: mSel.value, effort: eSel.value };
      if (ev.target === pSel) { next.model = ""; next.effort = defaultChoice(role).effort; }
      settings.ai[role] = next;
      saveSettings();
      if (ev.target === pSel) renderAiSettings();
    };
    pSel.onchange = mSel.onchange = eSel.onchange = onChange;
  }
}

// ======================================================================
// 主題房間（左欄）
// ======================================================================
async function loadTopics() {
  try {
    const r = await api.topics();
    state.topics = r.topics || [];
  } catch (e) {
    state.topics = [];
    toast(errMsg(e), true);
  }
  state.topics.sort((a, b) => (b.updated || 0) - (a.updated || 0));
  renderRooms();
}

function renderRooms() {
  const ul = $("#roomList");
  if (!state.topics.length) {
    ul.replaceChildren(h("li", { class: "muted pad" }, "還沒有主題。按上面的「＋ 學新主題」開始。"));
    return;
  }
  ul.replaceChildren(...state.topics.map((t) => {
    const pct = t.nodes ? Math.round((t.mastered / t.nodes) * 100) : 0;
    const initial = (t.title || t.topic).trim().charAt(0).toUpperCase();
    const btn = h("button", {
      type: "button", class: "room", "aria-current": t.topic === state.topic ? "true" : "false",
      "aria-label": `${t.title}，熟練 ${t.mastered}/${t.nodes}${t.due ? `，${t.due} 則迷思待複習` : ""}`,
      onclick: () => { selectTopic(t.topic); closeDrawer(); },
    },
    h("span", { class: "room-avatar", style: `--p:${pct}`, dataset: { initial }, "aria-hidden": "true" }),
    h("span", { class: "room-main" },
      h("span", { class: "room-title", text: t.title || t.topic }),
      h("span", { class: "room-sub", text: `熟練 ${t.mastered}/${t.nodes}` })),
    t.due ? h("span", { class: "badge", title: `${t.due} 則迷思到期`, "aria-hidden": "true" }, String(t.due)) : h("span"));
    return h("li", null, btn);
  }));
}

// ======================================================================
// 選主題 / 進度
// ======================================================================
function nodeMap() {
  return new Map((state.progress?.nodes || []).map((n) => [n.id, n]));
}
const passed = (n) => n && (n.status === "shaky" || n.status === "mastered");
function isLocked(n, map = nodeMap()) {
  return (n.prereqs || []).some((p) => map.has(p) && !passed(map.get(p)));
}
function dueMisconceptions() {
  const d = todayTW();
  return (state.progress?.misconceptions || []).filter((m) => !m.retired && (m.nextReview || "") <= d);
}

async function selectTopic(slug) {
  if (!slug) return;
  state.topic = slug;
  state.selected = null;
  journal.rows = []; journal.stale = true;
  if (!$("#journal").hidden) loadJournal(true);
  try { history.replaceState(null, "", `${location.search}#${encodeURIComponent(slug)}`); } catch { /* 忽略 */ }
  renderRooms();
  $("#emptyState").hidden = true;
  $("#topicView").hidden = false;
  $("#topicTitle").textContent = "載入中…";
  try {
    state.progress = await api.topic(slug);
  } catch (e) {
    toast(errMsg(e), true);
    state.progress = null;
    $("#topicTitle").textContent = "讀取失敗";
    return;
  }
  renderTopic(true);
}

function renderTopic(rebuildScene = false) {
  const p = state.progress;
  if (!p) return;
  const nodes = p.nodes || [];
  const mastered = nodes.filter((n) => n.status === "mastered").length;
  const learned = nodes.filter((n) => n.status !== "unseen").length;
  const cur = nodes.find((n) => n.id === p.current);
  $("#topicTitle").textContent = p.title || p.topic;
  $("#mobileTitle").textContent = p.title || p.topic;
  $("#topicSlug").textContent = p.topic || state.topic;
  $("#topicMeta").replaceChildren(...[
    h("span", null, "熟練 ", h("b", null, `${mastered}/${nodes.length}`)),
    h("span", null, "學過 ", h("b", null, `${learned}`), " 個節點"),
    cur ? h("span", null, "目前：", h("b", null, cur.name)) : null,
    p.stats ? h("span", null, "答對 ", h("b", null, `${p.stats.correct ?? 0}/${p.stats.answered ?? 0}`)) : null,
  ].filter(Boolean));
  const due = dueMisconceptions().length;
  const rb = $("#reviewBtn");
  rb.hidden = due === 0;
  rb.textContent = `複習 ${due} 則`;

  // 同步左欄數字
  const t = state.topics.find((x) => x.topic === state.topic);
  if (t) { t.mastered = mastered; t.nodes = nodes.length; t.due = due; renderRooms(); }

  renderViz(rebuildScene);
  renderList();
  renderNodeCard();
}

function sceneData() {
  const map = nodeMap();
  return {
    current: state.progress?.current || null,
    nodes: (state.progress?.nodes || []).map((n) => ({
      id: n.id, name: n.name, layer: n.layer, prereqs: n.prereqs || [], status: n.status || "unseen", locked: isLocked(n, map),
    })),
  };
}

async function renderViz(rebuild) {
  const map = $("#map");
  const list = $("#listView");
  const use3d = state.view === "3d" && webgl;
  map.hidden = !use3d;
  list.hidden = use3d;
  $("#view3d").setAttribute("aria-pressed", String(use3d));
  $("#viewList").setAttribute("aria-pressed", String(!use3d));
  if (!use3d) {
    nebula?.dispose();
    nebula = null;
    return;
  }
  if (nebula && !rebuild) {
    nebula.update(sceneData());
    return;
  }
  nebula?.dispose();
  nebula = null;
  try {
    sceneMod = sceneMod || (await import("./scene.js"));
    if (!sceneMod.webglAvailable()) throw new Error("no webgl");
    nebula = sceneMod.createNebula(map, sceneData(), { onSelect: (id) => selectNode(id, { fromScene: true }) });
    window.__learn3d = { get nodeCount() { return nebula?.count ?? 0; } }; // 給驗證腳本讀節點數
    if (state.selected) nebula.select(state.selected);
  } catch (e) {
    console.warn("3D 星雲無法啟動，改用清單檢視：", e);
    webgl = false;
    $("#view3d").disabled = true;
    renderViz(false);
  }
}

function setView(v) {
  state.view = v;
  settings.view = v;
  saveSettings();
  renderViz(true);
}

// ---------- 2D 清單（依層、鍵盤可操作） ----------
function renderList() {
  const box = $("#listView");
  const p = state.progress;
  if (!p) return box.replaceChildren();
  const map = nodeMap();
  const layers = new Map();
  for (const n of p.nodes || []) {
    if (!layers.has(n.layer)) layers.set(n.layer, []);
    layers.get(n.layer).push(n);
  }
  const sections = [...layers.keys()].sort((a, b) => a - b).map((L) => {
    const hid = nextId("layer");
    return h("section", { class: "layer", "aria-labelledby": hid },
      h("h2", { id: hid }, `第 ${L} 層${L === 1 ? "（起點）" : ""}`),
      h("ul", null, layers.get(L).map((n) => {
        const locked = isLocked(n, map);
        return h("li", null, h("button", {
          type: "button",
          class: `lnode${locked ? " locked" : ""}${n.id === p.current ? " current" : ""}`,
          "aria-pressed": String(n.id === state.selected),
          dataset: { id: n.id },
          "aria-label": `${n.name}，${STATUS_TEXT[n.status] || "未學"}${locked ? "，先修還沒到黃燈" : ""}${n.id === p.current ? "，目前節點" : ""}`,
          onclick: () => selectNode(n.id),
        },
        h("i", { class: "lg", dataset: { s: n.status || "unseen" }, "aria-hidden": "true" }),
        h("span", { class: "ln-name", text: n.name }),
        h("span", { class: "ln-tag", "aria-hidden": "true" }, STATUS_TEXT[n.status] || "未學")));
      })));
  });
  box.replaceChildren(...sections);
}

// 方向鍵在清單節點間移動
$("#listView").addEventListener("keydown", (ev) => {
  const keys = ["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"];
  if (!keys.includes(ev.key)) return;
  const btns = [...ev.currentTarget.querySelectorAll(".lnode")];
  const i = btns.indexOf(document.activeElement);
  if (i < 0) return;
  ev.preventDefault();
  let j = i;
  if (ev.key === "ArrowDown" || ev.key === "ArrowRight") j = Math.min(btns.length - 1, i + 1);
  if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") j = Math.max(0, i - 1);
  if (ev.key === "Home") j = 0;
  if (ev.key === "End") j = btns.length - 1;
  btns[j].focus();
});

// ---------- 節點卡 ----------
function selectNode(id, { fromScene = false } = {}) {
  state.selected = id || null;
  if (!fromScene) nebula?.select(state.selected);
  document.querySelectorAll(".lnode").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.id === state.selected)));
  renderNodeCard();
}

function renderNodeCard() {
  const card = $("#nodeCard");
  const n = state.selected && nodeMap().get(state.selected);
  if (!n) { card.hidden = true; card.replaceChildren(); return; }
  const map = nodeMap();
  const locked = isLocked(n, map);
  const mis = (state.progress.misconceptions || []).filter((m) => m.node === n.id && !m.retired);
  const hist = (n.history || []).slice(-4).reverse();
  const tid = "nodeCardTitle";
  const prereqNames = (n.prereqs || []).map((p) => map.get(p)?.name || p);
  card.replaceChildren(...[
    h("button", { type: "button", class: "icon-btn nc-close", "aria-label": "關閉節點卡", onclick: () => selectNode(null) },
      svgIcon("M6 6l12 12M18 6L6 18")),
    h("div", { class: "eyebrow" }, `第 ${n.layer} 層${n.id === state.progress.current ? " · 目前節點" : ""}`),
    h("h2", { class: "nc-name", id: tid, text: n.name }),
    h("div", { class: "row" },
      h("span", { class: "status-chip", dataset: { s: n.status || "unseen" } }, STATUS_TEXT[n.status] || "未學"),
      h("span", { class: "label" }, `分數 ${n.score ?? 0}/6`)),
    h("p", { class: "nc-summary", style: "margin-top:10px", text: n.summary || "" }),
    prereqNames.length ? h("div", { class: "nc-sec" }, h("h3", null, "先修"), h("div", { class: "muted", style: "font-size:13px" }, prereqNames.join("、"))) : null,
    locked ? h("div", { class: "nc-lock" }, "先修節點還沒到黃燈（半熟），建議先練先修；想挑戰也可以直接練。") : null,
    h("div", { class: "nc-sec" }, h("h3", null, "最近紀錄"),
      hist.length
        ? h("ul", { class: "nc-hist" }, hist.map((x) => h("li", null,
          h("span", null, x.date || ""), h("span", null, TYPE_TEXT[x.type] || x.type || ""),
          h("span", null, (CONF_TEXT[x.confidence] || "").slice(0, 2)), h("span", null, RESULT_TEXT[x.result] || x.result || ""))))
        : h("div", { class: "muted", style: "font-size:13px" }, "還沒練過")),
    h("div", { class: "nc-sec" }, h("h3", null, "相關迷思"),
      mis.length
        ? h("ul", { style: "padding:0;margin:0" }, mis.map((m) => h("li", { class: "mis-item" },
          h("b", { text: m.label }),
          m.right ? h("small", { text: `正確：${m.right}` }) : null,
          m.nextReview ? h("div", { class: "label", style: "margin-top:2px" }, `下次複習 ${m.nextReview}`) : null)))
        : h("div", { class: "muted", style: "font-size:13px" }, "目前沒有")),
    (n.pitfalls || []).length ? h("div", { class: "nc-sec" }, h("h3", null, "常見誤會"),
      h("ul", null, n.pitfalls.map((x) => h("li", { text: x })))) : null,
    h("div", { class: "nc-actions" },
      h("button", { type: "button", class: "btn btn-primary small", onclick: () => startLesson({ node: n.id }) }, "從這裡練")),
  ].filter(Boolean));
  card.hidden = false;
}

function svgIcon(d) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", d);
  s.append(p);
  return s;
}

// ======================================================================
// 面板與抽屜
// ======================================================================
let drawerTrigger = null;
function openDrawer(side, trigger) {
  if (!isMobile()) return;
  drawerTrigger = trigger || document.activeElement;
  app.dataset.drawer = side;
  $("#scrim").hidden = false;
  $("#stage").inert = true;
  $(".mobilebar").inert = true;
  $(side === "left" ? "#panel" : "#sidebar").inert = true;
  $(side === "left" ? "#sidebar" : "#panel").inert = false;
  $("#openSidebar").setAttribute("aria-expanded", String(side === "left"));
  $("#openPanelMobile").setAttribute("aria-expanded", String(side === "right"));
  const target = side === "left" ? $("#closeSidebar") : $("#closePanel");
  requestAnimationFrame(() => target.focus({ preventScroll: true }));
}
function closeDrawer() {
  if (!app.dataset.drawer) return;
  const side = app.dataset.drawer;
  delete app.dataset.drawer;
  $("#scrim").hidden = true;
  $("#stage").inert = false;
  $(".mobilebar").inert = false;
  $("#sidebar").inert = false;
  $("#panel").inert = false;
  $("#openSidebar").setAttribute("aria-expanded", "false");
  $("#openPanelMobile").setAttribute("aria-expanded", "false");
  if (side === "right") app.classList.remove("panel-open");
  const t = drawerTrigger;
  drawerTrigger = null;
  if (t && document.contains(t) && !t.closest("[inert]")) t.focus({ preventScroll: true });
}

function openPanel(trigger) {
  app.classList.add("panel-open");
  $("#panelTab").setAttribute("aria-expanded", "true");
  if (isMobile()) openDrawer("right", trigger);
}
function closePanel() {
  if (isMobile() && app.dataset.drawer === "right") { closeDrawer(); return; }
  app.classList.remove("panel-open");
  $("#panelTab").setAttribute("aria-expanded", "false");
}

// 跨過手機斷點時收起抽屜與面板，避免殘留 inert 狀態
mqMobile.addEventListener("change", () => {
  drawerTrigger = null;
  closeDrawer();
  app.classList.remove("panel-open");
  $("#panelTab").setAttribute("aria-expanded", "false");
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && app.dataset.drawer && !document.querySelector("dialog[open]")) {
    ev.preventDefault();
    closeDrawer();
  }
});

// ======================================================================
// 桌機版面：左右欄可收合、可拖曳調整寬度（寬度記在 localStorage）
// ======================================================================
const LAYOUT_KEY = "learn3d.layout";
const MIN_CENTER = 280;              // 中間星雲至少保留的寬度
const SIDE = { min: 200, max: 480, def: 288 };
const panelDefault = () => Math.round(Math.min(460, innerWidth * 0.38));
const panelMax = () => Math.max(360, Math.min(innerWidth * 0.75, innerWidth - curSide() - MIN_CENTER));
const PANEL_MIN = 320;

const layout = (() => {
  try { return { side: null, panel: null, collapsed: false, ...JSON.parse(localStorage.getItem(LAYOUT_KEY) || "{}") }; }
  catch { return { side: null, panel: null, collapsed: false }; }
})();
const saveLayout = () => { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch { /* 無痕模式等：只是不記住 */ } };
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
function curSide() { return app.classList.contains("side-collapsed") ? 0 : (layout.side ?? SIDE.def); }
function curPanel() { return layout.panel ?? panelDefault(); }

function applyLayout() {
  // 一律寫成純 px：Chrome 無法在 0px 與 min(…vw) 之間做動畫，面板會卡在 0 寬
  app.style.setProperty("--side-w", `${clamp(layout.side ?? SIDE.def, SIDE.min, SIDE.max)}px`);
  app.style.setProperty("--panel-w", `${Math.round(clamp(curPanel(), PANEL_MIN, panelMax()))}px`);
  const sideH = $("#resizeSide"), panelH = $("#resizePanel");
  sideH.setAttribute("aria-valuemin", SIDE.min); sideH.setAttribute("aria-valuemax", SIDE.max);
  sideH.setAttribute("aria-valuenow", Math.round(layout.side ?? SIDE.def));
  panelH.setAttribute("aria-valuemin", PANEL_MIN); panelH.setAttribute("aria-valuemax", Math.round(panelMax()));
  panelH.setAttribute("aria-valuenow", Math.round(clamp(curPanel(), PANEL_MIN, panelMax())));
  const wide = curPanel() >= innerWidth * 0.55;
  const w = $("#widenPanel");
  w.setAttribute("aria-pressed", String(wide));
  w.setAttribute("aria-label", wide ? "恢復問答面板寬度" : "加寬問答面板");
}

function setSideCollapsed(collapsed, { focus = true } = {}) {
  app.classList.toggle("side-collapsed", collapsed);
  $("#sideTab").hidden = !collapsed;
  $("#collapseSidebar").setAttribute("aria-expanded", String(!collapsed));
  $("#sideTab").setAttribute("aria-expanded", String(!collapsed));
  layout.collapsed = collapsed;
  saveLayout();
  applyLayout();
  if (focus) (collapsed ? $("#sideTab") : $("#collapseSidebar")).focus({ preventScroll: true });
}

/**
 * 拖曳把手：滑鼠、觸控、觸控筆都用 Pointer Events；鍵盤方向鍵每次 16px（Shift 64px）；
 * Home / End 到最小 / 最大；雙擊恢復預設。
 */
function makeResizer(handle, { get, set, min, max, reset, dir }) {
  let startX = 0, startW = 0;
  const onMove = (ev) => { set(clamp(startW + dir * (ev.clientX - startX), min(), max())); applyLayout(); };
  const onUp = (ev) => {
    handle.releasePointerCapture?.(ev.pointerId);
    handle.removeEventListener("pointermove", onMove);
    handle.removeEventListener("pointerup", onUp);
    handle.removeEventListener("pointercancel", onUp);
    handle.classList.remove("active");
    app.classList.remove("resizing");
    saveLayout();
  };
  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    startX = ev.clientX; startW = get();
    handle.setPointerCapture?.(ev.pointerId);
    handle.classList.add("active");
    app.classList.add("resizing");
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });
  handle.addEventListener("keydown", (ev) => {
    const step = ev.shiftKey ? 64 : 16;
    let w = null;
    if (ev.key === "ArrowLeft") w = get() - dir * step;
    else if (ev.key === "ArrowRight") w = get() + dir * step;
    else if (ev.key === "Home") w = min();
    else if (ev.key === "End") w = max();
    if (w == null) return;
    ev.preventDefault();
    set(clamp(w, min(), max())); applyLayout(); saveLayout();
  });
  handle.addEventListener("dblclick", () => { reset(); applyLayout(); saveLayout(); });
}

makeResizer($("#resizeSide"), {
  dir: 1, get: () => layout.side ?? SIDE.def, set: (w) => { layout.side = w; },
  min: () => SIDE.min, max: () => Math.min(SIDE.max, innerWidth - curPanelOpen() - MIN_CENTER),
  reset: () => { layout.side = null; },
});
makeResizer($("#resizePanel"), {
  dir: -1, get: () => clamp(curPanel(), PANEL_MIN, panelMax()), set: (w) => { layout.panel = w; },
  min: () => PANEL_MIN, max: panelMax,
  reset: () => { layout.panel = null; },
});
function curPanelOpen() { return app.classList.contains("panel-open") ? clamp(curPanel(), PANEL_MIN, panelMax()) : 0; }

$("#collapseSidebar").addEventListener("click", () => setSideCollapsed(true));
$("#sideTab").addEventListener("click", () => setSideCollapsed(false));
$("#widenPanel").addEventListener("click", () => {
  const wide = curPanel() >= innerWidth * 0.55;
  layout.panel = wide ? null : Math.round(Math.min(innerWidth * 0.62, panelMax()));
  applyLayout(); saveLayout();
});
addEventListener("resize", () => applyLayout());
if (layout.collapsed && !isMobile()) setSideCollapsed(true, { focus: false });
applyLayout();

// ======================================================================
// 作答日誌（右側面板「紀錄」分頁）
// ======================================================================
const journal = { rows: [], hasMore: false, onlyCurrent: false, stale: true, loading: false };

function showPanelTab(name) {
  const isQa = name === "qa";
  for (const [tab, panel, on] of [["#tabQa", "#qa", isQa], ["#tabJournal", "#journal", !isQa]]) {
    $(tab).setAttribute("aria-selected", String(on));
    $(tab).tabIndex = on ? 0 : -1;
    $(panel).hidden = !on;
  }
  if (!isQa && (journal.stale || !journal.rows.length)) loadJournal(true);
}
$("#tabQa").addEventListener("click", () => showPanelTab("qa"));
$("#tabJournal").addEventListener("click", () => showPanelTab("journal"));
$(".panel-tabs").addEventListener("keydown", (ev) => {
  if (ev.key !== "ArrowLeft" && ev.key !== "ArrowRight") return;
  ev.preventDefault();
  const next = $("#tabQa").getAttribute("aria-selected") === "true" ? "journal" : "qa";
  showPanelTab(next);
  $(next === "qa" ? "#tabQa" : "#tabJournal").focus();
});

async function loadJournal(reset) {
  const box = $("#journal");
  if (!state.topic) { box.replaceChildren(h("p", { class: "qa-empty" }, "先選一個主題。")); return; }
  if (journal.loading) return;
  journal.loading = true;
  if (reset) {
    journal.rows = [];
    box.replaceChildren(h("div", { class: "loading", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), "載入紀錄中…"));
  }
  try {
    const node = journal.onlyCurrent ? state.progress?.current : undefined;
    const before = reset ? undefined : journal.rows.at(-1)?.id;
    const r = await api.journal(state.topic, { limit: 20, before, node });
    journal.rows = reset ? r.entries : journal.rows.concat(r.entries);
    journal.hasMore = r.has_more;
    journal.stale = false;
  } catch (e) {
    box.replaceChildren(h("p", { class: "qa-empty" }, `紀錄載入失敗：${errMsg(e)}`));
    journal.loading = false;
    return;
  }
  journal.loading = false;
  renderJournal();
}

function renderJournal() {
  const box = $("#journal");
  const cur = state.progress?.nodes?.find((n) => n.id === state.progress?.current);
  const filter = h("label", { class: "jr-filter" },
    h("input", { type: "checkbox", checked: journal.onlyCurrent || null,
      onchange: (ev) => { journal.onlyCurrent = ev.target.checked; loadJournal(true); } }),
    ` 只看目前節點${cur ? `（${cur.name}）` : ""}`);
  const head = h("div", { class: "jr-head" }, filter,
    h("span", { class: "jr-note" }, "存在你電腦的 ~/.claude/learn-data/<主題>/journal.jsonl"));
  if (!journal.rows.length) {
    box.replaceChildren(head, h("p", { class: "qa-empty" }, "還沒有紀錄。答完一題，題目、你的答案和回饋就會出現在這裡。"));
    return;
  }
  const groups = new Map();
  for (const e of journal.rows) {
    const d = (e.at || "").slice(0, 10);
    if (!groups.has(d)) groups.set(d, []);
    groups.get(d).push(e);
  }
  const lists = [...groups].map(([d, items]) => h("section", { class: "jr-day" },
    h("h3", null, d || "未知日期"), ...items.map(journalItem)));
  const more = journal.hasMore
    ? h("button", { type: "button", class: "btn btn-ghost wide", onclick: () => loadJournal(false) }, "載入更早的紀錄")
    : null;
  box.replaceChildren(...[head, ...lists, more].filter(Boolean));
}

function journalItem(e) {
  const time = (e.at || "").slice(11, 16);
  const isCode = /^\s*(select|with|insert|update|delete|create)\b/i.test(e.answer || "") || !!e.exec_user;
  const block = (title, text, mono) => text ? h("div", { class: "jr-block" },
    h("div", { class: "jr-label" }, title),
    h(mono ? "pre" : "p", { class: mono ? "code" : "jr-text", text })) : null;
  return h("details", { class: `jr-item r-${e.result}` },
    h("summary", null,
      h("span", { class: "jr-res" }, RESULT_TEXT[e.result] || e.result),
      e.danger ? h("span", { class: "jr-danger" }, "🚨 危險迷思") : null,
      h("span", { class: "jr-node" }, e.node_name || e.node),
      h("span", { class: "jr-meta" }, `${time} · ${TYPE_TEXT[e.type] || e.type}${e.kind === "review" ? " · 複習" : ""}`),
      h("span", { class: "jr-q" }, e.question)),
    h("div", { class: "jr-body" },
      block("題目", e.question, false),
      block("你的答案", e.answer, isCode),
      h("div", { class: "jr-facts" },
        h("span", null, CONF_TEXT[e.confidence] || e.confidence),
        h("span", null, `分數 ${e.delta > 0 ? "+" : ""}${e.delta}`),
        e.source === "skill" ? h("span", null, "來自 Claude Code") : null),
      block("回饋", e.feedback, false),
      e.exec_user || e.exec_ref ? h("div", { class: "v-compare" },
        block("你的執行結果", e.exec_user, true), block("預期結果", e.exec_ref, true)) : null,
      block("參考答案", e.reference_answer, isCode),
      e.misconception_label ? h("p", { class: "jr-mis" }, `📝 迷思筆記：${e.misconception_label}`) : null));
}

// ======================================================================
// 問答
// ======================================================================
const qa = $("#qa");
const lessonRunner = (l) => l?.runner || state.progress?.runner || null;

function envFor(topic) {
  if (!state.envCache.has(topic)) {
    const p = api.env(topic).then((r) => r?.sql || null).catch(() => null);
    state.envCache.set(topic, p);
  }
  return state.envCache.get(topic);
}

function setPanelHead(eyebrow, title) {
  $("#panelEyebrow").textContent = eyebrow;
  $("#panelTitle").textContent = title;
}

async function startLesson(req = {}, trigger) {
  if (!state.topic) return;
  openPanel(trigger || document.activeElement);
  showPanelTab("qa");
  state.lastReq = req;
  const topic = state.topic;
  setPanelHead(req.review ? "複習" : "一問一答", "出題中…");
  qa.replaceChildren(h("div", { class: "loading", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }),
    req.review ? "正在挑一則到期的迷思、換個情境出題…" : "正在準備短講和題目，通常需要 10–40 秒…"));
  qa.scrollTop = 0;
  let lesson;
  try {
    lesson = await api.lesson(topic, { node: req.node || null, review: !!req.review, ai: aiChoice("daily") });
  } catch (e) {
    if (state.topic !== topic) return;
    setPanelHead("一問一答", "出題失敗");
    qa.replaceChildren(
      h("div", { class: "notice err", role: "alert" }, e.status === 503 ? FALLBACK_MSG : errMsg(e)),
      h("div", { class: "v-actions" },
        h("button", { type: "button", class: "btn btn-primary", onclick: () => startLesson(req) }, "再試一次"),
        h("button", { type: "button", class: "btn btn-ghost", onclick: closePanel }, "先到這")));
    return;
  }
  if (state.topic !== topic) return;
  state.lesson = lesson;
  if (state.progress && lesson.node && state.progress.current !== lesson.node && lesson.kind !== "review") {
    state.progress.current = lesson.node;
    renderTopic(false);
  }
  renderLesson(lesson);
}

function sec(title, ...kids) {
  return h("section", { class: "qa-sec" }, title ? h("h3", null, title) : null, ...kids);
}

function renderTable(columns, rows, { caption, sub } = {}) {
  const cid = nextId("cap");
  const cell = (v) => {
    if (v === null || v === undefined) return h("td", { class: "null" }, "（空白）");
    if (typeof v === "number") return h("td", { class: "num" }, String(v));
    return h("td", null, String(v));
  };
  return h("div", { class: "tbl-wrap", tabindex: "0", role: "region", "aria-labelledby": cid },
    h("table", { class: "tbl" },
      h("caption", { id: cid }, caption || "資料表", sub ? h("small", null, sub) : null),
      h("thead", null, h("tr", null, columns.map((c) => h("th", { scope: "col" }, String(c))))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map(cell))))));
}

function renderLesson(l) {
  const runner = lessonRunner(l);
  setPanelHead(l.kind === "review" ? "複習 · 換個情境" : "一問一答", l.node_name || "練習");
  const parts = [];
  parts.push(h("div", { class: "qa-node" },
    h("span", { class: "qa-kind" }, l.kind === "review" ? "複習題" : "新題"),
    h("span", null, TYPE_TEXT[l.type] || l.type),
    l.cached_lecture ? h("span", { class: "label" }, "短講來自快取") : null));

  if (l.lecture) parts.push(sec("短講", h("div", { class: "qa-lecture", text: l.lecture })));

  // 範例 + 逐條說明（runner 主題實際執行；失敗就整段隱藏）
  let exampleSec = null;
  let explainSec = null;
  if (l.example) {
    const out = h("div", { class: "example-out" });
    exampleSec = sec("範例", h("pre", { class: "code", tabindex: "0" }, h("code", { text: l.example })), out);
    if (runner) {
      exampleSec.hidden = true;
      out.append(h("div", { class: "loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "執行範例中…"));
    }
    parts.push(exampleSec);
  }
  if (l.example_explain?.length) {
    explainSec = sec("逐行白話說明", h("ol", { class: "explain" }, l.example_explain.map((x) => h("li", { text: x }))));
    if (runner && l.example) explainSec.hidden = true;
    parts.push(explainSec);
  }

  // 題目用到的資料表（SQL：從練習庫直接查，不花 token）
  let tablesSec = null;
  if (l.show_tables?.length) {
    tablesSec = sec("題目用到的資料表", h("div", { class: "loading" }, h("span", { class: "spinner", "aria-hidden": "true" }), "從練習資料庫讀取…"));
    parts.push(tablesSec);
  }

  // 圖表
  if (l.visuals?.length) {
    parts.push(sec("圖解", l.visuals.map(renderVisual)));
  }

  parts.push(sec("題目", h("div", { class: "question", id: "qText", text: l.question }),
    l.hint ? h("details", { class: "hint" }, h("summary", null, "看提示"), h("p", { text: l.hint })) : null));

  // 作答表單
  const isCode = !!runner && (l.type === "produce" || l.type === "debug");
  const mono = !!runner && l.type !== "explain";
  const form = h("form", { class: "qa-form", novalidate: true, autocomplete: "off" });
  const confName = nextId("conf");
  const confFs = h("fieldset", { class: "conf" },
    h("legend", null, "先選把握度 ", h("b", null, "（必選）")),
    h("div", { class: "conf-opts" }, ["high", "mid", "low"].map((c) => h("label", { class: "conf-opt" },
      h("input", { type: "radio", name: confName, value: c, required: true }),
      h("span", null, CONF_TEXT[c])))));
  const ta = h("textarea", {
    id: nextId("ans"), class: mono ? "mono" : null, rows: isCode ? 7 : 4, maxlength: isCode ? 5000 : 2000, required: true,
    spellcheck: isCode ? "false" : null, autocapitalize: "off",
    placeholder: isCode ? "在這裡寫你的程式…" : l.type === "predict" ? "寫下你預測會輸出什麼，以及為什麼" : "用你自己的話回答",
    "aria-describedby": "qText",
  });
  if (isCode) enableTabIndent(ta);
  const msg = h("div", { class: "form-msg", role: "status", "aria-live": "polite" });
  const submitBtn = h("button", { type: "submit", class: "btn btn-primary" }, "送出");
  // form.append 會把 null 變成文字「null」，所以先濾掉
  form.append(...[confFs,
    h("label", { class: "answer-box" }, h("span", { class: "field-label" }, isCode ? "你的程式" : "你的答案"), ta),
    isCode ? null : dictationBar(ta, state.progress?.domain === "language"),
    isCode ? h("div", { class: "answer-tip" }, "Tab 縮排、Shift+Tab 取消縮排；按 Esc 後再按 Tab 可離開輸入框。") : null,
    h("div", { class: "qa-submit" }, submitBtn), msg].filter(Boolean));
  const result = h("div", { class: "qa-result" });
  form.addEventListener("submit", (ev) => { ev.preventDefault(); submitAnswer(l, form, ta, confName, msg, submitBtn, result); });
  parts.push(form, result);

  qa.replaceChildren(...parts);
  qa.scrollTop = 0;

  // 背景載入：範例執行、資料表
  if (runner && l.example && exampleSec) runExample(l, runner, exampleSec, explainSec);
  if (tablesSec) fillTables(l, tablesSec);
}

/**
 * 語音輸入（瀏覽器內建 Web Speech API）。不支援的瀏覽器不顯示按鈕。
 * 注意：Chrome / Edge 會把錄音送到 Google / Microsoft 的伺服器辨識，按鈕旁明確標示。
 * @param {HTMLTextAreaElement} ta 作答框
 * @param {boolean} offerEnglish 語言類主題提供英文辨識
 */
function dictationBar(ta, offerEnglish) {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;
  let rec = null;
  let base = "";            // 開始錄音時作答框原有的文字
  let finalText = "";       // 這次錄音已確定的文字
  const btn = h("button", { type: "button", class: "btn btn-ghost mic-btn", "aria-pressed": "false" }, "🎤 語音輸入");
  const langSel = offerEnglish
    ? h("select", { class: "mic-lang", "aria-label": "辨識語言" },
        h("option", { value: "zh-TW" }, "中文"), h("option", { value: "en-US" }, "English"))
    : null;
  const status = h("span", { class: "mic-status", role: "status", "aria-live": "polite" });
  const note = h("span", { class: "mic-note" }, "語音由瀏覽器廠商（Google / Microsoft）的伺服器轉成文字；不想傳出錄音可改用 Windows 的 Win + H。");

  const stop = () => { try { rec?.stop(); } catch { /* 已停止 */ } };
  const setOn = (on) => {
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "■ 停止" : "🎤 語音輸入";
    btn.classList.toggle("on", on);
  };

  btn.addEventListener("click", () => {
    if (rec) { stop(); return; }
    rec = new SR();
    rec.lang = langSel?.value || "zh-TW";
    rec.interimResults = true;
    rec.continuous = true;
    base = ta.value && !/\s$/.test(ta.value) ? ta.value + " " : ta.value;
    finalText = "";
    rec.onresult = (ev) => {
      let interim = "";
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const r = ev.results[i];
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      ta.value = (base + finalText + interim).slice(0, Number(ta.maxLength) || 2000);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    };
    rec.onerror = (ev) => {
      status.textContent = ev.error === "not-allowed" || ev.error === "service-not-allowed"
        ? "沒有麥克風權限：請在網址列左側允許麥克風"
        : ev.error === "no-speech" ? "沒有聽到聲音，請再試一次"
        : ev.error === "network" ? "語音辨識需要網路連線"
        : `語音輸入停止（${ev.error}）`;
    };
    rec.onend = () => { rec = null; setOn(false); ta.focus(); };
    try {
      rec.start();
      setOn(true);
      status.textContent = "聆聽中…說完按「停止」";
    } catch (e) {
      rec = null;
      status.textContent = "無法啟動語音輸入";
    }
  });

  return h("div", { class: "mic-bar" }, btn, langSel, status, note);
}

async function runExample(l, runner, exampleSec, explainSec) {
  const topic = state.topic;
  const seed = runner === "sql" ? await envFor(topic) : null;
  const r = await runCode(runner, l.example, { seed });
  if (state.lesson !== l) return;
  const out = $(".example-out", exampleSec);
  if (!r.ok) {
    // 範例執行失敗就不顯示（不讓錯的範例誤導），說明一起隱藏
    exampleSec.remove();
    explainSec?.remove();
    return;
  }
  out.replaceChildren(h("div", { class: "out-label" }, "執行結果"),
    runner === "sql" && r.sql && !r.sql.empty
      ? renderTable(r.sql.columns, r.sql.rows, { caption: "執行結果", sub: `${r.sql.rows.length} 列` })
      : h("pre", { class: "out", tabindex: "0", text: r.text }));
  exampleSec.hidden = false;
  if (explainSec) explainSec.hidden = false;
}

async function fillTables(l, tablesSec) {
  const seed = await envFor(state.topic);
  if (state.lesson !== l) return;
  const body = [];
  if (!seed) {
    body.push(h("div", { class: "notice err" }, "讀不到練習資料庫（practice.sql），無法顯示資料表。"));
  } else {
    for (const t of l.show_tables) {
      const r = await queryTable(seed, t);
      if (state.lesson !== l) return;
      body.push(r.ok
        ? renderTable(r.columns, r.rows, { caption: t, sub: `${r.rows.length} 列` })
        : h("div", { class: "notice err" }, `資料表 ${t} 讀取失敗：${r.error}`));
    }
    body.push(h("p", { class: "fineprint" }, "「（空白）」代表這格是 NULL：資料庫裡「還不知道」的值。"));
  }
  tablesSec.replaceChildren(h("h3", null, "題目用到的資料表"), ...body);
}

// ---------- 圖表 ----------
function renderVisual(v) {
  if (v.kind === "table") {
    return h("div", { class: "visual" }, renderTable(v.columns || [], (v.rows || []).map((r) => r.map((x) => x)), { caption: v.title || "表格" }));
  }
  if (v.kind === "diagram") {
    const box = h("div", { class: "mermaid-box" }, h("span", { class: "spinner", "aria-hidden": "true" }));
    const wrap = h("figure", { class: "visual", style: "margin:0 0 14px" }, h("figcaption", { class: "visual-title", text: v.title || "圖解" }), box);
    renderMermaid(v.mermaid || "", box, v.title);
    return wrap;
  }
  if (v.kind === "chart") {
    return h("figure", { class: "visual", style: "margin:0 0 14px" }, h("figcaption", { class: "visual-title", text: v.title || "圖表" }), renderChart(v));
  }
  return h("div");
}

let mermaidPromise = null;
function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import(MERMAID_URL).then((m) => {
      const mermaid = m.default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "dark",
        fontFamily: "Geist, 'Noto Sans TC', sans-serif",
        themeVariables: { background: "#0e0e12", primaryColor: "#1d1d24", primaryTextColor: "#f5f5f6", lineColor: "#8f8f97", primaryBorderColor: "#f0a46a" },
      });
      return mermaid;
    });
    mermaidPromise.catch(() => { mermaidPromise = null; });
  }
  return mermaidPromise;
}

async function renderMermaid(code, box, title) {
  const showRaw = () => box.replaceChildren(h("pre", { class: "code", style: "text-align:left", text: code }));
  try {
    const mermaid = await loadMermaid();
    await mermaid.parse(code); // 語法錯誤會丟例外
    const id = nextId("mmd");
    const { svg } = await mermaid.render(id, code);
    box.innerHTML = svg; // securityLevel strict：mermaid 已清理輸出
    const s = box.querySelector("svg");
    if (s) {
      s.setAttribute("role", "img");
      s.setAttribute("aria-label", title || "圖解");
      // 用原始尺寸顯示（寬圖在框內左右捲動），避免縮到字看不清楚
      const vb = s.viewBox?.baseVal;
      if (vb?.width) { s.style.maxWidth = "none"; s.style.width = `${Math.round(vb.width)}px`; }
    }
  } catch (e) {
    console.info("Mermaid 圖無法繪製，改顯示原文：", e?.message || e);
    document.querySelectorAll('[id^="dmmd-"]').forEach((x) => x.remove());
    showRaw();
  }
}

function renderChart(v) {
  const ns = "http://www.w3.org/2000/svg";
  const W = 420, H = 230, L = 40, R = 12, T = 22, B = 46;
  const vals = (v.values || []).map(Number).filter((x) => Number.isFinite(x));
  const labels = v.labels || [];
  const n = Math.min(vals.length, Math.max(labels.length, vals.length));
  const max = Math.max(0, ...vals), min = Math.min(0, ...vals);
  const span = max - min || 1;
  const y = (x) => T + (H - T - B) * (1 - (x - min) / span);
  const step = (W - L - R) / Math.max(1, n);
  const el = (tag, attrs, text) => {
    const e = document.createElementNS(ns, tag);
    for (const [k, val] of Object.entries(attrs)) e.setAttribute(k, String(val));
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart-svg", role: "img",
    "aria-label": `${v.title || "圖表"}：${labels.slice(0, n).map((lb, i) => `${lb} ${vals[i]}`).join("、")}` });
  svg.append(el("line", { x1: L, x2: W - R, y1: y(0), y2: y(0), class: "axis" }));
  const fmt = (x) => (Math.abs(x) >= 1000 ? x.toLocaleString("zh-TW") : String(Math.round(x * 100) / 100));
  if (v.chart === "line") {
    const pts = vals.slice(0, n).map((x, i) => [L + step * i + step / 2, y(x)]);
    svg.append(el("polyline", { points: pts.map((p) => p.join(",")).join(" "), class: "lineplot" }));
    pts.forEach(([px, py], i) => {
      svg.append(el("circle", { cx: px, cy: py, r: 3.5, class: "pt" }));
      svg.append(el("text", { x: px, y: py - 8, "text-anchor": "middle", class: "val" }, fmt(vals[i])));
    });
  } else {
    vals.slice(0, n).forEach((x, i) => {
      const bw = Math.min(42, step * 0.62);
      const bx = L + step * i + (step - bw) / 2;
      const top = Math.min(y(x), y(0)), hgt = Math.abs(y(x) - y(0));
      svg.append(el("rect", { x: bx, y: top, width: bw, height: Math.max(1, hgt), rx: 4, class: "bar" }));
      svg.append(el("text", { x: bx + bw / 2, y: top - 6, "text-anchor": "middle", class: "val" }, fmt(x)));
    });
  }
  labels.slice(0, n).forEach((lb, i) => {
    const t = el("text", { x: L + step * i + step / 2, y: H - B + 18, "text-anchor": "middle" }, String(lb).slice(0, 8));
    svg.append(t);
  });
  return svg;
}

// ---------- 作答 ----------
function enableTabIndent(ta) {
  let escaped = false;
  ta.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { escaped = true; return; }
    if (ev.key !== "Tab" || escaped) { if (ev.key !== "Tab") escaped = false; return; }
    ev.preventDefault();
    const { selectionStart: s, selectionEnd: e, value } = ta;
    if (ev.shiftKey) {
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      const m = value.slice(lineStart).match(/^ {1,2}/);
      if (m) {
        ta.setRangeText("", lineStart, lineStart + m[0].length, "preserve");
        ta.setSelectionRange(Math.max(lineStart, s - m[0].length), Math.max(lineStart, e - m[0].length));
      }
    } else {
      ta.setRangeText("  ", s, e, "end");
    }
  });
  ta.addEventListener("blur", () => { escaped = false; });
}

async function submitAnswer(l, form, ta, confName, msg, submitBtn, result) {
  const conf = form.querySelector(`input[name="${confName}"]:checked`)?.value;
  const answer = ta.value;
  msg.classList.remove("err");
  if (!conf) { msg.textContent = "請先選把握度：🟢 有把握、🟡 不太確定，或 🔴 用猜的。"; msg.classList.add("err"); form.querySelector(`input[name="${confName}"]`).focus(); return; }
  if (!answer.trim()) { msg.textContent = "還沒寫答案喔。"; msg.classList.add("err"); ta.focus(); return; }
  const limit = Number(ta.getAttribute("maxlength")) || 2000;
  if (answer.length > limit) { msg.textContent = `答案太長了（上限 ${limit} 字）。`; msg.classList.add("err"); return; }

  const setBusy = (busy, text = "") => {
    form.querySelectorAll("input, textarea, button").forEach((x) => { x.disabled = busy; });
    msg.replaceChildren(busy ? h("span", { class: "spinner", "aria-hidden": "true", style: "margin-right:8px" }) : "", text);
  };
  result.replaceChildren();
  const runner = lessonRunner(l);
  let execRes = null;
  if (runner && l.ref_code && (l.type === "produce" || l.type === "debug" || l.type === "predict")) {
    setBusy(true, l.type === "predict" ? "執行參考程式，取得真正的輸出…" : "在瀏覽器執行你的程式和參考答案…");
    try {
      const seed = runner === "sql" ? await envFor(state.topic) : null;
      execRes = await buildExec({ runner, type: l.type, answer, refCode: l.ref_code, question: l.question, seed });
    } catch (e) {
      console.warn("執行失敗：", e);
      execRes = null;
    }
  }
  setBusy(true, execRes?.exec?.equal ? "結果和參考答案相同，記錄中…" : "判定中…");
  const req = { question_id: l.question_id, confidence: conf, answer, ai: aiChoice("daily") };
  if (execRes?.exec) req.exec = execRes.exec;
  let res;
  try {
    res = await api.grade(state.topic, req);
  } catch (e) {
    setBusy(false);
    result.replaceChildren(h("div", { class: "notice err", role: "alert" }, e.status === 503 ? FALLBACK_MSG : errMsg(e)),
      h("div", { class: "v-actions" }, h("button", { type: "button", class: "btn btn-primary", onclick: () => form.requestSubmit() }, "重試")));
    return;
  }
  if (res.offtopic) {
    setBusy(false);
    result.replaceChildren(h("div", { class: "notice", role: "alert" }, "這題先回答題目喔。", res.feedback && res.feedback !== "這題先回答題目喔" ? h("div", { class: "muted", style: "margin-top:4px", text: res.feedback }) : null,
      h("div", { style: "margin-top:6px" }, "題目：", h("span", { text: l.question }))));
    ta.focus();
    return;
  }
  if (res.fallback) {
    setBusy(false);
    result.replaceChildren(h("div", { class: "notice err", role: "alert" }, res.feedback || FALLBACK_MSG, h("div", { class: "fineprint" }, "這次沒有更新進度。")),
      h("div", { class: "v-actions" }, h("button", { type: "button", class: "btn btn-primary", onclick: () => form.requestSubmit() }, "重試")));
    return;
  }
  setBusy(true, "");
  msg.textContent = "";
  renderVerdict(l, res, execRes, conf, result);
  applyGrade(l, res, conf);
  if (!res.offtopic && !res.fallback) journal.stale = true;
}

function firstLines(s, n = 5) {
  const lines = String(s || "").split("\n");
  return lines.length > n ? [...lines.slice(0, n), `…（共 ${lines.length} 行）`].join("\n") : lines.join("\n");
}

function renderVerdict(l, res, execRes, conf, box) {
  const r = res.result;
  const parts = [];
  const delta = typeof res.delta === "number" && r !== "unjudgeable"
    ? h("span", { class: "v-delta" }, `${res.delta > 0 ? "+" : ""}${res.delta} 分 · 節點 ${res.node?.score ?? "?"}/6 · ${STATUS_TEXT[res.node?.status] || ""}`)
    : null;
  parts.push(h("div", { class: "v-result" }, RESULT_TEXT[r] || r, delta));
  if (res.danger) {
    parts.push(h("div", { class: "v-danger", role: "alert" },
      "🚨 危險迷思：你選了「有把握」，但答案錯了。這種「以為自己會」的觀念最需要修正，已經記進迷思筆記，之後會換個情境再考你。"));
  }
  if (r === "unjudgeable") parts.push(h("div", { class: "notice" }, "無法判定，請換個說法或看參考答案。"));
  if (res.feedback) parts.push(h("p", { class: "v-feedback", text: res.feedback }));
  if (execRes?.exec) {
    const eq = execRes.exec.equal;
    const mineLabel = l.type === "predict" ? "你的預測" : "你的結果";
    parts.push(h("div", { class: "v-compare" },
      h("div", null, h("div", { class: "out-label" }, mineLabel), h("pre", { class: `out${eq ? "" : " bad"}`, tabindex: "0", text: firstLines(execRes.exec.user_output) })),
      h("div", null, h("div", { class: "out-label" }, "預期結果"), h("pre", { class: "out", tabindex: "0", text: firstLines(execRes.exec.ref_output) }))));
  }
  if (res.reference_answer) {
    parts.push(h("details", { class: "v-ref" }, h("summary", null, "看參考答案"), h("pre", { class: "code", text: res.reference_answer })));
  }
  if (res.misconception_label) {
    parts.push(h("div", null, h("span", { class: "mis-tag" }, "📝 迷思筆記：", h("span", { text: res.misconception_label }))));
  }
  parts.push(h("div", { class: "v-actions" },
    h("button", { type: "button", class: "btn btn-primary", onclick: () => startLesson(nextReq()) }, "下一題"),
    h("button", { type: "button", class: "btn btn-ghost", onclick: closePanel }, "先到這")));
  const v = h("div", { class: "verdict", dataset: { r }, tabindex: "-1" }, ...parts);
  box.replaceChildren(v);
  v.focus({ preventScroll: true });
  // 只捲動問答面板本身（scrollIntoView 會連整頁一起捲）
  const top = v.getBoundingClientRect().top - qa.getBoundingClientRect().top + qa.scrollTop - 12;
  qa.scrollTo({ top, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

function nextReq() {
  if (state.lastReq?.review && dueMisconceptions().length > 0) return { review: true };
  return {};
}

/** 判定後立即更新節點顏色與左欄數字；橋接模式再背景同步一次完整進度 */
function applyGrade(l, res, conf) {
  const p = state.progress;
  if (!p || !res.node) return;
  const n = p.nodes.find((x) => x.id === res.node.id);
  if (n) {
    n.status = res.node.status;
    n.score = res.node.score;
    n.lastSeen = todayTW();
    (n.history ||= []).push({ date: todayTW(), type: l.type, confidence: conf, result: res.result });
  }
  if (p.stats && res.result !== "unjudgeable") {
    p.stats.answered = (p.stats.answered || 0) + 1;
    if (res.result === "ok") p.stats.correct = (p.stats.correct || 0) + 1;
  }
  if (getMode() === "mock" && res.misconception_label && !(p.misconceptions || []).some((m) => m.label === res.misconception_label)) {
    const d = new Date(Date.now() + 86400000);
    (p.misconceptions ||= []).push({ id: `m${Date.now()}`, node: res.node.id, label: res.misconception_label, wrong: "", right: "", scenarios: [], created: todayTW(),
      interval: 1, nextReview: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Taipei" }).format(d), hits: 1, retired: false });
  }
  renderTopic(false);
  if (getMode() === "bridge") {
    const topic = state.topic;
    Promise.all([api.topic(topic), api.topics()]).then(([prog, tops]) => {
      if (state.topic !== topic) return;
      state.progress = prog;
      state.topics = tops.topics || state.topics;
      renderTopic(false);
    }).catch(() => { /* 背景同步失敗不影響畫面 */ });
  }
}

// ======================================================================
// 學新主題
// ======================================================================
const ntDialog = $("#newTopicDialog");
const nt = { topic: "", intake: null, planning: false };

function ntStep(n) {
  [1, 2, 3].forEach((i) => { $(`#ntStep${i}`).hidden = i !== n; });
  $("#newTopicDialog .dlg-x").hidden = n === 3; // 規劃中不能關
  $("#newTopicTitle").textContent = n === 1 ? "想學什麼？" : n === 2 ? `規劃「${nt.topic}」前，先問你幾題` : `正在規劃「${nt.topic}」`;
}

function openNewTopic() {
  closeDrawer();
  nt.intake = null;
  nt.planning = false;
  $("#ntTopic").value = "";
  $("#ntNotes").value = "";
  $("#ntMsg1").textContent = "";
  $("#ntMsg2").textContent = "";
  ntStep(1);
  ntDialog.showModal();
}

ntDialog.addEventListener("cancel", (ev) => {
  if (nt.planning && ev.cancelable) { ev.preventDefault(); toast("規劃進行中，請稍候…"); }
});
ntDialog.addEventListener("close", () => { ntDialog.returnValue = ""; });

$("#ntTopicForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const topic = $("#ntTopic").value.trim();
  const msg = $("#ntMsg1");
  msg.classList.remove("err");
  if (!topic) { msg.textContent = "請輸入想學的主題。"; msg.classList.add("err"); return; }
  if (topic.length > 60) { msg.textContent = "主題最多 60 字。"; msg.classList.add("err"); return; }
  const btn = $("#ntNext");
  btn.disabled = true;
  msg.replaceChildren(h("span", { class: "spinner", "aria-hidden": "true", style: "margin-right:8px" }), "AI 正在想要問你哪些問題…");
  try {
    nt.topic = topic;
    nt.intake = await api.intake(topic, aiChoice("daily"));
    renderIntake(nt.intake);
    ntStep(2);
    msg.textContent = "";
    $("#ntQuestions input")?.focus();
  } catch (e) {
    msg.textContent = errMsg(e);
    msg.classList.add("err");
  } finally {
    btn.disabled = false;
  }
});

function renderIntake(intake) {
  const box = $("#ntQuestions");
  box.replaceChildren(...intake.questions.map((q) => {
    const name = `nt-${q.id}`;
    const otherId = nextId("other");
    const other = h("input", { type: "text", id: otherId, class: "nt-other", maxlength: 150, placeholder: "其他：自己寫", "aria-label": `${q.question}：其他（自己寫）` });
    const otherRadio = h("input", { type: "radio", name, value: "__other__" });
    other.addEventListener("input", () => { if (other.value.trim()) otherRadio.checked = true; });
    return h("fieldset", { class: "nt-q", dataset: { qid: q.id } },
      h("legend", { text: q.question }),
      h("div", { class: "nt-opts" },
        q.options.map((o) => h("label", { class: "nt-opt" },
          h("input", { type: "radio", name, value: `${o.label}${o.description ? `：${o.description}` : ""}` }),
          h("span", null, h("b", { text: o.label }), o.description ? h("small", { text: o.description }) : null))),
        h("label", { class: "nt-opt" }, otherRadio, h("span", null, h("b", null, "其他"), h("small", null, "下面自己寫")))),
      other);
  }));
}

$("#ntBack").addEventListener("click", () => ntStep(1));

$("#ntAnswerForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("#ntMsg2");
  msg.classList.remove("err");
  const answers = { goal: "", level: "", context: "", minutes: "" };
  for (const fs of document.querySelectorAll("#ntQuestions .nt-q")) {
    const qid = fs.dataset.qid;
    const picked = fs.querySelector("input[type=radio]:checked");
    let val = picked?.value || "";
    if (val === "__other__") val = fs.querySelector(".nt-other").value.trim();
    if (!val) {
      msg.textContent = `還有題目沒選：「${fs.querySelector("legend").textContent}」`;
      msg.classList.add("err");
      fs.querySelector("input")?.focus();
      return;
    }
    answers[qid] = val.slice(0, qid === "minutes" ? 60 : 200);
  }
  const notes = $("#ntNotes").value.trim() || null;
  const req = {
    topic: nt.topic, slug: nt.intake.topic_slug, domain: nt.intake.domain, answers, notes,
    ai: aiChoice("plan"), mode: "new", confirm: false,
  };
  await runPlan(req);
});

async function runPlan(req) {
  nt.planning = true;
  ntStep(3);
  try {
    const res = await api.plan(req);
    nt.planning = false;
    ntDialog.close();
    toast("學習路徑完成！");
    await loadTopics();
    selectTopic(res.topic || req.slug);
  } catch (e) {
    nt.planning = false;
    if (e instanceof ApiError && e.status === 409 && e.needsConfirm) {
      ntStep(2);
      const choice = await askReplan(req.slug);
      if (choice === "replan_keep" || choice === "replan_reset") {
        await runPlan({ ...req, mode: choice, confirm: true });
      }
      return;
    }
    ntStep(2);
    const msg = $("#ntMsg2");
    msg.textContent = e.status === 503 ? "這次沒能產生合格的學習路徑，請再試一次。" : errMsg(e);
    msg.classList.add("err");
  }
}

function askReplan(slug) {
  const d = $("#replanDialog");
  $("#replanSlug").textContent = slug;
  d.returnValue = "";
  return new Promise((resolve) => {
    d.addEventListener("close", () => { const v = d.returnValue; d.returnValue = ""; resolve(v || "cancel"); }, { once: true });
    d.showModal();
  });
}

// ======================================================================
// 刪除 / 重設
// ======================================================================
const delDialog = $("#deleteDialog");
function openDelete() {
  const p = state.progress;
  if (!p) return;
  $("#deleteName").textContent = p.title || state.topic;
  $("#deleteSlugHint").textContent = state.topic;
  $("#deleteInput").value = "";
  $("#deleteOk").disabled = true;
  $("#deleteMsg").textContent = "";
  delDialog.showModal();
}
$("#deleteInput").addEventListener("input", (ev) => { $("#deleteOk").disabled = ev.target.value.trim() !== state.topic; });
$("#deleteCancel").addEventListener("click", () => delDialog.close());
$("#deleteForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if ($("#deleteInput").value.trim() !== state.topic) return;
  const msg = $("#deleteMsg");
  $("#deleteOk").disabled = true;
  msg.textContent = "刪除中…";
  try {
    await api.remove(state.topic);
    delDialog.close();
    toast("已移到 .trash/");
    const gone = state.topic;
    state.topic = null;
    state.progress = null;
    await loadTopics();
    state.topics = state.topics.filter((t) => t.topic !== gone);
    renderRooms();
    showInitialTopic();
  } catch (e) {
    msg.textContent = errMsg(e);
    msg.classList.add("err");
    $("#deleteOk").disabled = false;
  }
});

const resetDialog = $("#resetDialog");
function openReset() {
  if (!state.progress) return;
  $("#resetName").textContent = state.progress.title || state.topic;
  $("#resetMsg").textContent = "";
  resetDialog.returnValue = "";
  resetDialog.showModal();
}
resetDialog.addEventListener("close", async () => {
  const v = resetDialog.returnValue;
  resetDialog.returnValue = "";
  if (v !== "reset") return;
  try {
    const r = await api.reset(state.topic);
    state.progress = r.progress;
    state.selected = null;
    renderTopic(true);
    toast("進度已重設（已備份 progress.json.bak）");
  } catch (e) {
    toast(errMsg(e), true);
  }
});

// 主題選單（popover）：開啟時定位在按鈕下方
const menu = $("#topicMenu");
menu.addEventListener("beforetoggle", (ev) => {
  if (ev.newState !== "open") return;
  const r = $("#topicMenuBtn").getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.top = `${Math.round(r.bottom + 6)}px`;
  menu.style.left = `${Math.round(Math.max(16, Math.min(window.innerWidth - 176, r.right - 170)))}px`;
});
menu.addEventListener("click", (ev) => {
  const a = ev.target.closest("[data-action]")?.dataset.action;
  if (!a) return;
  menu.hidePopover();
  if (a === "reset") openReset();
  if (a === "delete") openDelete();
});

// ======================================================================
// 事件綁定
// ======================================================================
$("#newTopicBtn").addEventListener("click", openNewTopic);
document.addEventListener("click", (ev) => {
  if (ev.target.closest('[data-action="new-topic"]')) openNewTopic();
});
$("#continueBtn").addEventListener("click", (ev) => startLesson({}, ev.currentTarget));
$("#reviewBtn").addEventListener("click", (ev) => startLesson({ review: true }, ev.currentTarget));
$("#view3d").addEventListener("click", () => setView("3d"));
$("#viewList").addEventListener("click", () => setView("list"));
$("#panelTab").addEventListener("click", (ev) => openPanel(ev.currentTarget));
$("#closePanel").addEventListener("click", closePanel);
$("#openSidebar").addEventListener("click", (ev) => openDrawer("left", ev.currentTarget));
$("#closeSidebar").addEventListener("click", closeDrawer);
$("#openPanelMobile").addEventListener("click", (ev) => openPanel(ev.currentTarget));
$("#scrim").addEventListener("click", closeDrawer);
$("#refreshProviders").addEventListener("click", () => loadProviders(true));
$("#dismissBridgeCard").addEventListener("click", () => { $("#bridgeCard").hidden = true; });
async function onBridgeReady() {
  state.envCache.clear();
  await Promise.all([loadProviders(), loadTopics()]);
  showInitialTopic();
}
$("#retryBridge").addEventListener("click", async () => {
  await detectBridge({ userGesture: true });
  if (state.bridge === "ok") await onBridgeReady();
  else toast("還是偵測不到橋接，請確認已啟動", true);
});

// 「啟動橋接」：透過安裝時註冊的 learn3d-bridge:// 連結打開本機橋接，再等它回應
$("#launchBridge").addEventListener("click", async (ev) => {
  const btn = ev.currentTarget;
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = "啟動中…";
  window.location.href = "learn3d-bridge://start";
  for (let i = 0; i < 40; i++) {             // 最多約 2 分鐘（剛安裝完第一次會比較久）
    await new Promise((r) => setTimeout(r, 3000));
    await detectBridge({ userGesture: true });
    if (state.bridge === "ok") break;
  }
  btn.disabled = false;
  btn.textContent = label;
  if (state.bridge === "ok") await onBridgeReady();
  else toast("沒有偵測到橋接：第一次使用請先下載並執行 Install-Learn-Bridge.bat", true);
});

function showInitialTopic() {
  const want = decodeURIComponent(location.hash.slice(1));
  const pick = state.topics.find((t) => t.topic === want) || state.topics[0];
  if (pick) selectTopic(pick.topic);
  else {
    $("#topicView").hidden = true;
    $("#emptyState").hidden = false;
    $("#mobileTitle").textContent = "Learn 3D";
  }
}

// ======================================================================
// 啟動
// ======================================================================
(async function init() {
  if (isForcedMock()) useMock();
  await detectBridge();
  await Promise.all([loadProviders(), loadTopics()]);
  showInitialTopic();
})();
