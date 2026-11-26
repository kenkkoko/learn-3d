// sql.js 執行緒：每次執行都從練習庫種子重建一個全新的資料庫，使用者的 INSERT/DELETE 不會互相影響
/* global initSqlJs */
const SQLJS_BASE = "https://cdn.jsdelivr.net/npm/sql.js@1.10.3/dist/";
importScripts(SQLJS_BASE + "sql-wasm.js");

let SQL = null;
const seedCache = new Map(); // seed 字串 → 匯出的 bytes

async function ready() {
  if (!SQL) SQL = await initSqlJs({ locateFile: (f) => SQLJS_BASE + f });
  return SQL;
}

function freshDb(seed) {
  if (!seed) return new SQL.Database();
  let bytes = seedCache.get(seed);
  if (!bytes) {
    const db = new SQL.Database();
    db.exec(seed);
    bytes = db.export();
    db.close();
    seedCache.clear();
    seedCache.set(seed, bytes);
  }
  return new SQL.Database(bytes);
}

self.onmessage = async (ev) => {
  const { id, op, seed, code } = ev.data || {};
  try {
    await ready();
    if (op === "load") {
      self.postMessage({ id, ok: true });
      return;
    }
    const db = freshDb(seed);
    try {
      const results = db.exec(code);
      const changes = db.getRowsModified();
      self.postMessage({ id, ok: true, results, changes });
    } finally {
      db.close();
    }
  } catch (e) {
    self.postMessage({ id, ok: false, error: String(e?.message || e) });
  }
};
