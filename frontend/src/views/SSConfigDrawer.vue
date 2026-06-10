<template>
  <el-drawer
    v-model="visible"
    title="节点配置"
    size="560px"
    direction="rtl"
    :modal="true"
    class="ss-drawer"
    @open="load"
  >
    <div v-loading="loading" class="ss-form">
      <el-form :model="form" label-width="92px" label-position="right" @submit.prevent>
        <!-- 基础 -->
        <div class="section">
          <div class="section-title">基础</div>

          <el-form-item label="启用节点">
            <el-switch v-model="form.enabled" />
          </el-form-item>

          <el-form-item label="入站协议">
            <el-radio-group v-model="form.protocol">
              <el-radio value="shadowsocks">Shadowsocks</el-radio>
              <el-radio value="socks">SOCKS5</el-radio>
            </el-radio-group>
          </el-form-item>

          <el-form-item label="监听 IP">
            <el-input v-model="form.listen_addr" placeholder="留空 = 全部接口" />
          </el-form-item>

          <el-form-item label="监听端口">
            <el-input
              v-model.number="form.listen_port"
              type="number"
              min="1"
              max="65535"
              placeholder="8388"
              style="width: 110px"
            />
          </el-form-item>
        </div>

        <!-- Shadowsocks -->
        <div v-if="form.protocol === 'shadowsocks'" class="section">
          <div class="section-title">Shadowsocks</div>

          <el-form-item label="加密方式">
            <el-select v-model="form.method" style="width: 100%">
              <el-option v-for="m in options.methods" :key="m" :label="m" :value="m" />
            </el-select>
          </el-form-item>

          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password>
              <template #append>
                <el-button type="primary" plain @click="genPassword">生成</el-button>
              </template>
            </el-input>
          </el-form-item>

          <el-form-item label="启用 UDP">
            <el-switch v-model="form.udp_enabled" />
          </el-form-item>

          <el-form-item v-if="isSS2022" label="NTP 校时">
            <el-switch v-model="form.ntp_enabled" />
          </el-form-item>
          <el-form-item v-if="isSS2022 && form.ntp_enabled" label="NTP 服务器">
            <el-input v-model="form.ntp_server" placeholder="time.apple.com" />
          </el-form-item>
        </div>

        <!-- SOCKS5 -->
        <div v-if="form.protocol === 'socks'" class="section">
          <div class="section-title">SOCKS5</div>

          <el-form-item label="启用认证">
            <el-switch v-model="form.socks_auth_enabled" />
          </el-form-item>

          <el-form-item v-if="form.socks_auth_enabled" label="用户名">
            <el-input v-model="form.socks_username" />
          </el-form-item>
          <el-form-item v-if="form.socks_auth_enabled" label="密码">
            <el-input v-model="form.socks_password" type="password" show-password>
              <template #append>
                <el-button type="primary" plain @click="genSocksPassword">生成</el-button>
              </template>
            </el-input>
          </el-form-item>
        </div>

        <!-- 访问控制 -->
        <div class="section">
          <div class="section-title">访问控制 · IP 白名单</div>
          <el-form-item label="允许的 IP">
            <el-input
              v-model="form.ip_allowlist"
              type="textarea"
              :rows="4"
              placeholder="每行一个 IP 或 CIDR&#10;留空 = 不限制&#10;1.2.3.4&#10;10.0.0.0/8"
              resize="none"
            />
          </el-form-item>
        </div>

        <!-- 高级 -->
        <div class="section">
          <div class="section-title">高级</div>

          <el-form-item label="域名嗅探">
            <el-switch v-model="form.sniff_enabled" />
          </el-form-item>
          <el-form-item v-if="form.sniff_enabled" label="嗅探协议">
            <el-checkbox-group v-model="form.sniff_protocols">
              <el-checkbox v-for="s in options.sniffers" :key="s" :value="s" :label="s.toUpperCase()" />
            </el-checkbox-group>
          </el-form-item>
        </div>

        <!-- DNS -->
        <div class="section">
          <div class="section-title">DNS</div>

          <el-form-item label="主 DNS">
            <el-input v-model="form.dns_primary" placeholder="https://1.1.1.1/dns-query" />
          </el-form-item>
          <el-form-item label="解析策略">
            <el-select v-model="form.dns_strategy" style="width: 100%">
              <el-option label="仅 IPv4" value="ipv4_only" />
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
        最后应用:{{ fmtTime(applyStatus.applied_at) }}
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

      <el-collapse v-if="previewJson" v-model="previewCollapse" class="preview-collapse">
        <el-collapse-item title="sing-box JSON 预览" name="preview">
          <pre class="preview">{{ previewJson }}</pre>
        </el-collapse-item>
      </el-collapse>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api.js'

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const applying = ref(false)
const previewing = ref(false)
const previewCollapse = ref('')  // el-collapse v-model: 空='收起', 'preview'='展开'
const previewJson = ref('')

