import { mkdir } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import browserify from 'browserify';
import { transformSync } from 'esbuild';
import { Transform, PassThrough } from 'node:stream';
await mkdir('.generated', { recursive: true });
const bundler = browserify('node_modules/exceljs/lib/exceljs.bare.js', { standalone: 'ExcelJS' });
// Workbook encryption/protection is outside the export feature set. Do not ship a crypto polyfill.
bundler.ignore('crypto');
bundler.transform(
  (file) => {
    if (!/[\\/]uuid[\\/]/.test(file)) return new PassThrough();
    let source = '';
    return new Transform({
      transform(chunk, _encoding, callback) {
        source += chunk;
        callback();
      },
      flush(callback) {
        try {
          this.push(transformSync(source, { target: 'es2018', format: 'cjs' }).code);
          callback();
        } catch (error) {
          callback(error);
        }
      },
    });
  },
  { global: true },
);
try {
  await pipeline(bundler.bundle(), createWriteStream('.generated/exceljs.cjs'));
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
