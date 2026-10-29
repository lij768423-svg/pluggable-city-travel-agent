<script setup lang="ts">
// 问卷模式的确认：只有预检发现问题时才出现（2026-10-28 用户决定）；修改直接改字段，改完重新核对，确认后才开始规划。
import { computed, ref } from 'vue'
import { PhArrowRight, PhWarningCircle } from '@phosphor-icons/vue'
import type { Meta, Understanding } from '../api'
import UnderstandingTable from './UnderstandingTable.vue'

const props = defineProps<{ understanding: Understanding; meta: Meta | null; busy: boolean; error: string; quota?: { limit: number; remaining: number } | null }>()
const emit = defineEmits<{ confirm: []; back: []; patch: [patch: Record<string, unknown>] }>()
const blocked = computed(() => props.understanding.issues.some(issue => issue.level === 'blocker'))
const outOfQuota = computed(() => !!props.quota && props.quota.remaining <= 0)
const pending = ref(0)  // 表里改了还没提交的处数：提交前不能开始规划，免得改的没算进去
</script>

<template>
  <section class="qform-card plan-confirm" aria-labelledby="plan-confirm-title" :aria-busy="busy">
    <header class="qform-head"><p class="preview-eyebrow">开始规划前</p><h2 id="plan-confirm-title" tabindex="-1">有几处要先看一下</h2></header>
    <div class="qform-body">
      <UnderstandingTable :understanding="understanding" :meta="meta" :busy="busy" @apply="patch => emit('patch', patch)" @pending="phrases => pending = phrases.length" />
      <p class="field-help">改完点“提交修改”会重新核对；更多条件可以回到问卷修改。</p>
      <p v-if="error" class="notice bad" role="alert"><PhWarningCircle :size="16" />{{ error }}</p>
    </div>
    <footer class="qform-foot">
      <div class="qform-actions">
        <span v-if="quota" class="qform-quota" :class="{ empty: outOfQuota }">{{ outOfQuota ? '今天的生成次数已用完' : `今天还可生成 ${quota.remaining}/${quota.limit} 次` }}</span>
        <button type="button" class="journey-secondary" :disabled="busy" @click="emit('back')">返回问卷</button>
        <button type="button" class="journey-primary" :disabled="busy || blocked || outOfQuota || pending > 0" :title="pending ? '先提交表里的修改' : undefined" @click="emit('confirm')"><span v-if="busy" class="spinner light" />{{ busy ? '正在核对…' : '照这样规划' }}<PhArrowRight v-if="!busy" :size="18" /></button>
      </div>
    </footer>
  </section>
</template>
