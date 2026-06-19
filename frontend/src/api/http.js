import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 统一 HTTP 客户端
 * baseURL: /api
 * 自动解包 {code: 200, data: ...} 响应格式
 * 兼容无 {code} 包裹的裸数据响应（后端未统一格式时的兜底）
 */
const http = axios.create({
  baseURL: '/api',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// 响应拦截器
http.interceptors.response.use(
  (response) => {
    const body = response.data

    // 接口约定文档标准格式: { code: 200, data: ... }
    if (body && typeof body.code === 'number') {
      if (body.code === 200) {
        return body.data
      }
      // 非 200 错误码
      const msg = body.message || body.detail || `请求失败 (code: ${body.code})`
      ElMessage.error(msg)
      return Promise.reject(new Error(msg))
    }

    // 兼容兜底：后端未包裹 {code, data} 时直接透传
    return body
  },
  (error) => {
    const msg =
      error.response?.data?.message ||
      error.response?.data?.detail ||
      error.message ||
      '网络请求失败'
    ElMessage.error(msg)
    return Promise.reject(error)
  }
)

export default http