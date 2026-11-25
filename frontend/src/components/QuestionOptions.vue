<script setup lang="ts">
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle, DialogTrigger } from 'reka-ui'
import { PhPlus, PhX } from '@phosphor-icons/vue'
defineProps<{ title: string; trigger: string }>()
</script>

<template>
  <DialogRoot>
    <DialogTrigger type="button" class="pill plain flush"><PhPlus :size="17" aria-hidden="true" />{{ trigger }}</DialogTrigger>
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
