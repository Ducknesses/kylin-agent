/**
 * 统一 HTTP 客户端 —— 自动携带认证 token
 *
 * 所有前端 API 调用应通过此模块发起，
 * 自动从 wsStore 读取 api_token 并注入 Authorization: Bearer 头。
 *
 * 使用方式：
 *   import http from '@/api/http'
 *   const res = await http.get('/config/whitelist')
 *   const res = await http.post('/sessions', { title: '...' })
 */
import axios from 'axios'
import { useWsStore } from '@/stores/wsStore'

const http = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// 请求拦截器：自动附加 Bearer token
http.interceptors.request.use((config) => {
  const wsStore = useWsStore()
  if (wsStore.token) {
    config.headers.Authorization = `Bearer ${wsStore.token}`
  }
  return config
}, (error) => {
  return Promise.reject(error)
})

// 响应拦截器：统一处理 401/403
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      const { status, data } = error.response
      if (status === 401) {
        console.warn('[HTTP] 认证失败 (401):', data?.detail || '未提供有效令牌')
      } else if (status === 403) {
        console.warn('[HTTP] 权限不足 (403):', data?.detail || '当前令牌权限不够')
      }
    }
    return Promise.reject(error)
  },
)

export default http