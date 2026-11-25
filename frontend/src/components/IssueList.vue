<script setup lang="ts">
// 规划前就能看出的问题和一键修改；一键修改只放进草稿
import { PhInfo, PhWarningCircle } from '@phosphor-icons/vue'
import type { UnderstandingIssue } from '../api'
import type { UnderstandingDraft } from '../understandingDraft'

defineProps<{ issues: UnderstandingIssue[]; edits: UnderstandingDraft; busy: boolean }>()
</script>

<template>
  <ul v-if="issues.length" class="confirm-issues">
    <li v-for="issue in issues" :key="`${issue.code}-${issue.message}`" class="notice" :class="{ bad: issue.level === 'blocker', warn: issue.level === 'warning' }" :role="issue.level === 'blocker' ? 'alert' : undefined">
      <PhWarningCircle v-if="issue.level !== 'info'" :size="16" /><PhInfo v-else :size="16" />
      <div><p>{{ issue.message }}</p>
        <div v-if="issue.fixes.length" class="confirm-fixes"><button v-for="fix in issue.fixes" :key="fix.label" type="button" :class="{ on: edits.fixed(fix) }" :aria-pressed="edits.fixed(fix)" :disabled="busy" @click="edits.applyFix(fix)">{{ fix.label }}</button></div>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.confirm-issues { list-style: none; margin: 0 0 14px; padding: 0; }
.confirm-issues p { margin: 0; }
.confirm-fixes { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.confirm-fixes button { font: inherit; font-size: 12px; padding: 4px 11px; border: 1px solid currentColor; border-radius: 3px; background: #ffffff; color: inherit; cursor: pointer; }
.confirm-fixes button.on { background: #151613; border-color: #151613; color: #ffffff; }
.confirm-fixes button:disabled { opacity: .5; cursor: default; }
</style>
