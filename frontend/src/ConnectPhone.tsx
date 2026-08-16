import { useEffect, useState } from 'preact/hooks'
import { api } from './api'
import {
  ConnectionQr,
  PhoneDnsChecklist,
  TailscaleConnection,
  connectionUrl,
  type RemoteStatus,
} from './remoteConnection'

/**
 * "Connect a phone" modal: one place with the QR of the connection URL, the
 * hostname, the phone DNS checklist, and the live Tailscale connection state.
 * Reachable from first-run and from Settings -> Remote. The URL uses the
 * `.ts.net` MagicDNS name, not the raw 100.x IP, because the HTTPS certificate is
 * bound to the name and the phone resolves it over Tailscale DNS.
 */
export function ConnectPhone({ onClose }: { onClose: () => void }) {
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [port, setPort] = useState(8765)

  useEffect(() => {
    let live = true
    void api<RemoteStatus>('GET', '/api/remote/status').then(value => { if (live) setRemote(value) }).catch(() => {})
    void api<{ port: number }>('GET', '/api/config').then(value => { if (live) setPort(value.port) }).catch(() => {})
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => { live = false; window.removeEventListener('keydown', onKey) }
  }, [onClose])

  const url = connectionUrl(remote, port)
  return <div class="connect-phone-backdrop" role="dialog" aria-modal="true" aria-label="Connect a phone" onClick={onClose}>
    <section class="connect-phone" onClick={event => event.stopPropagation()}>
      <header><strong>CONNECT::A PHONE</strong><button type="button" class="link" onClick={onClose} aria-label="Close">Close</button></header>
      <div class="connect-phone-body">
        <TailscaleConnection status={remote} />
        {url
          ? <div class="connect-phone-url">
              <ConnectionQr url={url} />
              <p>Scan this on the phone, or open <a href={url} target="_blank" rel="noreferrer">{url}</a>.</p>
            </div>
          : <p>Connect this machine to Tailscale to get a phone address. Follow the next step above, then reopen this panel.</p>}
        <PhoneDnsChecklist />
        <p class="connect-phone-note">Any device on this tailnet reaches this daemon with no login. Only connect phones you control, and do not enable the tailnet listener on a shared tailnet.</p>
      </div>
    </section>
  </div>
}
