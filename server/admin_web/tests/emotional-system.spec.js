import { test, expect } from '@playwright/test';
import { mockBackend } from './fixtures.js';

async function setup(page) {
  await mockBackend(page);
  const states = {};
  const commands = [];
  await page.route('https://mia-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await page.route('**/xiaozhi/agent/list', route => route.fulfill({ json: { code: 0, data: [
    { id: 'first', agentName: '智能体一' }, { id: 'second', agentName: '智能体二' }
  ] } }));
  await page.route('**/xiaozhi/agent/*/affect-system', async route => {
    const agent = route.request().url().split('/agent/')[1].split('/')[0];
    const command = route.request().postDataJSON();
    commands.push({ agent, command });
    const state = states[agent] ||= {
      enabled: false, version: 0, updated_at: null, values: { joy: 10 }, saved_values: { joy: 10 },
      catalog: { joy: { label: '快乐/喜悦', group: 'basic' } }, groups: { basic: '基础情绪' },
      dimensions: { joy: { enabled: true, baseline: 10, minimum: 0, maximum: 100, mode: 'dynamic',
        sensitivity: 1, max_change: 15, expression: 70, half_life_hours: 3, lock_until: 0 } }
    };
    if (command.action === 'configure') {
      state.enabled = command.enabled; state.dimensions = command.dimensions; state.version++;
    }
    if (command.action === 'adjust') {
      Object.assign(state.values, command.values); Object.assign(state.saved_values, command.values); state.version++;
    }
    await route.fulfill({ json: { code: 0, data: command.action === 'history' ? { records: [] } : state } });
  });
  await page.goto('https://mia-fixture.invalid/#/affect-system?agentId=first');
  await expect(page.getByRole('heading', { name: '情感系统', exact: true })).toBeVisible();
  await expect(page.getByText('正在管理：')).toContainText('智能体一');
  return { states, commands };
}

test('independent page saves only the selected agent and switches without sharing state', async ({ page }) => {
  const { states, commands } = await setup(page);
  await page.getByRole('switch', { name: '启用情感系统' }).click();
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByText('有未保存的配置')).toHaveCount(0);
  expect(states.first.enabled).toBe(true);
  await page.locator('.el-select').first().click();
  await page.getByText('智能体二', { exact: true }).last().click();
  await expect(page.getByText('正在管理：')).toContainText('智能体二');
  expect(states.second.enabled).toBe(false);
  expect(commands.filter(item => item.command.action === 'configure').map(item => item.agent)).toEqual(['first']);
});

test('failed configuration save retains the draft and never reports success', async ({ page }) => {
  await setup(page);
  await page.route('**/xiaozhi/agent/first/affect-system', async route => {
    await route.fulfill({ json: { code: 500, msg: 'synthetic failure' } });
  });
  await page.getByRole('switch', { name: '启用情感系统' }).click();
  await page.getByRole('button', { name: '保存配置', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('操作结果未确认');
  await expect(page.getByText('有未保存的配置')).toBeVisible();
  await expect(page.locator('.el-message--success')).toHaveCount(0);
});

test('mobile controls fit and reset requires confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const { commands } = await setup(page);
  await page.getByRole('tab', { name: '基础情绪' }).click();
  await expect(page.getByLabel('快乐/喜悦基线值', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole('button', { name: '恢复此项基线' }).click();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  expect(commands.some(item => item.command.action === 'reset')).toBe(false);
});
