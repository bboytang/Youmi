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
