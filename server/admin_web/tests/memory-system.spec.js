import { test, expect } from '@playwright/test';
import { mockBackend } from './fixtures.js';

const clone = value => JSON.parse(JSON.stringify(value));
const dimensions = Object.fromEntries(['recall', 'impression', 'expression', 'creativity'].map(key => [key, {
  minimum: ['recall', 'impression'].includes(key) ? 1 : 0,
  baseline: key === 'recall' ? 7 : key === 'impression' ? 3 : .5,
  maximum: key === 'recall' ? 20 : key === 'impression' ? 10 : 1, mode: 'fixed', sensitivity: .3
}]));

async function setup(page, query = 'agentId=first') {
  const commands = [], states = {};
  const backend = { fail: 0, unavailable: false };
  function state(agent) {
    return states[agent] ||= {
      revision: 1, features: { memory_recall: true },
      reflection: { enabled: false, version: 0, interval_seconds: 86400, last_maintenance_at: null },
      emotion_linkage: { enabled: false, version: 0, memory_to_emotion: true, emotion_to_memory: true,
        stimulus_weight: .25, ranking_limit: .1, cooldown_seconds: 86400 },
      cognition: { enabled: false, version: 0, dimensions: clone(dimensions), energy: { recall: 0, impression: 0, expression: 0, creativity: 0 } },
      capabilities: { engine: 'legacy', automatic_cognition: false },
      memories: [
        { id: agent + '-blue', content: agent + ' 我喜欢蓝色 <script>unsafe</script>', confirmed: true, state: 'active', content_version: 1, evidence_version: 2 },
        { id: agent + '-green', content: agent + ' 我喜欢绿色', confirmed: true, state: 'active', content_version: 1, evidence_version: 2 }
      ]
    };
  }
  await page.route('https://youmi-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  await page.route('**/xiaozhi/agent/list', route => route.fulfill({ json: { code: 0, data: [
    { id: 'first', agentName: '智能体一' }, { id: 'second', agentName: '智能体二' }
  ] } }));
  await page.route('**/xiaozhi/agent/*/memory-system', async route => {
    const agent = route.request().url().split('/agent/')[1].split('/')[0];
    const command = route.request().postDataJSON(), current = state(agent);
    commands.push({ agent, command, authorization: route.request().headers()['authorization'] });
    let data;
    const action = command.action;
    if (backend.fail && (command.operation || ['edit', 'delete', 'merge'].includes(action))) {
      await route.fulfill({ json: { code: backend.fail, msg: 'synthetic failure' } }); return;
    }
    if (action === 'status') data = current;
    if (action === 'list') data = { memories: current.memories, total: current.memories.length, offset: command.offset || 0, page_size: 50, revision: current.revision };
    if (action === 'detail') {
      const memory = current.memories.find(row => row.id === command.id);
      if (!memory || backend.unavailable) { await route.fulfill({ json: { code: 400, msg: 'unavailable' } }); return; }
      const sources = memory.content_version === 1 ? [{ excerpt: memory.content, session_id: 'synthetic-session', created_at: 1700000000 }] : [];
      data = { memory, sources, source_total: sources.length, offset: command.offset || 0, page_size: 50, replacements: [], revision: current.revision };
    }
    if (action === 'artifacts') data = { artifacts: current.memories.length ? [{
      id: 'note', title: '蓝色', kind: 'note', body: current.memories[0].content, version: 1,
      evidence: [{ id: current.memories[0].id, content_version: 1, evidence_version: 2 }], preview_only: false
    }] : [] };
    if (['reflection', 'cognition', 'emotion_linkage'].includes(action)) {
      const operation = command.operation;
      if (operation.version !== current[action].version) { await route.fulfill({ json: { code: 409 } }); return; }
      Object.assign(current[action], operation); delete current[action].action;
      current[action].version++; current.revision++; data = current[action];
    }
    if (action === 'edit') {
      const row = current.memories.find(item => item.id === command.id);
      row.content = command.content; row.confirmed = command.confirmed; row.content_version++; current.revision++;
      data = { saved: true };
    }
    if (action === 'delete') { current.memories = current.memories.filter(row => row.id !== command.id); current.revision++; data = { saved: true }; }
    if (action === 'merge') {
      const row = current.memories.find(item => item.id === command.retired_id);
      row.state = 'superseded'; row.confirmed = false; current.revision++; data = { duplicate: false };
    }
    await route.fulfill({ json: { code: 0, data } });
  });
  await page.goto('https://youmi-fixture.invalid/#/memory-system?' + query);
  await expect(page.getByRole('heading', { name: '记忆系统', exact: true })).toBeVisible();
  if (!query.includes('foreign')) await expect(page.getByText('正在管理：')).toContainText('智能体一');
  return { backend, states, commands };
}

