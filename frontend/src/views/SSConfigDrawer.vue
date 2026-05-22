<template>
  <el-drawer v-model="visible" title="节点配置" size="640px" @open="load">
    <div v-loading="loading" class="ss-form">
      <el-form :model="form" label-width="92px" label-position="right">
        <!-- 基础 -->
        <div class="section">
          <div class="section-title">基础</div>

          <el-form-item label="启用节点">
            <el-switch v-model="form.enabled" />
            <span class="status-text" :class="{ on: form.enabled }">
              {{ form.enabled ? '已启用' : '已关闭' }}
            </span>
          </el-form-item>

          <el-form-item label="监听 IP">
            <el-input v-model="form.listen_addr" placeholder="留空 = 全部接口（::）" />
            <div class="hint">留空或 <code>::</code> 监听 v4+v6 · <code>0.0.0.0</code> 仅 v4</div>
          </el-form-item>

          <el-form-item label="监听端口">
            <el-input-number
              v-model="form.listen_port"
              :min="1"
              :max="65535"
              :controls="false"
              style="width: 120px"
            />
            <span class="hint inline">1 – 65535</span>
          </el-form-item>
        </div>

        <!-- Shadowsocks -->
        <div class="section">
          <div class="section-title">Shadowsocks</div>

          <el-form-item label="加密方式">
            <el-select v-model="form.method" style="width: 100%">
              <el-option
                v-for="m in options.methods"
                :key="m"
                :label="m"
                :value="m"
              />
            </el-select>
          </el-form-item>

          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password>
              <template #append>
                <el-button type="primary" plain @click="genPassword">生成</el-button>
              </template>
            </el-input>
            <div class="hint">点「生成」按当前加密方式自动产出合法字节数</div>
          </el-form-item>
        </div>

        <!-- DNS -->
        <div class="section">
          <div class="section-title">DNS</div>

          <el-form-item label="主 DNS">
            <el-input v-model="form.dns_primary" placeholder="https://1.1.1.1/dns-query" />
          </el-form-item>
          <el-form-item label="备用 DNS">
            <el-input v-model="form.dns_backup" placeholder="留空 = 不使用备用" />
          </el-form-item>
          <div class="dns-hint">
            <code>https://</code> DoH ·
            <code>tls://</code> DoT ·
            <code>quic://</code> DoQ ·
            <code>udp://</code> 明文 ·
            纯 IP 走 UDP
          </div>

          <el-form-item label="解析策略">
            <el-select v-model="form.dns_strategy" style="width: 100%">
              <el-option label="仅 IPv4（海外节点推荐）" value="ipv4_only" />
              <el-option label="仅 IPv6" value="ipv6_only" />
              <el-option label="优先 IPv4" value="prefer_ipv4" />
              <el-option label="优先 IPv6" value="prefer_ipv6" />
            </el-select>
          </el-form-item>
        </div>
      </el-form>

      <el-alert
        v-if="applyStatus.apply_status === 'applied'"
        class="apply-alert"
        type="success"
        :closable="false"
        show-icon
      >
        最后一次应用：{{ fmtTime(applyStatus.applied_at) }}
      </el-alert>
      <el-alert
        v-else-if="applyStatus.apply_status === 'failed'"
        class="apply-alert"
        type="error"
        :closable="false"
        show-icon
      >
        <template #title>上次应用失败</template>
        <pre class="err">{{ applyStatus.apply_error }}</pre>
      </el-alert>

      <div class="actions">
        <el-button @click="preview" :loading="previewing">预览 JSON</el-button>
        <span class="spacer" />
        <el-button @click="save(false)" :loading="saving">仅保存</el-button>
        <el-button type="primary" @click="save(true)" :loading="saving || applying">
          保存并应用
        </el-button>
      </div>
    </div>

    <el-dialog v-model="previewVisible" title="sing-box JSON 预览" width="640px">
      <pre class="preview">{{ previewJson }}</pre>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api.js'

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const applying = ref(false)
const previewing = ref(false)
const previewVisible = ref(false)
const previewJson = ref('')

const nodeId = ref(null)
const form = reactive({
  enabled: false,
  listen_addr: '0.0.0.0',
  listen_port: 8388,
  password: '',
  method: '2022-blake3-aes-128-gcm',
  dns_primary: 'https://1.1.1.1/dns-query',
  dns_backup: 'https://8.8.8.8/dns-query',
  dns_strategy: 'ipv4_only',
})
const options = reactive({ methods: [], dns_strategies: [] })
const applyStatus = reactive({ apply_status: 'never', applied_at: null, apply_error: null })

