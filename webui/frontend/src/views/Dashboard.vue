<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'
import { useProxyStore } from '@/stores/proxy'
import { getDetailedStats } from '@/api/accounts'
import StatusDot from '@/components/StatusDot.vue'

const router = useRouter()
const { stats } = storeToRefs(useStatsStore())
const { autoStatus } = storeToRefs(useRuntimeStore())
const proxyStore = useProxyStore()
const { health, healthLoading, healthyCount, unhealthyCount, removedCount, totalInPool, syncError } = storeToRefs(proxyStore)

const cards = computed(() => [
  { label: '总计', value: stats.value.total, color: 'var(--brand)', icon: 'Files' },
  { label: '可用 available', value: stats.value.available, color: '#4caf50', icon: 'CircleCheck' },
  { label: '进行中 in_use', value: stats.value.in_use, color: '#ff9800', icon: 'Loading' },
  { label: '已完成 done', value: stats.value.done, color: '#2196f3', icon: 'Select' },
  { label: '失败 failed', value: stats.value.failed, color: '#e53935', icon: 'CircleClose' },
])

const autoStateLabel = computed(() => ({
  stopped: '未运行', running: '运行中', paused: '已暂停',
}[autoStatus.value.state] || autoStatus.value.state))
const autoStateType = computed(() => ({
  stopped: 'info', running: 'success', paused: 'warning',
}[autoStatus.value.state] || 'info'))

// ── 详细统计 ──
const detailed = ref(null)
let detailedTimer = null

async function refreshDetailed() {
  try {
    const { stats: s } = await getDetailedStats()
    if (s) detailed.value = s
  } catch (e) {
    console.error('detailed stats refresh:', e)
  }
}

const today = computed(() => detailed.value?.today || { success: 0, fail: 0 })
const trend = computed(() => detailed.value?.trend_7d || [])
const hourly = computed(() => detailed.value?.hourly || [])
const proxies = computed(() => detailed.value?.proxy_ranking || [])
const reasons = computed(() => detailed.value?.failure_reasons || {})

const maxTrend = computed(() => {
  const m = Math.max(1, ...trend.value.map(d => d.success + d.fail))
  return m
})
const maxHour = computed(() => {
  const m = Math.max(1, ...hourly.value.map(h => h.success + h.fail))
  return m
})
const reasonEntries = computed(() => {
  const map = {
    network: '网络', account: '账号', unknown: '未知', other: '其他', uncategorized: '未分类',
  }
  return Object.entries(reasons.value).map(([k, v]) => ({ key: k, label: map[k] || k, value: v }))
})
const reasonTotal = computed(() => reasonEntries.value.reduce((s, r) => s + r.value, 0))

// ── 代理健康状态（后端每 5 分钟自动检测，这里展示最近一次结果）──
const healthStateLabel = computed(() => {
  const s = health.value.checker_status || {}
  if (s.in_progress) return '检测中…'
  if (!health.value.last_check_at) return '尚未检测'
  return '已检测'
})
const healthStateType = computed(() => (health.value.checker_status || {}).in_progress ? 'warning' : 'success')
const lastCheckText = computed(() => {
  if (!health.value.last_check_at) return '—'
  const d = new Date(health.value.last_check_at * 1000)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
})
const healthyProxies = computed(() => {
  const proxies = health.value.proxies || {}
  return Object.entries(proxies).filter(([, v]) => v.ok).map(([p, v]) => ({ proxy: p, latency_ms: v.latency_ms, ip: v.ip })).slice(0, 8)
})
const unhealthyProxies = computed(() => {
  const proxies = health.value.proxies || {}
  const failed = Object.entries(proxies).filter(([, v]) => !v.ok).map(([p, v]) => ({ proxy: p, error: v.error, consecutive_fails: v.consecutive_fails }))
  const removed = (health.value.removed || []).map((p) => ({ proxy: p, error: '已自动移除（连续失败）', consecutive_fails: 3 }))
  return [...failed, ...removed].slice(0, 8)
})

