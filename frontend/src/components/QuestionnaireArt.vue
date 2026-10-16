<script setup lang="ts">
defineProps<{ step: number }>()
</script>

<template>
  <svg class="questionnaire-art" viewBox="0 0 160 90" fill="none" aria-hidden="true" focusable="false">
    <g stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
      <g v-if="step === 0">
        <path d="M33 34h36v39H33z M43 34v-7h16v7 M42 42v22 M60 42v22" />
        <path d="m90 40 37-18-13 36-9-13-15-5Zm15 5 22-23" />
        <path class="art-route" d="M66 67c24 11 52 7 60-8" />
        <circle cx="39" cy="76" r="2" /><circle cx="64" cy="76" r="2" />
      </g>
      <g v-else-if="step === 1">
        <path d="m20 66 27-32 22 25 15-17 29 24 M75 69h64 M85 63V37h32v26 M79 37h44l-9-11H88l-9 11Z M95 25v-7" />
        <path class="art-route" d="M16 77c26-14 45 13 77 0s45-2 49 0" />
        <circle class="art-spark" cx="128" cy="24" r="7" />
      </g>
      <g v-else-if="step === 2">
        <path d="M23 45h64a32 27 0 0 1-64 0Z M40 73h30 M33 26c-5 6 6 8 0 14 M52 22c-5 6 6 8 0 14 M71 26c-5 6 6 8 0 14" />
        <path d="M108 42h22v20a11 11 0 0 1-22 0V42Zm22 4h5a7 7 0 0 1 0 14h-5 M102 77h35" />
        <path class="art-spark" d="M119 19v10 M114 24h10" />
      </g>
      <g v-else>
        <path d="M40 22h55v55H40z M53 37h27 M53 47h19 M53 57h24" />
        <path class="art-route" d="M96 67c12 5 30 4 35-13" />
        <circle cx="121" cy="33" r="16" /><path d="m113 33 5 5 11-11" />
        <path class="art-spark" d="M23 35v10 M18 40h10" />
      </g>
    </g>
  </svg>
</template>
