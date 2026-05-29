<template>
  <div
    class="route-card"
    :class="{
      'is-fallback': route.is_fallback,
      'is-user': !route.is_fallback,
    }"
  >
    <!-- 头部 -->
    <div class="card-head">
      <span v-if="draggable" class="drag-handle" title="拖动排序" @click.stop>
        <el-icon><Rank /></el-icon>
      </span>
      <span class="idx">#{{ index + 1 }}</span>

      <span v-if="route.is_fallback" class="kind-chip kind-fb">兜底</span>

      <!-- 兜底显示备注只读 (不包括默认 "兜底" 字样) -->
      <span v-if="lockedPosition && route.remark && route.remark !== '兜底'" class="head-meta">{{ route.remark }}</span>

      <div class="head-actions" @click.stop>
        <button
          v-if="draggable"
          type="button"
          class="icon-btn icon-btn-danger"
          title="删除此路由"
          @click="$emit('remove')"
        >
          <el-icon><Delete /></el-icon>
        </button>
      </div>
    </div>

    <div class="card-body">
      <!-- 规则 -->
      <div class="field">
        <div class="field-label">规则</div>
        <div class="field-control">
          <div v-if="lockedRules" class="rules-ro">
            <span v-for="(rule, i) in route.rules" :key="i" class="rule-chip">{{ rule }}</span>
          </div>
          <div v-else class="rules-edit">
            <div v-for="(rule, i) in parsedRules" :key="rule._uid" class="rule-row">
              <el-select
                v-model="rule.prefix"
                size="small"
                class="rule-prefix"
                @change="syncRules"
              >
                <el-option label="domain" value="domain" />
                <el-option label="geosite" value="geosite" />
                <el-option label="geoip" value="geoip" />
              </el-select>
              <el-input
                v-model="rule.value"
                size="small"
                class="rule-value"
                :placeholder="rulePlaceholder(rule.prefix)"
                @blur="syncRules"
                @change="syncRules"
              />
              <button
                type="button"
                class="icon-btn icon-btn-ghost"
                title="移除此规则"
                @click="removeRule(i)"
              >
                <el-icon><Close /></el-icon>
              </button>
            </div>
            <button type="button" class="add-rule-btn" @click="addRule">
              <el-icon><Plus /></el-icon><span>添加规则</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 落地池 + 负载均衡 -->
      <div class="field">
        <div class="field-label">落地</div>
        <div class="field-control field-control-row">
          <el-select
            :model-value="route.outs.map(o => o.landing_node_id)"
            multiple
            filterable
            placeholder="选择落地节点"
            size="small"
            class="landing-select"
            @update:model-value="onLandingChange"
          >
            <el-option
              v-for="n in landings"
              :key="n.id"
              :label="n.name"
              :value="n.id"
            >
              <span>{{ n.name }}</span>
              <span class="opt-host">{{ n.host }}</span>
            </el-option>
          </el-select>

          <el-select
            v-if="route.outs.length > 1"
            v-model="route.balance"
            size="small"
            class="balance-select"
          >
            <el-option label="ip_hash" value="ip_hash" />
            <el-option label="random" value="random" />
            <el-option label="round_robin" value="round_robin" />
          </el-select>
        </div>
      </div>

      <!-- 备注 (兜底不显示) -->
      <div v-if="!lockedPosition" class="field">
        <div class="field-label">备注</div>
        <div class="field-control">
          <el-input
            v-model="route.remark"
            size="small"
            placeholder="Netflix / ChatGPT / 自用…"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Plus, Close, Delete, Rank } from '@element-plus/icons-vue'

const props = defineProps({
  route: { type: Object, required: true },
  index: { type: Number, required: true },
  landings: { type: Array, default: () => [] },
  draggable: { type: Boolean, default: false },
  lockedRules: { type: Boolean, default: false },
  lockedPosition: { type: Boolean, default: false },
})

defineEmits(['remove'])

const parsedRules = ref([])
let lastSyncedJson = ''

let _uidSeq = 0
function parse(rules) {
  return rules.map(r => {
    if (r === '*') return { _uid: ++_uidSeq, prefix: 'domain', value: '*' }
    const idx = r.indexOf(':')
    if (idx < 0) return { _uid: ++_uidSeq, prefix: 'domain', value: r }
    return { _uid: ++_uidSeq, prefix: r.slice(0, idx), value: r.slice(idx + 1) }
  })
}

watch(() => props.route.rules, (newR) => {
  if (props.lockedRules) return
  const newJson = JSON.stringify(newR || [])
  if (newJson === lastSyncedJson) return
  parsedRules.value = parse(newR || [])
  lastSyncedJson = newJson
}, { immediate: true })

function syncRules() {
  const out = parsedRules.value
    .filter(r => r.value && r.value.trim())
    .map(r => `${r.prefix}:${r.value.trim()}`)
  lastSyncedJson = JSON.stringify(out)
  props.route.rules = out
}

function addRule() {
  parsedRules.value.push({ _uid: ++_uidSeq, prefix: 'domain', value: '' })
}

function removeRule(i) {
  parsedRules.value.splice(i, 1)
  syncRules()
}

function rulePlaceholder(prefix) {
  if (prefix === 'domain') return 'www.netflix.com'
  if (prefix === 'geosite') return 'netflix'
  if (prefix === 'geoip') return 'cn'
  return ''
}

function onLandingChange(ids) {
  props.route.outs = ids.map(id => ({ landing_node_id: id }))
}
</script>

