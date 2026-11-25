import { readFile } from 'node:fs/promises';
import { test, expect } from '@playwright/test';
import { api, assertBalanced, attachJson, createPaymentUi, customer, demoPassword, loginApi, loginUi, uniqueName, wallet } from './support.mjs';

test('authentication rejects a wrong password, refreshes the real session and signs out', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('textbox', { name: 'Email address', exact: true }).fill('customer@ledgerflow.local');
  await page.getByLabel('Password', { exact: true }).fill('IncorrectSyntheticPassword!');
  await page.getByRole('button', { name: 'Sign in to workspace', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await page.getByLabel('Password', { exact: true }).fill(demoPassword);
  await page.getByRole('button', { name: 'Sign in to workspace', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Welcome to LedgerFlow', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Welcome to LedgerFlow', exact: true })).toBeVisible();
});

test('wallet UI posts virtual funds, reserves without posting and releases once', async ({ page, request }, testInfo) => {
  const actor = await customer(request, 'holds');
  await loginUi(page, actor);
  await page.getByRole('link', { name: 'Wallets', exact: true }).click();
  await page.locator('.page-heading').getByRole('button', { name: 'Create wallet', exact: true }).click();
  const name = uniqueName('reserve wallet');
  const dialog = page.getByRole('dialog', { name: 'Create wallet', exact: true });
  await dialog.getByRole('textbox', { name: 'Wallet name', exact: true }).fill(name);
  await dialog.getByRole('button', { name: 'Create wallet', exact: true }).click();
  const card = page.locator('.wallet-card').filter({ has: page.getByRole('heading', { name, exact: true }) });
  await expect(card).toBeVisible();
  const created = (await api(request, actor, 'GET', '/wallets')).items.find((item) => item.name === name);
  expect(created).toBeTruthy();
  await card.getByRole('button', { name: 'Sandbox top-up', exact: true }).click();
  const topup = page.getByRole('dialog', { name: 'Add sandbox funds', exact: true });
  await topup.getByRole('textbox', { name: /^Amount \(RUB\)/ }).fill('1000.00');
  await topup.getByRole('button', { name: 'Add virtual funds', exact: true }).click();
  await expect(topup).not.toBeVisible();
  await card.getByRole('button', { name: 'Reservations', exact: true }).click();
  const reservations = page.getByRole('dialog', { name: `${name} reservations`, exact: true });
  await reservations.getByRole('textbox', { name: /^Reserve amount \(RUB\)/ }).fill('125.50');
  await reservations.getByRole('button', { name: 'Reserve funds', exact: true }).click();
  const release = reservations.getByRole('button', { name: /^Release reservation / });
  await expect(release).toHaveCount(1);
  expect(await api(request, actor, 'GET', `/wallets/${created.id}`)).toMatchObject({ available_minor: 87450, reserved_minor: 12550, posted_minor: 100000 });
  await release.click();
  await reservations.getByRole('button', { name: 'Confirm release', exact: true }).click();
  await expect(release).toHaveCount(0);
  const after = await api(request, actor, 'GET', `/wallets/${created.id}`);
  expect(after).toMatchObject({ available_minor: 100000, reserved_minor: 0, posted_minor: 100000 });
  const history = await api(request, actor, 'GET', `/wallets/${created.id}/transactions`);
  expect(history.items).toHaveLength(1);
  assertBalanced(history.items[0]);
  await attachJson(testInfo, 'wallet-and-real-ledger', { wallet: after, journal: history.items });
  await reservations.getByRole('button', { name: 'Done', exact: true }).click();
});

