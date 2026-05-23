<template>
  <div class="page">
    <!-- 顶部 4 卡片占满 -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-card-label">服务器总数</div>
        <div class="stat-card-value"><span class="dot-mark blue" />{{ stats.total }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">在线服务器</div>
        <div class="stat-card-value"><span class="dot-mark green" />{{ stats.online }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-card-label">离线服务器</div>
        <div class="stat-card-value"><span class="dot-mark red" />{{ stats.offline }}</div>
      </div>
      <div class="stat-card stat-card-net">
        <div class="stat-card-label">网络</div>
        <div class="net-grid">
          <div class="net-row total"><el-icon class="ic up"><Top /></el-icon><span class="net-num">{{ splitBytes(stats.tx_total).num }}</span><span class="net-unit">{{ splitBytes(stats.tx_total).unit }}</span></div>
          <div class="net-row total"><el-icon class="ic down"><Bottom /></el-icon><span class="net-num">{{ splitBytes(stats.rx_total).num }}</span><span class="net-unit">{{ splitBytes(stats.rx_total).unit }}</span></div>
          <div class="net-row rate"><span class="ic-bullet up" />{{ splitBps(stats.tx_bps).num }} <span class="net-unit">{{ splitBps(stats.tx_bps).unit }}</span></div>
          <div class="net-row rate"><span class="ic-bullet down" />{{ splitBps(stats.rx_bps).num }} <span class="net-unit">{{ splitBps(stats.rx_bps).unit }}</span></div>
        </div>
      </div>
    </div>

    <!-- 节点分类 tabs -->
    <div class="kind-tabs">
      <button
        v-for="t in kindTabs"
        :key="t.value"
        class="kind-tab"
        :class="{ active: kindFilter === t.value }"
        @click="kindFilter = t.value"
      >
        {{ t.label }}
        <span class="kind-tab-count">{{ t.count }}</span>
      </button>
    </div>

    <!-- 空状态 -->
    <el-empty
      v-if="!loading && filteredNodes.length === 0"
      :description="nodes.length === 0 ? '还没有节点,点右上角添加一个' : '此分类下暂无节点'"
      class="empty"
    >
      <el-button v-if="nodes.length === 0" type="primary" @click="openCreate">添加第一个节点</el-button>
    </el-empty>

    <!-- 卡片视图 -->
    <draggable
      v-else
      v-model="nodes"
      class="grid"
      item-key="id"
      handle=".drag-handle"
      :animation="180"
      ghost-class="drag-ghost"
      :disabled="kindFilter !== 'all'"
      @end="onDragEnd"
      v-loading="loading && nodes.length === 0"
    >
      <template #item="{ element: row }">
      <div v-show="kindFilter === 'all' || (row.kind || 'landing') === kindFilter" class="card" :class="{ 'card-online': row.agent_status === 'online' }">
        <div class="card-head">
          <span
            class="drag-handle"
            :class="{ disabled: kindFilter !== 'all' }"
            :title="kindFilter === 'all' ? '拖动排序' : '到「全部」标签拖动排序'"
          >⠿</span>
          <div class="card-title">
            <span class="dot" :class="row.agent_status === 'online' ? 'online' : 'offline'" />
            <div class="title-text">
              <div class="name">
                <span class="kind-chip" :class="`kind-${row.kind || 'landing'}`">
                  {{ kindLabel(row.kind) }}
                </span>
                <img v-if="row.country" class="flag" :src="`https://flagcdn.com/w40/${row.country}.png`" :title="row.country.toUpperCase()" :alt="row.country" />
                {{ row.name }}
              </div>
              <div v-if="metrics[row.id]?.cpu_model" class="subtitle" :title="metrics[row.id].cpu_model">
                {{ metrics[row.id].cpu_model }}
              </div>
            </div>
          </div>
          <el-dropdown trigger="click" @command="(c) => onCardCmd(c, row)">
            <el-button text :icon="MoreFilled" class="more-btn" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item
                  command="deploy"
                  :icon="Upload"
                  :disabled="row.deploy_status === 'deploying'"
                >
                  {{ row.deploy_status === 'deployed' ? '重新部署' : (row.deploy_status === 'failed' ? '重试部署' : '部署') }}
                </el-dropdown-item>
                <el-dropdown-item
                  v-if="(row.kind || 'landing') !== 'other'"
                  command="ss"
                  :icon="Setting"
                  :disabled="row.deploy_status !== 'deployed'"
                >节点配置</el-dropdown-item>
                <el-dropdown-item command="detail" :icon="Document">详情</el-dropdown-item>
                <el-dropdown-item command="edit" :icon="Edit">编辑</el-dropdown-item>
                <el-dropdown-item
                  v-if="row.deploy_status === 'deployed'"
                  command="uninstall"
                  :icon="RemoveFilled"
                  divided
                >
                  <span class="danger-item">卸载</span>
                </el-dropdown-item>
                <el-dropdown-item v-else command="remove" :icon="Delete" divided>
                  <span class="danger-item">删除</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <div class="card-addr">
          <el-icon><Monitor /></el-icon>
          <span class="host-text">{{ row.host }}</span>
          <span v-if="metrics[row.id]?.iface" class="iface-chip">{{ metrics[row.id].iface }}</span>
        </div>

        <div class="card-tags">
          <el-tag :type="deployType(row.deploy_status)" size="small" effect="light">
            {{ deployLabel(row.deploy_status) }}
          </el-tag>
          <el-tag v-if="row.singbox_version" size="small" effect="plain">
            sing-box v{{ row.singbox_version }}
          </el-tag>
          <el-tag v-if="row.soga_version" size="small" effect="plain">
            Soga v{{ row.soga_version }}
          </el-tag>
          <el-tag v-if="row.agent_version" size="small" effect="plain" type="info">
            agent v{{ row.agent_version }}
          </el-tag>
        </div>

        <!-- 实时探针 -->
        <div v-if="metrics[row.id]" class="probe">
          <div class="probe-row probe-speed">
            <div class="speed-item down">
              <el-icon><Bottom /></el-icon>
              <span class="speed-val">{{ fmtBps(metrics[row.id].rx_bps) }}</span>
            </div>
            <div class="speed-divider" />
            <div class="speed-item up">
              <el-icon><Top /></el-icon>
              <span class="speed-val">{{ fmtBps(metrics[row.id].tx_bps) }}</span>
            </div>
          </div>
          <div class="probe-row probe-bars">
            <el-tooltip :content="`${metrics[row.id].cpu_pct.toFixed(1)}%${metrics[row.id].cpu_cores ? ' · ' + metrics[row.id].cpu_cores + ' 核' : ''}`" placement="top" :show-after="150">
              <div class="bar-block">
                <div class="bar-head">
                  <span>CPU</span>
                  <span class="bar-val">{{ Math.round(metrics[row.id].cpu_pct) }}%</span>
                </div>
                <div class="bar-track"><div class="bar-fill cpu" :style="{ width: Math.max(2, Math.min(100, metrics[row.id].cpu_pct)) + '%' }" /></div>
              </div>
            </el-tooltip>
            <el-tooltip :content="`${fmtBytes(metrics[row.id].mem_used)} / ${fmtBytes(metrics[row.id].mem_total)}`" placement="top" :show-after="150">
              <div class="bar-block">
                <div class="bar-head">
                  <span>内存</span>
                  <span class="bar-val">{{ memPct(row.id) }}%</span>
                </div>
                <div class="bar-track"><div class="bar-fill mem" :style="{ width: memPct(row.id) + '%' }" /></div>
              </div>
            </el-tooltip>
            <el-tooltip v-if="metrics[row.id].disk_total" :content="`${fmtBytes(metrics[row.id].disk_used)} / ${fmtBytes(metrics[row.id].disk_total)}`" placement="top" :show-after="150">
              <div class="bar-block">
                <div class="bar-head">
                  <span>硬盘</span>
                  <span class="bar-val">{{ diskPct(row.id) }}%</span>
                </div>
                <div class="bar-track"><div class="bar-fill disk" :style="{ width: diskPct(row.id) + '%' }" /></div>
              </div>
            </el-tooltip>
          </div>
          <div class="probe-row probe-total">
            <div class="total-item down">
              <el-icon><Bottom /></el-icon>
              <span class="total-val">{{ fmtBytes(metrics[row.id].rx_total) }}</span>
            </div>
            <div class="speed-divider" />
            <div class="total-item up">
              <el-icon><Top /></el-icon>
              <span class="total-val">{{ fmtBytes(metrics[row.id].tx_total) }}</span>
            </div>
          </div>
        </div>
        <div v-else-if="row.agent_status === 'online'" class="probe probe-empty">
          <el-icon class="loading-icon"><Loading /></el-icon> 正在采样...
        </div>

        <div class="card-foot">
          <span v-if="metrics[row.id]?.uptime_sec">
            <el-icon><Clock /></el-icon> 运行 {{ fmtUptime(metrics[row.id].uptime_sec) }}
          </span>
          <span v-else-if="row.agent_last_seen">
            <el-icon><Clock /></el-icon> {{ relTime(row.agent_last_seen) }}
          </span>
          <span v-else class="muted">从未上线</span>
        </div>

        <div v-if="row.deploy_status !== 'deployed'" class="card-quick">
          <el-button
            size="small"
            type="primary"
            :loading="row._deploying || row.deploy_status === 'deploying'"
            @click="deployNode(row)"
            class="quick-btn"
          >{{ row.deploy_status === 'failed' ? '重试部署' : '部署' }}</el-button>
        </div>
      </div>
      </template>
    </draggable>

    <!-- 新增/编辑 -->
    <el-drawer
      v-model="dialogOpen"
      :title="editingId ? '编辑节点' : '添加节点'"
      :size="drawerSize"
      @closed="resetForm"
    >
      <el-form ref="formRef" :model="form" :rules="rules" label-width="80px" class="edit-form">
        <div class="section-title">基础信息</div>
        <el-form-item label="类型" prop="kind">
          <el-radio-group v-model="form.kind">
            <el-radio value="landing">落地机</el-radio>
            <el-radio value="soga">入口机</el-radio>
            <el-radio value="other">监控机</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="例如:东京 1 号" />
        </el-form-item>
        <el-form-item label="国家/地区" prop="country">
          <el-input
            v-model="form.country"
            placeholder="留空自动识别"
            maxlength="2"
            clearable
            style="width: 200px"
          >
            <template #prepend>
              <img v-if="form.country && form.country.length === 2" :src="`https://flagcdn.com/w40/${form.country.toLowerCase()}.png`" style="width:20px;height:14px;object-fit:cover;border-radius:2px;vertical-align:middle" />
              <span v-else style="font-size: 14px">🌐</span>
            </template>
          </el-input>
        </el-form-item>

        <div class="section-title">SSH 连接</div>
        <el-form-item label="主机地址" prop="host">
          <el-input v-model="form.host" placeholder="IP 或域名" />
        </el-form-item>
        <el-form-item label="端口" prop="ssh_port">
          <el-input
            v-model.number="form.ssh_port"
            type="number"
            min="1"
            max="65535"
            placeholder="22"
            style="width: 110px"
          />
        </el-form-item>
        <el-form-item label="用户" prop="ssh_user">
          <el-input v-model="form.ssh_user" />
        </el-form-item>
        <el-form-item label="认证方式" prop="auth_type">
          <el-radio-group v-model="form.auth_type">
            <el-radio value="password">密码</el-radio>
            <el-radio value="key">私钥</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="form.auth_type === 'password'"
          label="密码"
          :prop="editingId ? undefined : 'ssh_password'"
        >
          <el-input
            v-model="form.ssh_password"
            type="password" show-password
            :placeholder="editingId ? '留空表示不修改' : '请输入 SSH 密码'"
          />
        </el-form-item>
        <el-form-item v-else label="私钥" :prop="editingId ? undefined : 'ssh_private_key'">
          <el-input
            v-model="form.ssh_private_key"
            type="textarea" :rows="6"
            :placeholder="editingId ? '留空表示不修改' : '粘贴 PEM 格式私钥'"
          />
        </el-form-item>

        <div class="section-title">高级</div>
        <el-form-item label="探针网卡" prop="agent_iface">
          <el-input
            v-model="form.agent_iface"
            placeholder="留空自动"
            clearable
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-drawer>

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="detailOpen"
      :title="detailNode ? `节点详情 · ${detailNode.name}` : '节点详情'"
      :size="drawerSize"
    >
      <div v-if="detailNode" class="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="ID">{{ detailNode.id }}</el-descriptions-item>
          <el-descriptions-item label="地址">{{ detailNode.host }}:{{ detailNode.ssh_port }}</el-descriptions-item>
          <el-descriptions-item label="SSH 用户">{{ detailNode.ssh_user }}</el-descriptions-item>
          <el-descriptions-item label="部署">
            <el-tag :type="deployType(detailNode.deploy_status)" size="small">{{ deployLabel(detailNode.deploy_status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="sing-box">{{ detailNode.singbox_version || '—' }}</el-descriptions-item>
          <el-descriptions-item label="架构">{{ detailNode.arch || '—' }}</el-descriptions-item>
          <el-descriptions-item label="config schema">{{ detailNode.config_schema || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部署时间">{{ fmtTime(detailNode.deployed_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="log-header">
          <span>部署日志</span>
          <div>
            <el-button size="small" :icon="Refresh" @click="refreshLog">刷新</el-button>
            <el-button size="small" :icon="CopyDocument" @click="copyLog">复制</el-button>
          </div>
        </div>
        <pre class="log-box">{{ logText || '（暂无日志）' }}</pre>
      </div>
    </el-drawer>

    <SSConfigDrawer ref="ssDrawerRef" />
    <SogaConfigDrawer ref="sogaDrawerRef" />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Plus, Refresh, MoreFilled, Monitor, Clock,
  Upload, Setting, Document, Edit, Delete, RemoveFilled, CopyDocument,
  Top, Bottom, Cpu, Loading,
} from '@element-plus/icons-vue'
import { nodeApi } from '../api.js'
import draggable from 'vuedraggable'
import SSConfigDrawer from './SSConfigDrawer.vue'
import SogaConfigDrawer from './SogaConfigDrawer.vue'

const ssDrawerRef = ref(null)
const sogaDrawerRef = ref(null)
const nodes = ref([])
const kindFilter = ref('all')   // all | landing | soga
const metrics = ref({})  // node_id -> { rx_bps, tx_bps, cpu_pct, mem_used, mem_total, uptime_sec, iface }
const loading = ref(false)
const dialogOpen = ref(false)
const saving = ref(false)
const editingId = ref(null)
const formRef = ref(null)



const detailOpen = ref(false)
const detailNode = ref(null)
const logText = ref('')

const winW = ref(window.innerWidth)
const drawerSize = computed(() => winW.value < 720 ? '92%' : '560px')

const filteredNodes = computed(() => {
  if (kindFilter.value === 'all') return nodes.value
  return nodes.value.filter(n => (n.kind || 'landing') === kindFilter.value)
})

const kindTabs = computed(() => {
  const landing = nodes.value.filter(n => (n.kind || 'landing') === 'landing').length
  const soga = nodes.value.filter(n => n.kind === 'soga').length
  const other = nodes.value.filter(n => n.kind === 'other').length
  return [
    { value: 'all', label: '全部', count: nodes.value.length },
    { value: 'landing', label: '落地机', count: landing },
    { value: 'soga', label: '入口机', count: soga },
    { value: 'other', label: '监控机', count: other },
  ]
})

function kindLabel(k) {
  return { soga: '入口', other: '监控' }[k] || '落地'
}

const stats = computed(() => {
  let rx = 0, tx = 0, rxT = 0, txT = 0
  for (const n of nodes.value) {
    const m = metrics.value[n.id]
    if (m) {
      rx += m.rx_bps || 0; tx += m.tx_bps || 0
      rxT += m.rx_total || 0; txT += m.tx_total || 0
    }
  }
  const online = nodes.value.filter((n) => n.agent_status === 'online').length
  return {
    total: nodes.value.length,
    online,
    offline: nodes.value.length - online,
    rx_bps: rx,
    tx_bps: tx,
    rx_total: rxT,
    tx_total: txT,
  }
})

const emptyForm = () => ({
  kind: 'landing',
  name: '', host: '', ssh_port: 22, ssh_user: 'root',
  auth_type: 'password', ssh_password: '', ssh_private_key: '',
  agent_iface: '',
  country: '',
})
const form = reactive(emptyForm())

const rules = {
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
  host: [{ required: true, message: '请输入主机地址', trigger: 'blur' }],
  ssh_port: [{ required: true, message: '请输入端口', trigger: 'blur' }],
  ssh_user: [{ required: true, message: '请输入 SSH 用户', trigger: 'blur' }],
  auth_type: [{ required: true, message: '请选择认证方式', trigger: 'change' }],
  ssh_password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  ssh_private_key: [{ required: true, message: '请粘贴私钥', trigger: 'blur' }],
}

function deployType(s) {
  return { deployed: 'success', failed: 'danger', deploying: 'warning', uninstalled: 'info' }[s] || 'info'
}
function deployLabel(s) {
  return {
    not_deployed: '未部署', deploying: '部署中', deployed: '已部署',
    failed: '部署失败', uninstalled: '已卸载',
  }[s] || s
}
function fmtTime(t) {
  if (!t) return '—'
  return new Date(t.endsWith('Z') ? t : t + 'Z').toLocaleString('zh-CN', { hour12: false })
}
function relTime(t) {
  if (!t) return ''
  const d = new Date(t.endsWith('Z') ? t : t + 'Z')
  const diff = Math.round((Date.now() - d.getTime()) / 1000)
  if (diff < 60) return `${diff} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 时前`
  return `${Math.floor(diff / 86400)} 天前`
}

async function load(silent = false) {
  if (!silent) loading.value = true
  try {
    const resp = await nodeApi.list()
    nodes.value = (resp.data || []).map((n) => ({ ...n, _deploying: false }))
  } finally {
    loading.value = false
  }
}

async function onDragEnd(evt) {
  if (evt.oldIndex === evt.newIndex) return
  try {
    await nodeApi.reorder(nodes.value.map((n) => n.id))
  } catch (e) {
    ElMessage.error('排序保存失败,已刷新')
    load(true)
  }
}

async function loadMetrics() {
  try {
    const resp = await nodeApi.metrics()
    metrics.value = resp.data || {}
  } catch {
    /* 静默 */
  }
}

function fmtBps(bps) {
  // 速率显示统一按"字节/秒"，跟累计流量单位一致：Bps = bps / 8
  const Bps = (bps || 0) / 8
  if (Bps < 1024) return `${Bps.toFixed(Bps >= 10 ? 0 : 1)} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = Bps / 1024, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`
}
function splitBps(bps) {
  const s = fmtBps(bps)
  const idx = s.lastIndexOf(' ')
  return idx > 0 ? { num: s.slice(0, idx), unit: s.slice(idx + 1) } : { num: s, unit: '' }
}
function splitBytes(b) {
  const s = fmtBytes(b)
  const idx = s.lastIndexOf(' ')
  return idx > 0 ? { num: s.slice(0, idx), unit: s.slice(idx + 1) } : { num: s, unit: '' }
}
function flagEmoji(cc) {
  if (!cc || cc.length !== 2) return ''
  const code = cc.toUpperCase()
  return String.fromCodePoint(0x1F1E6 + code.charCodeAt(0) - 65) +
         String.fromCodePoint(0x1F1E6 + code.charCodeAt(1) - 65)
}
function fmtBytes(b) {
  if (!b) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = b, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${units[i]}`
}
function fmtUptime(sec) {
  if (!sec) return '—'
  const d = Math.floor(sec / 86400)
  const h = Math.floor((sec % 86400) / 3600)
  const m = Math.floor((sec % 3600) / 60)
  if (d > 0) return `${d}天 ${h}小时`
  if (h > 0) return `${h}小时 ${m}分`
  return `${m} 分钟`
}

function memPct(id) {
  const m = metrics.value[id]
  if (!m || !m.mem_total) return 0
  return Math.round(m.mem_used / m.mem_total * 100)
}

function diskPct(id) {
  const m = metrics.value[id]
  if (!m || !m.disk_total) return 0
  return Math.max(2, Math.round(m.disk_used / m.disk_total * 100))
}

function resetForm() {
  Object.assign(form, emptyForm())
  editingId.value = null
  formRef.value?.clearValidate()
}

function openCreate() {
  resetForm()
  dialogOpen.value = true
}
defineExpose({ openCreate })

function openEdit(row) {
  resetForm()
  editingId.value = row.id
  form.kind = row.kind || 'landing'
  form.name = row.name
  form.host = row.host
  form.ssh_port = row.ssh_port
  form.ssh_user = row.ssh_user
  form.auth_type = row.auth_type
  form.agent_iface = row.agent_iface || ''
  form.country = row.country || ''
  dialogOpen.value = true
}

async function save() {
  try { await formRef.value.validate() } catch { return }
  saving.value = true
  try {
    const payload = {
      kind: form.kind || 'landing',
      name: form.name, host: form.host, ssh_port: form.ssh_port,
      ssh_user: form.ssh_user, auth_type: form.auth_type,
      agent_iface: form.agent_iface || null,
      country: form.country ? form.country.toLowerCase() : null,
    }
    if (form.auth_type === 'password') {
      if (form.ssh_password) payload.ssh_password = form.ssh_password
      if (editingId.value) payload.ssh_private_key = null
    } else {
      if (form.ssh_private_key) payload.ssh_private_key = form.ssh_private_key
      if (editingId.value) payload.ssh_password = null
    }
    if (editingId.value) {
      await nodeApi.update(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await nodeApi.create(payload)
      ElMessage.success('已添加')
    }
    dialogOpen.value = false
    await load()
  } finally {
    saving.value = false
  }
}

async function removeNode(row) {
  try {
    await ElMessageBox.confirm(`确定从面板删除节点「${row.name}」？`, '删除节点', { type: 'warning' })
  } catch { return }
  await nodeApi.remove(row.id)
  ElMessage.success('已删除')
  await load()
}

async function uninstallNode(row) {
  try {
    await ElMessageBox.confirm(
      `将通过 SSH 停止并清除节点「${row.name}」上的 sing-box / mesh-agent / 配置目录。面板记录会保留（状态变为「已卸载」）。`,
      '卸载节点',
      { type: 'warning', confirmButtonText: '执行卸载', cancelButtonText: '取消' },
    )
  } catch { return }

  row._uninstalling = true
  try {
    const resp = await nodeApi.uninstall(row.id, { delete_node: false })
    if (resp.data.success) {
      ElMessage.success('已卸载')
      await load()
    } else {
      ElMessageBox.confirm(
        `卸载失败：${resp.data.error || '未知'}\n\n是否强制从面板删除此节点？`,
        '卸载失败',
        { type: 'error', confirmButtonText: '强制删除', cancelButtonText: '保留' },
      ).then(async () => {
        await nodeApi.uninstall(row.id, { delete_node: true, force: true })
        ElMessage.success('已强制删除')
        await load()
      }).catch(() => {})
    }
  } finally {
    row._uninstalling = false
  }
}

async function deployNode(row) {
  if (row.deploy_status === 'deployed') {
    try {
      await ElMessageBox.confirm('该节点已部署，重新部署会覆盖现有 sing-box 二进制并重启服务，确定继续？', '确认重新部署', { type: 'warning' })
    } catch { return }
  }
  row._deploying = true
  row.deploy_status = 'deploying'
  try {
    const resp = await nodeApi.deploy(row.id)
    const { success, error, version, node } = resp.data
    Object.assign(row, node, { _deploying: false })
    if (success) {
      ElMessage.success(`部署成功，sing-box v${version}`)
    } else {
      ElMessageBox.alert(error || '部署失败', '部署失败', { type: 'error' })
    }
  } catch {
    row._deploying = false
    await load()
  } finally {
    row._deploying = false
  }
}

function openSSConfig(row) {
  if ((row.kind || 'landing') === 'soga') {
    sogaDrawerRef.value?.open(row.id)
  } else {
    ssDrawerRef.value?.open(row.id)
  }
}

async function openDetail(row) {
  detailNode.value = row
  detailOpen.value = true
  await refreshLog()
}
async function refreshLog() {
  if (!detailNode.value) return
  try {
    const resp = await nodeApi.deployLog(detailNode.value.id)
    logText.value = resp.data.deploy_log || ''
  } catch { logText.value = '加载失败' }
}
async function copyLog() {
  try { await navigator.clipboard.writeText(logText.value); ElMessage.success('已复制') }
  catch { ElMessage.error('复制失败') }
}

function onCardCmd(cmd, row) {
  if (cmd === 'deploy') return deployNode(row)
  if (cmd === 'ss') return openSSConfig(row)
  if (cmd === 'detail') return openDetail(row)
  if (cmd === 'edit') return openEdit(row)
  if (cmd === 'uninstall') return uninstallNode(row)
  if (cmd === 'remove') return removeNode(row)
}

function onResize() { winW.value = window.innerWidth }
let timer = null
let metricsTimer = null
onMounted(() => {
  load()
  loadMetrics()
  window.addEventListener('resize', onResize)
  timer = setInterval(() => {
    if (!loading.value && !saving.value) load(true)
  }, 5000)
  metricsTimer = setInterval(loadMetrics, 2000)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (timer) clearInterval(timer)
  if (metricsTimer) clearInterval(metricsTimer)
})

</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 16px; }

/* 工具栏 */
/* 顶部 4 卡片 stats-row（占满，等宽） */
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 14px;
}
.stat-card {
  background: #fff;
  border: 1px solid #eef0f3;
  border-radius: 10px;
  padding: 14px 18px;
  min-width: 0;
}
.stat-card-label {
  font-size: 12px; color: #9ca3af;
  margin-bottom: 8px;
}
.stat-card-value {
  font-size: 28px; font-weight: 600; color: #1f2937;
  line-height: 1.1;
  display: flex; align-items: center; gap: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, sans-serif;
  font-variant-numeric: tabular-nums;
}
.dot-mark { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.dot-mark.blue { background: #3b82f6; }
.dot-mark.green { background: #10b981; }
.dot-mark.red { background: #f43f5e; }
.stat-card-net .net-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 14px;
  align-items: baseline;
}
.net-row {
  display: flex; align-items: baseline; gap: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, sans-serif;
  font-variant-numeric: tabular-nums;
  white-space: nowrap; min-width: 0;
}
.net-row.total { font-size: 15px; font-weight: 600; color: #1f2937; }
.net-row.rate { font-size: 12px; color: #6b7280; }
.net-row .ic { font-size: 13px; transform: translateY(2px); }
.net-row .ic.up { color: #6366f1; }
.net-row .ic.down { color: #10b981; }
.ic-bullet { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; align-self: center; margin-right: 2px; }
.ic-bullet.up { background: #6366f1; }
.ic-bullet.down { background: #10b981; }
.net-row .net-num { color: inherit; }
.net-row .net-unit { font-size: 10px; color: #9ca3af; font-weight: 500; margin-left: 1px; }

.toolbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 12px; flex-wrap: wrap;
  background: #fff; border-radius: 10px; padding: 14px 18px;
  border: 1px solid #e5e7eb;
}
.stats {
  display: flex; align-items: center; gap: 0;
  background: #fff;
  border: 1px solid #eef0f3;
  border-radius: 10px;
  padding: 10px 4px;
}
.stat {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column; align-items: center;
  padding: 2px 16px;
}
.stat-sep { width: 1px; height: 32px; background: #eef0f3; flex-shrink: 0; }
.stat-num {
  font-size: 20px; font-weight: 600; color: #1f2937;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, sans-serif;
  display: inline-flex; align-items: baseline; gap: 4px;
  white-space: nowrap;
}
.stat-unit {
  font-size: 11px; font-weight: 500; color: #9ca3af;
  margin-left: 3px; letter-spacing: 0.02em;
}
.stat-icon {
  display: inline-flex; align-items: center;
  font-size: 14px; line-height: 1; margin-right: 1px;
  align-self: center;
}
.stat-icon.down { color: #10b981; }
.stat-icon.up   { color: #6366f1; }
.stat-label {
  font-size: 12px; color: #9ca3af; margin-top: 4px;
  letter-spacing: 0.02em;
}
.stat-warn { color: #f43f5e; }
.text-success { color: #10b981; }
.text-primary { color: #6366f1; }
.actions { display: flex; gap: 8px; align-items: center; }

/* 卡片网格 */
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}
.card {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 10px;
  transition: all 0.15s;
  position: relative;
}
.card:hover {
  border-color: #c7d2fe;
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.08);
  transform: translateY(-1px);
}
.card-online::before {
  content: '';
  position: absolute; top: 0; left: 16px; right: 16px; height: 2px;
  background: linear-gradient(90deg, #10b981, #6366f1);
  border-radius: 2px;
}
.card-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 6px;
}
.drag-handle {
  cursor: grab;
  color: #c0c4cc;
  font-size: 14px;
  line-height: 1;
  padding: 4px 2px;
  user-select: none;
  transition: color .12s;
}
.drag-handle:hover { color: #6366f1; }
.drag-handle:active { cursor: grabbing; }
.drag-handle.disabled { cursor: not-allowed; color: #e5e7eb; }
.drag-ghost {
  opacity: 0.4;
  background: #f5f3ff !important;
  border: 1px dashed #a5b4fc !important;
}
.card-title { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
.name { font-weight: 600; color: #111827; font-size: 15px; }
.more-btn { padding: 4px 8px; }
.card-addr {
  display: flex; align-items: center; gap: 6px;
  color: #4b5563; font-size: 13px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
}
.card-addr .muted { color: #9ca3af; }
.card-tags { display: flex; gap: 6px; flex-wrap: wrap; }
.card-foot {
  display: flex; justify-content: space-between; align-items: center;
  font-size: 12px; color: #6b7280;
  padding-top: 8px;
  border-top: 1px dashed #e5e7eb;
}
.card-foot .el-icon { vertical-align: -2px; margin-right: 2px; }
.card-foot .muted { color: #d1d5db; }
.card-quick { display: flex; gap: 6px; }
.card-quick .quick-btn { flex: 1; }

/* 状态点 */
.dot {
  display: inline-block; width: 8px; height: 8px;
  border-radius: 50%; background: #d1d5db;
}
.dot.online { background: #10b981; box-shadow: 0 0 6px rgba(16, 185, 129, 0.6); }
.dot.offline { background: #d1d5db; }
.dot.inline { margin-right: 6px; vertical-align: 2px; }

/* 探针面板 — 极简单色版 */
.probe {
  --probe-accent: #3b82f6;
  --probe-text: #1f2937;
  --probe-muted: #9ca3af;
  --probe-track: #eef2f7;
  background: #fafbfc;
  border: 1px solid #eef0f3;
  border-radius: 10px;
  padding: 14px 14px 12px;
  display: grid;
  gap: 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, sans-serif;
  font-variant-numeric: tabular-nums;
}
.probe-empty {
  color: var(--probe-muted); font-size: 12px;
  display: flex; align-items: center; gap: 6px;
  justify-content: center; padding: 18px;
  background: #fafbfc; border-radius: 10px;
}
.loading-icon { animation: spin 1.2s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* 速率行：实时 ↓ / ↑ */
.probe-speed {
  display: grid;
  grid-template-columns: 1fr 1px 1fr;
  align-items: center;
  padding: 0;
}
.speed-item {
  display: flex; align-items: center; justify-content: center; gap: 6px;
}
.speed-item .el-icon { font-size: 13px; opacity: 0.9; }
.speed-item.down .el-icon { color: #10b981; }
.speed-item.up .el-icon { color: #6366f1; }
.speed-val {
  font-size: 13px; font-weight: 600; color: var(--probe-text);
}
.speed-label { font-size: 11px; color: var(--probe-muted); margin-left: 2px; }
.speed-divider { width: 1px; height: 14px; background: #e5e7eb; }

/* 进度条组：CPU / 内存 / 硬盘 — 单列竖排，统一左基准 */
.probe-bars {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}
.bar-block { min-width: 0; }
.bar-head {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 11px; color: var(--probe-muted);
  margin-bottom: 5px; letter-spacing: 0.02em;
}
.bar-val {
  font-weight: 600; color: var(--probe-text);
  font-size: 12px;
}
.bar-sub { font-weight: 400; color: var(--probe-muted); margin-left: 3px; }
.bar-track {
  height: 4px; background: var(--probe-track);
  border-radius: 999px; overflow: hidden;
}
.bar-fill {
  height: 100%; border-radius: 999px;
  background: var(--probe-accent);
  transition: width 0.4s ease;
}
.bar-fill.cpu  { opacity: 1; }
.bar-fill.mem  { opacity: 0.75; }
.bar-fill.disk { opacity: 0.5; }

/* 累计流量：低优先级，灰一层 */
.probe-total {
  display: grid;
  grid-template-columns: 1fr 1px 1fr;
  align-items: center;
}
.total-item {
  display: flex; align-items: center; justify-content: center; gap: 6px;
  color: var(--probe-muted);
}
.total-item .el-icon {
  font-size: 15px;
  opacity: 0.95;
}
.total-item.down .el-icon { color: #10b981; }
.total-item.up .el-icon { color: #6366f1; }
.total-val {
  font-size: 14px; font-weight: 600; color: var(--probe-text);
}
.total-label { display: none; }

/* 卡片头部副标题（CPU 型号等） */
.title-text { display: flex; flex-direction: column; min-width: 0; gap: 5px; }
.title-text .name { font-weight: 600; color: #1f2937; line-height: 1.2; }
.title-text .name .flag { width: 18px; height: 13px; object-fit: cover; border-radius: 2px; margin-right: 6px; vertical-align: -1px; box-shadow: 0 0 0 1px rgba(0,0,0,0.06); }
.title-text .subtitle {
  font-size: 11px; color: #9ca3af; line-height: 1.2;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  padding-top: 4px;
  border-top: 1px dashed #eef0f3;
}

/* host 行的网卡 chip */
.card-addr .host-text { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.iface-chip {
  display: inline-block; padding: 1px 7px;
  background: rgba(59, 130, 246, 0.08);
  color: #3b82f6;
  border-radius: 999px;
  font-size: 11px; font-weight: 500;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, sans-serif;
  flex-shrink: 0;
}
.agent-ver { display: none; }
.probe-foot { display: none; }

.field-hint {
  font-size: 12px; color: #9ca3af; margin-top: 4px;
}

/* 编辑/新增抽屉:与 SettingsDialog 风格统一 */
.edit-form { padding: 4px 8px 0 0; }
.edit-form .section-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  padding: 0 0 12px;
  margin-bottom: 16px;
  border-bottom: 2px solid #6366f1;
  display: inline-block;
  min-width: 80px;
}
.edit-form .section-title:not(:first-child) { margin-top: 22px; }

/* 空状态 */
.empty {
  background: #fff;
  border-radius: 10px;
  padding: 60px 20px;
  border: 1px dashed #e5e7eb;
}

/* 详情抽屉 */
.detail { padding: 4px 8px 20px; }
.log-header {
  display: flex; justify-content: space-between; align-items: center;
  margin: 16px 0 8px;
  font-weight: 600; color: #374151;
}
.log-box {
  background: #1e1e1e; color: #d4d4d4;
  padding: 12px; border-radius: 6px;
  font-family: ui-monospace, Menlo, Consolas, monospace;
  font-size: 12px; line-height: 1.5;
  max-height: 50vh; overflow: auto;
  white-space: pre-wrap; word-break: break-all;
  margin: 0;
}

/* 危险菜单项 */
:deep(.danger-item) { color: #ef4444; }

/* 小屏 */
@media (max-width: 640px) {
  .toolbar { padding: 12px; }
  .stats { gap: 18px; }
  .stat-num { font-size: 18px; }
  .grid { grid-template-columns: 1fr; gap: 10px; }
}
/* ─── 节点分类 tabs ─── */
.kind-tabs {
  display: flex;
  gap: 4px;
  margin: 0 0 16px;
  padding: 4px;
  background: #f3f4f6;
  border-radius: 10px;
  width: fit-content;
}
.kind-tab {
  appearance: none;
  border: 0;
  background: transparent;
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 500;
  color: #6b7280;
  border-radius: 7px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: background .15s, color .15s;
  font-family: inherit;
}
.kind-tab:hover { color: #1f2937; }
.kind-tab.active {
  background: #fff;
  color: #1f2937;
  box-shadow: 0 1px 2px rgba(0,0,0,.06);
}
.kind-tab-count {
  font-size: 11.5px;
  color: #9ca3af;
  background: #e5e7eb;
  padding: 1px 7px;
  border-radius: 999px;
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.kind-tab.active .kind-tab-count {
  background: #eef2ff;
  color: #6366f1;
}

/* ─── kind 徽章(卡片左上角) ─── */
.kind-chip {
  display: inline-block;
  font-size: 10.5px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  margin-right: 4px;
  vertical-align: 1px;
  letter-spacing: 0.3px;
  line-height: 1.5;
}
.kind-chip.kind-landing {
  background: #eef2ff;
  color: #4f46e5;
}
.kind-chip.kind-soga {
  background: #fff7ed;
  color: #ea580c;
}
.kind-chip.kind-other {
  background: #f3f4f6;
  color: #6b7280;
}

</style>
