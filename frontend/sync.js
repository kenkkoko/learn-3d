// 跨裝置同步：本機橋接（檔案）⇄ Supabase（雲端）
// 規則：
// - 只有一邊改過 → 那一邊為準；兩邊都改過 → 進度以比較新的為準，作答紀錄兩邊合併
// - 刪除會同步：雲端留一列「已刪除」，其他電腦下次同步時也刪（移到 .trash，可救回）
import { api } from "./api.js";
import * as cloud from "./cloud.js";

let chain = Promise.resolve();
/** 同步一次只跑一個，避免同時上傳/下載同一個主題 */
function serial(fn) {
  const p = chain.then(fn, fn);
  chain = p.catch(() => {});
  return p;
}

const ts = (s) => (s ? Date.parse(s) || 0 : 0);

async function push(t, cloudHash) {
  const b = await api.syncExport(t);
  if (cloudHash && b.hash === cloudHash) return;   // 雲端已經一樣
  const at = await cloud.pushTopic(b);
  await api.syncMark(t, { cloud_at: at, hash: b.hash });
}

async function pull(t) {
  const row = await cloud.cloudTopicRow(t);
  if (!row || row.deleted || !row.data) return;
  const journal = await cloud.cloudJournalAll(t);
  await api.syncImport(t, { bundle: { ...row.data, hash: row.hash, journal }, cloud_at: row.updated_at });
}

/** 比對一個主題並同步；回傳本機內容有沒有被改（需要重新整理畫面） */
async function syncOne(t, l, c) {
  const localHas = !!(l && l.hash);
  if (!c) {
    if (localHas) await push(t);
    else if (l) await api.syncForget(t);
    return false;
  }
  if (c.deleted) {
    if (!localHas) { if (l) await api.syncForget(t); return false; }
    // 我上次同步之後，別台電腦刪掉了它：本機沒再改過就跟著刪
    if (l.base_cloud_at && l.base_cloud_at !== c.updated_at && l.hash === l.base_hash) {
      await api.syncDrop(t);
      return true;
    }
    await push(t);   // 本機有新內容（或是新建的同名主題）→ 保留並重新上傳
    return false;
  }
  if (!l) { await pull(t); return true; }
  if (!l.hash) {
    // 本機刪掉了：雲端從上次同步後沒變 → 雲端也刪；雲端有新內容 → 救回來
    if (l.base_cloud_at === c.updated_at) {
      await cloud.deleteCloudTopic(t);
      await api.syncForget(t);
      return false;
    }
    await pull(t);
    return true;
  }
  if (l.hash === c.hash) {
    if (l.base_cloud_at !== c.updated_at || l.base_hash !== l.hash) await api.syncMark(t, { cloud_at: c.updated_at, hash: c.hash });
    return false;
  }
  const localChanged = l.hash !== l.base_hash;
  const cloudChanged = l.base_cloud_at !== c.updated_at;
  if (localChanged && !cloudChanged) { await push(t, c.hash); return false; }
  if (!localChanged && cloudChanged) { await pull(t); return true; }
  // 兩邊都改過（或從沒同步過）
  if (ts(l.mtime) > ts(c.updated_at)) {
    const journal = await cloud.cloudJournalAll(t);
    await api.syncImport(t, { bundle: { journal }, journal_only: true });
  } else {
    await pull(t);
  }
  await push(t);
  return true;
}

/** 全部主題同步一次；回傳 { changed: 本機有沒有被改, errors } */
export function syncAll() {
  return serial(async () => {
    const [local, rows] = await Promise.all([api.syncManifest(), cloud.cloudIndex()]);
    const L = new Map(local.topics.map((x) => [x.topic, x]));
    const C = new Map(rows.map((r) => [r.topic, r]));
    let changed = false;
    const errors = [];
    for (const t of new Set([...L.keys(), ...C.keys()])) {
      try {
        if (await syncOne(t, L.get(t), C.get(t))) changed = true;
      } catch (e) {
        errors.push(`${t}：${e.message || e}`);
      }
    }
    return { changed, errors };
  });
}

/** 只同步一個主題（作答、刪除、重設之後呼叫） */
export function syncTopic(t) {
  return serial(async () => {
    const [local, c] = await Promise.all([api.syncManifest(), cloud.cloudTopicMeta(t)]);
    return syncOne(t, local.topics.find((x) => x.topic === t), c || null);
  });
}
