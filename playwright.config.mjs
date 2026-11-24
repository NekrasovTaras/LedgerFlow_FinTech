import { defineConfig, devices } from '@playwright/test';

const headed = process.env.LEDGERFLOW_HEADED === '1';
const slowMo = Number(process.env.LEDGERFLOW_SLOW_MO ?? '600');
if (!Number.isFinite(slowMo) || slowMo < 0 || slowMo > 5000) {
  throw new Error('LEDGERFLOW_SLOW_MO must be a number between 0 and 5000 milliseconds.');
}
const mode = headed ? 'headed' : 'headless';
const outputRoot = `artifacts/playwright/${mode}`;

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  timeout: 30000,
  globalTimeout: 120000,
  outputDir: `${outputRoot}/test-results`,
  reporter: [
    ['list'],
    ['html', { outputFolder: `${outputRoot}/html-report`, open: 'never' }],
    ['json', { outputFile: `${outputRoot}/results.json` }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: !headed,
    launchOptions: { slowMo: headed ? slowMo : 0 },
    screenshot: { mode: 'on', fullPage: true },
    video: { mode: 'on', size: { width: 1280, height: 800 } },
    trace: 'on',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: 'node scripts/harness-server.mjs',
    url: 'http://127.0.0.1:4173/health',
    reuseExistingServer: false,
    timeout: 15000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 3000 },
  },
});
