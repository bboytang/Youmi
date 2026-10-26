<template>
  <div class="emotion-page">
    <HeaderBar />
    <main v-loading="busy" class="emotion-main">
      <h1>情感系统</h1>
      <p>同一智能体绑定的设备、App、QQ共享配置和状态。不同智能体分别管理。</p>
      <label>当前智能体
        <el-select :value="agentId" :disabled="busy" placeholder="选择智能体" @change="selectAgent">
          <el-option v-for="agent in agents" :key="agent.id" :value="agent.id" :label="agent.agentName" />
        </el-select>
      </label>
      <p v-if="error" role="alert">{{ error }}</p>
      <template v-if="state && draft">
        <p>正在管理：<strong>{{ agentName }}</strong> · 最近保存 {{ time(state.updated_at) }}</p>
        <el-tabs v-model="tab" @tab-click="loadHistory">
          <el-tab-pane label="总览" name="overview">
            <el-switch v-model="draft.enabled" aria-label="启用情感系统" active-text="启用情感系统" :disabled="busy" />
            <p>开关与参数保存后生效。关闭保留数据；时间恢复后的当前值可能与最后保存值不同。</p>
            <p v-if="state.legacy_enabled">当前沿用原有持续情绪；保存此页配置后切换到新的情感系统。</p>
            <p v-else-if="!state.enabled">系统当前已停用。</p>
            <div class="emotion-overview">
              <article v-for="key in topDimensions" :key="key">
                <strong>{{ state.catalog[key].label }}</strong>
                <el-progress :percentage="Math.round(state.values[key])" />
              </article>
            </div>
          </el-tab-pane>
          <el-tab-pane v-for="(label, group) in state.groups" :key="group" :label="label" :name="group">
            <p v-if="group === 'needs'">感官满足和生理需要是角色模拟参数，不代表真实身体体验。</p>
            <p v-if="group === 'regulation'">调节倾向和表达可以配置；事实真实性、授权和禁止伤害等底线始终生效。</p>
            <YoumiCharacterEditor v-if="group === 'personality' && character" :profile="character" :disabled="busy" hide-dynamics @save="saveCharacter" />
            <article v-for="key in groupKeys(group)" :key="key" class="emotion-dimension">
              <h2>{{ state.catalog[key].label }} <small v-if="state.catalog[key].simulated">角色模拟</small></h2>
              <p>当前值 {{ state.values[key] }} · 最后保存值 {{ state.saved_values[key] }}</p>
              <div class="emotion-controls">
                <el-checkbox v-model="draft.dimensions[key].enabled" :disabled="busy">启用此项</el-checkbox>
                <label>变化模式
                  <el-select v-model="draft.dimensions[key].mode" :disabled="busy">
                    <el-option label="固定基线" value="fixed" /><el-option label="随互动变化" value="dynamic" />
                    <el-option label="临时锁定" value="locked" />
                  </el-select>
                </label>
                <label v-if="draft.dimensions[key].mode === 'locked'">锁定至
                  <el-date-picker v-model="draft.dimensions[key].lock_until" type="datetime" value-format="timestamp" :disabled="busy" />
                </label>
                <label v-for="field in fields" :key="field.key">{{ field.label }}
                  <el-input-number v-model="draft.dimensions[key][field.key]" :aria-label="state.catalog[key].label + field.label"
                    :disabled="busy" :min="field.min" :max="field.max" :step="field.step || 1" />
                </label>
              </div>
              <p>恢复半衰期为0时不自动恢复；表达强度为0时不参与回复表达。固定模式通过修改基线调整。</p>
              <div class="emotion-actions">
                <el-input-number v-model="currentValues[key]" :aria-label="state.catalog[key].label + '当前值'"
                  :min="state.dimensions[key].minimum" :max="state.dimensions[key].maximum"
                  :disabled="busy || dirty || state.dimensions[key].mode === 'fixed'" />
                <el-button :disabled="busy || dirty || state.dimensions[key].mode === 'fixed'" @click="adjust(key)">调整当前值</el-button>
                <el-button :disabled="busy || dirty" @click="reset([key])">恢复此项基线</el-button>
              </div>
            </article>
          </el-tab-pane>
          <el-tab-pane label="变化记录" name="history">
            <article v-for="record in history" :key="record.id" class="emotion-dimension">
              <p>{{ time(record.created_at) }} · {{ kindLabel(record.kind) }}</p>
              <p v-for="(change, key) in (record.changes.values || record.changes)" :key="key">
                <template v-if="state.catalog[key]">{{ state.catalog[key].label }}：{{ change.before }} → {{ change.after }}</template>
              </p>
              <p v-if="record.changes.configuration_changed">配置已修改</p>
              <template v-if="record.kind === 'memory_link'">
                <p>关联互动记录 {{ record.changes.interaction_history_id }}；联动贡献与当轮变化合并后统一限幅，不代表该记忆单独造成全部变化。</p>
                <p v-for="(decision, index) in record.changes.decisions" :key="index">
                  {{ (state.catalog[decision.dimension] || {}).label || '情感维度' }}：{{ memoryReason(decision.reason) }}
                  <el-button type="text" :disabled="busy" @click="$router.push({ path: '/memory-system', query: { agentId, memoryId: decision.memory_id } })">查看记忆来源</el-button>
                </p>
              </template>
            </article>
            <p v-if="!history.length">暂无变化记录</p>
            <el-button :disabled="busy || historyOffset === 0" @click="historyPage(-50)">上一页</el-button>
            <el-button :disabled="busy || history.length < 50" @click="historyPage(50)">下一页</el-button>
          </el-tab-pane>
        </el-tabs>
        <footer class="emotion-actions">
          <span v-if="dirty">有未保存的配置</span>
          <el-button type="primary" :disabled="busy || !dirty" @click="save">保存配置</el-button>
          <el-button :disabled="busy" @click="refresh">刷新</el-button>
          <el-button :disabled="busy || dirty" @click="reset()">全部恢复基线</el-button>
        </footer>
      </template>
      <p v-else-if="!busy && !agents.length">暂无可管理的智能体，请先创建智能体。</p>
    </main>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import YoumiCharacterEditor from '@/components/YoumiCharacterEditor.vue';
