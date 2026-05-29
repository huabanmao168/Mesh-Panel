<template>
  <el-drawer
    v-model="visible"
    :title="`Soga 路由配置 · ${node?.name || ''}`"
    :size="drawerSize"
    direction="rtl"
    :modal="true"
    class="soga-drawer"
  >
    <div v-loading="loadingNode" class="ss-form">
      <el-form label-width="92px" label-position="right" @submit.prevent>

        <!-- 入口机 -->
        <div class="section">
          <div class="section-head">
            <div class="section-title">入口机</div>
            <span class="meta">
              <template v-if="lastScannedAt">上次扫描 <strong>{{ relTime(lastScannedAt) }}</strong> · <strong>{{ instances.length }}</strong> 个实例</template>
              <template v-else-if="instances.length"><strong>{{ instances.length }}</strong> 个实例 · 尚未扫描</template>
              <template v-else>尚未加载</template>
            </span>
          </div>
          <div class="entry-actions">
            <el-button :icon="Refresh" :loading="scanning" @click="scan">加载配置</el-button>
            <el-button
              type="primary"
              plain
              :icon="Promotion"
              :loading="pushingAll"
              :disabled="!instances.length"
              @click="pushAll"
            >重新推送</el-button>
          </div>
        </div>

        <!-- 系统探活规则 (折叠) -->
        <div class="section">
          <div class="section-title">
            系统探活
            <span v-if="systemProbeCustom" class="custom-tag">自定义</span>
          </div>
          <el-form-item label="探活规则">
            <div class="probe-collapsed" v-if="!probeExpanded">
              <span class="probe-summary"><strong>{{ probeRulesCount }}</strong> 条规则</span>
              <el-button size="small" text type="primary" @click="probeExpanded = true">编辑</el-button>
            </div>
            <div v-else class="probe-edit">
              <el-input
                v-model="probeRulesText"
                type="textarea"
                :rows="7"
                :placeholder="defaultProbeHint"
                resize="none"
              />
              <div class="probe-actions">
                <el-button
                  size="small"
                  type="primary"
                  :loading="probeBusy"
                  :disabled="!probeRulesDirty"
                  @click="saveProbeRules"
                >保存</el-button>
                <el-button size="small" plain :loading="probeBusy" @click="resetProbeRules">恢复默认</el-button>
                <span class="spacer" />
                <el-button size="small" text @click="cancelProbeEdit">收起</el-button>
              </div>
            </div>
          </el-form-item>
        </div>

        <!-- 实例列表 -->
        <div class="section">
          <div class="section-title">Soga 实例</div>

          <div v-if="!instances.length && !scanning" class="empty-state">
            点上方「加载配置」拉取入口机上的实例
          </div>

          <div v-else class="inst-wrap">
            <div class="prefix-tabs">
              <button
                v-for="g in groupedInstances"
                :key="g.prefix"
                type="button"
                class="prefix-tab"
                :class="{ active: activePrefix === g.prefix }"
                @click="activePrefix = g.prefix"
              >
                <span class="prefix-name">{{ g.prefix }}</span>
                <span class="prefix-num">{{ g.items.length }}</span>
              </button>
            </div>
            <draggable
              v-model="dragList"
              :item-key="(it) => it.id || it.folder_name"
              tag="div"
              class="inst-list"
              handle=".inst-drag"
              @end="onDragEnd"
            >
              <template #item="{ element: inst }">
              <div
                class="inst-card"
                :class="{ disabled: inst.enabled === false }"
              >
                <span class="inst-drag" title="拖动排序">⠿</span>
                <div class="inst-main">
                  <div class="inst-title">
                    <template v-if="editingId === inst.id">
                      <el-input
                        v-model="editingName"
                        size="small"
                        class="alias-input"
                        maxlength="64"
                        placeholder="备注别名,如 香港主线"
                        @keyup.enter="saveAlias(inst)"
                        @keyup.esc="cancelAlias"
                        @blur="saveAlias(inst)"
                        ref="aliasInputRef"
                      />
                    </template>
                    <template v-else>
                      <span v-if="inst.display_name" class="alias">{{ inst.display_name }}</span>
                      <span class="folder" :class="{ muted: !!inst.display_name }">{{ inst.folder_name }}</span>
                      <el-button
                        size="small"
                        text
                        class="alias-btn"
                        :disabled="inst.enabled === false"
                        @click="startEditAlias(inst)"
                        :title="inst.display_name ? '修改别名' : '设置别名'"
                      >
                        <el-icon><Edit /></el-icon>
                      </el-button>
                    </template>
                  </div>
                  <span class="route-count"><strong>{{ inst.route_count ?? '?' }}</strong> 条用户路由</span>
                  <el-tag v-if="inst.enabled === false" size="small" type="info" effect="plain">已消失</el-tag>
                </div>

                <!-- 主操作:配置路由 -->
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :disabled="inst.enabled === false"
                  @click="openRouteEditor(inst.id)"
                >配置路由</el-button>

                <!-- 次要操作收进三点菜单 -->
                <el-dropdown
                  trigger="click"
                  @command="(cmd) => onInstMenu(cmd, inst)"
                  :disabled="inst.enabled === false && !canDeleteOnly(inst)"
                >
                  <el-button size="small" plain class="more-btn" :title="'更多操作'">
                    <el-icon><MoreFilled /></el-icon>
                  </el-button>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item
                        command="edit-conf"
                        :disabled="inst.enabled === false"
                      >
                        <el-icon><Edit /></el-icon>编辑配置
                      </el-dropdown-item>
                      <el-dropdown-item
                        command="push"
                        :disabled="inst.enabled === false || pendingSourceChanged(inst) || (inst.route_source || 'file') !== 'file'"
                      >
                        <el-icon><Promotion /></el-icon>重新推送
                      </el-dropdown-item>
                      <el-dropdown-item
                        command="restart"
                        :disabled="inst.enabled === false"
                      >
                        <el-icon><Refresh /></el-icon>重启实例
                      </el-dropdown-item>
                      <el-dropdown-item
                        v-if="inst.enabled === false"
                        command="delete"
                        divided
                        class="danger-item"
                      >
                        <el-icon><Delete /></el-icon>删除实例
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>

                <!-- 路由分发 -->
                <div class="route-source-block">
                  <el-radio-group
                    v-model="sourceDraft[inst.id]"
                    size="small"
                    :disabled="inst.enabled === false || sourceBusy === inst.id"
                  >
                    <el-radio value="http">HTTP 拉取</el-radio>
                    <el-radio value="file">本地文件</el-radio>
                  </el-radio-group>
                  <el-button
                    size="small"
                    type="primary"
                    :loading="sourceBusy === inst.id"
                    :disabled="!canApplySource(inst)"
                    :title="applyDisabledHint(inst)"
                    @click="applySource(inst)"
                  >应用</el-button>

                  <div class="route-source-detail" v-if="(sourceDraft[inst.id] || inst.route_source || 'file') === 'http' && !panelPublicUrl">
                    <div class="url-row muted">
                      请先在系统设置填写「面板公网地址」
                    </div>
                  </div>
                </div>
              </div>
              </template>
            </draggable>
          </div>
        </div>

      </el-form>
    </div>

    <SogaRouteEditor ref="routeEditorRef" />
    <SogaConfEditor ref="confEditorRef" />
  </el-drawer>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Promotion, Edit, Delete, MoreFilled } from '@element-plus/icons-vue'
