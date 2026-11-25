import { defineConfig } from '@playwright/test';

const headed = process.env.LEDGERFLOW_HEADED === '1';
const mode = headed ? 'headed' : 'headless';
const slowMo = Number(process.env.PLAYWRIGHT_SLOW_MO ?? 600);
const baseURL = process.env.LEDGERFLOW_UI_URL ?? 'http://127.0.0.1:5173';
const address = new URL(baseURL);
if (!['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname)) {
  throw new Error('Product tests must use a local LedgerFlow sandbox.');
}
if (!Number.isFinite(slowMo) || slowMo < 0 || slowMo > 5000) {
  throw new Error('PLAYWRIGHT_SLOW_MO must be between 0 and 5000 milliseconds.');
}

// Service lifecycle belongs to the project launcher, never the bootstrap fixture.
export default defineConfig({
  testDir: './tests/product',
  testMatch: '**/*.spec.mjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 120_000,
  globalTimeout: 15 * 60_000,
  expect: { timeout: 15_000 },
  outputDir: `artifacts/product-playwright/${mode}/test-results`,
  reporter: [
    ['list'],
    ['html', { outputFolder: `artifacts/product-playwright/${mode}/html-report`, open: 'never' }],
    ['json', { outputFile: `artifacts/product-playwright/${mode}/results.json` }],
  ],
  use: {
    baseURL,
    browserName: 'chromium',
    headless: !headed,
    launchOptions: { slowMo: headed ? slowMo : 0 },
    viewport: { width: 1280, height: 900 },
    screenshot: { mode: 'on', fullPage: true },
    video: { mode: 'on', size: { width: 1280, height: 900 } },
    trace: 'on',
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
  },
  projects: [{ name: 'product-chromium' }],
});
