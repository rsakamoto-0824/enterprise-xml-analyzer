import { readFile, writeFile, readdir, mkdir, cp, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '..');
process.chdir(root);
await cp('deployment/web.config', 'dist/web.config');
const packageInfo = JSON.parse(await readFile('package.json', 'utf8'));
const licenseDirs = await readdir('node_modules', { withFileTypes: true });
let licenses = 'Third-party notices — Enterprise XML Analyzer\n\n';
const seen = new Set();
async function collect(dir) {
  let metadata;
  try {
    metadata = JSON.parse(await readFile(path.join(dir, 'package.json'), 'utf8'));
  } catch {
    return;
  }
  const names = await readdir(dir);
  const license = names.find((n) => /^licen[sc]e(?:\.|$)/i.test(n));
  const key = metadata.name + '@' + metadata.version;
  if (license && !seen.has(key)) {
    seen.add(key);
    licenses += `\n=== ${metadata.name} ${metadata.version} (${metadata.license ?? 'see license'}) ===\n${await readFile(path.join(dir, license), 'utf8')}\n`;
  }
  if (names.includes('node_modules'))
    for (const name of await readdir(path.join(dir, 'node_modules'))) {
      if (name.startsWith('@'))
        for (const scoped of await readdir(path.join(dir, 'node_modules', name)))
          await collect(path.join(dir, 'node_modules', name, scoped));
      else await collect(path.join(dir, 'node_modules', name));
    }
}
for (const entry of licenseDirs) {
  if (entry.name.startsWith('.')) continue;
  if (entry.name.startsWith('@'))
    for (const name of await readdir(path.join('node_modules', entry.name)))
      await collect(path.join('node_modules', entry.name, name));
  else await collect(path.join('node_modules', entry.name));
}
await writeFile('dist/THIRD-PARTY-NOTICES.txt', licenses);
async function list(dir, prefix = '') {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const relative = prefix + entry.name;
    if (entry.isDirectory()) out.push(...(await list(path.join(dir, entry.name), relative + '/')));
    else if (relative !== 'deployment-manifest.json') {
      if (/\.map$|\.xml$|\.ts$|\.env$/.test(relative))
        throw new Error('Unexpected production file: ' + relative);
      out.push({
        path: relative,
        sha256: createHash('sha256')
          .update(await readFile(path.join(dir, entry.name)))
          .digest('hex'),
      });
    }
  }
  return out;
}
await writeFile(
  'dist/deployment-manifest.json',
  JSON.stringify(
    {
      appId: packageInfo.name,
      version: packageInfo.version,
      builtAt: new Date().toISOString(),
      files: await list('dist'),
    },
    null,
    2,
  ),
);
const output = 'artifacts/enterprise-xml-analyzer-iis';
// This is a generated staging folder under this repository, never a deployment destination.
await rm(output, { recursive: true, force: true });
await mkdir(output, { recursive: true });
await cp('dist', path.join(output, 'wwwroot'), { recursive: true });
await cp('deployment', path.join(output, 'deployment'), { recursive: true });
await cp('docs', path.join(output, 'docs'), { recursive: true });
await cp('README.md', path.join(output, 'README.md'));
await cp('issues.md', path.join(output, 'issues.md'));
await mkdir(path.join(output, 'release'), { recursive: true });
for (const name of [
  'build-and-publish-steps.html',
  'base.css',
  'procedure-guide.css',
  'procedure-guide.js',
])
  await cp(path.join('release', name), path.join(output, 'release', name));
// zip is available on macOS/Linux; Windows can use Compress-Archive on this folder.
if (process.platform !== 'win32') {
  await rm('artifacts/enterprise-xml-analyzer-iis.zip', { force: true });
  execFileSync('zip', ['-qr', 'enterprise-xml-analyzer-iis.zip', 'enterprise-xml-analyzer-iis'], {
    cwd: 'artifacts',
  });
}
console.log('IIS package: ' + path.join(root, output));
