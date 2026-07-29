// swe-mux service worker: receives Web Push and shows notifications even when no
// tab is alive (screen locked, app backgrounded). All filtering — which events
// notify, quiet hours, suppress-while-focused — happens server-side before the
// push is sent, so this worker simply renders whatever it receives. Keeping it
// dumb also honours Chrome's userVisibleOnly contract: every received push shows
// a notification.

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim())
})

self.addEventListener('push', event => {
  let data = {}
  try { data = event.data ? event.data.json() : {} } catch (_) { data = {} }
  const title = data.title || 'swe-mux'
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    tag: data.tag || 'swe-mux',
    renotify: true,
    icon: '/icons/icon-192.png',
    badge: '/icons/icon-192.png',
    data: { url: data.url || '/' },
  }))
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const target = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    for (const client of all) {
      if ('focus' in client) { return client.focus() }
    }
    if (self.clients.openWindow) { return self.clients.openWindow(target) }
  })())
})
