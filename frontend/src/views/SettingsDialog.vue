<template>
  <el-dialog v-model="visible" title="系统设置" width="640px" @open="load">
    <el-form :model="form" label-width="120px" v-loading="loading" class="settings-form">
      <!-- ===== 分区:面板访问 ===== -->
      <div class="section-title">面板访问</div>

      <el-form-item label="监听 IP">
        <el-input v-model="form.panel_host" placeholder="0.0.0.0" />
        <div class="hint">面板进程绑定的 IP。<code>0.0.0.0</code> 监听所有网卡,<code>127.0.0.1</code> 仅本机。</div>
      </el-form-item>

      <el-form-item label="监听端口">
        <el-input-number v-model="form.panel_port" :min="1" :max="65535" controls-position="right" style="width: 160px" />
        <span class="port-hint" v-if="form.panel_port < 1024">需要 root 或 CAP_NET_BIND_SERVICE</span>
        <div class="hint">面板 HTTP + WebSocket(agent 回连)共用此端口。改完需要 <code>systemctl restart meshpanel</code> 生效。</div>
      </el-form-item>

      <el-form-item label="绑定域名">
        <el-input v-model="form.panel_domain" placeholder="panel.example.com  留空=不强制" />
        <div class="hint">
          填了之后,**只能通过该域名访问面板**,用 IP 或别的域名访问会被拒(403)。
          <code>/api/health</code> 和本机请求豁免。Agent WS 凭 token 鉴权不受影响。
        </div>
      </el-form-item>

      <el-form-item label="启用 HTTPS">
        <el-switch v-model="form.tls_enabled_bool" />
        <span class="state-text">{{ form.tls_enabled_bool ? '已启用' : '已关闭' }}</span>
        <div class="hint">启用后由面板直接 serve HTTPS。需先上传证书。</div>
      </el-form-item>

      <el-form-item label="证书 / 私钥" v-if="form.tls_enabled_bool || form.tls_cert_path">
        <div style="width: 100%">
          <div v-if="form.tls_cert_path" class="cert-info">
            <el-tag size="small" type="success">已上传</el-tag>
            <code>{{ form.tls_cert_path }}</code>
          </div>
          <div style="display: flex; gap: 8px; align-items: center; margin-top: 6px">
            <el-upload
              ref="certUpload"
              :auto-upload="false"
              :show-file-list="false"
              :on-change="(f) => { certFile = f.raw }"
              accept=".pem,.crt,.cer"
            >
              <el-button size="small">
                {{ certFile ? `证书: ${certFile.name}` : '选择证书 (fullchain.pem)' }}
              </el-button>
            </el-upload>
            <el-upload
              :auto-upload="false"
              :show-file-list="false"
              :on-change="(f) => { keyFile = f.raw }"
              accept=".pem,.key"
            >
              <el-button size="small">
                {{ keyFile ? `私钥: ${keyFile.name}` : '选择私钥 (privkey.pem)' }}
              </el-button>
            </el-upload>
            <el-button
              size="small"
              type="primary"
              plain
              :disabled="!certFile || !keyFile"
              :loading="uploadingCert"
              @click="uploadCert"
            >上传</el-button>
          </div>
          <div class="hint">PEM 格式。私钥会以 0600 权限存到 <code>data/certs/</code>。</div>
        </div>
      </el-form-item>

      <!-- ===== 分区:Agent 回连 ===== -->
      <div class="section-title" style="margin-top: 8px">Agent 回连</div>

      <el-form-item label="回连地址">
        <el-input v-model="form.agent_endpoint" :placeholder="derivedEndpoint || 'ws://1.2.3.4:8000'">
          <template #append>
            <el-button @click="useDerived" :disabled="!derivedEndpoint">用推荐值</el-button>
          </template>
        </el-input>
        <div class="hint">
          节点 agent 拨号回主控的 WS 地址,**和面板共用同一端口**。
          根据上面的配置,推荐值: <code>{{ derivedEndpoint || '—' }}</code><br>
          改完点「保存并推送到所有节点」让旧节点切到新地址。
        </div>
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button @click="save(false)" :loading="saving">仅保存</el-button>
      <el-button type="primary" @click="save(true)" :loading="saving || pushing">
        保存并推送到所有节点
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { settingsApi, nodeApi } from '../api.js'

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const pushing = ref(false)
const uploadingCert = ref(false)
const certFile = ref(null)
const keyFile = ref(null)

const form = reactive({
  agent_endpoint: '',
  panel_host: '0.0.0.0',
  panel_port: 8000,
  panel_domain: '',
  tls_enabled_bool: false,
  tls_cert_path: '',
  tls_key_path: '',
})

const emit = defineEmits(['saved'])

function open() {
  visible.value = true
}
defineExpose({ open })