test('merchant payment has a causal Inspector timeline and partial then full compensating refunds', async ({ page, request }, testInfo) => {
  const actor = await customer(request, 'refund');
  const source = await wallet(request, actor, 'payment source', 50000);
  const merchant = (await api(request, actor, 'GET', '/merchants')).items[0];
  expect(merchant).toBeTruthy();
  await loginUi(page, actor);
  const description = uniqueName('merchant payment');
  const payment = await createPaymentUi(page, source, merchant, description);
  expect(payment.status).toBe('SUCCEEDED');
  await expect(page.locator('.payment-summary .status')).toHaveText('Succeeded');
  const timeline = (await api(request, actor, 'GET', `/payments/${payment.id}/timeline`)).items;
  expect(timeline[0].kind).toBe('payment.created');
  const kinds = timeline.map((event) => event.kind);
  for (const [before, after] of [['payment.created', 'risk.evaluated'], ['risk.evaluated', 'funds.reserved'], ['funds.reserved', 'provider.request'], ['provider.request', 'provider.response'], ['provider.response', 'ledger.posted']]) {
    expect(kinds.indexOf(before), `${before} exists`).toBeGreaterThanOrEqual(0);
    expect(kinds.indexOf(before), `${before} precedes ${after}`).toBeLessThan(kinds.indexOf(after));
  }
  expect(timeline.map((event) => event.created_at)).toEqual([...timeline.map((event) => event.created_at)].sort());
  await expect(page.locator('.timeline-event-heading strong')).toHaveText(timeline.map((event) => event.title));
  const original = (await api(request, actor, 'GET', `/wallets/${source.id}/transactions`)).items.find((entry) => entry.kind === 'PAYMENT');
  expect(original).toBeTruthy();
  assertBalanced(original);
  for (const [amount, status, refunded] of [['30.00', 'Partially Refunded', 3000], ['70.00', 'Refunded', 10000]]) {
    await page.getByRole('button', { name: 'Issue refund', exact: true }).click();
    const refund = page.getByRole('dialog', { name: 'Issue a virtual refund', exact: true });
    await refund.getByRole('textbox', { name: 'Refund amount (RUB)', exact: true }).fill(amount);
    await refund.getByRole('button', { name: 'Confirm refund', exact: true }).click();
    await expect(page.locator('.payment-summary .status')).toHaveText(status, { ignoreCase: true });
    expect(await api(request, actor, 'GET', `/payments/${payment.id}`)).toMatchObject({ refunded_minor: refunded });
  }
  await expect(page.getByRole('button', { name: 'Issue refund', exact: true })).toHaveCount(0);
  const after = await api(request, actor, 'GET', `/wallets/${source.id}`);
  expect(after).toMatchObject({ posted_minor: 50000, available_minor: 50000, reserved_minor: 0 });
  const journal = (await api(request, actor, 'GET', `/wallets/${source.id}/transactions`)).items;
  expect(journal.filter((entry) => entry.kind === 'REFUND')).toHaveLength(2);
  expect(journal.find((entry) => entry.id === original.id)).toEqual(original);
  journal.forEach(assertBalanced);
  await attachJson(testInfo, 'payment-timeline-refunds-ledger', { payment, timeline, journal, after });
  await page.getByRole('link', { name: 'Inspect ledger entries', exact: true }).click();
  await page.getByRole('textbox', { name: 'Search ledger…', exact: true }).fill(description);
  const entry = page.locator('.journal-entry').filter({ hasText: description });
  await expect(entry).toHaveCount(1);
  await entry.locator('summary').click();
  await expect(entry.getByRole('columnheader', { name: 'Debit', exact: true })).toBeVisible();
  await expect(entry.locator('tbody tr')).toHaveCount(2);
});

test('a lost provider response recovers through its original provider with one financial effect', async ({ page, request }, testInfo) => {
  const actor = await customer(request, 'unknown');
  const source = await wallet(request, actor, 'unknown source', 20000);
  const merchant = (await api(request, actor, 'GET', '/merchants')).items[0];
  await loginUi(page, actor);
  const payment = await createPaymentUi(page, source, merchant, uniqueName('lost response'), 'lost_response');
  expect(payment.status).toBe('UNKNOWN');
  const observedWallet = await api(request, actor, 'GET', `/wallets/${source.id}`);
  // The real three-second maintenance worker can win the race with slowed UI
  // actions. Both observable snapshots must have exactly the same available funds.
  expect(observedWallet.available_minor).toBe(10000);
  let recovery = 'background worker';
  if (observedWallet.reserved_minor === 10000) {
    expect(observedWallet).toMatchObject({ posted_minor: 20000, reserved_minor: 10000 });
    try {
      await page.getByRole('button', { name: 'Resolve status', exact: true }).click({ timeout: 5000 });
      recovery = 'manual resolution requested';
    } catch (error) {
      if (error.name !== 'TimeoutError') throw error;
      // Refetching may remove the button after automatic resolution. A timeout
      // is acceptable only if persisted original-provider recovery is proved below.
      recovery = 'background worker won the resolution-button race';
    }
  } else {
    expect(observedWallet).toMatchObject({ posted_minor: 10000, reserved_minor: 0 });
  }
  const before = (await api(request, actor, 'GET', `/payments/${payment.id}/timeline`)).items;
  expect(before.filter((event) => event.kind === 'provider.request')).toHaveLength(1);
  expect(before.some((event) => event.kind === 'provider.fallback')).toBe(false);
  await expect(page.locator('.payment-summary .status')).toHaveText('Succeeded');
  const settled = await api(request, actor, 'GET', `/payments/${payment.id}`);
  expect(settled.status).toBe('SUCCEEDED');
  expect(settled.provider).toBe(payment.provider);
  const timeline = (await api(request, actor, 'GET', `/payments/${payment.id}/timeline`)).items;
  const unknownIndex = timeline.findIndex((event) => event.kind === 'payment.state' && event.status === 'UNKNOWN');
  const reconciledIndex = timeline.findIndex((event) => event.kind === 'provider.reconciled' && event.status === 'SUCCEEDED');
  const succeededIndex = timeline.findIndex((event) => event.kind === 'payment.state' && event.status === 'SUCCEEDED');
  const postedIndex = timeline.findIndex((event) => event.kind === 'ledger.posted');
  expect(unknownIndex).toBeGreaterThanOrEqual(0);
  expect(reconciledIndex).toBeGreaterThan(unknownIndex);
  expect(succeededIndex).toBeGreaterThan(reconciledIndex);
  expect(postedIndex).toBeGreaterThan(succeededIndex);
  expect(timeline.filter((event) => event.kind === 'provider.request')).toHaveLength(1);
  expect(timeline.filter((event) => event.kind === 'provider.reconciled')).toHaveLength(1);
  expect(timeline.filter((event) => event.kind === 'ledger.posted')).toHaveLength(1);
  expect(timeline.some((event) => event.kind === 'provider.fallback')).toBe(false);
  const journal = (await api(request, actor, 'GET', `/wallets/${source.id}/transactions`)).items.filter((entry) => entry.kind === 'PAYMENT');
  expect(journal).toHaveLength(1);
  assertBalanced(journal[0]);
  expect(await api(request, actor, 'GET', `/wallets/${source.id}`)).toMatchObject({ posted_minor: 10000, available_minor: 10000, reserved_minor: 0 });
  await attachJson(testInfo, 'unknown-before-and-settled', { payment, observedWallet, recovery, before, settled, timeline, journal });
});

