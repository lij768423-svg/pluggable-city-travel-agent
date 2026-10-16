<script setup lang="ts">
// 换餐厅前的确认框：说清楚会发生什么（当天重新核对、时间可能顺延、可能移出景点、其他日期不变、可撤销）。
import { AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogOverlay,
  AlertDialogPortal, AlertDialogRoot, AlertDialogTitle } from 'reka-ui'
import { PhArrowRight, PhWarningCircle } from '@phosphor-icons/vue'
import type { MealAlternative } from '../api'

defineProps<{ open: boolean; day: number; meal: 'lunch' | 'dinner' | null; from: string; target: MealAlternative | null; busy: boolean; error: string }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; confirm: [] }>()
</script>

<template>
  <AlertDialogRoot :open="open" @update:open="value => !busy && emit('update:open', value)">
    <AlertDialogPortal>
      <AlertDialogOverlay class="confirm-overlay" />
      <AlertDialogContent class="confirm-dialog">
        <AlertDialogTitle>换成「{{ target?.name }}」？</AlertDialogTitle>
        <AlertDialogDescription as="div" class="confirm-body">
          <p class="confirm-swap"><span>第 {{ day }} 天{{ meal === 'lunch' ? '午餐' : '晚餐' }}</span><s>{{ from }}</s><PhArrowRight :size="14" /><strong>{{ target?.name }}</strong></p>
          <p v-if="target" class="muted">{{ target.cuisine }} · 参考人均 ¥{{ target.price.toFixed(0) }}<template v-if="target.travel_source !== 'previous'"> · 比原路线多绕约 {{ target.detour_min }} 分钟{{ target.travel_source === 'estimate' ? '（估算）' : '' }}</template></p>
          <ul>
            <li>会按真实路线重新核对这一天，之后的时间可能顺延；</li>
            <li>如果时间放不下，会调整当天景点顺序，必要时移出得分最低的景点；</li>
            <li>其他日期保持不变，换完可以一键撤销。</li>
          </ul>
          <p v-if="error" class="notice bad" role="alert"><PhWarningCircle :size="16" />{{ error }}</p>
        </AlertDialogDescription>
        <div class="confirm-actions">
          <AlertDialogCancel class="ghost-btn" :disabled="busy">取消</AlertDialogCancel>
          <AlertDialogAction class="primary-btn" :disabled="busy" @click.prevent="emit('confirm')">
            <span v-if="busy" class="spinner light" />{{ busy ? '正在重新规划…' : '确认更换并重新规划' }}
          </AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>
