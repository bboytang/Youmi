import { test, expect } from '@playwright/test';
import { mockBackend } from './fixtures.js';

async function setup(page) {
  await page.route('https://youmi-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  const state = {
    features: { memory_recall: true, personality_growth: true, interaction_events: true, companion_emotion: false },
    paused: false, emotion_tracking: { enabled: 0, version: 0 },
    youmi_emotion: { enabled: false, label: '平静', updated_at: null },
    counts: { memories: 2, traits: 0, events: 1 }
  };
  const requests = [];
  const backend = { fail: false };
  await page.route('**/agent/test-agent/youmi-settings', async route => {
    const request = route.request();
    const command = request.postDataJSON();
    requests.push({ command, authorization: request.headers()['authorization'] });
    if (backend.fail && command.action !== 'status') {
      await route.fulfill({ json: { code: 500, msg: 'synthetic failure' } }); return;
    }
    if (command.action === 'pause') state.paused = command.paused;
    if (command.action === 'set_feature_settings') {
      Object.assign(state.features, command.features);
      state.youmi_emotion.enabled = state.features.companion_emotion;
    }
    if (command.action === 'reset_youmi_emotion') { state.youmi_emotion.label = '平静'; state.youmi_emotion.updated_at = null; }
    if (command.action === 'emotion_consent') state.emotion_tracking.enabled = command.enabled ? 1 : 0;
    await route.fulfill({ json: { code: 0, data: state } });
  });
  await page.goto('https://youmi-fixture.invalid/#/home');
  await page.getByRole('button', { name: 'Youmi 功能管理', exact: true }).first().click();
  const dialog = page.locator('.el-dialog').filter({ hasText: 'Youmi 功能管理' });
  await expect(dialog.getByRole('switch', { name: '自动保存记忆', exact: true })).toHaveAttribute('aria-checked', 'true');
  return { state, requests, backend, dialog };
}

test('console switches read server state, save with login, and survive reopening', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { dialog, requests } = await setup(page);
  await expect(dialog.getByRole('switch')).toHaveCount(6);
  await expect(dialog.getByRole('switch', { name: '我的长期心情追踪' })).toHaveAttribute('aria-checked', 'false');
  await dialog.getByRole('switch', { name: '人格成长', exact: true }).click();
  await expect(dialog.getByRole('switch', { name: '人格成长', exact: true })).toHaveAttribute('aria-checked', 'false');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: 'Youmi 功能管理', exact: true }).first().click();
  await expect(dialog.getByRole('switch', { name: '人格成长', exact: true })).toHaveAttribute('aria-checked', 'false');
  expect(requests.filter(item => item.command.action !== 'status')).toEqual([
    { command: { action: 'set_feature_settings', features: { personality_growth: false } }, authorization: 'Bearer synthetic-test-token' }
  ]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('failed save reports uncertainty and refreshes the actual value without false success', async ({ page }) => {
  const { dialog, backend } = await setup(page);
  backend.fail = true;
  await dialog.getByRole('switch', { name: '对话中使用长期记忆' }).click();
  await expect(dialog.getByRole('alert')).toContainText('设置未确认保存');
  await expect(page.locator('.el-message--success')).toHaveCount(0);
  await dialog.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(dialog.getByRole('switch', { name: '对话中使用长期记忆' })).toHaveAttribute('aria-checked', 'true');
});

test('long-term user mood requires explicit consent and role editing never autosaves a persona', async ({ page }) => {
  const { dialog, requests } = await setup(page);
  await dialog.getByRole('switch', { name: '我的长期心情追踪' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  expect(requests.some(item => item.command.action === 'emotion_consent')).toBe(false);
  await dialog.getByRole('switch', { name: '我的长期心情追踪' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '确定', exact: true }).click();
  await expect(dialog.getByRole('switch', { name: '我的长期心情追踪' })).toHaveAttribute('aria-checked', 'true');
  await dialog.getByRole('button', { name: '编辑角色设定' }).click();
  await expect(page).toHaveURL(/role-config\?agentId=test-agent/);
  expect(requests.every(item => !('system_prompt' in item.command) && !('role' in item.command))).toBe(true);
});


test('Youmi emotion has its own server switch, visible state and confirmed reset', async ({ page }) => {
  const { dialog, requests, state } = await setup(page);
  await dialog.getByRole('switch', { name: 'Youmi 的持续情绪', exact: true }).click();
  await expect(dialog.getByRole('switch', { name: 'Youmi 的持续情绪', exact: true })).toHaveAttribute('aria-checked', 'true');
  expect(state.emotion_tracking.enabled).toBe(0);
  state.youmi_emotion.label = '难过';
  await dialog.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(dialog).toContainText('Youmi 当前情绪：难过');
  await dialog.getByRole('button', { name: '重置 Youmi 情绪', exact: true }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  expect(requests.some(item => item.command.action === 'reset_youmi_emotion')).toBe(false);
  await dialog.getByRole('button', { name: '重置 Youmi 情绪', exact: true }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '确定', exact: true }).click();
  await expect(dialog).toContainText('Youmi 当前情绪：平静');
  expect(requests.filter(item => item.command.action !== 'status').map(item => item.command)).toEqual([
    { action: 'set_feature_settings', features: { companion_emotion: true } }, { action: 'reset_youmi_emotion' }
  ]);
});
