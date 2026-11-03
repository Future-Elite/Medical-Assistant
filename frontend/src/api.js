export async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const error = new Error(typeof body.detail === 'string' ? body.detail : `服务请求失败 (${response.status})`)
    error.status = response.status
    throw error
  }
  return response.json()
}

export function post(path, body, options = {}) {
  return request(path, { ...options, method: 'POST', body: JSON.stringify(body) })
}

export async function downloadJob(id, format) {
  return downloadFile(`/generations/${encodeURIComponent(id)}/export?format=${format}`, `ehrworld-${id.slice(0, 8)}.${format}`)
}

export async function downloadBatch(id) {
  return downloadFile(`/batches/${encodeURIComponent(id)}/export`, `ehrworld-batch-${id.slice(0,8)}.json`)
}

export function downloadDreamerJob(id, format) {
  return downloadFile(`/dreamer/generations/${encodeURIComponent(id)}/export?format=${format}`, `ehrdreamer-${id.slice(0,8)}.${format}`)
}

export function downloadDreamerBatch(id) {
  return downloadFile(`/dreamer/batches/${encodeURIComponent(id)}/export`, `ehrdreamer-batch-${id.slice(0,8)}.json`)
}

async function downloadFile(path, filename) {
  const response = await fetch(`/api${path}`)
  if (!response.ok) throw new Error('导出失败，请确认轨迹已完整生成。')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.append(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
