<script setup>
import { onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getWebhookConfig, saveWebhookConfig } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const webhookUrl = ref('')
const savedUrl = ref('')
const saving = ref(false)

async function load() {
  try {
    const { config } = await getWebhookConfig()
    webhookUrl.value = config.webhook_url || ''
    savedUrl.value = config.webhook_url || ''
  } catch (e) { ElMessage.error(e.message) }
}

async function save() {
  saving.value = true
  try {
    const r = await saveWebhookConfig({ webhook_url: webhookUrl.value.trim() })
    webhookUrl.value = r.config.webhook_url || ''
    savedUrl.value = r.config.webhook_url || ''
    ElMessage.success('保存成功')
  } catch (e) { ElMessage.error(e.message) }
  finally { saving.value = false }
}

onActivated(() => load())
</script>

<template>
  <div class="page">
    <el-card shadow="never" style="max-width: 760px">
      <template #header>
        <span class="section-title" style="margin: 0">Webhook 通知配置</span>
      </template>
      <p class="hint">
        注册成功/失败时，向指定的 URL 发送 POST 请求推送通知。
        消息格式：<code>{"event":"register_done"|"register_failed", "email":"xxx", "time":timestamp, "error":"原因"}</code>
      </p>
      <el-form label-position="top">
        <el-form-item label="Webhook URL">
          <el-input v-model="webhookUrl" placeholder="https://hooks.example.com/notify" />
        </el-form-item>
        <p class="hint">留空并保存可清除已有配置。Webhook 异步发送，不阻塞注册流程。</p>
      </el-form>
    </el-card>

    <FooterToolbar>
      <template #left>
        {{ savedUrl ? '已配置' : '未配置' }}
      </template>
      <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
    </FooterToolbar>
  </div>
</template>