const nodeId = ref(null)
const originalProtocol = ref(null)

const form = reactive({
  enabled: false,
  protocol: 'shadowsocks',
  listen_addr: '0.0.0.0',
  listen_port: 8388,
  password: '',
  method: '2022-blake3-aes-128-gcm',
  udp_enabled: true,
  socks_auth_enabled: true,
  socks_username: '',
  socks_password: '',
  ip_allowlist: '',
  sniff_enabled: false,
  sniff_protocols: ['tls', 'http'],
  ntp_enabled: false,
  ntp_server: 'time.apple.com',
  dns_primary: 'https://1.1.1.1/dns-query',
  dns_strategy: 'ipv4_only',
})
const options = reactive({ methods: [], protocols: [], sniffers: [], log_levels: [], dns_strategies: [] })
const applyStatus = reactive({ apply_status: 'never', applied_at: null, apply_error: null })

const isSS2022 = computed(
  () => form.protocol === 'shadowsocks' && (form.method || '').startsWith('2022-')
)

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
    originalProtocol.value = cfgResp.data.config.protocol || 'shadowsocks'
    applyStatus.apply_status = cfgResp.data.apply_status
    applyStatus.applied_at = cfgResp.data.applied_at
    applyStatus.apply_error = cfgResp.data.apply_error
  } finally {
    loading.value = false
  }
}

function _randBytes(n) {
  const arr = new Uint8Array(n)
  crypto.getRandomValues(arr)
  let bin = ''
  arr.forEach((b) => (bin += String.fromCharCode(b)))
  return btoa(bin)
}

function genPassword() {
  const m = form.method
  const bytes = m === '2022-blake3-aes-128-gcm' ? 16 : 32
  form.password = _randBytes(bytes)
  ElMessage.success(`已生成 ${bytes} 字节密码`)
}

function genSocksPassword() {
  form.socks_password = _randBytes(16).replace(/=+$/, '')
  ElMessage.success('已生成')
}

async function save(thenApply) {
  if (originalProtocol.value && form.protocol !== originalProtocol.value) {
    try {
      await ElMessageBox.confirm(
        `协议将切换为 ${form.protocol},客户端需重新配置。确认?`,
        '协议切换',
        { type: 'warning', confirmButtonText: '切换', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  saving.value = true
  try {
    await http.put(`/nodes/${nodeId.value}/ss-config`, form)
    ElMessage.success('已保存')
    originalProtocol.value = form.protocol
    if (thenApply) {
      try {
        await ElMessageBox.confirm(
          '应用配置后 sing-box 会 reload，当前连接的客户端可能短暂断线。确认应用？',
          '确认应用',
          { confirmButtonText: '应用', cancelButtonText: '稍后', type: 'warning' },
        )
      } catch { saving.value = false; return }
      await apply()
    } else {
      visible.value = false
    }
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
        `阶段:${resp.data.stage}\n\n${applyStatus.apply_error}`,
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
    previewCollapse.value = 'preview'  // 自动展开预览面板
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
/* 锁死抽屉尺寸:禁用 element-plus 在某些场景下的边缘 resize 行为 */
:deep(.ss-drawer .el-drawer) {
  resize: none !important;
}
:deep(.ss-drawer .el-drawer__body) {
  overflow-y: auto;
  overflow-x: hidden;
}

/* 数字输入框去掉原生上下箭头 */
:deep(input[type=number]::-webkit-outer-spin-button),
:deep(input[type=number]::-webkit-inner-spin-button) {
  -webkit-appearance: none;
  margin: 0;
}
:deep(input[type=number]) {
  -moz-appearance: textfield;
}

.ss-form { padding: 0 20px 12px; }

.section { margin-bottom: 24px; }
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  padding: 0 0 12px;
  margin-bottom: 16px;
  border-bottom: 2px solid #6366f1;
  display: inline-block;
  min-width: 80px;
}

:deep(.el-form-item) { margin-bottom: 14px; }
:deep(.el-form-item__label) { color: #4b5563; font-weight: 500; }
:deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.6;
}

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