let healthTimer = null
onMounted(() => {
  refreshDetailed()
  detailedTimer = setInterval(refreshDetailed, 10000)
  // 代理健康：立即拉一次，之后每 30s 刷新（后端每 5 分钟自动检测）
  proxyStore.fetchHealth()
  healthTimer = setInterval(() => proxyStore.fetchHealth(), 30000)
})
onUnmounted(() => {
  if (detailedTimer) clearInterval(detailedTimer)
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<template>
  <div class="page">
    <el-row :gutter="16">
      <el-col v-for="c in cards" :key="c.label" :xs="12" :sm="8" :md="4" style="margin-bottom: 16px">
        <el-card class="stat-card" shadow="hover">
          <div style="display: flex; align-items: center; justify-content: space-between">
            <div>
              <div class="stat-value" :style="{ color: c.color }">{{ c.value }}</div>
              <div class="stat-label">{{ c.label }}</div>
            </div>
            <el-icon :size="30" :style="{ color: c.color, opacity: 0.5 }"><component :is="c.icon" /></el-icon>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">今日注册</span></template>
          <div style="display: flex; gap: 24px; align-items: center">
            <div>
              <div class="stat-value" style="color: #4caf50">{{ today.success }}</div>
              <div class="stat-label">成功</div>
            </div>
            <div>
              <div class="stat-value" style="color: #e53935">{{ today.fail }}</div>
              <div class="stat-label">失败</div>
            </div>
            <div>
              <div class="stat-value" style="color: var(--brand)">
                {{ (today.success + today.fail) ? Math.round(today.success / (today.success + today.fail) * 100) : 0 }}%
              </div>
              <div class="stat-label">成功率</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">自动跑号状态</span></template>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="状态"><StatusDot :type="autoStateType" :text="autoStateLabel" /></el-descriptions-item>
            <el-descriptions-item label="并发">{{ autoStatus.concurrency || 1 }}</el-descriptions-item>
            <el-descriptions-item label="成功">{{ autoStatus.registered_ok || 0 }}</el-descriptions-item>
            <el-descriptions-item label="失败">{{ autoStatus.registered_fail || 0 }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">近7天注册趋势</span></template>
          <div style="display: flex; align-items: flex-end; gap: 8px; height: 180px; padding-top: 8px">
            <div v-for="d in trend" :key="d.date" style="flex:1; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%">
              <div style="display:flex; gap:2px; align-items:flex-end; height:130px">
                <div
                  :style="{ height: (d.success / maxTrend * 130) + 'px', width: '10px', background: '#4caf50', borderRadius: '2px' }"
                  :title="`成功 ${d.success}`"></div>
                <div
                  :style="{ height: (d.fail / maxTrend * 130) + 'px', width: '10px', background: '#e53935', borderRadius: '2px' }"
                  :title="`失败 ${d.fail}`"></div>
              </div>
              <div style="font-size:12px; color:#909399; margin-top:4px">{{ d.date }}</div>
            </div>
          </div>
          <div style="display:flex; gap:16px; margin-top:8px; font-size:12px; color:#909399">
            <span><span style="display:inline-block;width:10px;height:10px;background:#4caf50;border-radius:2px;margin-right:4px"></span>成功</span>
            <span><span style="display:inline-block;width:10px;height:10px;background:#e53935;border-radius:2px;margin-right:4px"></span>失败</span>
          </div>
        </el-card>
      </el-col>
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">今日按小时成功率</span></template>
          <div style="display: flex; align-items: flex-end; gap: 3px; height: 180px; padding-top: 8px; overflow-x: auto">
            <div v-for="h in hourly" :key="h.hour" style="flex:1; min-width:14px; display:flex; flex-direction:column; align-items:center; justify-content:flex-end; height:100%">
              <div
                :style="{ height: ((h.success + h.fail) / maxHour * 130) + 'px', width: '100%', background: (h.success + h.fail) ? '#409eff' : '#f0f2f5', borderRadius: '2px' }"
                :title="`${h.hour}时: 成功 ${h.success}, 失败 ${h.fail}, 成功率 ${h.rate}%`"></div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">代理成功率排名</span></template>
          <el-table :data="proxies" size="small" max-height="300" empty-text="暂无代理使用记录">
            <el-table-column label="代理" prop="proxy" show-overflow-tooltip />
            <el-table-column label="成功" prop="success" width="70" align="center" />
            <el-table-column label="失败" prop="fail" width="70" align="center" />
            <el-table-column label="成功率" width="90" align="center">
              <template #default="{ row }">
                <el-tag :type="row.rate >= 60 ? 'success' : (row.rate >= 30 ? 'warning' : 'danger')" size="small">{{ row.rate }}%</el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :md="12" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">失败原因分布</span></template>
          <div v-if="reasonTotal > 0" style="display: flex; flex-direction: column; gap: 12px; padding-top: 8px">
            <div v-for="r in reasonEntries" :key="r.key">
              <div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px">
                <span>{{ r.label }}</span><span>{{ r.value }} ({{ Math.round(r.value / reasonTotal * 100) }}%)</span>
              </div>
              <el-progress :percentage="Math.round(r.value / reasonTotal * 100)" :stroke-width="12" :show-text="false"
                :color="{ network: '#e53935', account: '#ff9800', unknown: '#909399', other: '#607d8b', uncategorized: '#9c27b0' }[r.key] || '#409eff'" />
            </div>
          </div>
          <el-empty v-else description="暂无失败记录" :image-size="60" />
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="24" style="margin-bottom: 16px">
        <el-card shadow="never">
          <template #header><span class="section-title" style="margin: 0">快捷操作</span></template>
          <el-space wrap>
            <el-button @click="router.push('/import')"><el-icon><Upload /></el-icon>导入邮箱</el-button>
            <el-button @click="router.push('/register')"><el-icon><VideoPlay /></el-icon>单次注册</el-button>
            <el-button @click="router.push('/pool')"><el-icon><Files /></el-icon>邮箱列表</el-button>
            <el-button @click="router.push('/registered')"><el-icon><CircleCheck /></el-icon>注册结果</el-button>
            <el-button @click="router.push('/auto')"><el-icon><Loading /></el-icon>自动批量</el-button>
          </el-space>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>