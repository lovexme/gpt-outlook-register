<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { importAccounts } from '@/api/accounts'
import { useStatsStore } from '@/stores/stats'
import { useRuntimeStore } from '@/stores/runtime'

const statsStore = useStatsStore()
const runtime = useRuntimeStore()

const text = ref('')
const loading = ref(false)
const result = ref('')

async function doImport() {
  if (!text.value.trim()) {
    ElMessage.warning('请输入要导入的接码号')
    return
  }
  loading.value = true
  result.value = ''
  try {
    const r = await importAccounts(text.value.trim())
    result.value = `解析 ${r.parsed} 行，新增 ${r.inserted}，更新 ${r.updated}，跳过 ${r.skipped}`
    ElMessage.success('导入完成')
    text.value = ''
    statsStore.refresh()
    runtime.bumpData()
  } catch (e) {
    result.value = '导入失败: ' + e.message
    ElMessage.error(e.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="page">
    <el-card shadow="never">
      <template #header><span class="section-title" style="margin: 0">导入邮箱</span></template>
      <p class="hint">
        每行一个，4 段格式（用 <code>----</code> 分隔）：<br />
        <code>email----password----client_id----refresh_token</code>
      </p>
      <el-input
        v-model="text"
        type="textarea"
        :rows="12"
        class="mono"
        placeholder="charles123@outlook.jp----P@ssw0rd----9e5f94bc-...----M.C538_..."
      />
      <div style="margin-top: 12px; display: flex; align-items: center; gap: 12px">
        <el-button type="primary" :loading="loading" @click="doImport">导入</el-button>
        <span class="hint">{{ result }}</span>
      </div>
    </el-card>
  </div>
</template>
