<template>
  <el-dropdown trigger="click" @command="onCmd">
    <el-button text :icon="MoreFilled" :class="btnClass" />
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
          v-if="row.deploy_status === 'deploying'"
          command="resetDeploy"
          :icon="RefreshLeft"
        >
          <span class="danger-item">重置部署状态</span>
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
</template>

<script setup>
import {
  MoreFilled, Upload, Setting, Document, Edit, Delete, RemoveFilled, RefreshLeft,
} from '@element-plus/icons-vue'

const props = defineProps({
  row: { type: Object, required: true },
  btnClass: { type: String, default: 'more-btn' },
})
const emit = defineEmits(['deploy', 'resetDeploy', 'ss', 'detail', 'edit', 'uninstall', 'remove'])

function onCmd(cmd) {
  emit(cmd, props.row)
}
</script>

<style scoped>
:deep(.danger-item) { color: #ef4444; }
</style>
