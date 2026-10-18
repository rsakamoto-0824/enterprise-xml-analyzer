import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests',
  testMatch: process.env.PERFORMANCE ? 'performance/**/*.spec.ts' : 'e2e/**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:4173',
    channel: process.env.BROWSER_CHANNEL ?? 'msedge',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: process.env.PERFORMANCE ? 'off' : 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'node scripts/serve.mjs',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
  },
  reporter: [['list'], ['json', { outputFile: 'output/playwright/results.json' }]],
});
