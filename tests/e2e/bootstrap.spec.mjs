import { test, expect } from '@playwright/test';

test('fixture identifies its limited scope and all eight engineering roles', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle('LedgerFlow — QA bootstrap fixture');
  await expect(page.getByRole('heading', { name: 'QA bootstrap fixture' })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Awaiting product specification' })).toBeVisible();
  const roles = page.getByRole('list', { name: 'Engineering roles' }).getByRole('listitem');
  await expect(roles).toHaveCount(8);
  for (const name of ['Architect', 'Backend Engineer', 'Database Engineer', 'Frontend Engineer', 'QA Engineer', 'Security Engineer', 'DevOps Engineer', 'Technical Writer']) {
    await expect(roles.filter({ hasText: name })).toBeVisible();
  }
  await expect(page.getByText('Financial operations and Chaos Lab are not implemented here.', { exact: false })).toBeVisible();
});

test('scenario disclosure supports pointer and keyboard interaction', async ({ page }) => {
  await page.goto('/');
  const summary = page.locator('summary');
  const description = page.locator('#scenario-description');
  await expect(description).toBeHidden();

  await test.step('open the scenario with a visible pointer action', async () => {
    await summary.click();
    await expect(description).toBeVisible();
    await expect(page.locator('#interaction-status')).toHaveText('Scenario details are open.');
  });
  await test.step('close and reopen using keyboard controls', async () => {
    await summary.press('Space');
    await expect(description).toBeHidden();
    await expect(page.locator('#interaction-status')).toHaveText('Scenario details are closed.');
    await summary.press('Enter');
    await expect(description).toBeVisible();
    await expect(page.locator('#interaction-status')).toHaveText('Scenario details are open.');
  });
});

test('fixture remains readable without horizontal overflow on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'QA bootstrap fixture' })).toBeVisible();
  await expect(page.getByRole('list', { name: 'Engineering roles' }).getByRole('listitem')).toHaveCount(8);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('summary').click();
  await expect(page.locator('#scenario-description')).toBeVisible();
});

test('fixture server exposes only allowed routes and read-only HTTP methods', async ({ request }) => {
  const health = await request.get('/health');
  expect(health.status()).toBe(200);
  expect(await health.json()).toEqual({ status: 'ok', service: 'ledgerflow-qa-fixture' });
  for (const path of ['/.env', '/.git/config', '/package.json', '/%2e%2e/.env']) {
    const response = await request.get(path);
    expect(response.status(), path).toBe(404);
    expect(await response.text()).toBe('Not found');
  }
  const rejected = await request.post('/');
  expect(rejected.status()).toBe(405);
  expect(rejected.headers().allow).toBe('GET, HEAD');
  const head = await request.head('/');
  expect(head.status()).toBe(200);
  expect(head.headers()['content-type']).toContain('text/html');
  expect(head.headers()['content-security-policy']).toContain("default-src 'none'");
  expect(await head.text()).toBe('');
});
