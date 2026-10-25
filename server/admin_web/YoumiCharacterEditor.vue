<template>
  <section class="youmi-character">
    <h3>性格与情绪基线</h3>
    <p>由后端保存，当前智能体的已授权设备共用。初始性格与长期记忆、成长特点分别管理。</p>
    <p v-if="!profile.values">尚未配置，不会自动为其他智能体添加悠米设定。</p>
    <el-button size="small" :disabled="disabled" @click="loadPreset">载入悠米 V1 草稿</el-button>
    <div v-if="draft" class="character-form">
      <el-checkbox v-model="enabled" :disabled="disabled">启用性格与情绪基线</el-checkbox>
      <label>名字<el-input v-model="draft.name" label="性格名字" :disabled="disabled" maxlength="32" /></label>
      <label>年龄<el-input-number v-model="draft.age" aria-label="性格年龄" :disabled="disabled" :min="18" :max="100" /></label>
      <label>亲昵称呼<el-input v-model="draft.address" label="亲昵称呼" :disabled="disabled" maxlength="32" /></label>
      <label>关系<el-input v-model="draft.relationship" label="相处关系" :disabled="disabled" maxlength="160" /></label>
      <label>人设设定<el-input v-model="draft.background" type="textarea" aria-label="人设设定" :disabled="disabled" :rows="12"
        placeholder="可填写身份、性格、说话方式、相处规则和示例对话。支持多段文本和 Markdown。" /></label>
      <p>人设作为系统提示词参与每轮回复；明确写出的人设优先于基础字段和默认表达建议。不设输入和保存字数上限，模型上下文容量仍取决于所选模型；超出时会明确提示，不会截断人设。</p>
      <template v-if="!hideDynamics">
        <label v-for="field in dispositions" :key="field.key">{{ field.label }}<el-input-number v-model="draft[field.key]" :aria-label="field.label" :disabled="disabled" :min="0" :max="1" :step="0.05" :precision="2" /></label>
        <label>情绪恢复半衰期（小时）<el-input-number v-model="draft.recovery_hours" aria-label="情绪恢复半衰期" :disabled="disabled" :min="1" :max="24" /></label>
      </template>
      <el-checkbox v-model="draft.swearing" :disabled="disabled">情绪强烈时允许偶尔爆粗</el-checkbox>
      <p>温柔度与亲近度影响正向情绪反应；小脾气影响负向反应和缓和速度。生气会延续，时间恢复按半衰期计算；持续情绪开关关闭时不记录状态。基线停用后不参与回应或情绪计算，保留配置与已有情绪。</p>
      <p>身份、性格和表达方式按你的人设执行；人设中的虚构经历与真实互动记忆分别管理。</p>
      <p>载入或编辑只是草稿，点击保存才生效。保存不会修改原角色配置、真实记忆或用户心情授权。</p>
      <el-button type="primary" :disabled="disabled" @click="save">保存性格与情绪基线</el-button>
    </div>
  </section>
</template>

<script>
export default {
  props: { profile: { type: Object, required: true }, disabled: Boolean, hideDynamics: Boolean },
  data() {
    return { draft: null, enabled: false, dispositions: [
      { key: 'warmth', label: '温柔度' }, { key: 'attachment', label: '亲近度' }, { key: 'temper', label: '小脾气强度' }
    ] };
  },
  watch: { profile: { immediate: true, handler(profile, previous) {
    if (previous && profile.version === previous.version) return;
    this.draft = profile.values ? { ...profile.values } : null; this.enabled = profile.enabled;
  } } },
  methods: {
    loadPreset() { this.draft = { ...this.profile.preset }; this.enabled = true; },
    save() { this.$emit('save', { action: 'set_character', version: this.profile.version, enabled: this.enabled, values: { ...this.draft } }); }
  }
};
</script>

<style scoped>
.youmi-character { border-top: 1px solid #ddd; margin-top: 20px; padding-top: 12px; }
.character-form { display: grid; gap: 14px; margin-top: 16px; }
.character-form label { display: grid; gap: 6px; }
.character-form p { margin: 0; line-height: 1.6; color: #606266; }
</style>
