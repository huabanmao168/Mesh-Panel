import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true,  // 带 cookie
})

// 401 时，触发全局事件让 App 切到登录页
http.interceptors.response.use(
  (resp) => {
    const data = resp.data
    if (data && data.ok === false) {
      ElMessage.error(data.error || '请求失败')
      return Promise.reject(new Error(data.error || '请求失败'))
    }
    return data
  },
  (err) => {
    if (err.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('auth:logout'))
      return Promise.reject(err)
    }
    const msg = err.response?.data?.detail || err.response?.data?.error || err.message || '网络错误'
    ElMessage.error(msg)
    return Promise.reject(err)
  },
)

export const authApi = {
  status: () => http.get('/auth/status'),
  setup: (payload) => http.post('/auth/setup', payload),
  login: (payload) => http.post('/auth/login', payload),
  logout: () => http.post('/auth/logout'),
  me: () => http.get('/auth/me'),
  changePassword: (payload) => http.post('/auth/change-password', payload),
}

export const nodeApi = {
  list: () => http.get('/nodes'),
  metrics: () => http.get('/nodes/metrics'),
  create: (payload) => http.post('/nodes', payload),
  update: (id, payload) => http.patch(`/nodes/${id}`, payload),
  remove: (id) => http.delete(`/nodes/${id}`),
  deploy: (id) => http.post(`/nodes/${id}/deploy`, null, { timeout: 620000 }),
  deployLog: (id) => http.get(`/nodes/${id}/deploy/log`),
  redeployAgentConfig: (id) => http.post(`/nodes/${id}/agent/redeploy-config`),
  agentReload: (id) => http.post(`/nodes/${id}/agent/reload`),
  uninstall: (id, payload) => http.post(`/nodes/${id}/uninstall`, payload, { timeout: 90000 }),
  reorder: (ids) => http.put('/nodes/order', { ids }),
}

export const settingsApi = {
  get: () => http.get('/settings'),
  update: (payload) => http.patch('/settings', payload),
  uploadCert: (formData) =>
    http.post('/settings/cert', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 30000,
    }),
}

export default http
