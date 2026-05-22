<template>
  <el-dialog v-model="visible" title="系统设置" width="560px" @open="load">
    <el-form :model="form" label-width="160px" v-loading="loading">
      <el-form-item label="主控公网地址">
        <el-input v-model="form.agent_endpoint" placeholder="ws://1.2.3.4:8000 或 wss://panel.example.com" />
        <div class="hint">
          节点 agent 通过此地址回连主控，**所有部署后的节点都依赖它**。
          修改后请点"保存并推送到所有节点"，否则旧节点仍连旧地址。
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button @click="save(false)" :loading="saving">仅保存</el-button>
      <el-button type="primary" @click="save(true)" :loading="saving || pushing">
        保存并推送到所有已部署节点
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { settingsApi, nodeApi } from '../api.js'

const visible = ref(false)
const loading = ref(false)
const saving = ref(false)
const pushing = ref(false)
const form = reactive({ agent_endpoint: '' })

const emit = defineEmits(['saved'])

function open() {
  visible.value = true
}
defineExpose({ open })

async function load() {
  loading.value = true
  try {
    const resp = await settingsApi.get()
    Object.assign(form, resp.data)
  } finally {
    loading.value = false
  }
}

async function save(thenPush) {
  saving.value = true
  try {
    await settingsApi.update({ agent_endpoint: form.agent_endpoint })
    ElMessage.success('已保存')
    emit('saved')
  } finally {
    saving.value = false
  }
  if (thenPush) await pushAll()
  if (!thenPush) visible.value = false
}

async function pushAll() {
  if (!form.agent_endpoint) {
    ElMessage.warning('地址为空，跳过推送')
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
.hint { font-size: 12px; color: #888; margin-top: 4px; line-height: 1.5; }
</style>
