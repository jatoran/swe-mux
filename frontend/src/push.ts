// Browser side of Web Push: registers the service worker, manages the push
// subscription against the daemon, and reports focus "presence" so the server
// can skip notifying a device that is actively looking at the app.
import { api } from './api.ts'
import { currentProfile } from './deviceSettings.ts'

const SW_URL = '/sw.js'
const PRESENCE_TTL = 90
let presenceStarted = false

export function pushSupported(): boolean {
  return typeof navigator !== 'undefined'
    && 'serviceWorker' in navigator
    && typeof window !== 'undefined'
    && 'PushManager' in window
    && 'Notification' in window
}

export function notificationPermission(): NotificationPermission | 'unsupported' {
  return pushSupported() ? Notification.permission : 'unsupported'
}

function urlBase64ToUint8Array(base64: string): Uint8Array {
  const padded = (base64 + '='.repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  const out = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null
  try {
    await navigator.serviceWorker.register(SW_URL)
    return await navigator.serviceWorker.ready
  } catch { return null }
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  try {
    // getRegistration (not .ready) so this resolves to null when no worker is
    // registered yet, instead of hanging forever waiting for one to activate.
    const reg = await navigator.serviceWorker.getRegistration()
    if (!reg) return null
    return await reg.pushManager.getSubscription()
  } catch { return null }
}

export interface EnableResult { ok: boolean; reason?: string }

export async function enablePush(): Promise<EnableResult> {
  if (!pushSupported()) return { ok: false, reason: 'This browser does not support notifications.' }
  const reg = await registration()
  if (!reg) return { ok: false, reason: 'Could not register the service worker.' }
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') return { ok: false, reason: 'Notification permission was denied. Enable it in the browser site settings.' }
  try {
    const { key } = await api<{ key: string }>('GET', '/api/push/vapid-public-key')
    let sub = await reg.pushManager.getSubscription()
    if (!sub) {
      sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(key) as BufferSource })
    }
    await api('POST', '/api/push/subscribe', { subscription: sub.toJSON(), profile: currentProfile() })
    startPresence()
    return { ok: true }
  } catch (cause) {
    return { ok: false, reason: (cause as Error)?.message || 'Subscription failed.' }
  }
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription()
  if (sub) {
    await api('POST', '/api/push/unsubscribe', { endpoint: sub.endpoint }).catch(() => {})
    await sub.unsubscribe().catch(() => {})
  }
  stopPresence()
}

async function reportPresence(focused: boolean): Promise<void> {
  const sub = await currentSubscription()
  if (!sub) return
  await api('POST', '/api/push/presence', { endpoint: sub.endpoint, focused, ttl: PRESENCE_TTL }).catch(() => {})
}

const beat = () => void reportPresence(document.visibilityState === 'visible' && document.hasFocus())

export function startPresence(): void {
  if (presenceStarted) return
  presenceStarted = true
  beat()
  window.setInterval(beat, 60_000)
  window.addEventListener('visibilitychange', beat)
  window.addEventListener('focus', beat)
  window.addEventListener('blur', beat)
}

export function stopPresence(): void {
  void reportPresence(false)
}

// Called at app boot: if this device already has a subscription, resume presence
// reporting so an open tab keeps suppressing background pushes.
export async function initPush(): Promise<void> {
  if (!pushSupported()) return
  await registration().catch(() => null) // register/activate the worker for push
  const sub = await currentSubscription()
  if (sub) startPresence()
}