function open(id) {
  nodeId.value = id
  visible.value = true
}
defineExpose({ open })

async function load() {
  loading.value = true
  try {
    const [optResp, cfgResp] = await Promise.all([
      http.get('/nodes/ss-config/options'),
      http.get(`/nodes/${nodeId.value}/ss-config`),
    ])
    Object.assign(options, optResp.data)
    Object.assign(form, cfgResp.data.config)
    applyStatus.apply_status = cfgResp.data.apply_status
    applyStatus.applied_at = cfgResp.data.applied_at
    applyStatus.apply_error = cfgResp.data.apply_error
  } finally {
    loading.value = false
  }
}

function genPassword() {
  const m = form.method
  let bytes
  if (m === '2022-blake3-aes-128-gcm') {
    bytes = 16
  } else if (m === 'none') {
    ElMessage.warning('加密方式为 none，无需密码')
    return
  } else {
    bytes = 32
  }
  const arr = new Uint8Array(bytes)
  crypto.getRandomValues(arr)
  let bin = ''
  arr.forEach((b) => (bin += String.fromCharCode(b)))
  form.password = btoa(bin)
  ElMessage.success(`已生成 ${bytes} 字节密码`)
}

async function save(thenApply) {
  saving.value = true
  try {
    await http.put(`/nodes/${nodeId.value}/ss-config`, form)
    ElMessage.success('已保存')
    if (thenApply) await apply()
    else visible.value = false
  } catch (e) {
    // 拦截器已 toast
  } finally {
    saving.value = false
  }
}

async function apply() {
  applying.value = true
  try {
    const resp = await http.post(`/nodes/${nodeId.value}/ss-config/apply`, null, {
      timeout: 60000,
    })
    if (resp.data.success) {
      ElMessage.success('已应用到节点 ✓')
      applyStatus.apply_status = 'applied'
      applyStatus.applied_at = resp.data.applied_at
      applyStatus.apply_error = null
      visible.value = false
    } else {
      applyStatus.apply_status = 'failed'
      applyStatus.apply_error = (resp.data.error || '') + '\n' + (resp.data.check_output || resp.data.message || '')
      ElMessageBox.alert(
        `阶段：${resp.data.stage}\n\n${applyStatus.apply_error}`,
        '应用失败',
        { type: 'error' },
      )
    }
  } finally {
    applying.value = false
  }
}

async function preview() {
  previewing.value = true
  try {
    await http.put(`/nodes/${nodeId.value}/ss-config`, form)
    const resp = await http.get(`/nodes/${nodeId.value}/ss-config/preview`)
    previewJson.value = JSON.stringify(resp.data.singbox_config, null, 2)
    previewVisible.value = true
  } finally {
    previewing.value = false
  }
}

function fmtTime(t) {
  if (!t) return '—'
  return new Date(t.endsWith('Z') ? t : t + 'Z').toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.ss-form { padding: 0 20px 12px; }

.section { margin-bottom: 18px; }
.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #6366f1;
  letter-spacing: 0.5px;
  padding: 6px 0 10px;
  margin-bottom: 6px;
  border-bottom: 1px solid #f1f2f5;
}

:deep(.el-form-item) { margin-bottom: 14px; }
:deep(.el-form-item__label) { color: #4b5563; font-weight: 500; }

.hint {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.6;
  margin-top: 4px;
  width: 100%;
}
.hint.inline { margin-left: 10px; margin-top: 0; width: auto; }
.hint code,
.dns-hint code {
  background: #f3f4f6;
  padding: 0 5px;
  border-radius: 3px;
  color: #6366f1;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
}
.dns-hint {
  font-size: 12px;
  color: #9ca3af;
  margin: -4px 0 12px 92px;
  line-height: 1.8;
}

.status-text {
  margin-left: 12px;
  font-size: 12px;
  color: #9ca3af;
}
.status-text.on { color: #10b981; }

.apply-alert { margin-top: 8px; }

.actions {
  display: flex;
  margin-top: 24px;
  padding-top: 16px;
  gap: 8px;
  border-top: 1px solid #f1f2f5;
}
.spacer { flex: 1; }

.preview {
  background: #1f2937;
  color: #d1d5db;
  padding: 16px;
  border-radius: 6px;
  max-height: 480px;
  overflow: auto;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.err {
  white-space: pre-wrap;
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