import http from '../api.js'
import { settingsApi } from '../api.js'
import draggable from 'vuedraggable'
import SogaRouteEditor from './SogaRouteEditor.vue'
import SogaConfEditor from './SogaConfEditor.vue'

const routeEditorRef = ref(null)
function openRouteEditor(instanceId) {
  routeEditorRef.value?.open(instanceId)
}
const confEditorRef = ref(null)
function openConfEditor(instanceId) {
  confEditorRef.value?.open(instanceId)
}

const visible = ref(false)
const loadingNode = ref(false)
const node = ref(null)
const instances = ref([])
const scanning = ref(false)
const lastScannedAt = ref(null)
const pushingAll = ref(false)
const pushingId = ref(null)
const restartingId = ref(null)
const deletingId = ref(null)

const editingId = ref(null)
const editingName = ref('')
const aliasInputRef = ref(null)
function startEditAlias(inst) {
  editingId.value = inst.id
  editingName.value = inst.display_name || ''
  // 等 input mount 后 focus
  setTimeout(() => {
    const el = Array.isArray(aliasInputRef.value) ? aliasInputRef.value[0] : aliasInputRef.value
    el?.focus?.()
  }, 30)
}
function cancelAlias() {
  editingId.value = null
  editingName.value = ''
}
let aliasSaving = false
async function saveAlias(inst) {
  if (aliasSaving) return
  if (editingId.value !== inst.id) return
  const next = editingName.value.trim()
  const cur = (inst.display_name || '').trim()
  if (next === cur) { cancelAlias(); return }
  aliasSaving = true
  try {
    const r = await http.patch(`/soga/instances/${inst.id}`, { display_name: next })
    inst.display_name = r.instance?.display_name ?? (next || null)
    if (next) ElMessage.success('已保存别名')
    else ElMessage.success('已清除别名')
  } catch (e) {
  } finally {
    aliasSaving = false
    cancelAlias()
  }
}

