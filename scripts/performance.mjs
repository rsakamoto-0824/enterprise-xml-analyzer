import { spawn } from 'node:child_process';
const child = spawn(
  process.execPath,
  ['node_modules/@playwright/test/cli.js', 'test', 'tests/performance'],
  { stdio: 'inherit', env: { ...process.env, PERFORMANCE: '1' } },
);
child.on('exit', (code) => (process.exitCode = code ?? 1));
