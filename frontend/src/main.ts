import { createApp } from 'vue'
import 'leaflet/dist/leaflet.css'
import '@fontsource-variable/geist'
import '@fontsource-variable/noto-sans-sc'
import './style.css'
import App from './App.vue'

// 启动页沿用预载画面的淡入动画，从预载画面动画的实际进度接着播，接替时不闪
const splashTime = Number(document.getElementById('boot-splash')?.getAnimations()[0]?.currentTime ?? 800)
document.documentElement.style.setProperty('--boot-delay', `${-Math.round(splashTime)}ms`)
// 带 data-fade 的图片下载完成（或失败）后再淡入，不在过渡中途突然出现
for (const type of ['load', 'error']) {
  document.addEventListener(type, event => {
    const target = event.target
    if (target instanceof HTMLImageElement && target.hasAttribute('data-fade')) target.classList.add('is-loaded')
  }, true)
}
createApp(App).mount('#app')
document.getElementById('boot-splash')?.remove()
