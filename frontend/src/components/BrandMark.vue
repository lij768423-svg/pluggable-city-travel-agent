<script setup lang="ts">
// 城市标志随行程切换；杭州雷峰塔，南京城门与秦淮水纹，苏州月洞门与水巷。
withDefaults(defineProps<{ city?: string }>(), { city: 'hangzhou' })
</script>

<template>
  <svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
    <rect x=".5" y=".5" width="31" height="31" rx="6" fill="#fff" stroke="#dedfda" />
    <g v-if="city === 'hangzhou'" data-city-mark="hangzhou">
    <circle cx="23.6" cy="8.8" r="2.4" fill="#b93b28" />
    <g fill="#c9cac4">
      <rect x="13.4" y="8.9" width="5.2" height="2.2" />
      <rect x="12.9" y="12.2" width="6.2" height="2.4" />
      <rect x="12.4" y="15.7" width="7.2" height="2.6" />
      <rect x="11.8" y="19.4" width="8.4" height="2.8" />
    </g>
    <g fill="#151613">
      <rect x="15.5" y="4.4" width="1" height="3" rx=".5" /><circle cx="16" cy="4.3" r=".9" />
      <path d="M11.6 9.1 12.7 8.4 16 7l3.3 1.4 1.1.7Z" />
      <path d="M10.8 12.4 11.9 11.6 16 10l4.1 1.6 1.1.8Z" />
      <path d="M10 15.9 11.2 15 16 13.2l4.8 1.8 1.2.9Z" />
      <path d="M9.2 19.6 10.5 18.6 16 16.6l5.5 2 1.3 1Z" />
      <rect x="15.1" y="20.3" width="1.8" height="1.9" rx=".9" />
      <rect x="10" y="22.2" width="12" height="1.2" rx=".6" />
    </g>
    <path d="M7 25.6c1.5-.9 3-.9 4.5 0s3 .9 4.5 0 3-.9 4.5 0 3 .9 4.5 0" fill="none" stroke="#151613" stroke-width="1.4" stroke-linecap="round" />
    <path d="M10.2 28.1c1.2-.7 2.4-.7 3.6 0s2.4.7 3.6 0 2.4-.7 3.6 0" fill="none" stroke="#9a9c95" stroke-width="1.2" stroke-linecap="round" />
    </g>
    <g v-else-if="city === 'nanjing'" data-city-mark="nanjing">
      <circle cx="24" cy="8" r="2.4" fill="#b93b28" />
      <path d="M5 20h22v5H5Z" fill="#c9cac4" /><path d="M5 18h3v3H5Zm19 0h3v3h-3Z" fill="#9a9c95" />
      <path d="M10 15h12v10H10Z" fill="#9a9c95" /><path d="M13.5 25v-5a2.5 2.5 0 0 1 5 0v5Z" fill="#fff" />
      <path d="M11 11h10v5H11Z" fill="#e9eae6" /><path d="m8 12 3-3h10l3 3Z" fill="#151613" />
      <path d="M13 7h6v3h-6Z" fill="#c9cac4" /><path d="m11 8 5-4 5 4Z" fill="#151613" />
      <path d="M14 13h1v2h-1Zm3 0h1v2h-1Z" fill="#b93b28" />
      <path d="M7 27q3-2 6 0t6 0 6 0" fill="none" stroke="#151613" stroke-width="1.3" stroke-linecap="round" />
    </g>
    <g v-else-if="city === 'suzhou'" data-city-mark="suzhou">
      <circle cx="24.5" cy="7.5" r="2.2" fill="#b93b28" />
      <path d="M5 11h22v13H5Z" fill="#e9eae6" /><path d="M4 11.2 5.5 8.6h21L28 11.2Z" fill="#151613" />
      <circle cx="16" cy="18" r="5" fill="#c9cac4" /><path d="M11 24v-6a5 5 0 0 1 10 0v6Z" fill="#c9cac4" />
      <path d="M7.5 14h3v3h-3Zm14 0h3v3h-3Z" fill="#9a9c95" />
      <path d="M7 27q3-2 6 0t6 0 6 0" fill="none" stroke="#151613" stroke-width="1.3" stroke-linecap="round" />
    </g>
    <g v-else data-city-mark="neutral" fill="none" stroke="#696b64" stroke-width="1.3"><circle cx="16" cy="16" r="10" /><path d="m12 20 2-6 6-2-2 6Z" /></g>
  </svg>
</template>
