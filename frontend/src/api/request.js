import axios from 'axios'
import { ElMessage } from 'element-plus'

const request = axios.create({ baseURL: '/api', timeout: 30000 })
request.interceptors.response.use(
  (res) => {
    if (res.data.code !== 0) { ElMessage.error(res.data.message || '请求失败'); return Promise.reject(res.data) }
    return res.data
  },
  (err) => { ElMessage.error(err.message || '网络错误'); return Promise.reject(err) }
)
export default request
