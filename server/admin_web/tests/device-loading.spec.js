import { test, expect } from '@playwright/test';
import { mockBackend, device } from './fixtures.js';

test('device status failure leaves the loaded device list usable', async ({ page }) => {
  await mockBackend(page, false);
  await page.route('**/device/bind/test-agent', async route => {
    if (route.request().method() === 'POST') {
      await route.fulfill({ status: 500, json: { code: 500, msg: 'status unavailable' } });
    } else {
      await route.fulfill({ json: { code: 0, data: [device] } });
    }
  });
  await page.goto('/#/device-management?agentId=test-agent');
  await expect(page.getByRole('button', { name: '硬件授权', exact: true }).first()).toBeVisible();
  await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);
});

test('device list failure ends loading and reports an error', async ({ page }) => {
  await mockBackend(page, false);
  await page.route('**/device/bind/test-agent', route =>
    route.fulfill({ status: 500, json: { code: 500, msg: 'list unavailable' } }));
  await page.goto('/#/device-management?agentId=test-agent');
  await expect(page.locator('.el-message--error')).toBeVisible();
  await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);
});

test('a pending online status request does not obscure the device list', async ({ page }) => {
  await mockBackend(page, false);
  await page.route('**/device/bind/test-agent', async route => {
    if (route.request().method() === 'POST') return;
    await route.fulfill({ json: { code: 0, data: [device] } });
  });
  const statusRequest = page.waitForRequest(request => request.url().includes('/device/bind/test-agent') && request.method() === 'POST');
  await page.goto('/#/device-management?agentId=test-agent');
  await statusRequest;
  await expect(page.getByRole('button', { name: '硬件授权', exact: true }).first()).toBeVisible();
  await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);
});

test('a stalled device list stops loading and reports an error', async ({ page }) => {
  await mockBackend(page, false);
  await page.route('**/device/bind/test-agent', async () => {});
  await page.goto('/#/device-management?agentId=test-agent');
  await expect(page.locator('.el-message--error')).toBeVisible({ timeout: 15000 });
  await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);
});

for (const firmwareVersion of [null, undefined, '']) {
  test(`devices without a firmware version render alongside hardware (${firmwareVersion})`, async ({ page }) => {
    await mockBackend(page, false);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.route('**/device/bind/test-agent', route => route.fulfill({
      json: { code: 0, data: route.request().method() === 'POST' ? '' : [
        device,
        { ...device, id: 'qq-device', board: 'youmi-astrbot', alias: 'QQ virtual device', appVersion: firmwareVersion },
      ] },
    }));
    await page.goto('/#/device-management?agentId=test-agent');
    await expect(page.getByText('QQ virtual device', { exact: true }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: '硬件授权', exact: true })).toHaveCount(2);
    await expect(page.locator('.el-loading-mask:visible')).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
