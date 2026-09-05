import { useEffect, useRef, useState } from 'preact/hooks'
import { useModalFocus } from './modalFocus'
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
export function ConnectPhone({ onClose, onComplete }: { onClose: () => void; onComplete?: () => void }) {
  const [remote, setRemote] = useState<RemoteStatus | null>(null)
  const [port, setPort] = useState(8765)
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [authorization,setAuthorization]=useState('')
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose,true,'phone-setup')
  const refresh=async()=>{setError('');try{setRemote(await api<RemoteStatus>('GET','/api/remote/status',undefined,{timeoutMs:15000}))}catch(cause){setError((cause as Error).message)}}
  const secure=async()=>{
    setBusy(true);setError('')
    try{
      const response=await fetch('/api/remote/mobile-voice/enable',{method:'POST',headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'mobile-voice-setup'},body:'{}'})
      const result=await response.json()
      setAuthorization(result.authorization_url||'')
      if(!response.ok)throw new Error(result.error||result.diagnostic||'Secure phone setup failed.')
      if(result.diagnostic)setError(result.diagnostic)
      await refresh()
    }catch(cause){setError((cause as Error).message)}finally{setBusy(false)}
  }

  useEffect(() => {
    let live = true
    void api<RemoteStatus>('GET', '/api/remote/status').then(value => { if (live) setRemote(value) }).catch(cause => {if(live)setError(cause.message)})
    void api<{ port: number }>('GET', '/api/config').then(value => { if (live) setPort(value.port) }).catch(() => {})
    return () => { live = false }
  }, [])

  const url = connectionUrl(remote, port)
  return <div class="connect-phone-backdrop" role="dialog" aria-modal="true" aria-label="Connect a phone" onClick={onClose}>
    <section ref={panel} class="connect-phone" onClick={event => event.stopPropagation()}>
      <header><strong>CONNECT::A PHONE</strong><button type="button" class="link" onClick={onClose} aria-label="Close">Close</button></header>
      <div class="connect-phone-body">
        <TailscaleConnection status={remote} />
        {remote&&!remote.tailnet_enabled&&<p>Phone access is disabled. <button disabled={busy} onClick={()=>{setBusy(true);void api('PATCH','/api/config',{tailnet_enabled:true}).then(()=>refresh()).catch(cause=>setError(cause.message)).finally(()=>setBusy(false))}}>Enable private Tailscale access</button></p>}
        <p>Install Tailscale on this computer and your phone, then sign in to the same private network.</p>
        <a href="https://tailscale.com/download" target="_blank" rel="noreferrer">Get Tailscale</a>
        <div class="theme-actions"><button disabled={busy} onClick={()=>void refresh()}>Check connection again</button><button disabled={busy||remote?.connection_state!=='connected'} onClick={()=>void secure()}>Set up secure phone access</button></div>
        {authorization&&<p><a href={authorization} target="_blank" rel="noreferrer">Approve HTTPS in Tailscale</a>, then check again.</p>}
        {url
          ? <div class="connect-phone-url">
              <ConnectionQr url={url} />
              <p>Scan this on the phone, or open <a href={url} target="_blank" rel="noreferrer">{url}</a>.</p>
            </div>
          : <p>Connect this machine to Tailscale to get a phone address, then check the connection again.</p>}
        <PhoneDnsChecklist />
        {error&&<p role="alert">{error}</p>}
        {onComplete&&<button class="primary" disabled={!url} onClick={onComplete}>I opened the workspace on my phone</button>}
        <p class="connect-phone-note">Any device on this tailnet reaches this daemon with no login. Only connect phones you control, and do not enable the tailnet listener on a shared tailnet.</p>
      </div>
    </section>
  </div>
}
