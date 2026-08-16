// Shared remote-connection UI: the Tailscale connection-state readout, the phone
// DNS checklist, the Windows firewall panel, and the QR of the connection URL.
// Used by both Settings → Remote/Voice and the standalone "Connect a phone" modal.
import qrcode from 'qrcode-generator'

export type TailscaleConnectionState =
  'not_installed'|'logged_out'|'connecting'|'needs_machine_auth'|'stopped'|'connected'|'unknown'

export type RemoteStatus = {
  mode:string;listen_url:string;available:boolean;serve_configured:boolean
  serve_url?:string|null;funnel_detected:boolean;setup_command:string;diagnostic:string
  tailnet_enabled:boolean;tailnet_ip?:string|null;tailnet_urls:string[];direct_available:boolean
  mobile_voice_configured:boolean;mobile_voice_url?:string|null;mobile_voice_https_port:number
  connection_state:TailscaleConnectionState;device_name?:string|null
  connection_command?:string|null;connection_detail:string
}

export type FirewallStatus = {
  supported:boolean;inspection_available?:boolean;needs_repair?:boolean
  blocking_rule_detected?:boolean;rule_allowed?:boolean;private_firewall_enabled?:boolean
  network_category?:string;detail?:string;port?:number
  serve_active?:boolean;direct_path_blocked?:boolean
}

export const CONNECTION_LABEL:Record<TailscaleConnectionState,string>={
  not_installed:'Not installed',
  logged_out:'Logged out',
  connecting:'Connecting…',
  needs_machine_auth:'Waiting for tailnet approval',
  stopped:'Stopped',
  connected:'Connected',
  unknown:'Unknown',
}

// The URL a phone should open. MagicDNS resolves the .ts.net name to the 100.x
// IP, so the name (not the raw IP) is used: it is what the HTTPS cert is bound to
// and what survives an address change. Prefer the secure Serve address when up.
export function connectionUrl(remote:RemoteStatus|null,port:number):string|null{
  if(!remote?.device_name)return null
  if(remote.mobile_voice_url)return remote.mobile_voice_url
  if(remote.serve_configured)return `https://${remote.device_name}/`
  return `http://${remote.device_name}:${port}/`
}

// The Tailscale connection state, distinct from whether the CLI is merely on
// PATH: a fresh install is installed but logged out. Cause-pointing text mirrors
// Orca's unreachableHostDetail rather than a generic "unavailable".
export function TailscaleConnection({status}:{status:RemoteStatus|null}){
  if(!status)return <p aria-live="polite">Checking the Tailscale connection…</p>
  const state=status.connection_state
  const tone=state==='connected'?'remote-state-ok':state==='connecting'?'':'remote-state-warn'
  return <div class="remote-connection">
    <dl><dt>Tailscale</dt><dd class={`remote-state ${tone}`}>{CONNECTION_LABEL[state]||state}{state==='connected'&&status.device_name?` as ${status.device_name}`:''}</dd></dl>
    <p aria-live="polite">{status.connection_detail}</p>
    {status.connection_command&&<p>Next step: <code>{status.connection_command}</code>{state==='not_installed'&&<> · <a href="https://tailscale.com/download/windows" target="_blank" rel="noreferrer">Download Tailscale</a></>}</p>}
  </div>
}

// The daemon cannot see the phone's DNS settings, so the checklist states them.
// Missing Tailscale DNS is the top silent first-connect failure.
export function PhoneDnsChecklist(){
  return <details class="remote-phone-dns">
    <summary>Phone setup checklist</summary>
    <p>swe-mux cannot detect the phone's network settings, so confirm these on the phone itself. A missing Tailscale DNS setting is the most common silent failure.</p>
    <ol>
      <li>Install Tailscale on the phone and sign in to the same tailnet.</li>
      <li>In the Tailscale app, turn on <strong>Use Tailscale DNS</strong>. The <code>.ts.net</code> address only resolves when MagicDNS is on.</li>
      <li>On Android, set <strong>Private DNS</strong> (Settings → Network → Private DNS) to <strong>Off</strong> or <strong>Automatic</strong>. A strict Private DNS host blocks MagicDNS.</li>
    </ol>
  </details>
}

// Windows-only: swe-mux binds a real socket on the tailnet address, so Defender
// Firewall governs the first phone connect on the Private profile. Hidden off a
// frozen Windows build (status.supported is false).
export function FirewallPanel({status,busy,message,onRepair}:{status:FirewallStatus|null;busy:boolean;message:string;onRepair:()=>void}){
  if(!status||!status.supported)return null
  // Only an alarm when the direct 100.x path is actually the phone's path (Serve
  // down). With Serve up the phone arrives over loopback, so a missing rule is a
  // quiet, optional note about the direct fallback rather than a red error.
  const needsRepair=status.needs_repair===true
  const offerRepair=needsRepair||status.direct_path_blocked===true||status.inspection_available===false
  return <div class="remote-firewall">
    <strong>Windows Defender Firewall</strong>
    <p class={needsRepair?'settings-inline-error':'profile-hint'} aria-live="polite">{status.detail}</p>
    {offerRepair&&<div class="theme-actions"><button class={needsRepair?'primary':''} disabled={busy} onClick={onRepair}>{busy?'Repairing…':needsRepair?'Repair firewall rule':'Add rule for direct 100.x fallback'}</button></div>}
    {message&&<p class={/decline|did not|could not/i.test(message)?'settings-inline-error':''} aria-live="polite">{message}</p>}
  </div>
}

// A scalable inline SVG QR of the connection URL. Rendered on a white plate so it
// scans in either UI theme; scannable regardless of the surrounding dark chrome.
export function ConnectionQr({url}:{url:string}){
  const svg=(()=>{
    try{
      const qr=qrcode(0,'M')
      qr.addData(url)
      qr.make()
      return qr.createSvgTag({cellSize:4,margin:2,scalable:true})
    }catch{return ''}
  })()
  if(!svg)return null
  return <div class="remote-qr" aria-label={`QR code for ${url}`} dangerouslySetInnerHTML={{__html:svg}} />
}
