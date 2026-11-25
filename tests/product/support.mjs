import { randomUUID } from 'node:crypto';
import { expect } from '@playwright/test';

export const demoPassword = 'DemoPass!2026';
export const uniqueName = (prefix) => `PW ${prefix} ${randomUUID().slice(0, 8)}`;

export async function loginApi(request, email = 'admin@ledgerflow.local', password = demoPassword) {
  const response = await request.post('/api/v1/auth/login', { data: { email, password } });
  expect(response.status(), `Login ${email}`).toBe(200);
  const body = await response.json();
  return { token: body.access_token, user: body.user, email, password };
}

export async function api(request, actor, method, path, data, { key, status = 200 } = {}) {
  const response = await request.fetch(`/api/v1${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${actor.token}`,
      ...(method !== 'GET' ? { 'Idempotency-Key': key ?? randomUUID() } : {}),
    },
    ...(data === undefined ? {} : { data }),
  });
  expect(response.status(), `${method} ${path}: ${await response.text()}`).toBe(status);
  return response.json();
}

export async function customer(request, prefix = 'customer') {
  const email = `pw-${prefix}-${randomUUID()}@ledgerflow.local`;
  const response = await request.post('/api/v1/auth/register', {
    data: { email, password: demoPassword, display_name: uniqueName(prefix) },
  });
  expect(response.status()).toBe(201);
  return loginApi(request, email);
}

export async function wallet(request, actor, prefix = 'wallet', funds = 0) {
  const result = await api(request, actor, 'POST', '/wallets', {
    name: uniqueName(prefix), currency: 'RUB',
  });
  if (funds) await api(request, actor, 'POST', `/wallets/${result.id}/sandbox-topup`, { amount_minor: funds });
  return result;
}

export async function loginUi(page, actor = { email: 'admin@ledgerflow.local', password: demoPassword }) {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome to LedgerFlow', exact: true })).toBeVisible();
  await page.getByRole('textbox', { name: 'Email address', exact: true }).fill(actor.email);
  await page.getByLabel('Password', { exact: true }).fill(actor.password);
  await page.getByRole('button', { name: 'Sign in to workspace', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible();
}

export async function createPaymentUi(page, source, merchant, description, behavior = 'success') {
  await page.goto('/payments?new=1');
  const dialog = page.getByRole('dialog', { name: 'Create payment', exact: true });
  await dialog.getByRole('combobox', { name: 'Source wallet', exact: true }).selectOption(source.id);
  await dialog.getByRole('combobox', { name: /^Merchant/ }).selectOption(merchant.wallet_id);
  await dialog.getByRole('textbox', { name: 'Amount (RUB)', exact: true }).fill('100.00');
  await dialog.getByRole('combobox', { name: 'Provider behavior', exact: true }).selectOption(behavior);
  await dialog.getByRole('textbox', { name: 'Description', exact: true }).fill(description);
  const responsePromise = page.waitForResponse((response) => response.url().endsWith('/api/v1/payments') && response.request().method() === 'POST');
  await dialog.getByRole('button', { name: 'Create payment', exact: true }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  const payment = await response.json();
  await expect(page).toHaveURL(new RegExp(`/payments/${payment.id}$`));
  await expect(page.getByRole('heading', { name: description, exact: true })).toBeVisible();
  return payment;
}

export function assertBalanced(journal) {
  expect(journal.postings.length).toBeGreaterThanOrEqual(2);
  let signed = 0;
  for (const posting of journal.postings) {
    expect(Number.isSafeInteger(posting.amount_minor)).toBe(true);
    expect(posting.amount_minor).toBeGreaterThan(0);
    signed += posting.side.toUpperCase() === 'DEBIT' ? posting.amount_minor : -posting.amount_minor;
  }
  expect(signed, `Journal ${journal.id} debit minus credit`).toBe(0);
}

export async function attachJson(testInfo, name, body) {
  await testInfo.attach(name, { body: JSON.stringify(body, null, 2), contentType: 'application/json' });
}
