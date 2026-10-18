import { test, expect } from './fixtures';
import { mkdir, open, readFile, writeFile, unlink } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
test('streaming parser: 1/10/100/500 MiB and one million elements', async ({ page, browser }) => {
  if (['pair', 'csv'].includes(process.env.PERF_CASE ?? '')) test.skip();
  test.setTimeout(1800000);
  await mkdir('output/performance', { recursive: true });
  const manifest = JSON.parse(await readFile('dist/deployment-manifest.json', 'utf8'));
  const workerPath = (name: string) =>
    '/' + manifest.files.find((f: any) => f.path.includes(`${name}.worker-`)).path;
  const cases = process.env.PERF_CASE
    ? process.env.PERF_CASE.split(',')
    : ['1', '10', '100', '500', 'million'];
  const results: any[] = [];
  const contextMode = process.env.PERF_PERSISTENT
    ? 'persistent isolated profile'
    : 'isolated ephemeral context';
  const resultFile = `output/performance/results${process.env.PERF_CASE ? '-' + process.env.PERF_CASE.replaceAll(',', '-') : ''}${process.env.PERF_PERSISTENT ? '-persistent' : ''}.json`;
  for (const scale of cases) {
    const filePath = path.resolve(`output/performance/synthetic-${scale}.xml`),
      file = await open(filePath, 'w');
    let elements = 1,
      size = 0;
    async function emit(s: string) {
      size += Buffer.byteLength(s);
      await file.write(s);
    }
    await emit('<Root>');
    if (scale === 'million') {
      for (let i = 0; i < 1000; i++) {
        await emit('<Group>' + '<Item/>'.repeat(1000) + '</Group>');
        elements += 1001;
      }
    } else {
      const target = Number(scale) * 1024 * 1024,
        padding = 'x'.repeat(4000);
      let group = 0;
      while (size < target) {
        let content = `<Group id="${group++}">`;
        elements++;
        for (let i = 0; i < 100; i++) {
          content += `<Item id="${i}">${padding}</Item>`;
          elements++;
        }
        content += '</Group>';
        await emit(content);
      }
    }
    await emit('</Root>');
    await file.close();
    await page.goto('/xmlviewer/');
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'ファイルを選択', exact: true }).click();
    const start = performance.now();
    await (await chooser).setFiles(filePath);
    let peakHeap = 0;
    const samples = async () => {
      while (true) {
        try {
          const state = await page.evaluate(() => ({
            status: document.querySelector('[role=status]')?.textContent,
            heap: (performance as any).memory?.usedJSHeapSize ?? 0,
          }));
          peakHeap = Math.max(peakHeap, state.heap);
          if (state.status?.includes('完了')) return;
          if (
            state.status?.includes('_ERROR:') ||
            state.status?.includes('_FULL:') ||
            state.status?.includes('_LIMIT:')
          )
            throw new Error(state.status);
        } catch (e) {
          throw e;
        }
        await new Promise((r) => setTimeout(r, 250));
      }
    };
    try {
      await samples();
    } catch (error) {
      const failure = {
        scale,
        bytes: size,
        elements,
        status: 'ERROR',
        error: error instanceof Error ? error.message : 'unknown',
        elapsedMs: performance.now() - start,
        mainThreadPeakHeapBytes: peakHeap,
        remaining: await page.locator('.header-stats').innerText(),
      };
      results.push(failure);
      await writeFile(
        resultFile,
        JSON.stringify(
          {
            date: new Date().toISOString(),
            contextMode,
            platform: os.platform(),
            arch: os.arch(),
            cpu: os.cpus()[0].model,
            ramBytes: os.totalmem(),
            browser: browser.version(),
            results,
          },
          null,
          2,
        ),
      );
      await unlink(filePath);
      expect.soft(failure.error).toBe('');
      continue;
    }
    const wallMs = performance.now() - start;
    const data = await page.evaluate(async () => {
      const database: IDBDatabase = await new Promise((resolve, reject) => {
        const r = indexedDB.open('enterprise-xml-analyzer-v1');
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      });
      const files: any[] = await new Promise((resolve, reject) => {
        const r = database.transaction('Files').objectStore('Files').getAll();
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => reject(r.error);
      });
      database.close();
      return { file: files[0], storage: await navigator.storage.estimate() };
    });
    expect(data.file.elements).toBe(elements);
    expect(data.file.status).toBe('READY');
    const graphStart = performance.now();
    await page.getByRole('link', { name: 'グラフビュー', exact: true }).click();
    await expect(page.getByRole('img', { name: 'XMLノードリンク図' })).toBeVisible();
    const graphMs = performance.now() - graphStart;
    const rpc = async (worker: string, action: string, payload: any) =>
      page.evaluate(
        async ({ worker, action, payload }) => {
          const workerInstance = new Worker(worker, { type: 'module' });
          return new Promise<any>((resolve, reject) => {
            workerInstance.onmessage = ({ data }) => {
              if (data.kind === 'result') {
                workerInstance.terminate();
                resolve(data.value);
              } else if (data.kind === 'error') {
                workerInstance.terminate();
                reject(new Error(data.message));
              }
            };
            workerInstance.onerror = (e) => {
              workerInstance.terminate();
              reject(new Error(e.message));
            };
            workerInstance.postMessage({ id: 1, action, payload });
          });
        },
        { worker, action, payload },
      );
    const searchStart = performance.now();
    const found = await rpc(workerPath('index'), 'search', {
      fileId: data.file.fileId,
      options: { target: 'name', query: 'Group', exact: true, caseSensitive: true },
    });
    const searchMs = performance.now() - searchStart;
    let compareMs: number | null = null,
      xlsxMs: number | null = null;
    if (scale === '100') {
      // Reuse the immutable parsed file for the identity traversal, then perform changed-file tests separately.
      const compareStart = performance.now();
      await rpc(workerPath('diff'), 'compare', {
        a: data.file.fileId,
        b: data.file.fileId,
        options: {
          mode: 'structure',
          keyAttribute: 'id',
          whitespace: 'trim',
          comments: false,
          processingInstructions: false,
          ignoreOrder: false,
          verifyHashes: false,
        },
      });
      compareMs = performance.now() - compareStart;
      const exportStart = performance.now();
      await rpc(workerPath('export'), 'xlsx', {
        fileIds: [data.file.fileId],
        template: {
          version: 1,
          recordPath: '/Root/Group/Item',
          layout: 'records',
          columns: [
            {
              path: '@id',
              name: 'id',
              type: 'string',
              missing: 'blank',
              repeat: 'first',
              enabled: true,
            },
          ],
          filters: [],
          logic: 'AND',
          delimiter: ' | ',
          sourceFile: true,
          grouping: 'merged',
          sheetName: '結果',
          header: true,
          autoFilter: true,
          freeze: true,
          sheetRows: 1048575,
          format: 'xlsx',
        },
      });
      xlsxMs = performance.now() - exportStart;
    }
    results.push({
      scale,
      bytes: size,
      elements,
      nodes: data.file.nodes,
      wallMs,
      parseMs: data.file.parseMs,
      indexMs: data.file.indexMs,
      graphMs,
      searchMs,
      searchHits: found.count,
      identityCompareMs: compareMs,
      xlsxMs,
      mainThreadPeakHeapBytes: peakHeap,
      indexedDbUsageBytes: data.storage.usage,
      quotaBytes: data.storage.quota,
    });
    await writeFile(
      resultFile,
      JSON.stringify(
        {
          date: new Date().toISOString(),
          contextMode,
          platform: os.platform(),
          arch: os.arch(),
          cpu: os.cpus()[0].model,
          ramBytes: os.totalmem(),
          browser: browser.version(),
          results,
        },
        null,
        2,
      ),
    );
    console.log(JSON.stringify(results.at(-1)));
    await page.getByRole('link', { name: '設定', exact: true }).click();
    const cleanupStart = performance.now();
    await page.getByRole('button', { name: 'すべての作業データとテンプレートを削除' }).click();
    await expect(page.locator('.header-stats')).toContainText('0 ファイル', { timeout: 120000 });
    results.at(-1).cleanupMs = performance.now() - cleanupStart;
    const report = JSON.parse(await readFile(resultFile, 'utf8'));
    report.results = results;
    await writeFile(resultFile, JSON.stringify(report, null, 2));
    await unlink(filePath);
  }
});