import { getServiceUrl } from '@/apis/api';
import RequestService from '@/apis/httpRequest';

export default {
  name: 'YoumiEmotionSystem', components: { HeaderBar, YoumiCharacterEditor },
  data() {
    return { agents: [], agentId: '', state: null, draft: null, original: '', currentValues: {}, character: null,
      busy: false, error: '', tab: 'overview', history: [], historyOffset: 0,
      fields: [
        { key: 'baseline', label: '基线值', min: 0, max: 100 },
        { key: 'minimum', label: '下限', min: 0, max: 100 }, { key: 'maximum', label: '上限', min: 0, max: 100 },
        { key: 'sensitivity', label: '互动敏感度', min: 0, max: 3, step: .1 },
        { key: 'max_change', label: '单轮最大变化', min: 0, max: 100 },
        { key: 'expression', label: '表达强度', min: 0, max: 100 },
        { key: 'half_life_hours', label: '恢复半衰期（小时）', min: 0, max: 8760, step: .5 }
      ] };
  },
  computed: {
    dirty() { return this.draft && JSON.stringify(this.draft) !== this.original; },
    agentName() { const agent = this.agents.find(item => item.id === this.agentId); return agent ? agent.agentName : ''; },
    topDimensions() { return Object.keys(this.state.catalog).filter(key => this.state.dimensions[key].enabled)
      .sort((a, b) => this.state.values[b] - this.state.values[a]).slice(0, 8); }
  },
  async mounted() {
    this.busy = true;
    try {
      const agents = await this.request('/agent/list', null);
      if (!Array.isArray(agents)) throw new Error('智能体列表读取失败');
      this.agents = agents;
      const requested = this.$route.query.agentId;
      this.agentId = agents.some(item => item.id === requested) ? requested : (agents[0] || {}).id || '';
      if (this.agentId) await this.load();
    } catch (error) { this.error = error.message; }
    finally { this.busy = false; }
    window.addEventListener('beforeunload', this.beforeUnload);
  },
  beforeDestroy() { window.removeEventListener('beforeunload', this.beforeUnload); },
  beforeRouteLeave(to, from, next) { this.confirmDiscard().then(ok => next(ok)); },
  methods: {
    request(path, command) {
      return new Promise((resolve, reject) => {
        const url = new URL(getServiceUrl() + path, location.href);
        if (location.protocol !== 'https:' || url.protocol !== 'https:' || url.origin !== location.origin) {
          reject(new Error('请通过同源 HTTPS 打开管理页')); return;
        }
        const failed = () => reject(new Error('操作结果未确认，请刷新核对；如配置已变化，请重新载入。'));
        const request = RequestService.sendRequest().sensitive().url(url.href).method(command ? 'POST' : 'GET');
        if (command) request.data(command);
        request.success(res => {
          if (res.data && res.data.code === 0 && res.data.data) resolve(res.data.data); else failed();
        }).fail(failed).networkFail(failed).send();
      });
    },
    call(command) { return this.request('/agent/' + encodeURIComponent(this.agentId) + '/affect-system', command); },
    accept(state) {
      if (!state.dimensions || !state.values || !state.catalog) throw new Error('服务器状态不完整');
      this.state = state;
      this.draft = JSON.parse(JSON.stringify({ enabled: state.enabled, dimensions: state.dimensions }));
      Object.values(this.draft.dimensions).forEach(item => { item.lock_until *= 1000; });
      this.original = JSON.stringify(this.draft);
      this.currentValues = { ...state.values };
    },
    async load() {
      this.state = null; this.draft = null; this.character = null; this.history = []; this.historyOffset = 0;
      this.accept(await this.call({ action: 'status' }));
      const legacy = await this.request('/agent/' + encodeURIComponent(this.agentId) + '/youmi-settings', { action: 'status' });
      this.character = legacy.character;
    },
    async confirmDiscard() {
      if (!this.dirty) return true;
      try { await this.$confirm('当前配置尚未保存，确定放弃草稿？', '未保存的修改'); return true; } catch (_) { return false; }
    },
    async selectAgent(id) {
      if (!await this.confirmDiscard()) return;
      this.busy = true; this.error = ''; this.agentId = id;
      try { await this.load(); } catch (error) { this.error = error.message; }
      finally { this.busy = false; }
    },
    async refresh() {
      if (!await this.confirmDiscard()) return;
      await this.selectAgent(this.agentId);
    },
    async perform(command) {
      if (this.busy) return;
      this.busy = true; this.error = '';
      try { this.accept(await this.call({ ...command, version: this.state.version })); this.$message.success('已保存'); }
      catch (error) { this.error = error.message; }
      finally { this.busy = false; }
    },
    save() {
      const config = JSON.parse(JSON.stringify(this.draft));
      Object.values(config.dimensions).forEach(item => { item.lock_until = Math.floor(Number(item.lock_until || 0) / 1000); });
      return this.perform({ action: 'configure', ...config });
    },
    adjust(key) { return this.perform({ action: 'adjust', values: { [key]: this.currentValues[key] } }); },
    async reset(keys) {
      try { await this.$confirm(keys ? '将此项恢复至保存的基线？' : '将全部维度恢复至各自保存的基线？', '恢复基线'); }
      catch (_) { return; }
      await this.perform({ action: 'reset', ...(keys ? { dimensions: keys } : {}) });
    },
    async saveCharacter(command) {
      if (this.dirty) { this.$message.warning('请先保存或放弃情感配置草稿'); return; }
      this.busy = true; this.error = '';
      try {
        const values = { ...command.values, warmth: this.state.dimensions.warmth.baseline / 100,
          attachment: this.state.dimensions.closeness.baseline / 100, temper: this.state.dimensions.temper.baseline / 100 };
        const state = await this.request('/agent/' + encodeURIComponent(this.agentId) + '/youmi-settings', { ...command, values });
        this.character = state.character; this.accept(await this.call({ action: 'status' }));
        this.$message.success('角色基线已保存');
      } catch (error) { this.error = error.message; }
      finally { this.busy = false; }
    },
    groupKeys(group) { return Object.keys(this.state.catalog).filter(key => this.state.catalog[key].group === group); },
    time(value) { return value ? new Date(value * 1000).toLocaleString() : '尚未保存'; },
    kindLabel(kind) { return ({ configure: '配置修改', adjust: '手动调整', reset: '恢复基线', interaction: '互动变化', memory_link: '记忆联动记录' })[kind] || kind; },
    memoryReason(reason) {
      return ({ combined_before_limits: '已参与统一计算', cooldown: '同版本仍在冷却期', evidence_changed: '来源版本已变化',
        disabled_or_configuration_changed: '联动关闭或配置已变化', not_selected_or_evidence_changed: '未选入本轮或证据已变化',
        dimension_disabled_fixed_or_locked: '维度关闭、固定或锁定', zero_weight_or_delta: '权重或变化量为零' })[reason] || '本次未采用';
    },
    async loadHistory() {
      if (this.tab !== 'history' || this.busy) return;
      this.busy = true; this.error = '';
      try { const result = await this.call({ action: 'history', offset: this.historyOffset }); this.history = result.records; }
      catch (error) { this.error = error.message; }
      finally { this.busy = false; }
    },
    historyPage(delta) { this.historyOffset = Math.max(0, this.historyOffset + delta); return this.loadHistory(); },
    beforeUnload(event) { if (this.dirty) { event.preventDefault(); event.returnValue = ''; } }
  }
};
</script>

<style scoped>
.emotion-main { max-width: 1100px; margin: auto; padding: 24px; }
.emotion-main label { display: flex; flex-direction: column; gap: 8px; }
.emotion-main p { overflow-wrap: anywhere; line-height: 1.6; }
.emotion-dimension { padding: 20px 0; border-bottom: 1px solid #ddd; }
.emotion-dimension h2 { font-size: 18px; }
.emotion-controls, .emotion-overview { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; }
.emotion-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 20px; }
.emotion-actions .el-button { margin-left: 0; }
@media (max-width: 600px) { .emotion-main { padding: 14px; } .emotion-controls { grid-template-columns: 1fr; } }
</style>
