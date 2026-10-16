<script setup lang="ts">
import { DialogRoot, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogDescription, DialogClose } from 'reka-ui'
import { computed } from 'vue'
import { PhX, PhStar, PhClock, PhUsers, PhMapPin, PhArrowsClockwise } from '@phosphor-icons/vue'
import type { PlaceDetail, PlaceMedia, PlanItem } from '../api'
import { localQueryDate } from '../api'
import PhotoGallery from './PhotoGallery.vue'

const props = defineProps<{ open: boolean; item: PlanItem | null; detail: PlaceDetail | null; loading: boolean; media: PlaceMedia | null; mediaLoading: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; swap: [] }>()
const notices = computed(() => [...new Set([...(props.item?.notices || []), ...(props.detail?.tips || [])])])
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="place-overlay" />
      <DialogContent class="place-drawer" aria-describedby="place-description">
        <header class="place-drawer-head">
          <span class="place-eyebrow">{{ item?.is_alternative ? '备选餐厅 · 尚未替换' : item?.kind === 'meal' ? '用餐地点' : item?.evening ? '晚间散步' : '此行的一站' }}</span>
          <DialogClose class="icon-button" aria-label="关闭地点详情"><PhX :size="20" /></DialogClose>
          <DialogTitle>{{ item?.name }}</DialogTitle>
          <div class="place-rating" aria-live="polite">
            <PhStar :size="16" :weight="detail?.rating != null ? 'fill' : 'regular'" />
            <template v-if="detail?.rating != null"><strong>{{ detail.rating.toFixed(1) }}</strong><span>/ 5 · {{ detail.rating_source || '来源待确认' }}评分</span></template>
            <span v-else>{{ loading ? '正在查询公开评分…' : detail?.rating_status === 'unavailable' ? '评分暂时查询失败' : '暂无公开评分' }}</span>
          </div>
          <p v-if="detail?.rating_at && detail.rating != null" class="field-help">查询于 {{ localQueryDate(detail.rating_at) }}，评分不代表实时营业状况</p>
        </header>
        <div class="place-drawer-body">
          <PhotoGallery :media="media" :loading="mediaLoading" :name="item?.name || ''" />
          <DialogDescription id="place-description" class="place-introduction">{{ detail?.introduction || '地点资料暂不可用，请稍后重试。' }}</DialogDescription>
          <section v-if="item?.reason"><h3>为什么推荐给你</h3><p>{{ item.reason }}</p></section>
          <section v-if="item?.guide_evidence?.length" class="guide-sources">
            <h3>本次选点参考的攻略</h3>
            <article v-for="guide in item.guide_evidence" :key="guide.id">
              <a :href="guide.url" target="_blank" rel="noopener noreferrer">{{ guide.title }} ↗</a>
              <p>{{ guide.facts }}</p>
              <p v-if="guide.duration_scope" class="field-help">时长范围：{{ guide.duration_scope }}</p>
              <p v-if="guide.queue_note" class="field-help">{{ guide.queue_note }}</p>
              <p v-if="guide.season_note" class="field-help">{{ guide.season_note }}</p>
              <p class="field-help">{{ guide.publisher }} · {{ guide.source_date || '发布日期未注明' }}</p>
              <details><summary>来源与使用说明</summary><p class="field-help">{{ guide.attribution }}</p><p v-for="note in guide.caveats" :key="note" class="field-help">{{ note }}</p></details>
            </article>
          </section>
          <section v-if="detail?.highlights.length"><h3>{{ item?.kind === 'meal' ? '到店前参考' : '推荐看点与逛法' }}</h3><ul><li v-for="highlight in detail.highlights" :key="highlight">{{ highlight }}</li></ul></section>
          <section v-if="detail?.audiences.length"><h3><PhUsers :size="16" />适合人群</h3><div class="place-tags"><span v-for="audience in detail.audiences" :key="audience">{{ audience }}</span></div></section>
          <section v-if="detail"><h3><PhClock :size="16" />{{ item?.is_alternative ? '备选安排参考' : '这次怎么安排' }}</h3><dl class="place-facts"><div><dt>{{ item?.is_alternative ? '参考时段' : '行程时段' }}</dt><dd>{{ item?.start_hm }}–{{ item?.end_hm }}</dd></div><div v-if="item"><dt>{{ item.kind === 'meal' ? '参考人均' : '参考票价' }}</dt><dd>{{ item.cost > 0 ? `¥${item.cost.toFixed(0)}` : item.price_verified ? '免费' : '待核实' }}</dd></div><div><dt>参考停留</dt><dd>{{ detail.duration }}</dd></div><div><dt>开放记录</dt><dd>{{ detail.opening }}</dd></div></dl><p v-if="item?.is_alternative" class="field-help">当前仅查看备选，原餐厅和路线未更换；采纳前需重新核对后续时间安排。</p></section>
          <section v-if="detail?.address"><h3><PhMapPin :size="16" />地点地址</h3><p>{{ detail.address }}</p></section>
          <section v-if="notices.length"><h3>出发前留意</h3><ul><li v-for="tip in notices" :key="tip">{{ tip }}</li></ul></section>
          <p v-if="detail" class="place-provenance">{{ detail.content_source }}<a v-if="detail.source_url" :href="detail.source_url" target="_blank" rel="noopener noreferrer">查看开放信息来源</a></p>
        </div>
        <footer v-if="item?.is_alternative" class="place-drawer-action">
          <div class="swap-price"><span>参考人均</span><strong v-if="item.cost > 0">¥{{ item.cost.toFixed(0) }}<small> / 人</small></strong><strong v-else>待确认</strong></div>
          <button type="button" class="primary-btn swap-cta" @click="emit('swap')"><PhArrowsClockwise :size="17" />选这家餐厅</button>
          <p>确认后重新安排当天路线，支持撤销</p>
        </footer>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
