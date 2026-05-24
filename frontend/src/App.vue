<template>
  <div v-if="loading" class="boot">
    <el-icon class="boot-spin"><Loading /></el-icon>
    <div>加载中...</div>
  </div>

  <LoginView
    v-else-if="!authed"
    :mode="setupRequired ? 'setup' : 'login'"
    @logged-in="onLoggedIn"
  />

  <el-container v-else class="layout">
    <el-header class="header">
      <div class="brand">
        <div class="logo">MP</div>
        <div class="brand-text">
          <div class="brand-title">MeshPanel <span v-if="appVersion" class="brand-ver">v{{ appVersion }}</span></div>
          <div class="brand-sub">一站式服务器管理</div>
        </div>
      </div>
      <span class="spacer" />

      <el-tag v-if="warnNoEndpoint" type="warning" effect="light" class="warn-pill" @click="openSettings">
        <el-icon><Warning /></el-icon>
        主控公网地址未配置
      </el-tag>

      <el-dropdown trigger="click" @command="onCmd">
        <span class="user-chip">
          <el-icon><UserFilled /></el-icon>
          <span>{{ username }}</span>
          <el-icon class="chev"><ArrowDown /></el-icon>
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="add-node" :icon="Plus">添加节点</el-dropdown-item>
            <el-dropdown-item command="settings" :icon="Setting" divided>系统设置</el-dropdown-item>
            <el-dropdown-item command="passwd" :icon="Key">修改密码</el-dropdown-item>
            <el-dropdown-item command="logout" :icon="SwitchButton" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </el-header>

    <el-main class="main">
      <NodeList ref="nodeListRef" />
    </el-main>

    <SettingsDialog ref="settingsRef" @saved="refreshSettings" />
    <ChangePasswordDialog ref="pwdRef" />
  </el-container>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Setting, Warning, UserFilled, ArrowDown, SwitchButton, Key, Loading, Plus,
} from '@element-plus/icons-vue'
import NodeList from './views/NodeList.vue'
import SettingsDialog from './views/SettingsDialog.vue'
import LoginView from './views/LoginView.vue'
import ChangePasswordDialog from './views/ChangePasswordDialog.vue'
import { authApi, settingsApi, systemApi } from './api.js'

const loading = ref(true)
const authed = ref(false)
const setupRequired = ref(false)
const username = ref('')
const warnNoEndpoint = ref(false)
const appVersion = ref('')

const settingsRef = ref(null)
const nodeListRef = ref(null)
const pwdRef = ref(null)

async function bootstrap() {
  loading.value = true
  // 拉版本号(失败也不影响后续流程)
  try {
    const h = await systemApi.health()
    appVersion.value = h.data?.version || ''
  } catch {}
  try {
    const st = await authApi.status()
    setupRequired.value = !!st.data.setup_required
    if (setupRequired.value) {
      authed.value = false
    } else {
      try {
        const me = await authApi.me()
        username.value = me.data.username
        authed.value = true
        await refreshSettings()
      } catch {
        authed.value = false
      }
    }
  } finally {
    loading.value = false
  }
}

async function onLoggedIn(u) {
  username.value = u
  setupRequired.value = false
  authed.value = true
  await refreshSettings()
}

function onLogout() {
  authed.value = false
  username.value = ''
}

async function refreshSettings() {
  try {
    const resp = await settingsApi.get()
    warnNoEndpoint.value = !resp.data.agent_endpoint
  } catch {
    /* 拦截器处理 */
  }
}

function openSettings() {
  settingsRef.value?.open()
}

async function onCmd(cmd) {
  if (cmd === 'add-node') return nodeListRef.value?.openCreate()
  if (cmd === 'settings') return openSettings()
  if (cmd === 'passwd') return pwdRef.value?.open()
  if (cmd === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '退出', { type: 'warning' })
    } catch { return }
    try { await authApi.logout() } catch {}
    ElMessage.success('已退出')
    onLogout()
  }
}

// axios 401 时全局触发
function handleAuthEvent() {
  onLogout()
}

onMounted(() => {
  window.addEventListener('auth:logout', handleAuthEvent)
  bootstrap()
})
onBeforeUnmount(() => {
  window.removeEventListener('auth:logout', handleAuthEvent)
})
</script>

<style>
/* 全站:数字输入框去掉浏览器原生上下小箭头 */
input[type=number]::-webkit-outer-spin-button,
input[type=number]::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}
input[type=number] {
  -moz-appearance: textfield;
}

html, body, #app { height: 100%; margin: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
  background: #f5f7fb;
}
.layout { height: 100vh; display: flex; flex-direction: column; }

.header {
  --el-header-padding: 0 20px;
  display: flex; align-items: center; gap: 14px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  height: 60px !important;
  flex-shrink: 0;
}
.brand { display: flex; align-items: center; gap: 10px; }
.logo {
  width: 36px; height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff; font-weight: 700; font-size: 14px;
  display: flex; align-items: center; justify-content: center;
}
.brand-title { font-size: 16px; font-weight: 700; color: #111827; line-height: 1.2; }
.brand-ver { font-size: 10px; font-weight: 500; color: #9ca3af; margin-left: 4px; vertical-align: 1px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, sans-serif; }
.brand-sub { font-size: 11px; color: #9ca3af; line-height: 1.2; }
.spacer { flex: 1; }
.warn-pill {
  cursor: pointer;
  display: inline-flex; align-items: center; gap: 4px;
}
.user-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: #f3f4f6;
  cursor: pointer;
  color: #374151;
  font-size: 13px;
  transition: background 0.15s;
}
.user-chip:hover { background: #e5e7eb; }
.user-chip .chev { font-size: 12px; opacity: 0.6; }

.main {
  --el-main-padding: 18px 20px;
  flex: 1;
  overflow: auto;
}

.boot {
  height: 100vh;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 10px;
  color: #6b7280;
}
.boot-spin {
  font-size: 32px;
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

@media (max-width: 600px) {
  .brand-sub { display: none; }
  .warn-pill { display: none; }
}
</style>
