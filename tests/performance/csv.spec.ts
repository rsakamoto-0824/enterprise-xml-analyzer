import { test, expect } from '@playwright/test';
import { mkdir, open, writeFile, unlink } from 'node:fs/promises';
import path from 'node:path';
test('CSV writes over 32 MB incrementally to a real FileSystemWritableFileStream', async ({
  page,
}) => {
  if (process.env.PERF_CASE && process.env.PERF_CASE !== 'csv') test.skip();
  test.setTimeout(300000);
  await mkdir('output/performance', { recursive: true });
  const file = path.resolve('output/performance/csv-input.xml'),
    handle = await open(file, 'w');
  await handle.write('<r>');
  const value = 'v'.repeat(4000),
    count = 10500;
  for (let block = 0; block < 105; block++) {
    let chunk = '';
    for (let i = 0; i < 100; i++) chunk += `<n id="${block * 100 + i}">${value}</n>`;
    await handle.write(chunk);
  }
  await handle.write('</r>');
  await handle.close();
  await page.goto('/');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'ファイルを選択', exact: true }).click();
  await (await chooser).setFiles(file);
  await expect(page.getByRole('status')).toContainText('完了', { timeout: 120000 });
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await page.getByRole('button', { name: 'n', exact: true }).first().click();
  await page.getByRole('button', { name: 'この要素を出力の1行にする' }).click();
  await page.evaluate(() => {
    (window as any).showSaveFilePicker = async () => {
      const root = await navigator.storage.getDirectory();
      return root.getFileHandle('stream-test.csv', { create: true });
    };
  });
  const start = performance.now();
  await page.getByRole('button', { name: 'CSVを保存', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('CSV逐次保存 · 完了', { timeout: 120000 });
  const durationMs = performance.now() - start;
  const saved = await page.evaluate(async () => {
    const root = await navigator.storage.getDirectory(),
      handle = await root.getFileHandle('stream-test.csv'),
      file = await handle.getFile();
    const tail = await file.slice(file.size - 4100).text();
    const result = { size: file.size, tail };
    await root.removeEntry('stream-test.csv');
    return result;
  });
  expect(saved.size).toBeGreaterThan(32000000);
  expect(saved.tail).toContain('10499');
  await writeFile(
    'output/performance/csv.json',
    JSON.stringify(
      {
        date: new Date().toISOString(),
        rows: count,
        bytes: saved.size,
        durationMs,
        method: 'File System Access API stream backed by OPFS; native save dialog replaced in test',
      },
      null,
      2,
    ),
  );
  await page.getByRole('link', { name: '設定', exact: true }).click();
  await page.getByRole('button', { name: 'すべての作業データとテンプレートを削除' }).click();
  await expect(page.locator('.header-stats')).toContainText('0 ファイル');
  await unlink(file);
});
