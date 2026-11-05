import { ref } from 'vue'
import request from './request'
export const batches = ref([])
export const selectedBatch = ref(null)
export const batchParams = () => selectedBatch.value ? { batchId: selectedBatch.value } : {}
export async function refreshBatches(preferredId) {
  const res = await request.get('/neonatal/imports')
  batches.value = res.data
  const wanted = preferredId ?? selectedBatch.value
  selectedBatch.value = res.data.some(b => b.id === wanted) ? wanted : (res.data[0]?.id ?? null)
}
export const getRecords = params => request.get('/neonatal/records', { params })
export const getRecord = id => request.get(`/neonatal/records/${id}`)
export const getStats = params => request.get('/neonatal/stats', { params })
export const getQuality = params => request.get('/neonatal/quality', { params })
export function importWorkbook(file) {
  const data = new FormData()
  data.append('file', file)
  return request.post('/neonatal/imports', data, { timeout: 300000 })
}