async function pushOne(inst) {
  if (pushingId.value) return
  pushingId.value = inst.id
  try {
    await http.post(`/soga/instances/${inst.id}/push`)
    ElMessage.success(`已推送 ${inst.folder_name}`)
  } catch (e) {
  } finally {
    pushingId.value = null
  }
}

async function restartOne(inst) {
  if (restartingId.value) return
  try {
    await ElMessageBox.confirm(`重启 Soga 实例 ${inst.folder_name}?`, '重启实例', { type: 'warning' })
  } catch { return }
  restartingId.value = inst.id
  try {
    const data = await http.post(`/soga/instances/${inst.id}/restart`)
    if (data?.ok) ElMessage.success(`已重启 ${inst.folder_name}`)
    else ElMessage.warning(data?.output || '重启返回异常')
  } catch (e) {
  } finally {
    restartingId.value = null
  }
}

async function removeOne(inst) {
  if (deletingId.value) return
  try {
    await ElMessageBox.confirm(
      `永久删除已消失实例 ${inst.folder_name}?\n关联的用户路由和落地映射也会一起清掉,操作不可恢复。`,
      '删除实例',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' },
    )
  } catch { return }
  deletingId.value = inst.id
  try {
    await http.delete(`/soga/instances/${inst.id}`)
    ElMessage.success(`已删除 ${inst.folder_name}`)
    // 从本地列表移除,不用整个重拉
    instances.value = instances.value.filter(x => x.id !== inst.id)
  } catch (e) {
  } finally {
    deletingId.value = null
  }
}

// 三点菜单:已消失实例只允许"删除"命中,enabled 实例其它项都可点
function canDeleteOnly(inst) {
  return inst.enabled === false
}

function onInstMenu(cmd, inst) {
  if (cmd === 'edit-conf') return openConfEditor(inst.id)
  if (cmd === 'push') return pushOne(inst)
  if (cmd === 'restart') return restartOne(inst)
  if (cmd === 'delete') return removeOne(inst)
}

// ─── 路由分发模式 ───────────────────────────────────────────────────────
const panelPublicUrl = ref('')        // 系统设置里的面板公网地址
const sourceDraft = ref({})           // {instId: 'file'|'http'} 用户选中但未应用
const sourceBusy = ref(null)          // 正在切换的 instId

function pendingSourceChanged(inst) {
  const cur = inst.route_source || 'file'
  const draft = sourceDraft.value[inst.id] || cur
  return draft !== cur
}
function canApplySource(inst) {
  if (inst.enabled === false) return false
  if (sourceBusy.value) return false
  const cur = inst.route_source || 'file'
  const draft = sourceDraft.value[inst.id] || cur
  if (draft === cur) return false
  if (draft === 'http' && !panelPublicUrl.value) return false
  return true
}
function applyDisabledHint(inst) {
  const cur = inst.route_source || 'file'
  const draft = sourceDraft.value[inst.id] || cur
  if (draft === cur) return '未改变,无需应用'
  if (draft === 'http' && !panelPublicUrl.value) return '请先在系统设置填写面板公网地址'
  return ''
}
async function applySource(inst) {
  const draft = sourceDraft.value[inst.id]
  if (!draft) return
  sourceBusy.value = inst.id
  try {
    const r = await http.post(`/soga/instances/${inst.id}/route-source`, { mode: draft })
    inst.route_source = r.route_source
    inst.routes_token = r.routes_token
    sourceDraft.value[inst.id] = r.route_source
    if (r.restarted) {
      ElMessage.success(`已切换到「${draft === 'http' ? 'HTTP 拉取' : '本地文件'}」并重启 Soga`)
    } else {
      ElMessage.warning(`已切换但重启失败: ${r.restart_output || '未知错误'}`)
    }
  } catch (e) {
  } finally {
    sourceBusy.value = null
  }
}

