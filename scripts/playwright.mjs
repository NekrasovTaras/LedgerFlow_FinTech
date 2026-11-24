import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('../', import.meta.url));
const args = process.argv.slice(2);
// Workers reload the config with different argv. Propagate the mode to every
// descendant so the runner and workers agree on recording/report directories.
const headed = args.includes('--headed') || process.env.LEDGERFLOW_HEADED === '1';
const child = spawn(process.execPath, [
  fileURLToPath(import.meta.resolve('@playwright/test/cli')),
  ...args,
], {
  cwd: projectRoot,
  shell: false,
  stdio: 'inherit',
  env: {
    ...process.env,
    LEDGERFLOW_HEADED: headed ? '1' : '0',
    PLAYWRIGHT_BROWSERS_PATH: fileURLToPath(new URL('../.cache/ms-playwright', import.meta.url)),
  },
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}
child.on('error', (error) => {
  console.error(`Unable to run project Playwright: ${error.code ?? error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code, signal) => {
  process.exitCode = code ?? ({ SIGINT: 130, SIGTERM: 143 }[signal] ?? 1);
});
