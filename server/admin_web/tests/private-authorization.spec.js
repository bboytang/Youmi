import { test, expect } from '@playwright/test';
import { mockBackend, device } from './fixtures.js';

test('HTTP console cannot mint a private authorization code', async ({ page }) => {
  await mockBackend(page, false);
  const requests = [];
  page.on('request', request => {
    if (request.url().includes('/device/youmi-auth/')) requests.push(request.url());
  });
  await page.goto('/#/device-management?agentId=test-agent');
  await page.getByRole('button', { name: '私有授权码', exact: true }).first().click();
  await expect(page.getByText('请先通过 HTTPS 打开智控台，再办理私有授权。', { exact: true })).toBeVisible();
  expect(requests).toEqual([]);
});

test('console code and revoke reuse login, preserve binding and clear ephemeral code', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  // Browser routing supplies static assets for a synthetic HTTPS-origin UI.
  // This verifies UI/request policy; it is not actual TLS/certificate acceptance.
  await page.route('https://mia-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  const requests = [];
  page.on('request', request => {
    if (request.url().includes('/device/youmi-auth/')) requests.push({
      path: new URL(request.url()).pathname, body: request.postDataJSON(),
      authorization: request.headers()['authorization'], method: request.method()
    });
  });
  await page.goto('https://mia-fixture.invalid/#/device-management?agentId=test-agent');
  await page.getByRole('button', { name: '私有授权码', exact: true }).first().click();
  const dialog = page.locator('.el-dialog').filter({ hasText: 'Youmi 私有授权码' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('input')).toHaveValue('b'.repeat(22));
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain('b'.repeat(22));
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(dialog.locator('input')).toHaveValue('');
  await page.getByRole('button', { name: '撤销私有授权', exact: true }).first().click();
  await page.locator('.el-message-box').getByRole('button', { name: /确定/ }).click();
  await expect(page.getByText('私有授权已撤销，原设备绑定保留。', { exact: true })).toBeVisible();
  expect(requests).toEqual([
    { path: '/xiaozhi/device/youmi-auth/code', body: { device_id: device.id }, authorization: 'Bearer synthetic-test-token', method: 'POST' },
    { path: '/xiaozhi/device/youmi-auth/revoke', body: { device_id: device.id }, authorization: 'Bearer synthetic-test-token', method: 'POST' }
  ]);
  const width = await page.evaluate(() => ({ actual: document.documentElement.scrollWidth, available: innerWidth }));
  expect(width.actual).toBeLessThanOrEqual(width.available + 1);
});

test('hardware approval sends the physical code through the existing owner endpoint', async ({ page }) => {
  await page.route('https://mia-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  const code = '042617';
  const requests = [];
  await page.route('**/device/youmi-auth/code', async route => {
    const request = route.request();
    requests.push({ body: request.postDataJSON(), authorization: request.headers()['authorization'] });
    await route.fulfill({ json: { code: 0, data: {
      device_id: device.id, authorization_code: code, expires_in: 600
    } } });
  });
  await page.goto('https://mia-fixture.invalid/#/device-management?agentId=test-agent');
  await page.getByRole('button', { name: '硬件授权', exact: true }).first().click();
  const prompt = page.locator('.el-message-box');
  await prompt.locator('input').fill(code);
  await prompt.getByRole('button', { name: /确定/ }).click();
  await expect(page.getByText('硬件授权已确认，请等待 Youmi 完成安全连接。', { exact: true })).toBeVisible();
  expect(requests).toEqual([{ body: { device_id: device.id, hardware_code: code },
    authorization: 'Bearer synthetic-test-token' }]);
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(code);
});
