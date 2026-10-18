import { test as base, expect } from '@playwright/test';
import { mkdtemp, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
export const test = base.extend({
  context: async ({ playwright, browser }, use) => {
    if (!process.env.PERF_PERSISTENT) {
      const context = await browser.newContext({
        baseURL: 'http://127.0.0.1:4173',
        viewport: { width: 1440, height: 1000 },
      });
      try {
        await use(context);
      } finally {
        await context.close();
      }
      return;
    }
    const profile = await mkdtemp(path.join(os.tmpdir(), 'enterprise-xml-perf-'));
    const context = await playwright.chromium.launchPersistentContext(profile, {
      channel: process.env.BROWSER_CHANNEL ?? 'msedge',
      headless: true,
      baseURL: 'http://127.0.0.1:4173',
      viewport: { width: 1440, height: 1000 },
    });
    try {
      await use(context);
    } finally {
      await context.close();
      await rm(profile, { recursive: true, force: true });
    }
  },
});
export { expect };
