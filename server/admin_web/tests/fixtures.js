// Synthetic records only. No tests send traffic or credentials to production.
export const longText = '长文本配置与设备信息'.repeat(35);
export const agent = {
  id: 'test-agent', agentName: longText.slice(0, 64), systemPrompt: longText, agentId: 'test-agent',
  llmModelName: longText.slice(0, 64), ttsModelName: longText.slice(0, 64), ttsVoiceName: longText.slice(0, 64),
  deviceCount: 1, memModelId: 'Memory_local_short', functions: [], contextProvider: [], tags: [],
};
export const device = {
  id: 'test-device', alias: longText.slice(0, 64), board: longText, macAddress: 'AA:BB:CC:DD:EE:FF',
  appVersion: '0.9.7', autoUpdate: 0, createDateTimestamp: 1791936000000,
};
const model = { id: 'test-model', modelName: longText.slice(0, 64), modelCode: 'test', configJson: { type: 'test' }, isEnabled: 1, isDefault: 0, sort: 1 };
const param = { id: 'test-param', paramCode: 'server.long_parameter', paramValue: longText, valueType: 'string', remark: longText };
const features = Object.fromEntries(['voiceprintRecognition','voiceClone','knowledgeBase','mcpAccessPoint','vad','asr','addressBook'].map(key => [key, { enabled: true, name: key, description: longText }]));
export const pubConfig = { version: 'test', allowUserRegister: true, systemWebMenu: { features } };

export async function mockBackend(page, superAdmin = true) {
  await page.addInitScript(({ config, admin }) => {
    if (/^#\/(login|register|retrieve-password)(\?|$)/.test(location.hash)) localStorage.removeItem('token');
    else localStorage.setItem('token', JSON.stringify({ token: 'synthetic-test-token' }));
    localStorage.setItem('userLanguage', 'zh_CN');
    localStorage.setItem('userInfo', JSON.stringify({ username: '响应式测试', superAdmin: admin }));
    localStorage.setItem('pubConfig', JSON.stringify(config));
  }, { config: pubConfig, admin: superAdmin });
  await page.route('**/xiaozhi/**', async route => {
    const path = new URL(route.request().url()).pathname.replace(/^\/xiaozhi/, '');
    let data = { list: [], total: 0 };
    if (path.endsWith('/pub-config')) data = pubConfig;
    else if (path === '/user/info') data = { username: '响应式测试', superAdmin };
    else if (path === '/admin/dict/data/type/FIRMWARE_TYPE') data = [];
    else if (path === '/agent/list' || path.includes('/agent/search')) data = [agent];
    else if (path === '/agent/template') data = [agent];
    else if (path === '/agent/template/page') data = { list: [agent], total: 1 };
    else if (path === '/agent/template/test-template') data = agent;
    else if (path === '/agent/test-agent') data = agent;
    else if (path.includes('/tags')) data = [];
    else if (path.includes('/voice-print/list/')) data = [{ id: 'test-voiceprint', sourceName: longText.slice(0, 64), introduce: longText, createDate: '2026-10-14' }];
    else if (path.endsWith('/sessions')) data = { list: [{ sessionId: 'test-session', title: '测试会话', createdAt: '2026-10-14T00:00:00Z', chatCount: 1 }], total: 1 };
    else if (path.includes('/chat-history/test-session')) data = [{ id: 'test-message', chatType: 2, content: longText, createdAt: '2026-10-14T00:00:00Z' }];
    else if (path.includes('/snapshots/current')) data = { currentVersionNo: 1 };
    else if (path.includes('/device/bind/')) data = route.request().method() === 'POST' ? '{}' : [device];
    else if (path === '/device/youmi-auth/code') data = { device_id: device.id, authorization_code: 'b'.repeat(22), expires_in: 600 };
    else if (path === '/device/youmi-auth/revoke') data = {};
    else if (path.includes('/device/address-book/')) data = { alias: longText.slice(0, 64), permissions: [] };
    else if (path.includes('status')) data = '{}';
    else if (path === '/models/names' || path.includes('/llm/names') || path.endsWith('/plugin/names')) data = [];
    else if (path === '/models/provider') data = { list: [{ id: 'test-provider', name: longText.slice(0, 64), providerCode: 'test', modelType: 'LLM', fields: '[{"key":"test","label":"测试字段","type":"string"}]', sort: 1 }], total: 1 };
    else if (path.startsWith('/models/') && path.endsWith('/voices')) data = { list: [], total: 0 };
    else if (path.endsWith('/provideTypes')) data = [];
    else if (path === '/models/list') data = { list: [model], total: 1 };
    else if (path === '/admin/params/page') data = { list: [param], total: 1 };
    else if (path === '/admin/server/server-list') data = ['http://example.invalid/' + 'long-path/'.repeat(20)];
    else if (path === '/admin/users') data = { list: [{ id: 'test-user', username: longText.slice(0, 32).slice(0, 64), mobile: '13800000000', status: 1, selected: false }], total: 1 };
    else if (path === '/admin/dict/type/page') data = { list: [{ id: 'test-dict', dictName: longText.slice(0, 64), dictType: 'test', name: longText.slice(0, 64) }], total: 1 };
    else if (path === '/admin/dict/data/page') data = { list: [{ id: 'test-dict-data', dictLabel: longText.slice(0, 64), dictValue: longText, remark: longText }], total: 1 };
    else if (path === '/otaMag') data = { list: [{ id: 'test-firmware', firmwareName: longText.slice(0, 64), type: 'esp32s3', version: '1.0', size: 1024, remark: longText }], total: 1 };
    else if (path === '/voiceResource' || path === '/voiceClone') data = { list: [{ id: 'test-voice', voiceId: 'test-voice', name: longText.slice(0, 64), modelName: longText.slice(0, 64), userName: longText, languages: '中文', trainStatus: 0, hasVoice: false }], total: 1 };
    else if (path === '/voiceResource/ttsPlatforms') data = [];
    else if (path === '/correct-word/file/list') data = { list: [{ id: 'test-words', fileName: longText.slice(0, 64), content: '[{"source":"测试","target":"替换"}]', wordCount: 1 }], total: 1 };
    else if (path === '/datasets/rag-models') data = [];
    else if (path === '/datasets') data = { list: [{ datasetId: 'test-kb', name: longText.slice(0, 64), description: longText, status: 1 }], total: 1 };
    else if (path.endsWith('/documents')) data = { list: [{ id: 'test-document', name: longText.slice(0, 64) + '.txt', parseStatusCode: 0, size: 1024 }], total: 1 };
    else if (path.includes('/agent/') && (path.includes('template') || path.includes('voice'))) data = [];
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ code: 0, data }) });
  });
}
