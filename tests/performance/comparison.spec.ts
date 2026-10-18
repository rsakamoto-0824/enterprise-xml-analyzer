import { test, expect } from '@playwright/test';
import { mkdir, open, readFile, writeFile, unlink } from 'node:fs/promises';
import path from 'node:path';
test('100 MiB pair with one changed text node', async ({ page, browser }) => {
  test.setTimeout(600000);
  if (process.env.PERF_CASE && process.env.PERF_CASE !== 'pair') test.skip();
  await mkdir('output/performance', { recursive: true });
  const paths = [
    path.resolve('output/performance/pair-a.xml'),
    path.resolve('output/performance/pair-b.xml'),
  ];
  const handles = await Promise.all(paths.map((p) => open(p, 'w')));
  let size = 0,
    groups = 0;
  const text = 'x'.repeat(4000);
  await Promise.all(handles.map((h) => h.write('<Root>')));
  size += 6;
  while (size < 100 * 1024 * 1024) {
    let left = `<Group id="${groups}">`,
      right = left;
    for (let i = 0; i < 100; i++) {
      left += `<Item id="${i}">${text}</Item>`;
      right += `<Item id="${i}">${groups === 2 && i === 7 ? 'y' + text.slice(1) : text}</Item>`;
    }
    left += '</Group>';
    right += '</Group>';
    await Promise.all([handles[0].write(left), handles[1].write(right)]);
    size += Buffer.byteLength(left);
    groups++;
  }
  await Promise.all(
    handles.map(async (h) => {
      await h.write('</Root>');
      await h.close();
    }),
  );
  size += 7;
  await page.goto('/xmlviewer/');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'ファイルを選択', exact: true }).click();
  const parseStart = performance.now();
  await (await chooser).setFiles(paths);
  await expect(page.getByRole('status')).toContainText('完了', { timeout: 300000 });
  const pairParseMs = performance.now() - parseStart;
  await page.getByRole('link', { name: 'XML比較', exact: true }).click();
  await page.getByRole('checkbox', { name: '前後の空白を無視' }).uncheck();
  await page.getByRole('checkbox', { name: 'コメントを比較' }).check();
  await page.getByRole('checkbox', { name: '処理命令を比較' }).check();
  const compareStart = performance.now();
  await page.getByRole('button', { name: '比較を開始' }).click();
  await expect(page.getByRole('status')).toContainText('XML比較 · 完了', { timeout: 300000 });
  const compareMs = performance.now() - compareStart;
  await expect(
    page
      .locator('.compare-view .metric')
      .filter({ has: page.locator('.MODIFIED') })
      .locator('strong'),
  ).toHaveText('1');
  const metadata = await page.evaluate(async () => {
    const database: IDBDatabase = await new Promise((resolve) => {
      const r = indexedDB.open('enterprise-xml-analyzer-v1');
      r.onsuccess = () => resolve(r.result);
    });
    const values: any[] = await new Promise((resolve) => {
      const r = database.transaction('Comparisons').objectStore('Comparisons').getAll();
      r.onsuccess = () => resolve(r.result);
    });
    database.close();
    return { comparison: values.at(-1), storage: await navigator.storage.estimate() };
  });
  await writeFile(
    'output/performance/comparison.json',
    JSON.stringify(
      {
        date: new Date().toISOString(),
        browser: browser.version(),
        bytesPerFile: size,
        pairParseMs,
        compareMs,
        ...metadata,
      },
      null,
      2,
    ),
  );
  console.log(
    JSON.stringify({
      bytesPerFile: size,
      pairParseMs,
      compareMs,
      counts: metadata.comparison.counts,
    }),
  );
  await page.getByRole('link', { name: '設定', exact: true }).click();
  await page.getByRole('button', { name: 'すべての作業データとテンプレートを削除' }).click();
  await expect(page.locator('.header-stats')).toContainText('0 ファイル', { timeout: 120000 });
  for (const file of paths) await unlink(file);
});
