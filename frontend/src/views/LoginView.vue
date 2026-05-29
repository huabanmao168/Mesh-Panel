<template>
  <div class="login-page">
    <div class="login-card">
      <div class="form-title">
        {{ mode === 'setup' ? '初始化' : '登录' }}
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
          <el-input v-model="form.username" autofocus autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            @keydown.enter="submit"
          />
        </el-form-item>
        <el-form-item v-if="mode === 'setup'" label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
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
          {{ mode === 'setup' ? '提交' : '登录' }}
        </el-button>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { authApi } from '../api.js'

const props = defineProps({ mode: { type: String, default: 'login' } })
const emit = defineEmits(['logged-in'])

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '' })

const rules = computed(() => ({
  username: [
    { required: true, message: ' ', trigger: 'blur' },
    { min: 2, max: 32, message: '2-32 字符', trigger: 'blur' },
  ],
  password: [
    { required: true, message: ' ', trigger: 'blur' },
    ...(props.mode === 'setup' ? [{ min: 6, message: '至少 6 位', trigger: 'blur' }] : []),
  ],
  confirm: props.mode === 'setup'
    ? [
        { required: true, message: ' ', trigger: 'blur' },
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
    } else {
      await authApi.login({ username: form.username, password: form.password })
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
  background: #f5f7fb;
  padding: 20px;
}
.login-card {
  width: 100%; max-width: 360px;
  background: #fff;
  border-radius: 14px;
  padding: 32px 28px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.08);
}
.form-title {
  font-size: 18px; font-weight: 600; color: #111827;
  margin-bottom: 18px;
  text-align: center;
}
.form { margin-top: 6px; }
.submit { width: 100%; margin-top: 4px; }
</style>
