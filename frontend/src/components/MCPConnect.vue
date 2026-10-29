<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { AccountUser } from '../api'
const props = defineProps<{ brand: string; connectionId: string; user: AccountUser }>()
const emit = defineEmits<{ connected: []; switchAccount: [] }>()
const name = ref('旅行 MCP')
const ready = ref(false)
const done = ref(false)
const busy = ref(false)
const error = ref('')
async function read(response: Response) {
  const body = await response.json()
  if (!response.ok) throw new Error(body.detail || '授权暂时无法完成，请稍后重试。')
  return body
}
onMounted(async () => {
  try {
    const info = await read(await fetch(`/api/mcp/connect/${encodeURIComponent(props.connectionId)}`, { cache: 'no-store' }))
    if (info.approved && !info.account_matches) throw new Error('该链接已用于其他账号，请回到 MCP 重新获取授权链接。')
    name.value = info.client_name
    done.value = info.approved
    ready.value = !info.approved
  } catch (failure) { error.value = (failure as Error).message }
})
async function approve() {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    await read(await fetch('/api/mcp/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ connection_id: props.connectionId }) }))
    done.value = true; ready.value = false; emit('connected')
    // 地址栏移除一次性授权编号；刷新仅保留完成提示。
    history.replaceState(null, '', location.pathname + '#mcp-connected')
  } catch (failure) { error.value = (failure as Error).message }
  finally { busy.value = false }
}
</script>

<template>
  <section class="mcp-connect" aria-labelledby="mcp-connect-title">
    <div class="mcp-card">
      <p class="mcp-eyebrow">{{ brand }} · MCP</p>
      <h1 id="mcp-connect-title">{{ done ? '账号已连接' : '连接你的旅行账号' }}</h1>
      <template v-if="done">
        <p>已连接账号 <strong>{{ user.username }}</strong>。回到聊天即可使用，之后无需重复登录 MCP。</p>
        <p>这个浏览器将保留 30 天登录，点行程链接就能直接查看。</p>
        <a href="/">进入{{ brand }}</a>
      </template>
      <template v-else>
        <p>将 <strong>{{ name }}</strong> 连接到账号 <strong>{{ user.username }}</strong>。</p>
        <p>允许它生成和查看你的行程、保存历史，并在你确认后修改餐次。聊天 AI 不会收到你的密码。</p>
        <p class="mcp-note">只授权你刚刚配置的 MCP。授权后，同一浏览器保留 30 天登录；可在聊天中要求解绑。</p>
        <button v-if="ready" :disabled="busy" @click="approve">{{ busy ? '正在连接…' : '授权并继续' }}</button>
        <button v-if="ready" class="mcp-switch" :disabled="busy" @click="emit('switchAccount')">切换账号</button>
      </template>
      <p v-if="error" class="mcp-error" role="alert">{{ error }}</p>
    </div>
  </section>
</template>

<style scoped>
.mcp-connect { position: fixed; inset: 64px 0 0; z-index: 1300; display: grid; place-items: center; overflow-y: auto; padding: 24px; background: #f2f3f0; }
.mcp-card { width: min(480px, 100%); box-sizing: border-box; background: #fff; padding: 32px; border-radius: 6px; box-shadow: 0 24px 64px -32px #15161366; }
.mcp-eyebrow { color: #747572; font-size: 13px; letter-spacing: .1em; }
h1 { margin: 12px 0 20px; font-size: 28px; color: #2a2b28; }
p { margin: 12px 0; color: #575855; font-size: 15px; line-height: 1.8; }
.mcp-note { font-size: 13px; }
button, a { display: block; width: 100%; box-sizing: border-box; margin-top: 20px; padding: 14px; border: 0; border-radius: 6px; background: #151613; color: #fff; text-align: center; font-size: 15px; text-decoration: none; cursor: pointer; }
button:disabled { opacity: .6; cursor: wait; }
.mcp-switch { margin-top: 8px; background: #f2f3f0; color: #151613; }
.mcp-error { color: #b73931; }
</style>
