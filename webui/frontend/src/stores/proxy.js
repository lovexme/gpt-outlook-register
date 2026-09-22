import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { syncProxyPool, getProxyHealth } from '@/api/proxy'

const KEY = 'dango_proxy_pool_v1'
const OLD_FORM_KEY = 'gpt_outlook_register_form_v2'

function parseLines(s) {
  return String(s || '').split('\n').map((x) => x.trim()).filter(Boolean)
}
function dedup(arr) {
  return [...new Set(arr)]
}

// 代理池：独立管理的代理列表，localStorage 持久化。
// 自动跑号时按 worker 顺序轮流取用（后端 /api/auto/start 的 proxy_pool 字段）。
export const useProxyStore = defineStore('proxy', () => {
  let saved = []
  try { saved = (JSON.parse(localStorage.getItem(KEY) || '[]') || []).filter((x) => typeof x === 'string') } catch (_) { saved = [] }
  // 从旧版「全自动批量」页的 autoProxyPool textarea 迁移一次
  if (!saved.length) {
    try {
      const old = JSON.parse(localStorage.getItem(OLD_FORM_KEY) || '{}')
      if (old.autoProxyPool) saved = dedup(parseLines(old.autoProxyPool))
    } catch (_) { /* ignore */ }
  }

  const list = ref(saved)
  const text = computed(() => list.value.join('\n'))
  const count = computed(() => list.value.length)

  // 代理健康状态
  const health = ref({ proxies: {}, last_check_at: 0, healthy: 0, unhealthy: 0, removed: [], total: 0 })
  const healthLoading = ref(false)
  const healthyCount = computed(() => health.value.healthy ?? 0)
  const unhealthyCount = computed(() => health.value.unhealthy ?? 0)
  const removedCount = computed(() => health.value.removed_count ?? 0)
  const totalInPool = computed(() => health.value.total ?? 0)

  const syncError = ref('')
  const syncErrorTimer = ref(null)

  watch(list, (v) => {
    const clean = v.filter((x) => typeof x === 'string')   // 防御：只持久化字符串代理
    try { localStorage.setItem(KEY, JSON.stringify(clean)) } catch (_) {}
    // 同步到后端（防抖：1s 内多次修改只发一次）
    debounceSync(clean)
  }, { deep: true })

  // 防抖同步到后端
  let syncTimer = null
  function debounceSync(v) {
    if (syncTimer) clearTimeout(syncTimer)
    syncTimer = setTimeout(async () => {
      try {
        await syncProxyPool(v)
        syncError.value = ''
      } catch (e) {
        syncError.value = e?.message || '代理池同步失败'
        if (syncErrorTimer.value) clearTimeout(syncErrorTimer.value)
        syncErrorTimer.value = setTimeout(() => { syncError.value = '' }, 8000)
      }
    }, 1000)
  }

  /** 用整段文本覆盖代理池（自动去重）。返回 { added, duplicated } 供提示。 */
  function setFromText(s) {
    const parsed = parseLines(s)
    const unique = dedup(parsed)
    list.value = unique
    return { total: parsed.length, kept: unique.length, duplicated: parsed.length - unique.length }
  }

  /** 追加一批（去重合并）。 */
  function append(s) {
    const merged = dedup([...list.value, ...parseLines(s)])
    const added = merged.length - list.value.length
    list.value = merged
    return { added }
  }

  function remove(proxy) {
    list.value = list.value.filter((x) => x !== proxy)
  }
  function clear() {
    list.value = []
  }

  /** 从后端获取代理健康状态。 */
  async function fetchHealth() {
    healthLoading.value = true
    try {
      const res = await getProxyHealth()
      if (res.ok) {
        health.value = {
          proxies: res.proxies || {},
          last_check_at: res.last_check_at || 0,
          healthy: res.healthy || 0,
          unhealthy: res.unhealthy || 0,
          removed: res.removed || [],
          removed_count: res.removed_count || 0,
          total: res.total || 0,
          checker_status: res.checker_status || {},
        }
      }
    } catch (_) { /* silent */ }
    finally { healthLoading.value = false }
  }

  /** 获取某代理的健康状态。 */
  function getProxyHealthItem(proxy) {
    return health.value.proxies[proxy] || null
  }

  return {
    list, text, count,
    health, healthLoading, healthyCount, unhealthyCount, removedCount, totalInPool,
    syncError,
    setFromText, append, remove, clear,
    fetchHealth, getProxyHealthItem,
  }
})

/**
 * 判断代理格式是否合法：[scheme://][user:pass@]host:port
 * 协议可省略——省略时 curl 按 HTTP 代理处理，所以裸写 host:port 也算合法。
 */
export function isValidProxy(p) {
  return /^((socks5h?|socks4|https?):\/\/)?\S+:\d+$/i.test(p.trim())
}

/** 该代理生效的协议类型（用于提示：未写协议默认 http）。 */
export function proxyScheme(p) {
  const m = /^(socks5h?|socks4|https?):\/\//i.exec(p.trim())
  return m ? m[1].toLowerCase() : 'http(默认)'
}