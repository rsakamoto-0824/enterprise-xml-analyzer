import { test, expect } from '@playwright/test';
import fs from 'node:fs/promises';
async function drop(page: any, content: string, name = 'drop.xml') {
  const transfer = await page.evaluateHandle(
    ({ content, name }: any) => {
      const d = new DataTransfer();
      d.items.add(new File([content], name, { type: 'application/xml' }));
      return d;
    },
    { content, name },
  );
  await page.dispatchEvent('.drop-zone', 'drop', { dataTransfer: transfer });
  await transfer.dispose();
  await expect(page.getByRole('status')).toContainText('完了');
}
test('drag/drop, pagination, collapse, keyboard, theme, graph images and offline use', async ({
  page,
  context,
}) => {
  await page.goto('/');
  await drop(
    page,
    '<r>' + Array.from({ length: 600 }, (_, i) => `<n id="${i}">value${i}</n>`).join('') + '</r>',
  );
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await expect(page.locator('.tree-row').first()).toBeVisible();
  expect(await page.locator('.tree-row').count()).toBeLessThan(50);
  await page.getByRole('button', { name: 'r 折りたたむ', exact: true }).click();
  await expect(page.locator('.tree-row')).toHaveCount(1);
  await page.getByRole('button', { name: 'r 展開', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('button', { name: 'r 折りたたむ', exact: true })).toBeVisible();
  await page.locator('.tree-pane .virtual-list').evaluate((e) => (e.scrollTop = e.scrollHeight));
  await page.getByRole('button', { name: '次の250件を表示…', exact: true }).click();
  await expect(page.locator('.tree-row').first()).toBeVisible();
  await page.getByRole('button', { name: '◐ テーマ' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('link', { name: 'グラフビュー', exact: true }).click();
  await expect(page.getByRole('img', { name: 'XMLノードリンク図' })).toBeVisible();
  const svg = page.waitForEvent('download');
  await page.getByRole('button', { name: 'SVG保存' }).click();
  await (await svg).saveAs('output/playwright/graph.svg');
  const png = page.waitForEvent('download');
  await page.getByRole('button', { name: 'PNG保存' }).click();
  await (await png).saveAs('output/playwright/exported-graph.png');
  const graphic = page.getByRole('img', { name: 'XMLノードリンク図' });
  const before = await graphic.locator(':scope > g').getAttribute('transform');
  await graphic.hover();
  await page.mouse.wheel(0, -300);
  await expect(graphic.locator(':scope > g')).not.toHaveAttribute('transform', before!);
  await context.setOffline(true);
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await page.getByRole('textbox', { name: 'XML内を検索' }).fill('n');
  await page.getByRole('button', { name: '検索', exact: true }).click();
  await expect(page.locator('.search-results')).toContainText('600件');
  await context.setOffline(false);
  await page.screenshot({ path: 'output/playwright/tree-dark.png', fullPage: true });
});
test('cancel parsing and reload safely', async ({ page }) => {
  await page.goto('/');
  const transfer = await page.evaluateHandle(() => {
    const d = new DataTransfer();
    d.items.add(new File(['<r>', '<n>value</n>'.repeat(300000), '</r>'], 'cancel.xml'));
    return d;
  });
  await page.dispatchEvent('.drop-zone', 'drop', { dataTransfer: transfer });
  await expect(page.getByRole('button', { name: '中断', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '中断', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('CANCELLED');
  await expect(page.locator('.header-stats')).toContainText('0 ファイル');
  await page.reload();
  await expect(page.locator('.header-stats')).toContainText('0 ファイル');
  await transfer.dispose();
});
test('template round trip, column order and repeat child sheets', async ({ page }) => {
  await page.goto('/');
  await drop(page, '<r><n id="001"><v>A</v><v>B</v></n></r>');
  await page.getByRole('link', { name: '構造ビュー', exact: true }).click();
  await page.getByRole('button', { name: 'n', exact: true }).click();
  await page.getByRole('button', { name: 'この要素を出力の1行にする' }).click();
  await page.getByRole('button', { name: 'vを上へ', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '列名 1', exact: true })).toHaveValue('v');
  await page.getByRole('combobox', { name: '繰り返し 1', exact: true }).selectOption('sheet');
  const saved = page.waitForEvent('download');
  await page.getByRole('button', { name: '設定JSON保存' }).click();
  await (await saved).saveAs('output/playwright/template.json');
  const t = JSON.parse(await fs.readFile('output/playwright/template.json', 'utf8'));
  expect(t.version).toBe(1);
  expect(t.columns[0].repeat).toBe('sheet');
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: '設定JSON読込' }).click();
  await (await chooser).setFiles('output/playwright/template.json');
  await page.getByRole('button', { name: 'プレビュー（100行）' }).click();
  await expect(page.locator('.preview')).toContainText('RecordPath');
  await expect(page.locator('.preview')).toContainText('/r[1]/n[1]');
  await expect(page.locator('.preview')).toContainText('B');
});
