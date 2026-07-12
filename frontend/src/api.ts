export async function api<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const token = localStorage.getItem('mux.token')
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && !retried) {
    const supplied = prompt('Enter the swe-mux access token')?.trim()
    if (supplied) {
      localStorage.setItem('mux.token', supplied)
      return api<T>(method, path, body, true)
    }
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }))
    throw new Error(detail.error || `${method} ${path} failed`)
  }
  return response.json()
}

export function wsUrl(path: string): string {
  const url = new URL(path, location.href)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('mux.token')
  if (token) url.searchParams.set('token', token)
  return url.toString()
}
