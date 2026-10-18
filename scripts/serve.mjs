import http from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '../dist');
const config = await readFile(
  path.resolve(import.meta.dirname, '../deployment/web.config'),
  'utf8',
);
const csp = /name="Content-Security-Policy" value="([^"]+)"/.exec(config)[1];
const mime = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.txt': 'text/plain',
  '.svg': 'image/svg+xml',
};
http
  .createServer(async (req, res) => {
    try {
      if (!['GET', 'HEAD'].includes(req.method)) {
        res.writeHead(405);
        res.end();
        return;
      }
      let relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      if (relative.startsWith('/xmlviewer/')) relative = relative.slice('/xmlviewer'.length);
      if (relative.endsWith('/')) relative += 'index.html';
      const file = path.resolve(root, '.' + relative);
      if (
        !file.startsWith(root + path.sep) ||
        file.endsWith('web.config') ||
        !(await stat(file)).isFile()
      )
        throw new Error('Not found');
      res.writeHead(200, {
        'Content-Type': mime[path.extname(file)] ?? 'application/octet-stream',
        'Content-Security-Policy': csp,
        'X-Content-Type-Options': 'nosniff',
        'Cache-Control': 'no-store',
        'Referrer-Policy': 'no-referrer',
      });
      res.end(req.method === 'HEAD' ? undefined : await readFile(file));
    } catch {
      res.writeHead(404);
      res.end('Not found');
    }
  })
  .listen(4173, '127.0.0.1', () =>
    console.log('Local production test server http://127.0.0.1:4173/xmlviewer/'),
  );