const systemProbeRules = ref([])      // 服务端当前生效列表
const systemProbeCustom = ref(false)
const probeRulesText = ref('')        // textarea 双向绑定
const probeBusy = ref(false)
const probeExpanded = ref(false)

function cancelProbeEdit() {
  probeRulesText.value = systemProbeRules.value.join('\n')
  probeExpanded.value = false
}

const defaultProbeHint =
  'domain:cp.cloudflare.com\ndomain:connectivitycheck.gstatic.com\ndomain:www.gstatic.com\n…每行一条 · domain:/geosite:/geoip: 前缀'

const winW = ref(window.innerWidth)
const _onResize = () => (winW.value = window.innerWidth)
window.addEventListener('resize', _onResize)

import { onBeforeUnmount } from 'vue'
onBeforeUnmount(() => { window.removeEventListener('resize', _onResize) })

const drawerSize = computed(() => winW.value < 720 ? '94%' : '560px')

const probeRulesCount = computed(
  () => probeRulesText.value.split('\n').map(s => s.trim()).filter(Boolean).length,
)

const groupedInstances = computed(() => {
  const groups = new Map()
  for (const inst of instances.value) {
    const name = inst.folder_name || ''
    const idx = name.indexOf('-')
    const prefix = idx > 0 ? name.slice(0, idx) : (name || '其它')
    if (!groups.has(prefix)) groups.set(prefix, [])
    groups.get(prefix).push(inst)
  }
  return [...groups.entries()]
    .map(([prefix, items]) => ({ prefix, items }))
    .sort((a, b) => a.prefix.localeCompare(b.prefix))
})

const activePrefix = ref('')
const activeGroupItems = computed(
  () => groupedInstances.value.find(g => g.prefix === activePrefix.value)?.items || []
)
// draggable 用本地副本,@end 后调 API 持久化
const dragList = ref([])
watch(activeGroupItems, (v) => { dragList.value = [...v] }, { immediate: true })
async function onDragEnd() {
  if (!node.value?.id) return
  const ids = dragList.value.map(i => i.id).filter(Boolean)
  if (!ids.length) return
  dragList.value.forEach((it, idx) => {
    const local = instances.value.find(i => i.id === it.id)
    if (local) local.sort_order = idx * 10
  })
  try {
    await http.put(`/soga/${node.value.id}/instances/order`, { ids }, { _suppressToast: true })
  } catch (e) {
    ElMessage.error('排序保存失败: ' + (e?.response?.data?.detail || e.message))
  }
}
watch(groupedInstances, (gs) => {
  if (!gs.length) { activePrefix.value = ''; return }
  if (!gs.find(g => g.prefix === activePrefix.value)) activePrefix.value = gs[0].prefix
}, { immediate: true })
const probeRulesDirty = computed(() => {
  const arr = probeRulesText.value.split('\n').map(s => s.trim()).filter(Boolean)
  return JSON.stringify(arr) !== JSON.stringify(systemProbeRules.value)
})

async function open(nodeId) {
  visible.value = true
  node.value = null
  instances.value = []
  loadingNode.value = true
  try {
    const r = await http.get(`/nodes/${nodeId}`, { _suppressToast: true })
    node.value = r.data || r
  } catch {
    ElMessage.error('加载节点失败')
    loadingNode.value = false
    return
  }
  try {
    const r = await http.get(`/soga/${nodeId}/instances`)
    instances.value = r.instances || []
    syncSourceDraft()
    applyProbeFromResp(r)
  } catch {
    // 没扫过 → 空
  } finally {
    loadingNode.value = false
  }
  // 拉系统设置里的 panel_public_url(每次打开都拉一次,免得用户改了不刷新)
  try {
    const s = await settingsApi.get()
    panelPublicUrl.value = (s.data?.panel_public_url || '').replace(/\/+$/, '')
  } catch {
    panelPublicUrl.value = ''
  }
}

function syncSourceDraft() {
  const d = {}
  for (const inst of instances.value) {
    d[inst.id] = inst.route_source || 'file'
  }
  sourceDraft.value = d
}

function applyProbeFromResp(r) {
  systemProbeRules.value = r.system_probe_rules || []
  systemProbeCustom.value = !!r.system_probe_custom
  probeRulesText.value = systemProbeRules.value.join('\n')
  if (r.last_scanned_at) {
    lastScannedAt.value = new Date(r.last_scanned_at.endsWith('Z') ? r.last_scanned_at : r.last_scanned_at + 'Z').getTime()
  }
}

