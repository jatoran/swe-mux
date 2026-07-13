export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ error: response.statusText }))
    const error = new Error(detail.error || `${method} ${path} failed`) as Error & { fields?: Record<string,string>; status?:number }
    error.fields = detail.fields
    error.status = response.status
    throw error
  }
  return response.json()
}

export async function upload<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'X-Mux-User-Gesture': 'clipboard-image' },
    body,
  })
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
  return new WebSocket(wsUrl(path))
}
