import { test, expect } from '@playwright/test';
import ExcelJS from 'exceljs';
import fs from 'node:fs/promises';
const xml =
  '<Root><Product id="001"><Name>りんご</Name><Price>100</Price></Product><Product id="002"><Name>みかん</Name><Price>200</Price></Product></Root>';
async function upload(page: any, name: string, content: string) {
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: 'ファイルを選択', exact: true }).click();
  await (
    await chooser
  ).setFiles({ name, mimeType: 'application/xml', buffer: Buffer.from(content) });
  await expect(page.getByRole('status')).toContainText('完了', { timeout: 60000 });
}
test('virtual directory: import, tree, graph, search, extract, XLSX and CSV; no data upload', async ({
  page,
}) => {
  const external: string[] = [],
    writes: string[] = [],
    errors: string[] = [];
  page.on('request', (r) => {
    if (!r.url().startsWith('http://127.0.0.1:4173') && !r.url().startsWith('blob:'))
      external.push(r.url());
    if (!['GET', 'HEAD'].includes(r.method())) writes.push(r.url());
  });
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/xmlviewer/');
  await upload(page, '日本語 製品.xml', xml);
  await expect(page.getByRole('cell', { name: '日本語 製品.xml', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await page.getByRole('button', { name: 'Product', exact: true }).first().click();
  await expect(page.locator('.detail')).toContainText('/Root[1]/Product[1]');
  await page.getByRole('button', { name: 'この要素を出力の1行にする' }).click();
  await expect(page).toHaveURL(/#\/export/);
  await page.getByRole('button', { name: 'プレビュー（100行）' }).click();
  await expect(page.locator('.preview')).toContainText('りんご');
  await expect(page.locator('.preview')).toContainText('みかん');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'XLSXを保存', exact: true }).click();
  const file = await download;
  await fs.mkdir('output/playwright', { recursive: true });
  await file.saveAs('output/playwright/extracted.xlsx');
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.readFile('output/playwright/extracted.xlsx');
  expect(workbook.worksheets[0].getCell('B2').value).toBe('001');
  expect(workbook.worksheets[0].rowCount).toBe(3);
  await page.evaluate(() => {
    Object.defineProperty(window, 'showSaveFilePicker', { value: undefined, configurable: true });
    delete (window as any).showSaveFilePicker;
  });
  // Export through the browser fallback by hiding the optional API from the prototype too.
  await page.evaluate(() => {
    let p: any = window;
    while (p) {
      if (Object.prototype.hasOwnProperty.call(p, 'showSaveFilePicker'))
        delete p.showSaveFilePicker;
      p = Object.getPrototypeOf(p);
    }
  });
  const csv = page.waitForEvent('download');
  await page.getByRole('button', { name: 'CSVを保存', exact: true }).click();
  await (await csv).saveAs('output/playwright/extracted.csv');
  expect(await fs.readFile('output/playwright/extracted.csv', 'utf8')).toContain('001');
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await page.getByRole('textbox', { name: 'XML内を検索' }).fill('Price');
  await page.getByRole('button', { name: '検索', exact: true }).click();
  await expect(page.locator('.search-results')).toContainText('2件');
  await page.getByRole('link', { name: 'グラフビュー', exact: true }).click();
  await expect(page.getByRole('img', { name: 'XMLノードリンク図' })).toBeVisible();
  await page.getByRole('button', { name: '縦 / 横' }).click();
  await page.screenshot({ path: 'output/playwright/graph.png', fullPage: true });
  expect(external).toEqual([]);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
});
test('comparison, filters, safe markup and deletion', async ({ page }) => {
  await page.goto('/');
  await upload(
    page,
    'a.xml',
    '<Root><Product id="001"><Name>&lt;img src=x onerror=alert(1)&gt;</Name></Product></Root>',
  );
  await upload(
    page,
    'b.xml',
    '<Root><Product id="001"><Name>new</Name></Product><Product id="003"/></Root>',
  );
  await page.getByRole('link', { name: 'XML比較', exact: true }).click();
  await page.getByRole('button', { name: '比較を開始' }).click();
  await expect(page.getByRole('status')).toContainText('完了');
  await expect(page.locator('.compare-view .metric')).toHaveCount(6);
  await page.getByRole('combobox', { name: '差分種別' }).selectOption('MODIFIED');
  await expect(page.locator('.diff-row')).not.toHaveCount(0);
  await page.screenshot({ path: 'output/playwright/comparison.png', fullPage: true });
  expect(await page.locator('img[src=x]').count()).toBe(0);
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '差分XLSX保存' }).click();
  await (await download).saveAs('output/playwright/differences.xlsx');
  await page.getByRole('link', { name: '設定', exact: true }).click();
  await page.getByRole('button', { name: 'すべての作業データとテンプレートを削除' }).click();
  await expect(page.locator('.header-stats')).toContainText('0 ファイル');
});
test('malformed XML and DTD fail closed', async ({ page }) => {
  await page.goto('/');
  for (const [name, content, code] of [
    ['broken.xml', '<r><a></r>', 'XML_SYNTAX'],
    ['entity.xml', '<!DOCTYPE r SYSTEM "https://example.invalid/a"><r/>', 'DTD_FORBIDDEN'],
  ]) {
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'ファイルを選択', exact: true }).click();
    await (
      await chooser
    ).setFiles({ name, mimeType: 'application/xml', buffer: Buffer.from(content) });
    await expect(page.getByRole('status')).toContainText(code);
    await expect(page.locator('.header-stats')).toContainText('0 ファイル');
  }
});
test('session recovery, multiple tab lock and display scaling', async ({ page, context }) => {
  await page.goto('/xmlviewer/');
  await upload(page, 'session.xml', xml);
  const second = await context.newPage();
  await second.goto('/xmlviewer/');
  await expect(
    second.getByText('このアプリは別のタブで使用中です。', { exact: false }),
  ).toBeVisible();
  await second.close();
  await page.reload();
  await expect(page.locator('.header-stats')).toContainText('0 ファイル');
  for (const scale of [1.25, 1.5]) {
    await page.evaluate((scale) => (document.documentElement.style.zoom = String(scale)), scale);
    await expect(page.getByRole('button', { name: 'ファイルを選択', exact: true })).toBeVisible();
    await page.screenshot({ path: `output/playwright/scale-${scale}.png`, fullPage: true });
  }
});
