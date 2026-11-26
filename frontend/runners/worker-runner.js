// 共用：在 Web Worker 裡執行、逾時就中止並重建（不讓無窮迴圈卡住整個頁面）
export class TimeoutError extends Error {
  constructor(sec) {
    super(`執行超過 ${sec} 秒，已中止。程式可能有無窮迴圈或太花時間的計算。`);
    this.name = "TimeoutError";
  }
}

export class WorkerRunner {
  /** @param {URL} url @param {WorkerOptions} [opts] */
  constructor(url, opts) {
    this.url = url;
    this.opts = opts;
    this.worker = null;
    this.seq = 0;
    this.pending = new Map();
  }

  ensure() {
    if (this.worker) return this.worker;
    const w = new Worker(this.url, this.opts);
    w.onmessage = (ev) => {
      const p = this.pending.get(ev.data?.id);
      if (!p) return;
      this.pending.delete(ev.data.id);
      clearTimeout(p.timer);
      p.resolve(ev.data);
    };
    w.onerror = (ev) => {
      ev.preventDefault?.();
      const err = new Error(ev.message || "執行環境載入失敗（請確認網路可以連到 CDN）");
      this.kill(err);
    };
    this.worker = w;
    return w;
  }

  kill(err) {
    this.worker?.terminate();
    this.worker = null;
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
  }

  /** 送出一個工作；timeoutMs 內沒回應就中止 worker */
  run(payload, timeoutMs) {
    const w = this.ensure();
    const id = ++this.seq;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.kill(new TimeoutError(Math.round(timeoutMs / 1000))), timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      w.postMessage({ ...payload, id });
    });
  }
}
