<template>
  <el-dialog title="Youmi 功能管理" :visible.sync="visible" width="min(92vw, 560px)" :close-on-click-modal="false">
    <p>角色内容由你设定，不会自动补写。开关只控制功能，关闭不会删除已有数据。</p>
    <el-button size="small" @click="$emit('configure'); visible = false">编辑角色设定</el-button>
    <el-button size="small" @click="$router.push({ path: '/affect-system', query: { agentId } }); visible = false">情感系统</el-button>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="state" v-loading="busy" class="youmi-settings">
      <div v-for="item in switches" :key="item.key" class="youmi-setting">
        <div><strong>{{ item.label }}</strong><p>{{ item.description }}</p></div>
        <button type="button" role="switch" class="youmi-toggle" :class="{ 'is-on': settingValue(item.key) }"
          :disabled="busy" :aria-label="item.label" :aria-checked="settingValue(item.key) ? 'true' : 'false'"
          @click="change(item.key, !settingValue(item.key))">
          <span class="youmi-toggle-track" aria-hidden="true"><span class="youmi-toggle-thumb" /></span>
        </button>
      </div>
      <div v-if="state.youmi_emotion && !state.youmi_emotion.system_configured" class="youmi-emotion-status">
        <p>{{ state.character && state.character.enabled ? state.character.values.name : 'Youmi' }} 当前情绪：{{ state.youmi_emotion.enabled ? state.youmi_emotion.label : '已停用' }}<span v-if="state.youmi_emotion.updated_at"> · 最近互动 {{ new Date(state.youmi_emotion.updated_at * 1000).toLocaleString() }}</span></p>
        <el-button size="small" :disabled="busy" @click="change('reset_youmi_emotion', true)">重置 Youmi 情绪</el-button>
      </div>
      <YoumiCharacterEditor v-if="state.character && !(state.youmi_emotion && state.youmi_emotion.system_configured)" :profile="state.character" :disabled="busy" @save="saveCharacter" />
      <p>已保存记忆 {{ state.counts.memories }} 条 · 成长特点 {{ state.counts.traits }} 个 · 互动事件 {{ state.counts.events }} 个</p>
      <p>保存后立即影响后续处理；使用旧上下文的回复可能中止，可重新发起对话。已发出的声音无法撤回。</p>
    </div>
    <p v-else-if="busy">正在读取服务器设置…</p>
    <span slot="footer"><el-button :disabled="busy" @click="load">刷新</el-button><el-button @click="visible = false">关闭</el-button></span>
  </el-dialog>
</template>

<script>
import { getServiceUrl } from '@/apis/api';
import RequestService from '@/apis/httpRequest';
import YoumiCharacterEditor from './YoumiCharacterEditor.vue';
import { normalizeYoumiSettings } from './youmi_contract';

