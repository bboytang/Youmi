import { test, expect } from '@playwright/test';
import { mockBackend } from './fixtures.js';

async function setup(page) {
  await page.route('https://youmi-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  const backend = { fail: false, memories: [
    { id: 'memory-1', content: '我喜欢蓝色 <script>unsafe</script>', confirmed: true, updated_at: 1700000000 },
    { id: 'memory-2', content: '正在准备毕业项目', confirmed: false, updated_at: 1700000001 }
  ] };
  const requests = [];
  await page.route('**/agent/test-agent/youmi-settings', async route => {
    const command = route.request().postDataJSON();
    requests.push({ command, authorization: route.request().headers()['authorization'] });
    if (backend.fail) { await route.fulfill({ json: { code: 500 } }); return; }
    if (command.action === 'memory_delete') backend.memories = backend.memories.filter(m => m.id !== command.id);
    await route.fulfill({ json: { code: 0, data: { memories: backend.memories, total: backend.memories.length, offset: 0, page_size: 50 } } });
  });
  await page.goto('https://youmi-fixture.invalid/#/home');
  await page.getByRole('button', { name: '记忆管理', exact: true }).first().click();
  const dialog = page.locator('.el-dialog').filter({ hasText: '共 2 条记忆' });
  await expect(dialog).toContainText('我喜欢蓝色 <script>unsafe</script>');
  return { backend, requests, dialog };
}

test('memory entry lists actual server records and confirms individual deletion', async ({ page }) => {
  const { dialog, requests } = await setup(page);
  await expect(dialog.locator('script')).toHaveCount(0);
  await dialog.getByRole('button', { name: '删除记忆' }).first().click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  expect(requests.filter(r => r.command.action === 'memory_delete')).toHaveLength(0);
  await dialog.getByRole('button', { name: '删除记忆' }).first().click();
  await page.locator('.el-message-box').getByRole('button', { name: '删除', exact: true }).click();
  const active = page.locator('.el-dialog').filter({ hasText: '共 1 条记忆' });
  await expect(active).toContainText('正在准备毕业项目');
  await expect(active).not.toContainText('我喜欢蓝色');
  expect(requests.find(r => r.command.action === 'memory_delete')).toEqual({
    command: { action: 'memory_delete', id: 'memory-1', offset: 0 }, authorization: 'Bearer synthetic-test-token'
  });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('unconfirmed delete reports failure and reload restores server truth', async ({ page }) => {
  const { dialog, backend } = await setup(page);
  backend.fail = true;
  await dialog.getByRole('button', { name: '删除记忆' }).first().click();
  await page.locator('.el-message-box').getByRole('button', { name: '删除', exact: true }).click();
  const active = page.locator('.el-dialog').filter({ hasText: '记忆操作结果未确认' });
  await expect(active.getByRole('alert')).toContainText('请刷新后核对');
  await expect(page.locator('.el-message--success')).toHaveCount(0);
  backend.fail = false;
  await active.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.locator('.el-dialog').filter({ hasText: '共 2 条记忆' })).toContainText('我喜欢蓝色');
});
