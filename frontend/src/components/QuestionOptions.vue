<script setup lang="ts">
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle, DialogTrigger } from 'reka-ui'
import { PhX } from '@phosphor-icons/vue'
// art：触发卡片右侧的填色小插画（时段与步行 / 规划设置 / 每餐预算），悬停时轻微浮动
defineProps<{ title: string; trigger: string; description?: string; art?: 'time' | 'settings' | 'meal' }>()
</script>

<template>
  <DialogRoot>
    <DialogTrigger type="button" class="question-options-trigger" :class="{ 'with-description': description, 'with-art': art }"><span>{{ trigger }}<small v-if="description">{{ description }}</small></span>
      <svg v-if="art" class="option-art" viewBox="0 0 96 56" fill="none" aria-hidden="true" focusable="false">
        <template v-if="art === 'time'">
          <path d="M0 48c12-8 24-10 36-5s24 5 36-2 18-7 24-6v21H0Z" fill="#e9eae6" />
          <path d="M44 56c10-9 26-12 52-10v10Z" fill="#dedfda" />
          <circle class="art-float" cx="76" cy="15" r="8" fill="#b93b28" />
          <g class="art-slide" fill="#a3a59e"><ellipse cx="54" cy="46" rx="2.2" ry="3.4" transform="rotate(-20 54 46)" /><ellipse cx="61" cy="41" rx="2.2" ry="3.4" transform="rotate(-20 61 41)" /><ellipse cx="68" cy="45" rx="2.2" ry="3.4" transform="rotate(-20 68 45)" /></g>
          <g class="art-float"><circle cx="28" cy="22" r="15" fill="#fff" stroke="#383a35" stroke-width="2.6" /><path d="M28 13v9l6.5 4" stroke="#383a35" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" /><circle cx="28" cy="22" r="2" fill="#383a35" /></g>
        </template>
        <template v-else-if="art === 'settings'">
          <rect x="6" y="11" width="72" height="5" rx="2.5" fill="#e9eae6" /><rect x="6" y="11" width="44" height="5" rx="2.5" fill="#a3a59e" />
          <rect x="6" y="26" width="72" height="5" rx="2.5" fill="#e9eae6" /><rect x="6" y="26" width="22" height="5" rx="2.5" fill="#a3a59e" />
          <rect x="6" y="41" width="72" height="5" rx="2.5" fill="#e9eae6" /><rect x="6" y="41" width="58" height="5" rx="2.5" fill="#a3a59e" />
          <circle cx="50" cy="13.5" r="7" fill="#fff" stroke="#383a35" stroke-width="2.6" />
          <circle class="art-slide" cx="28" cy="28.5" r="7" fill="#383a35" />
          <circle cx="64" cy="43.5" r="7" fill="#fff" stroke="#383a35" stroke-width="2.6" />
          <path class="art-float" d="M88 4l2 5 5 2-5 2-2 5-2-5-5-2 5-2Z" fill="#b93b28" />
        </template>
        <template v-else>
          <ellipse cx="38" cy="52" rx="28" ry="3.5" fill="#e9eae6" />
          <g class="art-float" stroke="#a3a59e" stroke-width="2.4" stroke-linecap="round"><path d="M27 6c-4 4 4 6 0 11" /><path d="M38 3c-4 4 4 6 0 11" /><path d="M49 6c-4 4 4 6 0 11" /></g>
          <path d="M12 25h52a26 21 0 0 1-52 0Z" fill="#383a35" />
          <ellipse cx="38" cy="25" rx="26" ry="4.5" fill="#e9eae6" /><ellipse cx="38" cy="25" rx="18" ry="2.4" fill="#b93b28" opacity=".85" />
          <rect x="28" y="44" width="20" height="4" rx="2" fill="#151613" />
          <g class="art-slide" stroke="#696b64" stroke-width="2.6" stroke-linecap="round"><path d="M66 6 80 33" /><path d="M73 4 86 31" /></g>
        </template>
      </svg>
      <span :class="art ? 'option-art-arrow' : undefined" aria-hidden="true">↗</span></DialogTrigger>
    <DialogPortal>
      <DialogOverlay class="question-options-overlay" />
      <DialogContent class="question-options-dialog">
        <header><DialogTitle>{{ title }}</DialogTitle><DialogClose type="button" class="question-options-close" aria-label="关闭设置"><PhX :size="22" /></DialogClose><DialogDescription>选择会保留在本次问卷中。</DialogDescription></header>
        <div class="question-options-body"><slot /></div>
        <footer><DialogClose type="button" class="question-options-done">保存选择</DialogClose></footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
