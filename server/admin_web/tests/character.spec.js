import { test, expect } from '@playwright/test';
import { mockBackend } from './fixtures.js';

async function setup(page) {
  await page.route('https://youmi-fixture.invalid/**', async route => {
    const url = new URL(route.request().url());
    const response = await route.fetch({ url: 'http://127.0.0.1:4173' + url.pathname + url.search });
    await route.fulfill({ response });
  });
  await mockBackend(page, false);
  const preset = { name: '悠米', age: 24, address: '哥哥', relationship: '暧昧期，没有亲属关系',
    background: '父母经营花店，自己是插画师。', warmth: 0.85, attachment: 0.75, temper: 0.7, swearing: true, recovery_hours: 3 };
  const state = { features: { memory_recall: true, personality_growth: true, interaction_events: true, companion_emotion: true },
    paused: false, emotion_tracking: { enabled: 0, version: 0 }, counts: { memories: 47, traits: 0, events: 0 },
    character: { enabled: false, version: 0, values: null, preset } };
  const backend = { fail: false }, requests = [];
  await page.route('**/agent/test-agent/youmi-settings', async route => {
    const command = route.request().postDataJSON(); requests.push(command);
    if (command.action === 'set_feature_settings') Object.assign(state.features, command.features);
    if (command.action === 'set_character') {
      if (backend.fail || command.version !== state.character.version) {
        await route.fulfill({ json: { code: 500 } }); return;
      }
      state.character = { ...state.character, enabled: command.enabled, version: command.version + 1, values: command.values };
    }
    await route.fulfill({ json: { code: 0, data: state } });
  });
  await page.goto('https://youmi-fixture.invalid/#/home');
  await page.getByRole('button', { name: 'Youmi 功能管理', exact: true }).first().click();
  const dialog = page.locator('.el-dialog').filter({ hasText: 'Youmi 功能管理' });
  return { dialog, state, backend, requests };
}

test('baseline is an explicit owner draft, edits save to backend and survive reopening', async ({ page }) => {
  const { dialog, state, requests } = await setup(page);
  await expect(dialog).toContainText('尚未配置');
  await dialog.getByRole('button', { name: '载入悠米 V1 草稿' }).click();
  await expect(dialog.getByRole('textbox', { name: '性格名字', exact: true })).toHaveValue('悠米');
  expect(requests.filter(r => r.action === 'set_character')).toHaveLength(0);
  await dialog.getByRole('textbox', { name: '亲昵称呼', exact: true }).fill('哥哥呀');
  const persona = '# 人设\n你是图书管理员，使用简洁文言回答。\n' + '示例：来客问好，答安好。\n'.repeat(500);
  const editor = dialog.getByRole('textbox', { name: '人设设定', exact: true });
  await expect(editor).not.toHaveAttribute('maxlength');
  await editor.fill(persona);
  await dialog.getByRole('switch', { name: '人格成长', exact: true }).click();
  await expect(dialog.getByRole('textbox', { name: '亲昵称呼', exact: true })).toHaveValue('哥哥呀');
  await dialog.getByRole('button', { name: '保存性格与情绪基线' }).click();
  await expect(page.locator('.el-message--success').filter({ hasText: '性格与情绪基线已保存' })).toBeVisible();
  expect(state.character.values.address).toBe('哥哥呀');
  expect(state.character.values.background).toBe(persona);
  expect(state.character.enabled).toBe(true);
  expect(state.emotion_tracking.enabled).toBe(0);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByRole('button', { name: 'Youmi 功能管理', exact: true }).first().click();
  await expect(dialog.getByRole('textbox', { name: '亲昵称呼', exact: true })).toHaveValue('哥哥呀');
  await expect(dialog.getByRole('textbox', { name: '人设设定', exact: true })).toHaveValue(persona);
  await dialog.locator('.el-checkbox').filter({ hasText: '启用性格与情绪基线' }).click();
  await dialog.getByRole('button', { name: '保存性格与情绪基线' }).click();
  await expect(dialog.getByRole('checkbox', { name: '启用性格与情绪基线' })).not.toBeChecked();
  expect(state.character.values.name).toBe('悠米');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test('failed character save reports uncertainty instead of retaining a false local persona', async ({ page }) => {
  const { dialog, backend, state } = await setup(page);
  await dialog.getByRole('button', { name: '载入悠米 V1 草稿' }).click();
  backend.fail = true;
  await dialog.getByRole('button', { name: '保存性格与情绪基线' }).click();
  await expect(dialog.getByRole('alert')).toContainText('设置未确认保存');
  await expect(page.locator('.el-message--success')).toHaveCount(0);
  expect(state.character.values).toBeNull();
  await dialog.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(dialog).toContainText('尚未配置');
});
