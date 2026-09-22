<script setup>
import { ref, onActivated, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { CopyDocument, Refresh, Delete } from '@element-plus/icons-vue'

const tableRef = ref(null)
const items = ref([])
const loading = ref(false)
const search = ref('')
const refreshing = ref({})
const page = ref(1)
const total = ref(0)
const pageSize = ref(20)
const selected = ref([])

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (search.value.trim()) params.set('search', search.value.trim())
    params.set('limit', pageSize.value)
    params.set('offset', (page.value - 1) * pageSize.value)
    const r = await fetch('/api/tempo/store?' + params.toString())
    const d = await r.json()
    if (d.ok) {
      items.value = d.items || []
      total.value = d.total || 0
    }
    else ElMessage.error(d.error || '加载失败')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  load()
}

watch(search, (v) => {
  if (!v) { page.value = 1; load() }
})

async function refreshMail(item) {
  if (refreshing.value[item.email]) return
  refreshing.value = { ...refreshing.value, [item.email]: true }
  try {
    const r = await fetch('/api/tempo/store/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: item.email, app_token: item.app_token }),
    })
    const d = await r.json()
    if (!d.ok) {
      ElMessage.error(d.error || '刷新失败')
      return
    }
    item._messages = d.messages || []
    if (!item._messages.length) {
      ElMessage.info('暂无新邮件')
      return
    }
    // 展开行显示邮件内容
    tableRef.value?.toggleRowExpansion(item, true)
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    refreshing.value = { ...refreshing.value, [item.email]: false }
  }
}

function copyText(text) {
  navigator.clipboard.writeText(text)
  ElMessage.success('已复制')
}

function expiredLabel(row) {
  if (row.expired === true) return '已过期'
  if (row.expired === null) return '未知'
  return '有效'
}

async function deleteSelected() {
  if (!selected.value.length) return
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${selected.value.length} 个邮箱？`, '确认删除', {
      type: 'warning',
    })
    const r = await fetch('/api/tempo/store/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emails: selected.value.map(s => s.email) }),
    })
    const d = await r.json()
    if (d.ok) {
      ElMessage.success(`已删除 ${d.deleted} 个`)
      selected.value = []
      load()
    } else {
      ElMessage.error(d.error || '删除失败')
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

onActivated(() => load())
load()
</script>

<template>
  <div class="page-container">
    <div class="page-header">
      <span class="section-title">邮箱库</span>
      <div class="header-actions">
        <el-input
          v-model="search" placeholder="搜索邮箱" clearable
          style="width: 240px" @keyup.enter="onSearch" @clear="onSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>
        <el-button type="primary" @click="load" :loading="loading">刷新</el-button>
        <el-button type="danger" plain :disabled="!selected.length" @click="deleteSelected">
          <el-icon><Delete /></el-icon>删除选中 ({{ selected.length }})
        </el-button>
      </div>
    </div>

    <el-table :data="items" v-loading="loading" stripe style="width: 100%"
      ref="tableRef" :row-key="row => row.email" @selection-change="v => selected.value = v">
      <el-table-column type="selection" width="42" />
      <el-table-column type="expand" width="36">
        <template #default="{ row }">
          <div v-if="row._messages && row._messages.length" class="msg-list">
            <div v-for="(m, i) in row._messages.slice().reverse()" :key="m.id" class="msg-item">
              <div class="msg-head">
                <el-tag v-if="m.otp" type="success" size="small" class="otp-tag"
                  style="cursor:pointer" @click="copyText(m.otp)">
                  {{ m.otp }}
                </el-tag>
                <span class="msg-subject">{{ m.subject || '(无主题)' }}</span>
                <span class="msg-from">{{ m.from }}</span>
              </div>
              <pre class="msg-body">{{ m.body || m.preview || '' }}</pre>
            </div>
          </div>
          <div v-else class="empty-hint">暂无邮件</div>
        </template>
      </el-table-column>

      <el-table-column prop="email" label="邮箱" min-width="260" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="mono">{{ row.email }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.expired === true" type="danger" size="small">已过期</el-tag>
          <el-tag v-else-if="row.expired === null" type="info" size="small">未知</el-tag>
          <el-tag v-else type="success" size="small">有效</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="domain" label="域名" width="150" />
      <el-table-column prop="created_at" label="创建时间" width="170" />
      <el-table-column label="过期时间" width="170">
        <template #default="{ row }">
          <span v-if="row.expire_at" :class="{ 'expired-time': row.expired === true }">{{ row.expire_at }}</span>
          <span v-else class="empty-hint">—</span>
        </template>
      </el-table-column>
      <el-table-column prop="source" label="来源" width="80" />
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button
            size="small" type="primary" text
            @click="refreshMail(row)"
            :loading="refreshing[row.email]"
          >
            <el-icon><Refresh /></el-icon>刷新接码
          </el-button>
          <el-button
            size="small" text type="primary"
            @click="copyText(row.email)"
          >
            <el-icon><CopyDocument /></el-icon>
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && items.length === 0 && total === 0" description="暂无保存的邮箱，注册后会自动保存" :image-size="60" />

    <div class="pager-wrap">
      <el-pagination
        v-if="total > pageSize"
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </div>
  </div>
</template>

<style scoped>
.page-container { padding: 16px; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.header-actions { display: flex; gap: 8px; }
.mono { font-family: 'SF Mono', 'Menlo', 'Consolas', monospace; font-size: 13px; }
/* 展开行内容全宽可读 */
:deep(.el-table__expanded-cell) { padding: 16px 20px; }
.msg-list { padding: 4px 0; display: flex; flex-direction: column; gap: 12px; }
.msg-item { border: 1px solid #eee; border-radius: 8px; padding: 12px 16px; background: #fafafa; }
.msg-item:last-child { border-bottom: 1px solid #eee; }
.msg-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.otp-tag { font-family: 'SF Mono', 'Menlo', 'Consolas', monospace; font-weight:bold; }
.msg-subject { font-weight: 500; font-size: 13px; }
.msg-from { color: #999; font-size: 12px; margin-left: auto; }
.msg-body { font-size: 13px; color: #333; line-height: 1.7; white-space: pre-wrap; word-break: break-word; margin: 0; max-height: 320px; overflow: auto; }
.empty-hint { color: #999; font-size: 13px; padding: 8px 0; }
.expired-time { color: #f56c6c; text-decoration: line-through; }
.pager-wrap { display: flex; justify-content: center; margin-top: 16px; }
</style>