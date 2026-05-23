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

        <el-form-item label="验证码" prop="captcha">
          <div class="captcha-row">
            <el-input
              v-model="form.captcha"
              placeholder="不区分大小写"
              maxlength="4"
              @keydown.enter="submit"
            />
            <div class="captcha-box" @click="refreshCaptcha" :title="'点击换一张'">
              {{ captcha.code }}
            </div>
          </div>
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
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '../api.js'

const props = defineProps({ mode: { type: String, default: 'login' } })
const emit = defineEmits(['logged-in'])

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: '', password: '', confirm: '', captcha: '' })

const captcha = reactive({ code: '' })

function refreshCaptcha() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
  let s = ''
  for (let i = 0; i < 4; i++) s += chars[Math.floor(Math.random() * chars.length)]
  captcha.code = s
  form.captcha = ''
}

onMounted(refreshCaptcha)

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
            v === form.password ? cb() : cb(new Error('两次不一致')),
          trigger: 'blur',
        },
      ]
    : [],
  captcha: [
    { required: true, message: ' ', trigger: 'blur' },
    {
      validator: (_, v, cb) =>
        String(v).trim().toLowerCase() === captcha.code.toLowerCase() ? cb() : cb(new Error('验证码错误')),
      trigger: 'blur',
    },
  ],
}))

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  if (String(form.captcha).trim().toLowerCase() !== captcha.code.toLowerCase()) {
    ElMessage.error('验证码错误')
    refreshCaptcha()
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
  } catch (e) {
    refreshCaptcha()
    throw e
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
  width: 100%; max-width: 360px;
  background: #fff;
  border-radius: 14px;
  padding: 32px 28px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.18);
}
.form-title {
  font-size: 18px; font-weight: 600; color: #111827;
  margin-bottom: 18px;
  text-align: center;
}
.form { margin-top: 6px; }
.submit { width: 100%; margin-top: 4px; }
.captcha-row {
  display: flex; gap: 10px; width: 100%;
}
.captcha-row .el-input { flex: 1; }
.captcha-box {
  flex: 0 0 110px;
  height: 32px;
  background: linear-gradient(135deg, #eef2ff, #ede9fe);
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-weight: 700;
  font-size: 18px;
  color: #4f46e5;
  cursor: pointer;
  user-select: none;
  letter-spacing: 4px;
  font-style: italic;
}
.captcha-box:hover { background: #f3f4f6; }
</style>
