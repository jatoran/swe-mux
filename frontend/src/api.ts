export type ApiError = Error & { fields?: Record<string,string>; status?: number; timeout?: boolean }
export type ApiOptions = {
  /** Abort the request (and its body read) after this long. Requests a view cannot
   *  render without should always set one: a fetch issued while a dormant PWA is
   *  waking can hang forever instead of failing, which used to leave a tab stuck. */
  timeoutMs?: number
  signal?: AbortSignal
}

export async function api<T>(method: string, path: string, body?: unknown, options: ApiOptions = {}): Promise<T> {
  const controller = options.timeoutMs === undefined ? null : new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  let timedOut = false
  if (controller && options.timeoutMs !== undefined) {
    timer = setTimeout(() => { timedOut = true; controller.abort() }, options.timeoutMs)
    if (options.signal?.aborted) controller.abort()
    else options.signal?.addEventListener('abort', () => controller.abort(), { once: true })
  }
  try {
    const response = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller ? controller.signal : options.signal,
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => ({ error: response.statusText }))
      const error = new Error(detail.error || `${method} ${path} failed`) as ApiError
      error.fields = detail.fields
      error.status = response.status
      throw error
    }
    return await response.json() as T
  } catch (cause) {
    if (!timedOut) throw cause
    const error = new Error('The daemon did not respond in time.') as ApiError
    error.timeout = true
    throw error
  } finally {
    if (timer !== undefined) clearTimeout(timer)
  }
}

export async function uploadTerminalImage<T>(path: string, body: FormData): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'X-Mux-User-Gesture': 'terminal-image' },
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
