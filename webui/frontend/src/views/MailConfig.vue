<script setup>
import { onActivated, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getMailConfig, saveMailConfig, testMail } from '@/api/settings'
import FooterToolbar from '@/components/FooterToolbar.vue'

const source = ref('outlook')
const cfApiUrl = ref('')
const cfDomain = ref('')
const cfAdminToken = ref('')
const tokenPlaceholder = ref('Worker 配置的 ADMIN_PASSWORDS')
const saving = ref(false)
const testing = ref(false)

async function load() {
  try {
    const { config } = await getMailConfig()
    source.value = config.mail_source || 'outlook'
    cfApiUrl.value = config.cf_api_url || ''
    cfDomain.value = config.cf_domain || ''
    cfAdminToken.value = ''
    tokenPlaceholder.value = config.cf_admin_token === '***'
      ? '已设置（留空不修改）' : 'Worker 配置的 ADMIN_PASSWORDS'
  } catch (e) { ElMessage.error(e.message) }
}

async function save() {
  const isCf = source.value === 'cf_temp'
  saving.value = true
  try {
    await saveMailConfig({
      mail_source: source.value,
      cf_api_url: isCf ? cfApiUrl.value.trim() : '',
      cf_admin_token: isCf ? (cfAdminToken.value.trim() || '***') : '***',
      cf_domain: isCf ? cfDomain.value.trim() : '',
    })
    ElMessage.success('保存成功')
    load()
  } catch (e) { ElMessage.error(e.message) }
  finally { saving.value = false }
}

async function test() {
  testing.value = true
  try { const r = await testMail(); ElMessage.success(r.message || '连通正常') }
  catch (e) { ElMessage.error(e.message) }
  finally { testing.value = false }
}

onActivated(() => load())
</script>

<template>
  <div class="page">
    <el-card shadow="never" style="max-width: 720px">
      <template #header><span class="section-title" style="margin: 0">邮箱来源配置</span></template>
      <p class="hint">
        OpenAI 注册需要邮箱收 OTP。可选 Outlook 接码池（从邮箱列表 claim），
        或用自建 CF Worker catch-all 邮箱。
      </p>
      <el-form label-position="top">
        <el-form-item label="邮箱来源">
          <el-radio-group v-model="source">
            <el-radio value="outlook">Outlook 接码池</el-radio>
            <el-radio value="cf_temp">CF Temp Email（自建 catch-all）</el-radio>
          </el-radio-group>
        </el-form-item>

        <template v-if="source === 'cf_temp'">
          <el-form-item label="API URL（Worker 部署地址）">
            <el-input v-model="cfApiUrl" placeholder="https://mail.example.com" />
          </el-form-item>
          <el-form-item label="Admin Token（Worker 环境变量 ADMIN_PASSWORDS）">
            <el-input v-model="cfAdminToken" type="password" show-password :placeholder="tokenPlaceholder" />
          </el-form-item>
          <el-form-item label="Catch-all 域名">
            <el-input v-model="cfDomain" placeholder="example.com" />
          </el-form-item>
          <el-alert
            type="warning" :closable="false" show-icon
            title="需在 Cloudflare Email Routing 配置 catch-all 转发到 Worker，否则收不到邮件。"
          />
        </template>

      </el-form>
    </el-card>

    <FooterToolbar>
      <template #left>邮箱来源：{{ source === 'cf_temp' ? 'CF Temp Email' : 'Outlook 接码池' }}</template>
      <el-button v-if="source === 'cf_temp'" :loading="testing" @click="test">测试 CF 连通性</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
    </FooterToolbar>
  </div>
</template>
