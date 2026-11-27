// 雲端（Supabase）：Google 登入、讀寫自己的主題與作答紀錄
// - 資料表 topics / journal 都有 RLS：只能讀寫 user_id = 自己的列
// - 這裡用的是公開的 anon key（本來就會出現在網頁裡），權限由 RLS 擋
import { createClient } from "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/+esm";

const SUPABASE_URL = "https://uhvlvfiqnsbvjwprohhe.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVodmx2ZmlxbnNidmp3cHJvaGhlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTU3NjgwNzcsImV4cCI6MjExMTM0NDA3N30.3SBaXPUPEnci-Hhm13jx88-O0cNZKKCWp5JsMI2r51g";

export const sb = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true, flowType: "pkce" },
});

let user = null;
export const getUser = () => user;

export async function loadSession() {
  const { data } = await sb.auth.getSession();
  user = data?.session?.user || null;
  return user;
}

export function onAuthChange(cb) {
  sb.auth.onAuthStateChange((_ev, session) => {
    const next = session?.user || null;
    const changed = (next?.id || null) !== (user?.id || null);
    user = next;
    if (changed) cb(user);
  });
}

export function signInWithGoogle() {
  const back = location.origin + location.pathname + location.search;
  return sb.auth.signInWithOAuth({ provider: "google", options: { redirectTo: back } });
}

export async function signOut() {
  await sb.auth.signOut();
  user = null;
}

export class CloudError extends Error {}
function check({ data, error }) {
  if (error) throw new CloudError(`雲端同步失敗：${error.message || error}`);
  return data;
}

/** 分頁拿完所有列（Supabase 一次最多 1000 列） */
async function allRows(build, page = 1000) {
  const out = [];
  for (let from = 0; ; from += page) {
    const rows = check(await build().range(from, from + page - 1));
    out.push(...rows);
    if (rows.length < page) return out;
  }
}

// ---------- 同步用 ----------
export async function cloudIndex() {
  return allRows(() => sb.from("topics").select("topic,hash,deleted,updated_at").order("topic"));
}

export async function cloudTopicRow(topic) {
  return check(await sb.from("topics").select("topic,data,hash,deleted,updated_at").eq("topic", topic).maybeSingle());
}

export async function cloudTopicMeta(topic) {
  return check(await sb.from("topics").select("topic,hash,deleted,updated_at").eq("topic", topic).maybeSingle());
}

export async function cloudJournalAll(topic) {
  const rows = await allRows(() => sb.from("journal").select("entry").eq("topic", topic).order("at"));
  return rows.map((r) => r.entry);
}

async function cloudJournalIds(topic) {
  const rows = await allRows(() => sb.from("journal").select("id").eq("topic", topic));
  return new Set(rows.map((r) => r.id));
}

/** 上傳一個主題：作答紀錄只補雲端沒有的；回傳伺服器時間 updated_at */
export async function pushTopic(bundle) {
  const { topic, hash, journal = [], progress, lessons, env_sql } = bundle;
  const have = await cloudJournalIds(topic);
  const add = journal.filter((e) => !have.has(e.id))
    .map((e) => ({ user_id: user.id, topic, id: e.id, at: String(e.at || ""), entry: e }));
  for (let i = 0; i < add.length; i += 200) {
    check(await sb.from("journal").upsert(add.slice(i, i + 200), { onConflict: "user_id,topic,id", ignoreDuplicates: true }));
  }
  const row = check(await sb.from("topics")
    .upsert({ user_id: user.id, topic, hash, deleted: false, data: { progress, lessons, env_sql } }, { onConflict: "user_id,topic" })
    .select("updated_at").single());
  return row.updated_at;
}

/** 雲端標記刪除（留一列墓碑，讓其他電腦知道要刪），作答紀錄一起刪 */
export async function deleteCloudTopic(topic) {
  check(await sb.from("journal").delete().eq("topic", topic));
  const row = check(await sb.from("topics")
    .upsert({ user_id: user.id, topic, hash: null, deleted: true, data: null }, { onConflict: "user_id,topic" })
    .select("updated_at").single());
  return row.updated_at;
}

// ---------- 複習模式（沒有橋接，例如手機）：唯讀 ----------
export async function cloudTopics() {
  return allRows(() => sb.from("topics").select("topic,updated_at,progress:data->progress").eq("deleted", false));
}

export async function cloudTopic(topic) {
  const r = check(await sb.from("topics").select("data,updated_at").eq("topic", topic).eq("deleted", false).maybeSingle());
  return r;
}

/**
 * 作答紀錄（新到舊）。before＝上一頁最後一筆 id；node＝只看某節點；only＝wrong / unsure / danger
 */
export async function cloudJournal(topic, { limit = 30, before, node, only } = {}) {
  let q = sb.from("journal").select("entry").eq("topic", topic);
  if (node) q = q.eq("entry->>node", node);
  if (only === "wrong") q = q.in("entry->>result", ["wrong", "partial"]);
  if (only === "unsure") q = q.in("entry->>confidence", ["low", "mid"]);
  if (only === "danger") q = q.eq("entry->>danger", "true");
  if (before) {
    const b = check(await sb.from("journal").select("at").eq("topic", topic).eq("id", before).maybeSingle());
    if (b) q = q.lt("at", b.at);
  }
  const rows = check(await q.order("at", { ascending: false }).limit(limit + 1));
  const entries = rows.slice(0, limit).map((r) => r.entry);
  return { entries, has_more: rows.length > limit };
}
