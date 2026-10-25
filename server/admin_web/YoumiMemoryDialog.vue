<template>
  <el-dialog title="记忆管理" :visible.sync="visible" width="min(92vw, 640px)" :close-on-click-modal="false">
    <p>这里是当前智能体在服务器保存的长期记忆，所有已授权绑定设备共用。删除会撤回相关记忆及其衍生记录，无法恢复。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="state" v-loading="busy">
      <p>共 {{ state.total }} 条记忆</p>
      <p v-if="!state.memories.length">暂无记忆</p>
      <article v-for="memory in state.memories" :key="memory.id" class="youmi-memory">
        <p class="memory-content">{{ memory.content }}</p>
        <p class="memory-meta">{{ memory.confirmed ? '已确认' : '待确认' }} · {{ new Date(memory.updated_at * 1000).toLocaleString() }}</p>
        <el-button type="danger" plain size="small" :disabled="busy" @click="remove(memory)">删除记忆</el-button>
      </article>
      <div class="memory-pages">
        <el-button :disabled="busy || !state.offset" @click="load(Math.max(0, state.offset - state.page_size))">上一页</el-button>
        <span>{{ Math.floor(state.offset / state.page_size) + 1 }} 页</span>
        <el-button :disabled="busy || state.offset + state.page_size >= state.total" @click="load(state.offset + state.page_size)">下一页</el-button>
      </div>
    </div>
    <p v-else-if="busy">正在读取服务器记忆…</p>
    <span slot="footer"><el-button :disabled="busy" @click="load(0)">刷新</el-button><el-button @click="visible = false">关闭</el-button></span>
  </el-dialog>
</template>

<script>
import { getServiceUrl } from '@/apis/api';
import RequestService from '@/apis/httpRequest';

export default {
  name: 'YoumiMemoryDialog',
  props: { value: Boolean, agentId: { type: String, required: true } },
  data() { return { state: null, busy: false, error: '', generation: 0 }; },
  computed: { visible: { get() { return this.value; }, set(next) { this.$emit('input', next); } } },
  watch: {
    value(open) { if (open) this.load(0); else { this.generation++; this.state = null; this.busy = false; this.error = ''; } },
    agentId() { this.generation++; this.state = null; if (this.value) this.load(0); }
  },
  methods: {
    call(command) {
      return new Promise((resolve, reject) => {
        const url = new URL(`${getServiceUrl()}/agent/${encodeURIComponent(this.agentId)}/youmi-settings`, window.location.href);
        if (location.protocol !== 'https:' || url.protocol !== 'https:' || url.origin !== location.origin) {
          reject(new Error('请通过 HTTPS 打开智控台，再管理记忆。')); return;
        }
        const failed = () => reject(new Error('记忆操作结果未确认，请刷新后核对。'));
        RequestService.sendRequest().sensitive().url(url.href).method('POST').data(command)
          .success(res => {
            const body = res.data;
            if (!body || body.code !== 0 || !body.data || !Array.isArray(body.data.memories)) { failed(); return; }
            resolve(body.data);
          }).fail(failed).networkFail(failed).send();
      });
    },
    async load(offset) { await this.perform({ action: 'memory_list', offset }); },
    async perform(command) {
      const generation = ++this.generation;
      this.busy = true; this.error = ''; this.state = null;
      try {
        const state = await this.call(command);
        if (generation === this.generation) {
          this.state = state;
          if (command.action === 'memory_delete') this.$message.success('记忆已删除');
        }
      } catch (error) { if (generation === this.generation) this.error = error.message; }
      finally { if (generation === this.generation) this.busy = false; }
    },
    async remove(memory) {
      if (this.busy) return;
      const agentId = this.agentId, generation = this.generation;
      try {
        await this.$confirm(`确定删除这条记忆？删除后无法恢复。\n${memory.content}`, '删除记忆', {
          type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消', dangerouslyUseHTMLString: false
        });
      } catch (_) { return; }
      if (!this.value || this.busy || agentId !== this.agentId || generation !== this.generation) return;
      await this.perform({ action: 'memory_delete', id: memory.id, offset: 0 });
    }
  }
};
</script>

<style scoped>
.youmi-memory { padding: 12px 0; border-bottom: 1px solid #eee; }
.memory-content { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.6; }
.memory-meta { color: #606266; font-size: 13px; }
.memory-pages { display: flex; gap: 12px; align-items: center; justify-content: center; margin-top: 16px; }
</style>
