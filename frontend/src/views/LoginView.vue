<template>
  <div class="login-page">
    <div class="login-card">
      <div class="brand">
        <div class="logo">MP</div>
        <div>
          <div class="brand-title">MeshPanel</div>
          <div class="brand-sub">轻量 SS 节点管理面板</div>
        </div>
      </div>

      <div class="form-title">
        {{ mode === 'setup' ? '首次启动 · 设置管理员' : '登录' }}
      </div>
      <div v-if="mode === 'setup'" class="form-hint">
        系统尚未设置管理员账户，请设置一个用于后续登录。
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        class="form"
        @submit.prevent="submit"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="form.username" placeholder="admin" autofocus />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            :placeholder="mode === 'setup' ? '至少 6 位' : '请输入密码'"
            @keydown.enter="submit"
          />
        </el-form-item>
        <el-form-item v-if="mode === 'setup'" label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            placeholder="再输入一次"
            @keydown.enter="submit"
          />
        </el-form-item>

        <el-button
          type="primary"
          size="large"
          class="submit"
          :loading="loading"
          @click="submit"
        >
          {{ mode === 'setup' ? '设置并登录' : '登录' }}
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '../api.js'

const props = defineProps({ mode: { type: String, default: 'login' } })
const emit = defineEmits(['logged-in'])

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '' })

const rules = computed(() => ({
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 2, max: 32, message: '用户名 2-32 个字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    ...(props.mode === 'setup' ? [{ min: 6, message: '密码至少 6 位', trigger: 'blur' }] : []),
  ],
  confirm: props.mode === 'setup'
    ? [
        { required: true, message: '请确认密码', trigger: 'blur' },
        {
          validator: (_, v, cb) =>
            v === form.password ? cb() : cb(new Error('两次密码不一致')),
          trigger: 'blur',
        },
      ]
    : [],
}))

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  loading.value = true
  try {
    if (props.mode === 'setup') {
      await authApi.setup({ username: form.username, password: form.password })
      ElMessage.success('管理员已设置，欢迎使用')
    } else {
      await authApi.login({ username: form.username, password: form.password })
      ElMessage.success('登录成功')
    }
    emit('logged-in', form.username)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex; align-items: center; justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  padding: 20px;
}
.login-card {
  width: 100%; max-width: 420px;
  background: #fff;
  border-radius: 14px;
  padding: 32px 28px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.18);
}
.brand {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 24px;
}
.logo {
  width: 44px; height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  font-size: 18px;
  letter-spacing: 0.5px;
}
.brand-title { font-size: 20px; font-weight: 700; color: #1f2937; }
.brand-sub { font-size: 12px; color: #6b7280; margin-top: 2px; }
.form-title {
  font-size: 16px; font-weight: 600; color: #111827;
  margin-bottom: 6px;
}
.form-hint {
  font-size: 12px; color: #6b7280; margin-bottom: 14px;
  background: #f3f4f6; padding: 8px 12px; border-radius: 8px;
}
.form { margin-top: 6px; }
.submit { width: 100%; margin-top: 4px; }
</style>
