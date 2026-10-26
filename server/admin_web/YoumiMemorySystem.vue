<template>
  <div>
    <HeaderBar />
    <main class="memory-main" v-loading="busy">
      <h1>记忆系统</h1>
      <p>同一智能体绑定的设备、App、QQ共用记忆与配置，不同智能体分别管理。</p>
      <label>当前智能体
        <el-select :value="agentId" :disabled="busy" placeholder="选择智能体" @change="selectAgent">
          <el-option v-for="agent in agents" :key="agent.id" :label="agent.agentName" :value="agent.id" />
        </el-select>
      </label>
      <p v-if="error" role="alert">{{ error }}</p>
      <p v-if="!busy && !agents.length">暂无可管理的智能体。</p>
      <template v-if="state">
        <p>正在管理：<strong>{{ agentName }}</strong></p>
        <el-button :disabled="busy" @click="refresh">刷新并核对服务器状态</el-button>
        <p v-if="dirty">有未保存的修改</p>
        <el-tabs v-model="tab">
          <el-tab-pane label="记忆与来源" name="memories">
            <p v-if="listing">共 {{ listing.total }} 条；已确认不代表永久固定，待确认和已合并记忆保留原状态。</p>
            <article v-for="memory in memories" :key="memory.id">
              <p>{{ memory.content }}</p>
              <p>{{ memoryState(memory) }} · 内容版本 {{ memory.content_version }} · 证据版本 {{ memory.evidence_version }}</p>
              <el-button :disabled="busy" @click="openDetail(memory.id)">查看来源与编辑</el-button>
              <el-checkbox v-if="memory.confirmed && memory.state === 'active'" :value="selected.includes(memory.id)"
                :disabled="busy" @change="choose(memory.id, $event)">选择合并</el-checkbox>
            </article>
            <div v-if="listing" class="actions">
              <el-button :disabled="busy || listing.offset === 0" @click="loadPage(listing.offset - 50)">上一页记忆</el-button>
              <el-button :disabled="busy || listing.offset + listing.page_size >= listing.total" @click="loadPage(listing.offset + 50)">下一页记忆</el-button>
              <el-button :disabled="busy || selected.length !== 2 || dirty" @click="mergeSelected">合并所选记忆</el-button>
            </div>
            <section v-if="detail && edit" aria-label="记忆详情">
              <h2>记忆详情</h2>
              <p>{{ memoryState(detail.memory) }} · {{ detail.memory.id }}</p>
              <p>修改原句会撤回旧来源和相关整理记录；角色人设在原人设页独立编辑。</p>
              <el-input v-model="edit.content" type="textarea" :rows="4" label="记忆内容" :disabled="busy || retired" />
              <el-checkbox v-model="edit.confirmed" :disabled="busy || retired">已确认</el-checkbox>
              <div class="actions">
                <el-button :disabled="busy || retired || !factDirty || factBlocked" @click="saveFact">保存记忆</el-button>
                <el-button type="danger" plain :disabled="busy || factDirty || factBlocked" @click="deleteFact">删除此条记忆</el-button>
              </div>
              <h3>来源摘录（{{ detail.source_total }}）</h3>
              <article v-for="(source, index) in detail.sources" :key="index">
                <p>{{ source.excerpt }}</p><small>{{ time(source.created_at) }} · 会话 {{ source.session_id }}</small>
              </article>
              <p v-if="!detail.sources.length">当前没有可用来源，不从历史记录恢复正文。</p>
              <div class="actions">
                <el-button :disabled="busy || detail.offset === 0" @click="openDetail(detail.memory.id, detail.offset - 50)">上一页来源</el-button>
                <el-button :disabled="busy || detail.offset + detail.page_size >= detail.source_total" @click="openDetail(detail.memory.id, detail.offset + 50)">下一页来源</el-button>
              </div>
              <h3>合并关系（最多显示50条）</h3>
              <p v-for="link in detail.replacements" :key="link.retired_id">
                {{ link.retired_id }} →
                <el-button type="text" :disabled="busy" @click="openDetail(link.survivor_id)">查看保留记忆</el-button>
              </p>
            </section>
          </el-tab-pane>
          <el-tab-pane label="画像与笔记" name="artifacts">
            <p>整理内容来自当前有效的完整事实原句；合并、退役候选只供核对，不会自动执行。</p>
            <p v-if="!state.features.memory_recall">回复时使用记忆已关闭，整理视图暂时隐藏，原数据保留。</p>
            <section v-for="(label, kind) in kinds" :key="kind">
              <h2>{{ label }}</h2>
              <article v-for="item in artifacts.filter(row => row.kind === kind)" :key="item.id">
                <h3>{{ item.title }}</h3><p>{{ item.body }}</p>
                <p v-if="item.preview_only">仅候选，尚未执行</p>
                <el-button v-for="proof in item.evidence" :key="proof.id" :disabled="busy" @click="openDetail(proof.id)">查看证据版本 {{ proof.content_version }}</el-button>
                <el-button :disabled="busy || dirty" @click="removeArtifact(item)">删除整理条目</el-button>
              </article>
            </section>
          </el-tab-pane>
          <el-tab-pane label="反思设置" name="reflection">
            <el-switch v-model="draft.reflection.enabled" aria-label="启用反思" active-text="启用反思" :disabled="busy" />
            <p>成功对话触发提取，到期后在下一次成功对话整理；没有新对话时不会额外请求模型。</p>
            <label>整理间隔（秒）<el-input-number v-model="draft.reflection.interval_seconds" aria-label="整理间隔（秒）" :min="60" :max="604800" :disabled="busy" /></label>
            <p>最近成功整理：{{ time(state.reflection.last_maintenance_at) }}</p>
            <el-button :disabled="busy || !changed('reflection') || blocked.reflection" @click="saveConfig('reflection')">保存反思设置</el-button>
          </el-tab-pane>
          <el-tab-pane label="认知设置" name="cognition">
            <p>四维认知与情感分别管理。当前支持保存配置和手动能量调整，自动能量变化及回复策略尚未接入。</p>
            <el-switch v-model="draft.cognition.enabled" aria-label="启用认知配置" active-text="启用认知配置" :disabled="busy" />
            <article v-for="(label, key) in cognitiveNames" :key="key">
              <h2>{{ label }}</h2>
              <div class="controls">
                <label>模式<el-select v-model="draft.cognition.dimensions[key].mode" :disabled="busy"><el-option label="固定" value="fixed" /><el-option label="动态" value="dynamic" /></el-select></label>
                <label v-for="(name, field) in cognitiveFields" :key="field">{{ name }}
                  <el-input-number v-model="draft.cognition.dimensions[key][field]" :aria-label="label + name" :disabled="busy"
                    :min="field === 'sensitivity' ? 0 : (key === 'recall' || key === 'impression' ? 1 : 0)"
                    :max="field === 'sensitivity' ? 3 : (key === 'recall' ? 20 : (key === 'impression' ? 10 : 1))" :step=".1" />
                </label>
              </div>
              <p>当前能量 {{ state.cognition.energy[key] }}（固定模式为0）</p>
              <el-button :disabled="busy || changed('cognition') || blocked.cognition || state.cognition.dimensions[key].mode === 'fixed'" @click="adjustEnergy(key)">调整{{ label }}能量</el-button>
            </article>
            <el-button :disabled="busy || !changed('cognition') || blocked.cognition" @click="saveConfig('cognition')">保存认知设置</el-button>
          </el-tab-pane>
          <el-tab-pane label="情感联动" name="emotion_linkage">
            <p>联动继承记忆召回及情感系统总开关；固定、锁定和单轮变化上限仍由情感系统控制。</p>
            <el-switch v-model="draft.emotion_linkage.enabled" aria-label="启用记忆情感联动" active-text="启用记忆情感联动" :disabled="busy" />
            <div class="controls">
              <el-checkbox v-model="draft.emotion_linkage.memory_to_emotion" :disabled="busy">记忆影响情感</el-checkbox>
              <el-checkbox v-model="draft.emotion_linkage.emotion_to_memory" :disabled="busy">情感影响召回</el-checkbox>
              <label>记忆刺激权重<el-input-number v-model="draft.emotion_linkage.stimulus_weight" aria-label="记忆刺激权重" :min="0" :max="1" :step=".05" :disabled="busy" /></label>
              <label>情感排序影响上限<el-input-number v-model="draft.emotion_linkage.ranking_limit" aria-label="情感排序影响上限" :min="0" :max=".1" :step=".01" :disabled="busy" /></label>
              <label>刺激冷却（秒）<el-input-number v-model="draft.emotion_linkage.cooldown_seconds" aria-label="刺激冷却（秒）" :min="0" :max="2592000" :disabled="busy" /></label>
            </div>
            <p>当前默认召回保持原算法，情感排序调整仅用于影子检索。保存联动设置不会切换记忆引擎。</p>
            <el-button :disabled="busy || !changed('emotion_linkage') || blocked.emotion_linkage" @click="saveConfig('emotion_linkage')">保存联动设置</el-button>
            <el-button :disabled="busy" @click="$router.push({ path: '/affect-system', query: { agentId } })">查看情感变化记录</el-button>
          </el-tab-pane>
        </el-tabs>
      </template>
    </main>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import { getServiceUrl } from '@/apis/api';