<style scoped>
/* ====================================================================
   路由卡片 — 统一 SaaS 风格
   - 所有卡片白底 + 1px 边,靠左侧 chip + 序号区分类型,不用底色
   - field-label 用 11px uppercase 灰字,不用下划线/竖线
   - 操作图标 28x28 ghost,hover 才变实色
==================================================================== */
.route-card {
  background: #fff;
  border: 1px solid #e8eaed;
  border-radius: 8px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, sans-serif;
  font-variant-numeric: tabular-nums;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.route-card:hover { border-color: #d4d6da; }

.route-card.is-system.is-collapsed {
  gap: 0;
  cursor: pointer;
  background: #fafbfc;
}
.route-card.is-system .card-head { cursor: pointer; }

/* ===== 头部 ===== */
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
}
.drag-handle {
  cursor: grab;
  color: #c1c4c9;
  display: inline-flex;
  align-items: center;
  padding: 4px;
  border-radius: 4px;
  transition: color 0.12s, background 0.12s;
  font-size: 14px;
}
.drag-handle:hover { color: #6366f1; background: rgba(99, 102, 241, 0.08); }
.drag-handle:active { cursor: grabbing; }

.idx {
  font-weight: 600;
  color: #9ca3af;
  font-size: 12px;
  min-width: 14px;
  text-align: center;
}

.kind-chip {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 3px;
  font-weight: 500;
  letter-spacing: 0.2px;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 18px;
}
.kind-sys { background: rgba(99, 102, 241, 0.1); color: #4f46e5; }
.kind-fb { background: rgba(107, 114, 128, 0.1); color: #4b5563; }

.head-meta {
  font-size: 12px;
  color: #9ca3af;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}

.remark-input { display: none; }

.head-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.collapse-arrow {
  color: #9ca3af;
  font-size: 13px;
  transition: transform 0.18s;
  padding: 0 2px;
}
.collapse-arrow.open { transform: rotate(180deg); }

/* ===== 通用图标按钮 ===== */
.icon-btn {
  width: 26px;
  height: 26px;
  border: 0;
  background: transparent;
  border-radius: 5px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: #9ca3af;
  transition: color 0.12s, background 0.12s;
  font-size: 14px;
  padding: 0;
}
.icon-btn:hover { background: #f3f4f6; color: #1f2937; }
.icon-btn-danger:hover { background: rgba(244, 63, 94, 0.08); color: #f43f5e; }
.icon-btn-ghost { width: 24px; height: 24px; font-size: 12px; }

/* ===== 卡片正文 ===== */
.card-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-top: 4px;
  border-top: 1px solid #f1f2f4;
}

/* field: 标签 + 控件水平排布,标签固定宽 */
.field {
  display: grid;
  grid-template-columns: 56px 1fr;
  gap: 12px;
  align-items: start;
}
.field-label {
  font-size: 11px;
  font-weight: 500;
  color: #9ca3af;
  text-transform: uppercase;
  letter-spacing: 0.6px;
  padding-top: 6px;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
}
.field-control { min-width: 0; }
.field-control-row {
  display: grid;
  grid-template-columns: 1fr 140px;
  gap: 8px;
}
.field-control-row > :only-child { grid-column: 1 / -1; }

/* ===== 只读规则 chips ===== */
.rules-ro {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 6px;
  padding-top: 3px;
}
.rule-chip {
  font-size: 11.5px;
  background: #f3f4f6;
  border-radius: 4px;
  padding: 2px 7px;
  color: #4b5563;
  line-height: 18px;
}

/* ===== 规则行 ===== */
.rules-edit {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rule-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.rule-prefix { width: 100px; flex-shrink: 0; }
.rule-value { flex: 1; }

.add-rule-btn {
  align-self: flex-start;
  height: 26px;
  padding: 0 10px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 1px dashed #d4d6da;
  border-radius: 5px;
  color: #6b7280;
  font-size: 12px;
  cursor: pointer;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  transition: border-color 0.12s, color 0.12s, background 0.12s;
}
.add-rule-btn:hover {
  border-color: #6366f1;
  border-style: solid;
  color: #6366f1;
  background: rgba(99, 102, 241, 0.04);
}
.add-rule-btn .el-icon { font-size: 12px; }

/* ===== 落地 / 负载均衡 ===== */
.system-out {
  font-size: 12.5px;
  color: #6b7280;
  padding: 4px 0;
}
.landing-select { width: 100%; }
.balance-select { width: 100%; }
/* 落地节点 tag: 绿调,跟规则灰 chip / 兜底灰 chip 区分 */
.landing-select :deep(.el-select__tags-text) {
  color: #047857;
  font-weight: 500;
}
.landing-select :deep(.el-tag) {
  background: rgba(16, 185, 129, 0.1) !important;
  border-color: rgba(16, 185, 129, 0.25) !important;
  color: #047857 !important;
}
.landing-select :deep(.el-tag .el-tag__close) {
  color: #047857 !important;
  background: transparent !important;
}
.landing-select :deep(.el-tag .el-tag__close:hover) {
  background: rgba(16, 185, 129, 0.2) !important;
  color: #065f46 !important;
}
.opt-host {
  margin-left: 12px;
  color: #9ca3af;
  font-size: 11.5px;
}

/* ===== Element Plus 输入框统一外观 ===== */
:deep(.el-input__wrapper),
:deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px #e8eaed inset !important;
  background: #fff;
  transition: box-shadow 0.12s;
}
:deep(.el-input__wrapper:hover),
:deep(.el-select__wrapper:hover) {
  box-shadow: 0 0 0 1px #c1c4c9 inset !important;
}
:deep(.el-input__wrapper.is-focus),
:deep(.el-select__wrapper.is-focused) {
  box-shadow: 0 0 0 1px #6366f1 inset !important;
}
</style>
