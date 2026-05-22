<template>
  <el-dialog v-model="open" title="修改密码" width="420px" @closed="reset">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="84px">
      <el-form-item label="原密码" prop="old_password">
        <el-input v-model="form.old_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="新密码" prop="new_password">
        <el-input v-model="form.new_password" type="password" show-password placeholder="至少 6 位" />
      </el-form-item>
      <el-form-item label="确认" prop="confirm">
        <el-input v-model="form.confirm" type="password" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '../api.js'

const open = ref(false)
const saving = ref(false)
const formRef = ref(null)
const form = reactive({ old_password: '', new_password: '', confirm: '' })

const rules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, message: '新密码至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请确认新密码', trigger: 'blur' },
    {
      validator: (_, v, cb) =>
        v === form.new_password ? cb() : cb(new Error('两次密码不一致')),
      trigger: 'blur',
    },
  ],
}

function reset() {
  Object.assign(form, { old_password: '', new_password: '', confirm: '' })
  formRef.value?.clearValidate()
}

async function save() {
  try { await formRef.value.validate() } catch { return }
  saving.value = true
  try {
    await authApi.changePassword({
      old_password: form.old_password,
      new_password: form.new_password,
    })
    ElMessage.success('密码已修改')
    open.value = false
  } finally {
    saving.value = false
  }
}

defineExpose({ open: () => { open.value = true } })
</script>
