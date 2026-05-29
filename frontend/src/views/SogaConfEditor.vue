<template>
  <el-drawer
    v-model="visible"
    :size="drawerSize"
    :destroy-on-close="true"
    direction="rtl"
    class="conf-drawer"
  >
    <template #header>
      <div class="drawer-title">
        <span>编辑配置</span>
        <span v-if="folder" class="sub">{{ folder }} · soga.conf</span>
      </div>
    </template>

    <div v-loading="loading" class="ss-form">
      <div v-if="path" class="path-line">{{ path }}</div>
      <div ref="hostEl" class="cm-host"></div>
    </div>

    <template #footer>
      <div class="footer-actions">
        <span v-if="dirty" class="dirty-flag">未保存</span>
        <el-button @click="reload" :disabled="loading || saving">重新加载</el-button>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">保存</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup>
import { ref, computed, nextTick, onBeforeUnmount, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { EditorState, Compartment } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { StreamLanguage, syntaxHighlighting, HighlightStyle, bracketMatching } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import http from '../api.js'

// 简易 INI / soga.conf 语法
const iniLang = StreamLanguage.define({
  name: 'ini',
  startState: () => ({ inValue: false }),
  token(stream, state) {
    if (stream.sol()) {
      state.inValue = false
      if (stream.eatSpace()) return null
      const ch = stream.peek()
      if (ch === '#' || ch === ';' || ch === '!') {
        stream.skipToEnd()
        return 'comment'
      }
      if (ch === '[') {
        stream.skipToEnd()
        return 'heading'
      }
    }
    if (!state.inValue) {
      // 在 = 之前 → key
      if (stream.eat('=') || stream.eat(':')) {
        state.inValue = true
        return 'operator'
      }
      // 行内注释
      if (stream.peek() === '#' || stream.peek() === ';') {
        stream.skipToEnd()
        return 'comment'
      }
      // 吃到 = / : / 行内注释 / 行尾 之前都算 key
      if (stream.eatWhile(/[^=:#;\n]/)) return 'propertyName'
      stream.next()
      return null
    }
    // 已进入 value
    if (stream.eatSpace()) return null
    if (stream.peek() === '#' || stream.peek() === ';') {
      stream.skipToEnd()
      return 'comment'
    }
    // 布尔
    if (stream.match(/true\b|false\b/)) return 'bool'
    // 数字
    if (stream.match(/-?\d+(\.\d+)?\b/)) return 'number'
    // URL
    if (stream.match(/https?:\/\/\S+/)) return 'link'
    // 其它字符串
    if (stream.eatWhile(/[^#;\n\s]/)) return 'string'
    stream.next()
    return null
  },
})

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const instanceId = ref(null)
const folder = ref('')
const path = ref('')
const text = ref('')
const original = ref('')

const dirty = computed(() => text.value !== original.value)

const winW = ref(window.innerWidth)
const _onResize = () => { winW.value = window.innerWidth }
window.addEventListener('resize', _onResize)
const drawerSize = computed(() => winW.value < 720 ? '94%' : '640px')

// CodeMirror 配色 — 跟之前的色板一致
const highlightStyle = HighlightStyle.define([
  { tag: t.comment, color: '#8a94a6', fontStyle: 'italic' },
  { tag: t.propertyName, color: '#6f42c1' },
  { tag: t.definitionOperator, color: '#9ca3af' },
  { tag: t.operator, color: '#9ca3af' },
  { tag: t.string, color: '#047857' },
  { tag: t.number, color: '#2563eb', fontWeight: '600' },
  { tag: t.bool, color: '#2563eb', fontWeight: '600' },
  { tag: t.atom, color: '#2563eb', fontWeight: '600' },
  { tag: t.heading, color: '#b45309', fontWeight: '600' },
  { tag: t.link, color: '#0ea5e9', textDecoration: 'underline' },
])

const themeExt = EditorView.theme({
  '&': { height: '100%', fontSize: '12px', backgroundColor: '#fafbfc' },
  '.cm-scroller': {
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    lineHeight: '1.65',
  },
  '.cm-content': { padding: '12px 0', caretColor: '#4f46e5' },
  '.cm-gutters': {
    backgroundColor: '#f3f4f6',
    color: '#9ca3af',
    border: 'none',
    borderRight: '1px solid #e5e7eb',
  },
  '.cm-activeLineGutter': { backgroundColor: '#eef2ff', color: '#4f46e5' },
  '.cm-activeLine': { backgroundColor: 'rgba(99,102,241,0.04)' },
  '.cm-lineNumbers .cm-gutterElement': { padding: '0 10px 0 12px', minWidth: '28px' },
  '.cm-cursor': { borderLeftColor: '#4f46e5', borderLeftWidth: '2px' },
  '.cm-selectionBackground, ::selection': { backgroundColor: 'rgba(79,70,229,0.18) !important' },
})

const hostEl = ref(null)
let view = null
let updateListener = null

function buildState(doc) {
  return EditorState.create({
    doc,
    extensions: [
      lineNumbers(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      drawSelection(),
      history(),
      bracketMatching(),
      iniLang,
      syntaxHighlighting(highlightStyle),
      themeExt,
      keymap.of([
        ...defaultKeymap,
        ...historyKeymap,
        indentWithTab,
        { key: 'Mod-s', preventDefault: true, run: () => { save(); return true } },
      ]),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) text.value = u.state.doc.toString()
      }),
    ],
  })
}

function mountEditor() {
  if (view || !hostEl.value) return
  view = new EditorView({ state: buildState(text.value), parent: hostEl.value })
}

function setDoc(doc) {
  if (!view) return
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: doc } })
}

watch(visible, async (v) => {
  if (v) {
    await nextTick()
    mountEditor()
  } else {
    view?.destroy()
    view = null
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', _onResize)
  view?.destroy(); view = null
})

async function open(id) {
  instanceId.value = id
  visible.value = true
  folder.value = ''
  path.value = ''
  text.value = ''
  original.value = ''
  await reload()
}

async function reload() {
  if (!instanceId.value) return
  loading.value = true
  try {
    const r = await http.get(`/soga/instances/${instanceId.value}/conf`)
    folder.value = r.folder || ''
    path.value = r.path || ''
    text.value = r.text || ''
    original.value = text.value
    await nextTick()
    if (view) setDoc(text.value)
  } catch (e) {
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!dirty.value || saving.value) return
  try {
    await ElMessageBox.confirm(
      '保存并重启 soga?',
      '保存配置',
      { confirmButtonText: '保存并重启', cancelButtonText: '取消', type: 'warning' },
    )
  } catch { return }
  saving.value = true
  try {
    const r = await http.put(`/soga/instances/${instanceId.value}/conf`, { text: text.value })
    original.value = text.value
    if (r?.restarted === false) {
      ElMessage.warning(`已保存,但 Soga restart 失败: ${r.restart_output || '未知错误'}`)
    } else {
      ElMessage.success('已保存并重启')
    }
  } catch (e) {
  } finally {
    saving.value = false
  }
}

defineExpose({ open })
</script>

<style>
/* drawer body 不滚,内部 CodeMirror 自己滚 */
.conf-drawer .el-drawer__body {
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.conf-drawer .el-drawer__body > .ss-form {
  flex: 1 1 auto;
  min-height: 0;
}
</style>

<style scoped>
.drawer-title { display: flex; align-items: baseline; gap: 8px; }
.drawer-title .sub {
  font-size: 12px;
  color: #6b7280;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.ss-form {
  padding: 12px 20px 12px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.path-line {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #9ca3af;
  margin-bottom: 8px;
  flex: 0 0 auto;
}
.cm-host {
  flex: 1 1 auto;
  min-height: 0;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  overflow: hidden;
  background: #fafbfc;
}
.cm-host :deep(.cm-editor) { height: 100%; }
.cm-host :deep(.cm-editor.cm-focused) { outline: none; }

.footer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
.dirty-flag {
  font-size: 12px;
  color: #b45309;
  margin-right: 4px;
}
</style>
