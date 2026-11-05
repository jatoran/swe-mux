import { rich, t } from './localization/index.ts'
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
      if(!response.ok)throw new Error(result.error||result.diagnostic||t("ConnectPhone.d5af83335a"))
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
  return <div class="connect-phone-backdrop" role="dialog" aria-modal="true" aria-label={t("ConnectPhone.8b4df73a36")} onClick={onClose}>
    <section ref={panel} class="connect-phone" onClick={event => event.stopPropagation()}>
      <header><strong>{t("ConnectPhone.f8a1bec3ba")}</strong><button type="button" class="link" onClick={onClose} aria-label={t("ConnectPhone.7d9eb7acb1")}>{t("ConnectPhone.7d9eb7acb1")}</button></header>
      <div class="connect-phone-body">
        <TailscaleConnection status={remote} />
        {remote&&!remote.tailnet_enabled && <p>{rich("ConnectPhone.d4c85590d1", { value1: <button disabled={busy} onClick={()=>{setBusy(true);void api('PATCH','/api/config',{tailnet_enabled:true}).then(()=>refresh()).catch(cause=>setError(cause.message)).finally(()=>setBusy(false))}}>{t("ConnectPhone.3235ea00c6")}</button> })}</p>}
        <p>{t("ConnectPhone.47d4f2e82d")}</p>
        <a href="https://tailscale.com/download" target="_blank" rel="noreferrer">{t("ConnectPhone.05cbce45ad")}</a>
        <div class="theme-actions"><button disabled={busy} onClick={()=>void refresh()}>{t("ConnectPhone.e44eed5584")}</button><button disabled={busy||remote?.connection_state!=='connected'} onClick={()=>void secure()}>{t("ConnectPhone.b1ac52eebd")}</button></div>
        {authorization && <p>{rich("ConnectPhone.cedf4cd0e3", { value1: <a href={authorization} target="_blank" rel="noreferrer">{t("ConnectPhone.f998e98ea8")}</a> })}</p>}
        {url ? <div class="connect-phone-url">
              <ConnectionQr url={url} />
              <p>{rich("ConnectPhone.d5e8e3a1a0", { value1: <a href={url} target="_blank" rel="noreferrer">{url}</a> })}</p>
            </div> : <p>{t("ConnectPhone.64ededcef7")}</p>}
        <PhoneDnsChecklist />
        {error && <p role="alert">{error}</p>}
        {onComplete && <button class="primary" disabled={!url} onClick={onComplete}>{t("ConnectPhone.5e09b8c145")}</button>}
        <p class="connect-phone-note">{t("ConnectPhone.99467ff88c")}</p>
      </div>
    </section>
  </div>
}