async function scan() {
  if (!node.value) return
  scanning.value = true
  try {
    await http.post(`/soga/${node.value.id}/scan`)
    const r = await http.get(`/soga/${node.value.id}/instances`)
    instances.value = r.instances || []
    syncSourceDraft()
    applyProbeFromResp(r)
    ElMessage.success(`已加载 ${instances.value.length} 个实例`)
  } catch (e) {
  } finally {
    scanning.value = false
  }
}

async function saveProbeRules() {
  const cleaned = probeRulesText.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (!cleaned.length) {
    ElMessage.error('至少保留一条规则,否则点「恢复默认」')
    return
  }
  for (const r of cleaned) {
    if (!/^(domain|geosite|geoip):/.test(r)) {
      ElMessage.error(`格式错误: "${r}",必须 domain:/geosite:/geoip: 开头`)
      return
    }
  }
  probeBusy.value = true
  try {
    const r = await http.patch(`/soga/${node.value.id}/system-probe`, { rules: cleaned })
    systemProbeRules.value = r.rules || cleaned
    systemProbeCustom.value = !!r.custom
    probeRulesText.value = systemProbeRules.value.join('\n')
    ElMessage.success('已保存 · 用「重新推送」下发到实例')
  } catch (e) {
  } finally {
    probeBusy.value = false
  }
}

async function resetProbeRules() {
  try {
    await ElMessageBox.confirm(
      '恢复默认探活规则?',
      '恢复默认',
      { type: 'warning' },
    )
  } catch { return }
  probeBusy.value = true
  try {
    const r = await http.patch(`/soga/${node.value.id}/system-probe`, { rules: null })
    systemProbeRules.value = r.rules || []
    systemProbeCustom.value = !!r.custom
    probeRulesText.value = systemProbeRules.value.join('\n')
    ElMessage.success('已恢复默认')
  } catch (e) {
  } finally {
    probeBusy.value = false
  }
}

async function pushAll() {
  try {
    await ElMessageBox.confirm(
      `推送到 ${instances.value.length} 个实例?`,
      '重新推送',
      { type: 'warning' },
    )
  } catch { return }
  pushingAll.value = true
  try {
    const r = await http.post(`/soga/${node.value.id}/push-all`)
    if (r.failed?.length) {
      ElMessage.warning(`${r.pushed}/${r.total} 成功 · ${r.failed.length} 失败: ${r.failed.map(f => f.folder).join(', ')}`)
    } else {
      ElMessage.success(`已推送 ${r.pushed} 个实例`)
    }
  } catch (e) {
  } finally {
    pushingAll.value = false
  }
}

function relTime(ts) {
  const d = Math.floor((Date.now() - ts) / 1000)
  if (d < 5) return '刚刚'
  if (d < 60) return `${d} 秒前`
  if (d < 3600) return `${Math.floor(d/60)} 分钟前`
  if (d < 86400) return `${Math.floor(d/3600)} 小时前`
  if (d < 86400 * 7) return `${Math.floor(d/86400)} 天前`
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}

defineExpose({ open })
</script>

<style scoped>
/* —— 跟 SSConfigDrawer 同款骨架 —— */
:deep(.soga-drawer .el-drawer) { resize: none !important; }
:deep(.soga-drawer .el-drawer__body) {
  overflow-y: auto;
  overflow-x: hidden;
}

.ss-form { padding: 0 20px 12px; }

.section { margin-bottom: 24px; }
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 12px;
  gap: 12px;
}
.section-head .section-title { margin-bottom: 0; }
.inst-drag {
  cursor: grab;
  color: #94a3b8;
  font-size: 16px;
  user-select: none;
  padding: 0 4px;
  letter-spacing: -2px;
}
.inst-drag:hover { color: #6366f1; }
.inst-drag:active { cursor: grabbing; }

.entry-actions {
  display: flex;
  gap: 8px;
}
.entry-actions .el-button { flex: 0 0 auto; }
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2937;
  padding: 0 0 12px;
  margin-bottom: 16px;
  border-bottom: 2px solid #6366f1;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 80px;
}
.custom-tag {
  font-size: 10px;
  font-weight: 500;
  padding: 1px 6px;
  background: rgba(99, 102, 241, 0.1);
  color: #4f46e5;
  border-radius: 4px;
  letter-spacing: 0.3px;
}

