import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
  withCredentials: true,  // 带 cookie
})

// 拦截器统一弹错误 toast。
// 如果调用方自己想弹自定义文案,在 config 上设 _suppressToast: true 跳过此次。
// 例: http.post(url, body, { _suppressToast: true }).catch(e => ElMessage.error('自定义...'))
http.interceptors.response.use(
  (resp) => {
    const data = resp.data
    if (data && data.ok === false) {
      if (!resp.config?._suppressToast) {
        ElMessage.error(data.error || '请求失败')
      }
      return Promise.reject(new Error(data.error || '请求失败'))
    }
    return data
  },
  (err) => {
    if (err.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('auth:logout'))
      return Promise.reject(err)
    }
    if (err.config?._suppressToast) {
      return Promise.reject(err)
    }
    // 按状态码 / 错误类型给用户友好文案
    const status = err.response?.status
    const serverMsg = err.response?.data?.detail || err.response?.data?.error
    let msg
    if (serverMsg) {
      msg = serverMsg
    } else if (err.code === 'ECONNABORTED' || err.message?.includes('timeout')) {
      msg = '请求超时，请稍后重试'
    } else if (!err.response) {
      msg = '网络连接失败，请检查网络'
    } else if (status === 403) {
      msg = '无权限执行此操作'
    } else if (status === 404) {
      msg = '请求的资源不存在'
    } else if (status >= 500) {
      msg = '服务端异常，请稍后重试'
    } else {
      msg = err.message || '请求失败'
    }
    ElMessage.error(msg)
    return Promise.reject(err)
  },
)

export const authApi = {
  status: (opts) => http.get('/auth/status', opts),
  setup: (payload, opts) => http.post('/auth/setup', payload, opts),
  login: (payload, opts) => http.post('/auth/login', payload, opts),
  logout: (opts) => http.post('/auth/logout', null, opts),
  me: (opts) => http.get('/auth/me', opts),
  changePassword: (payload, opts) => http.post('/auth/change-password', payload, opts),
}

export const systemApi = {
  health: (opts) => http.get('/health', opts),
}

export const nodeApi = {
  list: (opts) => http.get('/nodes', opts),
  metrics: (opts) => http.get('/nodes/metrics', opts),
  create: (payload, opts) => http.post('/nodes', payload, opts),
  update: (id, payload, opts) => http.patch(`/nodes/${id}`, payload, opts),
  remove: (id, opts) => http.delete(`/nodes/${id}`, opts),
  deploy: (id, opts) => http.post(`/nodes/${id}/deploy`, null, { timeout: 620000, ...opts }),
  deployReset: (id, opts) => http.post(`/nodes/${id}/deploy/reset`, null, opts),
  deployLog: (id, opts) => http.get(`/nodes/${id}/deploy/log`, opts),
  redeployAgentConfig: (id, opts) => http.post(`/nodes/${id}/agent/redeploy-config`, null, opts),
  agentReload: (id, opts) => http.post(`/nodes/${id}/agent/reload`, null, opts),
  uninstall: (id, payload, opts) => http.post(`/nodes/${id}/uninstall`, payload, { timeout: 90000, ...opts }),
  reorder: (ids, opts) => http.put('/nodes/order', { ids }, opts),
}

export const settingsApi = {
  get: (opts) => http.get('/settings', opts),
  update: (payload, opts) => http.patch('/settings', payload, opts),
  uploadCert: (formData, opts) =>
    http.post('/settings/cert', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 30000,
      ...opts,
    }),
}

export default http