import RequestService from '@/apis/httpRequest';

const copy = value => JSON.parse(JSON.stringify(value));
const groups = ['reflection', 'cognition', 'emotion_linkage'];
export default {
  name: 'YoumiMemorySystem', components: { HeaderBar },
  data() {
    return { agents: [], agentId: '', state: null, listing: null, artifacts: [], detail: null, edit: null,
      originalEdit: '', draft: {}, originals: {}, blocked: {}, factBlocked: false, selected: [],
      busy: false, error: '', tab: 'memories', generation: 0,
      kinds: { profile: '画像', note: '笔记', retirement: '退役候选', merge: '合并候选' },
      cognitiveNames: { recall: '召回', impression: '印象', expression: '表达', creativity: '创造' },
      cognitiveFields: { minimum: '下限', baseline: '基线', maximum: '上限', sensitivity: '敏感度' } };
  },
  computed: {
    memories() { return this.listing ? this.listing.memories : []; },
    agentName() { return (this.agents.find(agent => agent.id === this.agentId) || {}).agentName; },
    factDirty() { return this.edit && JSON.stringify(this.edit) !== this.originalEdit; },
    dirty() { return this.factDirty || groups.some(key => this.changed(key)); },
    retired() { return this.detail && this.detail.memory.state === 'superseded'; }
  },
  async mounted() {
    window.addEventListener('beforeunload', this.beforeUnload);
    await this.run(async () => {
      const agents = await this.request('/agent/list');
      if (!Array.isArray(agents)) throw new Error('智能体列表读取失败');
      this.agents = agents;
      const id = this.$route.query.agentId || (agents[0] || {}).id;
      if (id) await this.loadAgent(id, this.$route.query.memoryId);
    });
  },
  beforeDestroy() { this.generation++; window.removeEventListener('beforeunload', this.beforeUnload); },
  beforeRouteLeave(to, from, next) { this.confirmDiscard().then(ok => next(ok)); },
  async beforeRouteUpdate(to, from, next) {
    if (!await this.confirmDiscard()) { next(false); return; }
    next();
    await this.run(() => this.loadAgent(to.query.agentId || this.agentId || (this.agents[0] || {}).id, to.query.memoryId));
  },
  methods: {
    changed(key) { return !!this.draft[key] && JSON.stringify(this.draft[key]) !== this.originals[key]; },
    beforeUnload(event) { if (this.dirty || this.busy) { event.preventDefault(); event.returnValue = ''; } },
    async confirmDiscard() {
      if (this.busy) return false;
      if (!this.dirty) return true;
      try { await this.$confirm('有未保存的修改，确定放弃草稿？', '未保存的修改'); return true; } catch (_) { return false; }
    },
    request(path, command) {
      return new Promise((resolve, reject) => {
        const url = new URL(getServiceUrl() + path, location.href), generation = this.generation;
        if (location.protocol !== 'https:' || url.protocol !== 'https:' || url.origin !== location.origin) {
          reject(new Error('请通过同源 HTTPS 打开管理页')); return;
        }
        const failed = info => reject(new Error(info && info.data && info.data.code === 409
          ? '记忆或配置已变化，草稿已保留，请刷新核对后再保存。'
          : '操作结果未确认，请刷新核对；草稿仍然保留。'));
        const request = RequestService.sendRequest().sensitive().url(url.href).method(command ? 'POST' : 'GET');
        if (command) request.data(command);
        request.success(res => {
          if (generation !== this.generation) { reject(new Error('页面已切换')); return; }
          const body = res.data;
          if (body && body.code === 0 && body.data) resolve(body.data);
          else if (body && body.code === 409) reject(new Error('记忆或配置已变化，草稿已保留，请刷新核对后再保存。'));
          else failed();
        }).fail(failed).networkFail(failed).send();
      });
    },
    call(command) { return this.request('/agent/' + encodeURIComponent(this.agentId) + '/memory-system', command); },
    async run(operation) {
      if (this.busy) return;
      const generation = this.generation;
      this.busy = true; this.error = '';
      try { await operation(); } catch (error) { if (generation === this.generation) this.error = error.message; }
      finally { if (generation === this.generation) this.busy = false; }
    },
    accept(key, value) {
      this.$set(this.state, key, value);
      const draft = key === 'reflection' ? { enabled: value.enabled, interval_seconds: value.interval_seconds }
        : key === 'cognition' ? { enabled: value.enabled, dimensions: copy(value.dimensions) }
          : Object.fromEntries(Object.entries(value).filter(([field]) => field !== 'version'));
      this.$set(this.draft, key, draft); this.$set(this.originals, key, JSON.stringify(draft)); this.$set(this.blocked, key, false);
    },
    async loadAgent(id, memoryId) {
      this.state = null; this.listing = null; this.detail = null; this.edit = null;
      this.draft = {}; this.originals = {}; this.artifacts = []; this.selected = []; this.factBlocked = false;
      if (!this.agents.some(agent => agent.id === id)) throw new Error('该智能体不可管理，请重新选择。');
      this.agentId = id;
      const state = await this.call({ action: 'status' });
      const listing = await this.call({ action: 'list' });
      const items = await this.call({ action: 'artifacts' });
      if (!state.reflection || !state.cognition || !state.emotion_linkage || !Array.isArray(listing.memories) || !Array.isArray(items.artifacts)) throw new Error('服务器状态不完整');
      this.state = state; groups.forEach(key => this.accept(key, state[key]));
      this.listing = listing; this.artifacts = items.artifacts;
      if (memoryId) { this.tab = 'memories'; await this.readDetail(memoryId); }
    },
    selectAgent(id) { return this.$router.push({ path: '/memory-system', query: { agentId: id } }); },
    async refresh() { if (await this.confirmDiscard()) await this.run(() => this.loadAgent(this.agentId, this.detail && this.detail.memory.id)); },
    async readDetail(id, offset = 0) {
      this.detail = null; this.edit = null; this.factBlocked = false;
      let detail;
      try { detail = await this.call({ action: 'detail', id, offset }); }
      catch (error) { throw new Error('当前记忆来源不可用或读取失败，请核对。' + error.message); }
      this.detail = detail; this.edit = { content: detail.memory.content, confirmed: detail.memory.confirmed };
      this.originalEdit = JSON.stringify(this.edit);
    },
    async openDetail(id, offset = 0) {
      if (this.busy || (this.factDirty && !await this.confirmDiscard())) return;
      this.tab = 'memories'; await this.run(() => this.readDetail(id, offset));
    },
    async loadPage(offset) {
      await this.run(async () => { this.listing = await this.call({ action: 'list', offset }); this.selected = []; });
    },
    async saveConfig(key) {
      await this.run(async () => {
        try {
          const value = await this.call({ action: key, operation: { action: 'configure', version: this.state[key].version, ...copy(this.draft[key]) } });
          this.accept(key, value); this.$message.success('配置已保存');
        } catch (error) { this.$set(this.blocked, key, true); throw error; }
      });
    },
    async adjustEnergy(key) {
      let value;
      const agent = this.agentId, version = this.state.cognition.version;
      try { value = (await this.$prompt('输入 -20 至 20 的能量值，固定模式不会改变。', '调整认知能量', {
        inputValue: String(this.state.cognition.energy[key]), inputValidator: text => text.trim() !== '' && Number.isFinite(Number(text)) && Math.abs(Number(text)) <= 20
      })).value; } catch (_) { return; }
      if (agent !== this.agentId || !this.state || version !== this.state.cognition.version) return;
      await this.run(async () => {
        try { this.accept('cognition', await this.call({ action: 'cognition', operation: {
          action: 'adjust', version: this.state.cognition.version, energy: { [key]: Number(value) } } })); this.$message.success('能量已保存'); }
        catch (error) { this.$set(this.blocked, 'cognition', true); throw error; }
      });
    },
    expected() { return { revision: this.detail.revision, content_version: this.detail.memory.content_version, evidence_version: this.detail.memory.evidence_version }; },
    async saveFact() {
      await this.run(async () => {
        try {
          await this.call({ action: 'edit', id: this.detail.memory.id, expected: this.expected(), ...this.edit });
          this.originalEdit = JSON.stringify(this.edit);
          await this.reloadFacts(); this.$message.success('记忆已保存');
        } catch (error) { this.factBlocked = true; throw error; }
      });
    },
    async reloadFacts() {
      const id = this.detail && this.detail.memory.id;
      this.detail = null; this.edit = null; this.listing = null; this.artifacts = [];
      this.listing = await this.call({ action: 'list' }); this.selected = [];
      this.artifacts = (await this.call({ action: 'artifacts' })).artifacts;
      if (id && this.memories.some(row => row.id === id)) await this.readDetail(id);
    },
    async deleteFact() {
      const id = this.detail.memory.id, expected = this.expected(), agent = this.agentId;
      try { await this.$confirm('确定永久删除这条记忆及相关衍生记录？\n' + this.detail.memory.content, '删除记忆', { confirmButtonText: '删除', cancelButtonText: '取消', dangerouslyUseHTMLString: false }); } catch (_) { return; }
      if (agent !== this.agentId || !this.detail || this.detail.memory.id !== id) return;
      await this.run(async () => {
        try { await this.call({ action: 'delete', id, expected }); this.detail = null; this.edit = null; await this.reloadFacts(); this.$message.success('记忆已删除'); }
        catch (error) { this.factBlocked = true; throw error; }
      });
    },
    async removeArtifact(item) {
      const agent = this.agentId;
      try { await this.$confirm('只删除此整理条目，保留原始事实。', '删除整理条目'); } catch (_) { return; }
      if (agent !== this.agentId) return;
      await this.run(async () => { await this.call({ action: 'delete_artifact', id: item.id, version: item.version }); await this.reloadFacts(); this.$message.success('整理条目已删除'); });
    },
    choose(id, checked) { this.selected = checked ? [...this.selected, id].slice(-2) : this.selected.filter(value => value !== id); },
    async mergeSelected() {
      const rows = this.selected.map(id => this.memories.find(row => row.id === id));
      if (rows.length !== 2 || rows.some(row => !row)) return;
      const [survivor, retired] = rows, agent = this.agentId;
      const command = { action: 'merge', survivor_id: survivor.id, retired_id: retired.id, revision: this.listing.revision,
        operation_id: window.crypto.randomUUID(), versions: { survivor: [survivor.content_version, survivor.evidence_version], retired: [retired.content_version, retired.evidence_version] } };
      try { await this.$confirm('保留：' + survivor.content + '\n退役：' + retired.content + '\n原ID和来源保留，退役记忆停止召回。确认合并？', '核对合并', { dangerouslyUseHTMLString: false }); } catch (_) { return; }
      if (agent !== this.agentId) return;
      await this.run(async () => { await this.call(command); await this.reloadFacts(); this.$message.success('记忆已合并'); });
    },
    memoryState(row) { return row.state === 'superseded' ? '已合并退役' : row.confirmed && row.state === 'active' ? '已确认' : '待确认或不可用'; },
    time(value) { return value ? new Date(value * 1000).toLocaleString() : '尚无记录'; }
  }
};
</script>

<style scoped>
.memory-main { max-width: 1100px; margin: auto; padding: 24px; }
.memory-main p, .memory-main small { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
.memory-main article, .memory-main section { padding: 16px 0; border-bottom: 1px solid #ddd; }
.memory-main h2 { font-size: 19px; }
.memory-main label { display: flex; flex-direction: column; gap: 8px; }
.controls { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin: 20px 0; }
.actions { display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }
.memory-main .el-button { max-width: 100%; white-space: normal; margin: 6px 0; }
@media (max-width: 600px) { .memory-main { padding: 14px; } .controls { grid-template-columns: 1fr; } }
</style>
