import { test, expect } from '@playwright/test';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

test('desktop deployment guide has working local navigation and no missing resources', async ({
  page,
}) => {
  const failures: string[] = [];
  page.on('pageerror', (e) => failures.push(e.message));
  page.on('requestfailed', (request) => failures.push(request.url()));
  await page.goto(pathToFileURL(path.resolve('release/build-and-publish-steps.html')).href);
  await expect(page.locator('h1')).toContainText('XML');
  const links = page.locator('nav a[href^="#"]');
  expect(await links.count()).toBeGreaterThanOrEqual(6);
  for (const href of await links.evaluateAll((elements) =>
    elements.map((a) => a.getAttribute('href')!),
  )) {
    expect(await page.locator(href).count()).toBe(1);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: 'output/playwright/deployment-guide.png', fullPage: true });
  expect(failures).toEqual([]);
});