.probe-collapsed {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}
.probe-summary {
  flex: 1;
  font-size: 12.5px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}
.probe-edit {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.probe-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.probe-actions .spacer { flex: 1; }

:deep(.el-form-item) { margin-bottom: 14px; }
:deep(.el-form-item__label) { color: #4b5563; font-weight: 500; }
:deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.7;
}

.row-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  width: 100%;
}
.meta {
  font-size: 12px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}
.meta strong,
.probe-summary strong,
.route-count strong {
  font-weight: 600;
  color: #1f2937;
  font-variant-numeric: tabular-nums;
}

/* 实例列表 */
.empty-state {
  padding: 24px 16px;
  text-align: center;
  color: #9ca3af;
  font-size: 13px;
  background: #f9fafb;
  border-radius: 6px;
  border: 1px dashed #e5e7eb;
}
.inst-wrap { display: flex; flex-direction: column; gap: 12px; }
.prefix-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding-bottom: 8px;
  border-bottom: 1px solid #eef0f3;
}
.prefix-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid #e5e7eb;
  background: #fff;
  border-radius: 13px;
  cursor: pointer;
  font-size: 12px;
  color: #4b5563;
  transition: all .15s;
}
.prefix-tab:hover { border-color: #c7d2fe; color: #1f2937; }
.prefix-tab.active {
  background: #eef2ff;
  border-color: #c7d2fe;
  color: #4f46e5;
}
.prefix-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.prefix-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 16px;
  height: 14px;
  padding: 0 4px;
  border-radius: 7px;
  background: rgba(0,0,0,0.05);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  color: inherit;
  opacity: 0.85;
}
.prefix-tab.active .prefix-num { background: rgba(79,70,229,0.15); }

.inst-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.inst-card {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border: 1px solid #e8eaed;
  border-radius: 6px;
  padding: 10px 12px;
  background: #fff;
  transition: border-color .12s;
}
.inst-card:hover { border-color: #c7d2fe; }
.inst-card.disabled { opacity: 0.5; }

.route-source-block {
  flex-basis: 100%;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 12px;
  padding: 10px 0 2px;
  margin-top: 4px;
  border-top: 1px dashed #eef0f3;
}
.route-source-block :deep(.el-radio) { margin-right: 8px; }
.route-source-block :deep(.el-radio__label) {
  font-size: 12.5px;
  color: #4b5563;
  font-weight: 500;
}
.route-source-detail {
  flex-basis: 100%;
  padding-top: 2px;
}
.url-row {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #f8fafc;
  border: 1px solid #eef0f3;
  border-radius: 4px;
  padding: 6px 10px;
}
.url-row .url {
  flex: 1;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  color: #1f2937;
  word-break: break-all;
  background: transparent;
  padding: 0;
  font-variant-numeric: tabular-nums;
}
.url-row.muted {
  background: transparent;
  border: none;
  padding: 4px 0;
  color: #9ca3af;
  font-size: 12px;
}
.inst-main {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  flex: 1 1 auto;
}
.inst-title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  width: 100%;
}
.alias {
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.folder {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}
.folder.muted {
  font-size: 11px;
  font-weight: 500;
  color: #9ca3af;
}
.alias-btn {
  padding: 0 4px !important;
  height: 22px;
  color: #9ca3af;
  margin-left: -2px;
}
.alias-btn:hover { color: #4f46e5; }
.del-btn {
  padding: 0 4px;
  min-height: 22px;
  color: #9ca3af;
}
.del-btn:hover { color: #ef4444; }
.more-btn {
  padding: 5px 8px !important;
  color: #6b7280;
}
.more-btn:hover { color: #4f46e5; border-color: #c7d2fe; }
/* dropdown 内的危险项 - 红字 hover 浅红底 */
:deep(.el-dropdown-menu__item.danger-item) {
  color: #ef4444;
}
:deep(.el-dropdown-menu__item.danger-item:not(.is-disabled):hover) {
  background-color: #fef2f2;
  color: #dc2626;
}
:deep(.el-dropdown-menu__item .el-icon) {
  margin-right: 6px;
}
.alias-input { width: 200px; }
.route-count {
  font-size: 12px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}
.inst-card .el-button + .el-button { margin-left: 6px; }
</style>
