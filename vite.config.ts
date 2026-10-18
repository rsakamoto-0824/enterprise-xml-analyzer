import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';
export default defineConfig({
  base: './',
  resolve: {
    alias: { exceljs: fileURLToPath(new URL('./.generated/exceljs.cjs', import.meta.url)) },
  },
  build: { sourcemap: false, target: 'es2022' },
  worker: { format: 'es' },
  test: { include: ['tests/unit/**/*.test.ts'], setupFiles: ['tests/unit/setup.ts'] },
});
