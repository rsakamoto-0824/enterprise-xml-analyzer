import { readFile, readdir } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { SaxesParser } from 'saxes';

const root = path.resolve(import.meta.dirname, '..');
const manifest = JSON.parse(
  await readFile(path.join(root, 'dist/deployment-manifest.json'), 'utf8'),
);
if (manifest.appId !== 'enterprise-xml-analyzer') throw new Error('Unexpected package identity');
const roots = ['dist', 'artifacts/enterprise-xml-analyzer-iis/wwwroot'];
for (const folder of roots) {
  for (const entry of manifest.files) {
    if (entry.path.startsWith('/') || entry.path.includes('..') || /[\\:]/.test(entry.path))
      throw new Error('Invalid asset path');
    const bytes = await readFile(path.join(root, folder, entry.path));
    if (createHash('sha256').update(bytes).digest('hex') !== entry.sha256)
      throw new Error('Checksum mismatch: ' + entry.path);
  }
  async function inspect(relative = '') {
    for (const item of await readdir(path.join(root, folder, relative), { withFileTypes: true })) {
      const name = relative + item.name;
      if (item.isDirectory()) await inspect(name + '/');
      else if (name !== 'deployment-manifest.json' && !manifest.files.some((f) => f.path === name))
        throw new Error('Unexpected asset: ' + name);
      if (/\.(?:map|xml|ts|tsx|ps1|env|log)$/i.test(name))
        throw new Error('Source/test file in public package: ' + name);
    }
  }
  await inspect();
}
for (const name of ['deployment/web.config', 'release/iis/web.config', 'dist/web.config']) {
  const parser = new SaxesParser();
  parser.on('error', (error) => {
    throw error;
  });
  parser.write(await readFile(path.join(root, name), 'utf8')).close();
}
if (manifest.files.filter((f) => /\.worker-.*\.js$/.test(f.path)).length !== 4)
  throw new Error('Expected four workers');
console.log(
  `PASS: ${manifest.files.length} assets, package checksums, four workers, XML configuration syntax, no source/test data in wwwroot.`,
);
console.log('IIS semantics and PowerShell execution still require Windows acceptance tests.');