// 根据 TLS + 域名/IP + 端口拼出推荐的 agent 回连地址
const derivedEndpoint = computed(() => {
  const scheme = form.tls_enabled_bool ? 'wss' : 'ws'
  const host = form.panel_domain.trim() || window.location.hostname || ''
  if (!host) return ''
  const port = form.panel_port
  // 标准端口省略
  const isStdPort =
    (form.tls_enabled_bool && port === 443) || (!form.tls_enabled_bool && port === 80)
  return isStdPort ? `${scheme}://${host}` : `${scheme}://${host}:${port}`
})

function useDerived() {
  if (derivedEndpoint.value) form.agent_endpoint = derivedEndpoint.value
}

async function load() {
  loading.value = true
  certFile.value = null
  keyFile.value = null
  try {
    const resp = await settingsApi.get()
    const d = resp.data || {}
    form.agent_endpoint = d.agent_endpoint || ''
    form.panel_host = d.panel_host || '0.0.0.0'
    form.panel_port = parseInt(d.panel_port || '8000', 10)
    form.panel_domain = d.panel_domain || ''
    form.tls_enabled_bool = d.tls_enabled === '1'
    form.tls_cert_path = d.tls_cert_path || ''
    form.tls_key_path = d.tls_key_path || ''
  } finally {
    loading.value = false
  }
}

async function uploadCert() {
  if (!certFile.value || !keyFile.value) return
  uploadingCert.value = true
  try {
    const fd = new FormData()
    fd.append('cert', certFile.value)
    fd.append('key', keyFile.value)
    const resp = await settingsApi.uploadCert(fd)
    form.tls_cert_path = resp.data.tls_cert_path
    form.tls_key_path = resp.data.tls_key_path
    certFile.value = null
    keyFile.value = null
    ElMessage.success('证书已上传,保存设置后重启服务生效')
  } catch (e) {
    ElMessage.error(`上传失败: ${e.response?.data?.error || e.message}`)
  } finally {
    uploadingCert.value = false
  }
}

async function save(thenPush) {
  saving.value = true
  try {
    const payload = {
      agent_endpoint: form.agent_endpoint.trim(),
      panel_host: form.panel_host.trim() || '0.0.0.0',
      panel_port: String(form.panel_port),
      panel_domain: form.panel_domain.trim(),
      tls_enabled: form.tls_enabled_bool ? '1' : '0',
    }
    await settingsApi.update(payload)
    ElMessage.success('已保存(端口/TLS/域名改动需重启服务生效:systemctl restart meshpanel)')
    emit('saved')
  } catch (e) {
    ElMessage.error(`保存失败: ${e.response?.data?.error || e.message}`)
    saving.value = false
    return
  } finally {
    saving.value = false
  }
  if (thenPush) await pushAll()
  else visible.value = false
}

async function pushAll() {
  if (!form.agent_endpoint) {
    ElMessage.warning('agent 回连地址为空,跳过推送')
    return
  }
  pushing.value = true
  try {
    const resp = await nodeApi.list()
    const deployed = (resp.data || []).filter((n) => n.deploy_status === 'deployed')
    if (deployed.length === 0) {
      ElMessage.info('暂无已部署节点')
      visible.value = false
      return
    }
    let okCount = 0
    const failed = []
    for (const n of deployed) {
      try {
        const r = await nodeApi.redeployAgentConfig(n.id)
        if (r.data.success) okCount += 1
        else failed.push(`${n.name}: ${r.data.message}`)
      } catch (e) {
        failed.push(`${n.name}: ${e.message}`)
      }
    }
    if (failed.length === 0) {
      ElMessage.success(`已推送到 ${okCount} 个节点`)
      visible.value = false
    } else {
      ElMessageBox.alert(
        `成功 ${okCount} / 失败 ${failed.length}\n\n${failed.join('\n')}`,
        '部分失败',
        { type: 'warning' },
      )
    }
  } finally {
    pushing.value = false
  }
}
</script>

<style scoped>
.settings-form { padding: 4px 8px 0 0; }

.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #6366f1;
  letter-spacing: 0.5px;
  padding: 10px 0 8px 4px;
  border-bottom: 1px solid #eef0f4;
  margin-bottom: 14px;
  text-transform: uppercase;
}

.hint {
  font-size: 12px;
  color: #8a8f99;
  margin-top: 4px;
  line-height: 1.55;
}

.hint code {
  background: #f1f3f7;
  color: #4f46e5;
  padding: 1px 5px;
  border-radius: 3px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11.5px;
}

.port-hint {
  margin-left: 10px;
  font-size: 12px;
  color: #f59e0b;
}

.state-text {
  margin-left: 10px;
  font-size: 12px;
  color: #6b7280;
}

.cert-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.cert-info code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #6b7280;
  font-size: 11.5px;
}
</style>