export default {
  name: 'YoumiSettingsDialog',
  components: { YoumiCharacterEditor },
  props: { value: Boolean, agentId: { type: String, required: true } },
  data() {
    return {
      state: null, busy: false, error: '', generation: 0,
      switches: [
        { key: 'automatic_memory', label: '自动保存记忆', description: '聊天后提取并保存长期信息。关闭后暂停新的记忆、成长和你的心情采集；Youmi 自己的持续情绪由独立开关控制。' },
        { key: 'memory_recall', label: '对话中使用长期记忆', description: '关闭后不在回复中使用已保存的记忆、成长特点、互动事件或心情趋势；不会删除记录，也不会清空当前聊天。' },
        { key: 'personality_growth', label: '人格成长', description: '根据多个真实会话形成表达特点。关闭后停止生成，也不在回复中使用已有特点；不会修改你的角色设定。' },
        { key: 'interaction_events', label: '互动事件延续', description: '记忆使用开启时，保存并使用有来源的互动事件。关闭后停止新增，也不在回复中使用。' },
        { key: 'companion_emotion', label: 'Youmi 的持续情绪', description: 'Youmi 自己的情绪随互动变化、随时间逐渐恢复，跨会话、重启和已授权设备延续，影响回应。关闭后停止更新和使用；不改变你的角色设定。' },
        { key: 'emotion_tracking', label: '我的长期心情追踪', description: '这是对你长期心情的观察，不是 Youmi 的情绪。默认关闭，只有你明确开启才采集；关闭不删除已有记录。' }
      ]
    };
  },
  computed: {
    visible: { get() { return this.value; }, set(next) { this.$emit('input', next); } }
  },
  watch: {
    value(open) { if (open) this.load(); else { this.generation++; this.state = null; this.busy = false; this.error = ''; } },
    agentId() { if (this.value) this.load(); }
  },
  methods: {
    settingValue(key) {
      if (key === 'automatic_memory') return !this.state.paused;
      if (key === 'emotion_tracking') return !!this.state.emotion_tracking.enabled;
      return !!this.state.features[key];
    },
    call(command) {
      return new Promise((resolve, reject) => {
        const url = new URL(`${getServiceUrl()}/agent/${encodeURIComponent(this.agentId)}/youmi-settings`, window.location.href);
        if (window.location.protocol !== 'https:' || url.protocol !== 'https:' || url.origin !== location.origin) {
          reject(new Error('请通过 HTTPS 打开智控台，再管理 Youmi 功能。')); return;
        }
        const failed = () => reject(new Error('设置未确认保存，请刷新后核对。'));
        RequestService.sendRequest().sensitive().url(url.href).method('POST').data(command)
          .success(res => {
            const body = res.data;
            if (!body || body.code !== 0 || !body.data || !body.data.features || !body.data.counts) { failed(); return; }
            resolve(normalizeYoumiSettings(body.data));
          }).fail(failed).networkFail(failed).send();
      });
    },
    async load() {
      const generation = ++this.generation;
      this.busy = true; this.error = ''; this.state = null;
      try {
        const state = await this.call({ action: 'status' });
        if (generation === this.generation) this.state = state;
      } catch (error) { if (generation === this.generation) this.error = error.message; }
      finally { if (generation === this.generation) this.busy = false; }
    },
    async saveCharacter(command) {
      if (this.busy || !this.value) return;
      const generation = ++this.generation;
      this.busy = true; this.error = '';
      try {
        const state = await this.call(command);
        if (generation === this.generation) { this.state = state; this.$message.success('性格与情绪基线已保存'); }
      } catch (error) {
        if (generation === this.generation) { this.error = error.message; this.state = null; }
      } finally { if (generation === this.generation) this.busy = false; }
    },
    async change(key, next) {
      if (this.busy) return;
      const agentId = this.agentId, openedGeneration = this.generation;
      if (key === 'emotion_tracking' && next) {
        try { await this.$confirm('开启后会根据你的真实对话持续记录心情观察，用于长期趋势。是否同意开启？', '长期心情追踪', { type: 'warning' }); }
        catch (_) { return; }
      }
      if (key === 'reset_youmi_emotion') {
        try { await this.$confirm('将 Youmi 当前情绪恢复为平静，不修改角色、记忆或你的心情记录。是否重置？', '重置 Youmi 情绪', { type: 'warning' }); }
        catch (_) { return; }
      }
      if (!this.value || this.agentId !== agentId || this.generation !== openedGeneration) return;
      const generation = ++this.generation;
      this.busy = true; this.error = '';
      const command = key === 'reset_youmi_emotion' ? { action: 'reset_youmi_emotion' }
        : key === 'automatic_memory' ? { action: 'pause', paused: !next }
        : key === 'emotion_tracking' ? { action: 'emotion_consent', enabled: next }
          : { action: 'set_feature_settings', features: { [key]: next } };
      try {
        const state = await this.call(command);
        if (generation === this.generation) { this.state = state; this.$message.success('已保存'); }
      } catch (error) {
        if (generation === this.generation) { this.error = error.message; this.state = null; }
      } finally { if (generation === this.generation) this.busy = false; }
    }
  }
};
</script>

<style scoped>
.youmi-setting { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 14px 0; border-bottom: 1px solid #eee; }
.youmi-setting p { margin: 6px 0 0; color: #606266; line-height: 1.5; }
.youmi-settings { margin-top: 16px; }
.youmi-toggle { flex: 0 0 44px; width: 44px; height: 44px; padding: 2px; border: 0; background: transparent; cursor: pointer; }
.youmi-toggle-track { display: block; box-sizing: border-box; width: 40px; height: 22px; padding: 2px; border-radius: 12px; background: #909399; }
.youmi-toggle-thumb { display: block; width: 18px; height: 18px; border-radius: 50%; background: white; transition: transform .15s; }
.youmi-toggle.is-on .youmi-toggle-track { background: #409eff; }
.youmi-toggle.is-on .youmi-toggle-thumb { transform: translateX(18px); }
.youmi-toggle:focus-visible { outline: 2px solid #409eff; outline-offset: 2px; }
.youmi-toggle:disabled { opacity: .6; cursor: wait; }
</style>
