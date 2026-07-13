let accessTokenRequester: (() => Promise<string | null>) | null = null
let pendingAccessToken: Promise<string | null> | null = null

export function setAccessTokenRequester(requester: (() => Promise<string | null>) | null) {
  accessTokenRequester = requester
}

export async function api<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const token = localStorage.getItem('mux.token')
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && !retried) {
    pendingAccessToken ||= accessTokenRequester?.() || Promise.resolve(null)
    const supplied = (await pendingAccessToken)?.trim()
    pendingAccessToken = null
    if (supplied) {
      localStorage.setItem('mux.token', supplied)
      return api<T>(method, path, body, true)
    }
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }))
    const error = new Error(detail.error || `${method} ${path} failed`) as Error & { fields?: Record<string,string>; status?:number }
    error.fields = detail.fields
    error.status = response.status
    throw error
  }
  return response.json()
}

export async function upload<T>(path: string, body: FormData, retried = false): Promise<T> {
  const token = localStorage.getItem('mux.token')
  const response = await fetch(path, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    body,
  })
  if (response.status === 401 && !retried) {
    pendingAccessToken ||= accessTokenRequester?.() || Promise.resolve(null)
    const supplied = (await pendingAccessToken)?.trim()
    pendingAccessToken = null
    if (supplied) {
      localStorage.setItem('mux.token', supplied)
      return upload<T>(path, body, true)
    }
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }))
    throw new Error(detail.error || `POST ${path} failed`)
  }
  return response.json()
}

export function wsUrl(path: string): string {
  const url = new URL(path, location.href)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export function openWebSocket(path: string): WebSocket {
  const token = localStorage.getItem('mux.token')
  return token ? new WebSocket(wsUrl(path), [`mux.auth.${token}`]) : new WebSocket(wsUrl(path))
}
