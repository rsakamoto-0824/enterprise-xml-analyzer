export type WorkerName = 'parser' | 'index' | 'diff' | 'export';
export class WorkerClient {
  worker: Worker;
  busy = false;
  private sequence = 0;
  private pending = new Map<
    number,
    { resolve: (v: any) => void; reject: (e: Error) => void; progress?: (v: any) => void }
  >();
  constructor(kind: WorkerName) {
    if (kind === 'parser')
      this.worker = new Worker(new URL('./parser.worker.ts', import.meta.url), { type: 'module' });
    else if (kind === 'index')
      this.worker = new Worker(new URL('./index.worker.ts', import.meta.url), { type: 'module' });
    else if (kind === 'diff')
      this.worker = new Worker(new URL('./diff.worker.ts', import.meta.url), { type: 'module' });
    else
      this.worker = new Worker(new URL('./export.worker.ts', import.meta.url), { type: 'module' });
    this.worker.onmessage = ({ data }) => {
      const p = this.pending.get(data.id);
      if (!p) return;
      if (data.kind === 'progress') {
        p.progress?.(data.value);
        return;
      }
      this.pending.delete(data.id);
      this.busy = this.pending.size > 0;
      data.kind === 'error' ? p.reject(new Error(data.message)) : p.resolve(data.value);
    };
    this.worker.onerror = () => {
      for (const p of this.pending.values())
        p.reject(
          new Error('WORKER_ERROR: 処理が停止しました。再読込すると不完全なデータを削除します。'),
        );
      this.pending.clear();
      this.busy = false;
    };
  }
  call<T = any>(action: string, payload: unknown, progress?: (v: any) => void): Promise<T> {
    const id = ++this.sequence;
    this.busy = true;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, progress });
      this.worker.postMessage({ id, action, payload });
    });
  }
  cancel() {
    this.worker.postMessage({ action: 'cancel' });
  }
}
