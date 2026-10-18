export function serve(
  handler: (
    action: string,
    payload: any,
    ctx: { cancelled: () => boolean; progress: (v: unknown) => void },
  ) => Promise<unknown>,
) {
  let cancelled = false,
    busy = false;
  self.onmessage = async ({ data }) => {
    if (data.action === 'cancel') {
      cancelled = true;
      return;
    }
    if (busy) {
      self.postMessage({
        id: data.id,
        kind: 'error',
        message: 'BUSY: 前の処理が完了してから実行してください。',
      });
      return;
    }
    busy = true;
    cancelled = false;
    try {
      const value = await handler(data.action, data.payload, {
        cancelled: () => cancelled,
        progress: (value) => self.postMessage({ id: data.id, kind: 'progress', value }),
      });
      self.postMessage({ id: data.id, kind: 'result', value });
    } catch (error) {
      const message =
        error instanceof Error && /^[A-Z_]+:/.test(error.message)
          ? error.message
          : 'PROCESS_ERROR: 処理に失敗しました。入力・保存領域を確認してください。';
      self.postMessage({ id: data.id, kind: 'error', message });
    } finally {
      busy = false;
    }
  };
}