test('independent memory page saves only the selected agent and sends the existing login', async ({ page }) => {
  const { states, commands } = await setup(page);
  await page.getByRole('tab', { name: '反思设置', exact: true }).click();
  await page.getByRole('switch', { name: '启用反思', exact: true }).click();
  await page.getByRole('button', { name: '保存反思设置' }).click();
  await expect(page.getByText('有未保存的修改', { exact: true })).toHaveCount(0);
  expect(states.first.reflection.enabled).toBe(true);
  await page.locator('.el-select').first().click();
  await page.getByText('智能体二', { exact: true }).last().click();
  await expect(page.getByText('正在管理：')).toContainText('智能体二');
  expect(states.second.reflection.enabled).toBe(false);
  expect(commands.filter(item => item.command.operation).map(item => item.agent)).toEqual(['first']);
  expect(commands.every(item => item.authorization === 'Bearer synthetic-test-token')).toBe(true);
});

for (const code of [409, 500]) test('failed save retains draft and blocks stale retry: ' + code, async ({ page }) => {
  const { backend } = await setup(page);
  await page.getByRole('tab', { name: '情感联动', exact: true }).click();
  await page.getByRole('switch', { name: '启用记忆情感联动' }).click();
  backend.fail = code;
  await page.getByRole('button', { name: '保存联动设置' }).click();
  await expect(page.getByRole('alert').filter({ hasText: code === 409 ? '已变化' : '操作结果未确认' })).toBeVisible();
  await expect(page.getByText('有未保存的修改', { exact: true })).toBeVisible();
  await expect(page.getByRole('switch', { name: '启用记忆情感联动' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByRole('button', { name: '保存联动设置' })).toBeDisabled();
  await expect(page.locator('.el-message--success')).toHaveCount(0);
  backend.fail = 0;
  await page.getByRole('button', { name: '刷新并核对服务器状态' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '确定', exact: true }).click();
  await expect(page.getByText('有未保存的修改', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('switch', { name: '启用记忆情感联动' })).toHaveAttribute('aria-checked', 'false');
});

test('switching agents or leaving the page protects unsaved drafts', async ({ page }) => {
  await setup(page);
  await page.getByRole('tab', { name: '反思设置', exact: true }).click();
  await page.getByRole('switch', { name: '启用反思', exact: true }).click();
  await page.locator('.el-select').first().click();
  await page.getByText('智能体二', { exact: true }).last().click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByText('正在管理：')).toContainText('智能体一');
  await expect(page.getByText('有未保存的修改', { exact: true })).toBeVisible();
  await page.evaluate(() => { location.hash = '#/affect-system?agentId=first'; });
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByRole('heading', { name: '记忆系统', exact: true })).toBeVisible();
});

test('fact editing and confirmed deletion use current source versions and escaped text', async ({ page }) => {
  const { commands } = await setup(page);
  await page.getByRole('button', { name: '查看来源与编辑' }).first().click();
  await expect(page.getByRole('heading', { name: '来源摘录（1）' })).toBeVisible();
  await expect(page.locator('.memory-main script')).toHaveCount(0);
  await page.getByRole('textbox', { name: '记忆内容' }).fill('我现在不喜欢蓝色');
  await page.getByRole('button', { name: '保存记忆', exact: true }).click();
  await expect(page.getByRole('heading', { name: '来源摘录（0）' })).toBeVisible();
  expect(commands.find(item => item.command.action === 'edit').command.expected).toEqual({ revision: 1, content_version: 1, evidence_version: 2 });
  await page.getByRole('button', { name: '删除此条记忆' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  expect(commands.some(item => item.command.action === 'delete')).toBe(false);
  await page.getByRole('button', { name: '删除此条记忆' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '删除', exact: true }).click();
  await expect(page.getByText('共 1 条；')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '记忆内容' })).toHaveCount(0);
});

test('source links re-read canonical evidence and do not restore unavailable content', async ({ page }) => {
  const { backend } = await setup(page);
  await page.getByRole('tab', { name: '画像与笔记', exact: true }).click();
  backend.unavailable = true;
  await page.getByRole('button', { name: '查看证据版本 1' }).click();
  await expect(page.getByRole('alert').filter({ hasText: '来源不可用' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '记忆内容' })).toHaveCount(0);
  await expect(page.getByRole('heading', { name: '来源摘录（1）' })).toHaveCount(0);
});

test('manual merge requires confirmation and carries both versions', async ({ page }) => {
  const { commands } = await setup(page);
  await page.getByText('选择合并', { exact: true }).first().click();
  await page.getByText('选择合并', { exact: true }).last().click();
  await page.getByRole('button', { name: '合并所选记忆' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '取消', exact: true }).click();
  expect(commands.some(item => item.command.action === 'merge')).toBe(false);
  await page.getByRole('button', { name: '合并所选记忆' }).click();
  await page.locator('.el-message-box').getByRole('button', { name: '确定', exact: true }).click();
  await expect(page.getByText('已合并退役', { exact: false }).first()).toBeVisible();
  const command = commands.find(item => item.command.action === 'merge').command;
  expect(command.versions).toEqual({ survivor: [1, 2], retired: [1, 2] });
  expect(command.operation_id).toMatch(/^[a-f0-9-]{36}$/);
});

test('mobile cognition controls fit and all four dimensions remain editable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.getByRole('tab', { name: '认知设置', exact: true }).click();
  await expect(page.getByText('自动能量变化及回复策略尚未接入', { exact: false })).toBeVisible();
  for (const label of ['召回', '印象', '表达', '创造']) await expect(page.getByLabel(label + '基线', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('foreign agent query is rejected without silently opening another agents data', async ({ page }) => {
  const { commands } = await setup(page, 'agentId=foreign');
  await expect(page.getByRole('alert').filter({ hasText: '不可管理' })).toBeVisible();
  expect(commands).toHaveLength(0);
  await expect(page.getByText('正在管理：')).toHaveCount(0);
});

test('deep link loads the selected agent current source', async ({ page }) => {
  await setup(page, 'agentId=first&memoryId=first-blue');
  await expect(page.getByRole('textbox', { name: '记忆内容' })).toHaveValue('first 我喜欢蓝色 <script>unsafe</script>');
});

test('navigation entry preserves the selected agent when already on the memory page', async ({ page }) => {
  await setup(page);
  // A compact navigation uses the same entry as desktop.
  const toggle = page.getByRole('button', { name: '导航菜单', exact: true });
  if (await toggle.isVisible()) await toggle.click();
  await page.getByRole('navigation').getByText('记忆系统', { exact: true }).click();
  await expect(page.getByText('正在管理：')).toContainText('智能体一');
  await expect(page.locator('.memory-main [role="alert"]')).toHaveCount(0);
});

test('an in-flight source read cannot apply to a different selected agent', async ({ page }) => {
  await setup(page);
  let release;
  await page.route('**/xiaozhi/agent/first/memory-system', async route => {
    if (route.request().postDataJSON().action !== 'detail') { await route.fallback(); return; }
    await new Promise(resolve => { release = resolve; });
    await route.fulfill({ json: { code: 0, data: { memory: { id: 'first-blue', content: 'first 当前来源', confirmed: true,
      state: 'active', content_version: 1, evidence_version: 2 }, sources: [], source_total: 0,
      offset: 0, page_size: 50, revision: 1, replacements: [] } } });
  });
  await page.getByRole('button', { name: '查看来源与编辑' }).first().click();
  await expect.poll(() => typeof release).toBe('function');
  await page.evaluate(() => { location.hash = '#/memory-system?agentId=second'; });
  await expect(page).toHaveURL(/agentId=first/);
  release();
  await expect(page.getByRole('textbox', { name: '记忆内容' })).toHaveValue('first 当前来源');
  await page.locator('.el-select').first().click();
  await page.getByText('智能体二', { exact: true }).last().click();
  await expect(page.getByText('正在管理：')).toContainText('智能体二');
  await expect(page.getByRole('textbox', { name: '记忆内容' })).toHaveCount(0);
  await expect(page.getByText('first 当前来源', { exact: true })).toHaveCount(0);
});