test('transfer UI rejects insufficient funds and its idempotency key prevents a second posting', async ({ page, request }, testInfo) => {
  const actor = await customer(request, 'transfer');
  const source = await wallet(request, actor, 'transfer source', 20000);
  const destination = await wallet(request, actor, 'transfer destination');
  await loginUi(page, actor);
  await page.getByRole('link', { name: 'Transfers', exact: true }).click();
  await page.getByRole('combobox', { name: 'From wallet', exact: true }).selectOption(source.id);
  await page.getByRole('combobox', { name: /^To wallet ID/ }).fill(destination.id);
  await page.getByRole('textbox', { name: 'Amount (RUB)', exact: true }).fill('999.00');
  const description = uniqueName('UI transfer');
  await page.getByRole('textbox', { name: 'Description', exact: true }).fill(description);
  await page.getByRole('button', { name: 'Send virtual funds', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText(/insufficient/i);
  expect(await api(request, actor, 'GET', `/wallets/${source.id}`)).toMatchObject({ posted_minor: 20000 });
  await page.getByRole('textbox', { name: 'Amount (RUB)', exact: true }).fill('50.25');
  const postedPromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/transfers') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Send virtual funds', exact: true }).click();
  const postedResponse = await postedPromise;
  expect(postedResponse.status()).toBe(200);
  const posted = await postedResponse.json();
  const body = postedResponse.request().postDataJSON();
  const key = postedResponse.request().headers()['idempotency-key'];
  expect(key).toBeTruthy();
  const replay = await api(request, actor, 'POST', '/transfers', body, { key });
  expect(replay.id).toBe(posted.id);
  expect(replay.journal_id).toBe(posted.journal_id);
  const conflict = await api(request, actor, 'POST', '/transfers', { ...body, amount_minor: body.amount_minor + 1 }, { key, status: 409 });
  expect(conflict.error.code).toBe('IDEMPOTENCY_CONFLICT');
  await expect(page.getByRole('cell', { name: new RegExp(description) })).toBeVisible();
  const transfers = (await api(request, actor, 'GET', '/transfers')).items;
  expect(transfers.filter((item) => item.description === description)).toHaveLength(1);
  expect(await api(request, actor, 'GET', `/wallets/${source.id}`)).toMatchObject({ posted_minor: 14975, available_minor: 14975 });
  expect(await api(request, actor, 'GET', `/wallets/${destination.id}`)).toMatchObject({ posted_minor: 5025 });
  const entries = (await api(request, actor, 'GET', `/wallets/${destination.id}/transactions`)).items;
  expect(entries).toHaveLength(1);
  assertBalanced(entries[0]);
  await attachJson(testInfo, 'transfer-ledger', { transfers, entries });
});

