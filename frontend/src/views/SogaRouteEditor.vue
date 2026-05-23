<template>
  <el-drawer
    v-model="visible"
    :title="`路由配置 · ${instTitle}`"
    size="560px"
    direction="rtl"
    :modal="true"
    class="route-editor-drawer"
    :before-close="onBeforeClose"
  >
    <div v-loading="loading" class="ss-form">
      <el-form label-width="92px" label-position="right">

        <!-- 路由列表 -->
        <div class="section">
          <div class="section-head">
            <div class="section-title">用户路由</div>
            <el-button size="small" type="primary" plain :icon="Plus" @click="addRoute">添加路由</el-button>
          </div>

          <div class="route-list">
            <div v-if="!userRoutes.length && fallbackRoute" class="empty-state">
              还没有用户路由,所有流量走兜底
            </div>

            <draggable
              v-model="userRoutes"
              item-key="_uid"
              handle=".drag-handle"
              animation="180"
              ghost-class="dragging-ghost"
              class="drag-zone"
            >
              <template #item="{ element, index }">
                <RouteCard
                  :route="element"
                  :index="index"
                  :landings="landings"
                  :draggable="true"
                  @remove="removeUserRoute(index)"
                />
              </template>
            </draggable>

            <RouteCard
              v-if="fallbackRoute"
              :route="fallbackRoute"
              :index="userRoutes.length"
              :landings="landings"
              locked-rules
              locked-position
            />
          </div>
        </div>

      </el-form>

      <div class="actions">
        <span class="dirty-hint" v-if="dirty">未保存</span>
        <span class="spacer" />
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">
          保存并推送
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import draggable from 'vuedraggable'
import http from '../api.js'
import RouteCard from './RouteCard.vue'

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const instance = ref(null)
const routes = ref([])
const original = ref('')
const landings = ref([])

const fallbackRoute = computed(() => routes.value.find(r => r.is_fallback))
const userRoutes = computed({
  get: () => routes.value.filter(r => !r.is_fallback),
  set: (arr) => {
    const fb = fallbackRoute.value ? [fallbackRoute.value] : []
    routes.value = [...arr, ...fb]
  },
})

const instTitle = computed(() => {
  if (!instance.value) return ''
  return instance.value.display_name || instance.value.folder_name
})

const dirty = computed(() => JSON.stringify(routes.value) !== original.value)

async function open(instanceId) {
  visible.value = true
  loading.value = true
  routes.value = []
  try {
    const [r1, r2] = await Promise.all([
      http.get(`/soga/instances/${instanceId}/routes`),
      http.get('/nodes'),
    ])
    instance.value = r1.instance
    routes.value = (r1.routes || []).map(normalize)
    if (!routes.value.some(r => r.is_fallback)) {
      routes.value.push(makeFallback())
    }
    original.value = JSON.stringify(routes.value)
    landings.value = (r2.data || []).filter(n => (n.kind || 'landing') === 'landing')
  } catch (e) {
    ElMessage.error('加载失败')
    visible.value = false
  } finally {
    loading.value = false
  }
}

let uidCounter = 1
function normalize(r) {
  return {
    _uid: uidCounter++,
    rules: r.rules || [],
    balance: r.balance || 'ip_hash',
    is_fallback: !!r.is_fallback,
    remark: r.remark || '',
    outs: (r.outs || []).map(o => ({ landing_node_id: o.landing_node_id })),
  }
}

function makeFallback() {
  return {
    _uid: uidCounter++,
    rules: ['*'],
    balance: 'ip_hash',
    is_fallback: true,
    remark: '兜底',
    outs: [],
  }
}

function addRoute() {
  const newR = {
    _uid: uidCounter++,
    rules: [],
    balance: 'ip_hash',
    is_fallback: false,
    remark: '',
    outs: [],
  }
  const arr = userRoutes.value.slice()
  arr.push(newR)
  userRoutes.value = arr
}

function removeUserRoute(idx) {
  const arr = userRoutes.value.slice()
  arr.splice(idx, 1)
  userRoutes.value = arr
}

async function save() {
  for (const r of routes.value) {
    if (!r.rules.length) {
      ElMessage.error('每条路由至少一条规则')
      return
    }
    for (const rule of r.rules) {
      if (!rule || rule === ':' || rule.endsWith(':')) {
        ElMessage.error(`规则不完整: "${rule}"`)
        return
      }
    }
    if (!r.outs.length) {
      ElMessage.error('每条路由至少一个落地出站')
      return
    }
  }

  saving.value = true
  try {
    const payload = {
      routes: routes.value.map(r => ({
        rules: r.rules,
        balance: r.balance,
        is_fallback: r.is_fallback,
        remark: r.remark || null,
        outs: r.outs.map(o => ({ landing_node_id: o.landing_node_id })),
      })),
    }
    const r = await http.put(`/soga/instances/${instance.value.id}/routes`, payload)
    ElMessage.success(`已推送 (${r.bytes} 字节)`)
    original.value = JSON.stringify(routes.value)
    visible.value = false
  } catch (e) {
    const msg = e?.response?.data?.detail || e?.message || '保存失败'
    ElMessage.error(msg)
  } finally {
    saving.value = false
  }
}

async function onBeforeClose(done) {
  if (!dirty.value) return done()
  try {
    await ElMessageBox.confirm('有未保存的改动,确定关闭?', '提示', { type: 'warning' })
    done()
  } catch {}
}

defineExpose({ open })
</script>

<style scoped>
/* —— 与 SSConfigDrawer 同款骨架 —— */
:deep(.route-editor-drawer .el-drawer) { resize: none !important; }
:deep(.route-editor-drawer .el-drawer__body) {
  overflow-y: auto;
  overflow-x: hidden;
}

.ss-form { padding: 0 20px 12px; }

.section { margin-bottom: 24px; }
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
}
.section-head .section-title { margin-bottom: 0; }
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

.row-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.meta {
  font-size: 12px;
  color: #6b7280;
  font-variant-numeric: tabular-nums;
}

.route-list,
.drag-zone {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.empty-state {
  padding: 16px;
  text-align: center;
  color: #9ca3af;
  font-size: 12.5px;
  background: #f9fafb;
  border-radius: 6px;
  border: 1px dashed #e5e7eb;
}

.dragging-ghost {
  opacity: 0.4;
  background: rgba(99, 102, 241, 0.06);
}

/* 底部 actions 与 SSConfigDrawer 一致 */
.actions {
  display: flex;
  align-items: center;
  margin-top: 24px;
  padding: 16px 0 0;
  gap: 8px;
  border-top: 1px solid #f1f2f5;
}
.spacer { flex: 1; }

.dirty-hint {
  font-size: 12px;
  color: #6366f1;
  font-weight: 500;
}
</style>