test('customer isolation and staff authorization are enforced by API and navigation', async ({ page, request }) => {
  const alice = await customer(request, 'alice');
  const bob = await customer(request, 'bob');
  const privateWallet = await wallet(request, bob, 'private', 10000);
  const owned = await wallet(request, alice, 'owned', 10000);
  const visible = (await api(request, alice, 'GET', '/wallets')).items;
  expect(visible.some((item) => item.id === privateWallet.id)).toBe(false);
  await api(request, alice, 'GET', `/wallets/${privateWallet.id}`, undefined, { status: 404 });
  await api(request, alice, 'POST', `/wallets/${privateWallet.id}/sandbox-topup`, { amount_minor: 100 }, { status: 404 });
  await api(request, alice, 'GET', '/chaos/scenarios', undefined, { status: 403 });
  await api(request, alice, 'POST', `/wallets/${owned.id}/block`, {}, { status: 403 });
  await loginUi(page, alice);
  await expect(page.getByRole('link', { name: /^Chaos Lab/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Reconciliation', exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Wallets', exact: true }).click();
  await expect(page.getByRole('heading', { name: privateWallet.name, exact: true })).toHaveCount(0);
  const admin = await loginApi(request);
  await api(request, admin, 'POST', `/wallets/${owned.id}/block`, {});
  expect(await api(request, alice, 'GET', `/wallets/${owned.id}`)).toMatchObject({ status: 'BLOCKED' });
  await api(request, admin, 'POST', `/wallets/${owned.id}/unblock`, {});
  const analyst = await loginApi(request, 'analyst@ledgerflow.local');
  await api(request, analyst, 'POST', `/wallets/${owned.id}/sandbox-topup`, { amount_minor: 100 }, { status: 403 });
});

test('reconciliation exports the actual completed report as CSV', async ({ page, request }, testInfo) => {
  await loginUi(page);
  await page.getByRole('link', { name: 'Reconciliation', exact: true }).click();
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/reconciliation/runs') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Run reconciliation', exact: true }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const run = await response.json();
  const dialog = page.getByRole('dialog', { name: 'Reconciliation report', exact: true });
  await expect(dialog).toBeVisible();
  expect(['COMPLETED', 'DISCREPANCIES']).toContain(run.status);
  expect(run.summary.invariants.valid).toBe(true);
  const downloadPromise = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Export CSV', exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`reconciliation-${run.id}.csv`);
  const path = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(path);
  const csv = await readFile(path, 'utf8');
  expect(csv).toContain('classification');
  expect(csv.length).toBeGreaterThan(20);
  await testInfo.attach('actual-reconciliation-export', { path, contentType: 'text/csv' });
  await attachJson(testInfo, 'actual-reconciliation-report', run);
});

test('Chaos Lab records lost-response recovery and actual financial invariants', async ({ page, request }, testInfo) => {
  const admin = await loginApi(request);
  const scenario = (await api(request, admin, 'GET', '/chaos/scenarios')).items.find((item) => item.id === 'provider_lost_response');
  expect(scenario).toBeTruthy();
  await loginUi(page);
  await page.getByRole('link', { name: /^Chaos Lab/ }).click();
  const card = page.locator('.scenario-card').filter({ has: page.getByRole('heading', { name: scenario.name, exact: true }) });
  await card.getByRole('button', { name: 'Run experiment', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Run controlled experiment?', exact: true });
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/chaos/experiments') && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Run sandbox experiment', exact: true }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(202);
  const started = await response.json();
  await expect(page.locator('.experiment-summary .status')).toHaveText('Passed', { timeout: 100_000 });
  const experiment = await api(request, admin, 'GET', `/chaos/experiments/${started.id}`);
  expect(experiment.status).toBe('PASSED');
  expect(experiment.invariants.valid).toBe(true);
  expect(experiment.result.passed).toBe(true);
  expect(experiment.timeline.length).toBeGreaterThan(1);
  await expect(page.locator('.invariant-list')).toContainText('Passed');
  await expect(page.getByRole('heading', { name: 'Recovery procedure', exact: true })).toBeVisible();
  await attachJson(testInfo, 'actual-chaos-experiment', experiment);
});

test('375px mobile workspace exposes navigation without horizontal page overflow', async ({ page, request }, testInfo) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const actor = await customer(request, 'mobile');
  await wallet(request, actor, 'mobile wallet', 10000);
  await loginUi(page, actor);
  for (const destination of ['Wallets', 'Payments', 'Transfers', 'Ledger explorer']) {
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await page.getByRole('link', { name: destination, exact: true }).click();
    await expect(page.getByRole('heading', { name: destination, exact: true })).toBeVisible();
    await expect(page.getByText('Loading live data…', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('alert')).toHaveCount(0);
    const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth }));
    expect(dimensions.scrollWidth, `${destination} horizontal overflow`).toBeLessThanOrEqual(dimensions.width + 1);
    await page.screenshot({ path: testInfo.outputPath(`mobile-${destination.toLowerCase().replaceAll(' ', '-')}.png`), fullPage: true });
  }
});
