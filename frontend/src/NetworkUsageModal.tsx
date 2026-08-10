import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'
import {
  formatByteRate, formatBytes, formatElapsed, snapshotTraffic, trafficDirections,
  type NetworkUsageSnapshot,
} from './networkUsage'

const REFRESH_MS=3000

const integer=new Intl.NumberFormat()

export function NetworkUsageModal({onClose}:{onClose:()=>void}) {
  const [snapshot,setSnapshot]=useState<NetworkUsageSnapshot|null>(null)
  const [refreshing,setRefreshing]=useState(true)
  const [resetting,setResetting]=useState(false)
  const [confirmReset,setConfirmReset]=useState(false)
  const [error,setError]=useState('')
  const [updatedAt,setUpdatedAt]=useState<number|null>(null)
  const sequence=useRef(0)
  const panel=useRef<HTMLElement>(null)
  useModalFocus(panel,onClose)

  const refresh=async(silent=false)=>{
    const request=++sequence.current
    if(!silent)setRefreshing(true)
    try{
      const next=await api<NetworkUsageSnapshot>('GET','/api/diagnostics/network',undefined,{timeoutMs:10_000})
      if(request!==sequence.current)return
      setSnapshot(next)
      setUpdatedAt(Date.now())
      setError('')
    }catch(cause){
      if(request===sequence.current)setError(cause instanceof Error?cause.message:String(cause))
    }finally{
      if(request===sequence.current)setRefreshing(false)
    }
  }

  useEffect(()=>{
    void refresh()
    const timer=window.setInterval(()=>{if(!document.hidden)void refresh(true)},REFRESH_MS)
    const visible=()=>{if(!document.hidden)void refresh(true)}
    document.addEventListener('visibilitychange',visible)
    return()=>{sequence.current+=1;window.clearInterval(timer);document.removeEventListener('visibilitychange',visible)}
  },[])

  const reset=async()=>{
    if(!confirmReset){setConfirmReset(true);return}
    setResetting(true)
    setConfirmReset(false)
    sequence.current+=1
    try{
      await api('DELETE','/api/diagnostics/network',undefined,{timeoutMs:10_000})
      await refresh()
    }catch(cause){setError(cause instanceof Error?cause.message:String(cause))}
    finally{setResetting(false)}
  }

  const totals=snapshot?snapshotTraffic(snapshot):null
  const routes=[...(snapshot?.http_routes||[])].sort((left,right)=>(right.request_bytes+right.response_bytes)-(left.request_bytes+left.response_bytes))
  const channels=[...(snapshot?.websocket_channels||[])].sort((left,right)=>(right.received_bytes+right.sent_bytes)-(left.received_bytes+left.sent_bytes))
  const peers=[...(snapshot?.peers||[])].sort((left,right)=>trafficDirections(right.http,right.websocket).total-trafficDirections(left.http,left.websocket).total)

  return <div class="usage-layer network-usage-layer" role="dialog" aria-modal="true" aria-label="Bandwidth usage" onMouseDown={event=>event.target===event.currentTarget&&onClose()}>
    <section class="usage-panel network-usage-panel" ref={panel}>
      <header><div><span>NETWORK::USAGE</span><strong>Bandwidth metrics for this daemon measurement window</strong></div><div class="usage-header-actions"><button disabled={refreshing||resetting} onClick={()=>void refresh()}>{refreshing?'refreshing…':'refresh'}</button><button aria-label="Close bandwidth usage" onClick={onClose}>×</button></div></header>
      <div class="network-usage-actions">
        <div><strong>{snapshot?`Window ${formatElapsed(snapshot.uptime_seconds)}`:'Loading measurement window…'}</strong><span>{snapshot?`started ${new Date(snapshot.started_at*1000).toLocaleString()}`:'Waiting for daemon counters'}{updatedAt?` · updated ${new Date(updatedAt).toLocaleTimeString()}`:''}</span></div>
        <button class={confirmReset?'confirming':''} disabled={resetting||!snapshot} onClick={()=>void reset()}>{resetting?'resetting…':confirmReset?'✓ reset measurement window':'reset counters'}</button>
      </div>
      {error&&<div class="usage-error" role="alert">{error}</div>}
      <main>
        {!snapshot?<div class="usage-empty"><strong>{error?'Bandwidth metrics are unavailable.':'Loading bandwidth metrics…'}</strong><p>{error||'Reading daemon-local counters.'}</p></div>:<>
          <section class="network-usage-summary" aria-label="Bandwidth totals">
            <article><span>downloaded</span><strong>{formatBytes(totals?.downloaded||0)}</strong><small>{formatByteRate(totals?.downloaded||0,snapshot.uptime_seconds)} average</small></article>
            <article><span>uploaded</span><strong>{formatBytes(totals?.uploaded||0)}</strong><small>{formatByteRate(totals?.uploaded||0,snapshot.uptime_seconds)} average</small></article>
            <article><span>total payload</span><strong>{formatBytes(totals?.total||0)}</strong><small>{formatBytes(snapshot.uptime_seconds>0?(totals?.total||0)/snapshot.uptime_seconds*3600:0)}/h pace</small></article>
            <article><span>HTTP requests</span><strong>{integer.format(snapshot.totals.http.requests)}</strong><small>{integer.format(snapshot.totals.http.compressed_responses)} compressed</small></article>
            <article><span>WebSocket frames</span><strong>{integer.format(snapshot.totals.websocket.received_frames+snapshot.totals.websocket.sent_frames)}</strong><small>{snapshot.totals.websocket.active_connections} active connection(s)</small></article>
          </section>

          <section class="network-usage-table">
            <h3>HTTP routes</h3>
            {routes.length?<div class="usage-table-scroll"><table><thead><tr><th>method</th><th>route</th><th>requests</th><th>uploaded</th><th>downloaded</th><th>compressed</th></tr></thead><tbody>{routes.map(row=><tr key={`${row.method}:${row.route}`}><td>{row.method}</td><td title={row.route}>{row.route}</td><td>{integer.format(row.requests)}</td><td>{formatBytes(row.request_bytes)}</td><td>{formatBytes(row.response_bytes)}</td><td>{integer.format(row.compressed_responses)}</td></tr>)}</tbody></table></div>:<p>No HTTP traffic recorded in this window.</p>}
          </section>

          <section class="network-usage-table">
            <h3>WebSocket channels</h3>
            {channels.length?<div class="usage-table-scroll"><table><thead><tr><th>channel</th><th>connections</th><th>active</th><th>uploaded</th><th>downloaded</th><th>frames</th></tr></thead><tbody>{channels.map(row=><tr key={row.channel}><td>{row.channel}</td><td>{integer.format(row.connections)}</td><td>{integer.format(row.active_connections)}</td><td>{formatBytes(row.received_bytes)}</td><td>{formatBytes(row.sent_bytes)}</td><td>{integer.format(row.received_frames+row.sent_frames)}</td></tr>)}</tbody></table></div>:<p>No WebSocket application frames recorded in this window.</p>}
          </section>

          <section class="network-usage-table">
            <h3>Peers</h3>
            {peers.length?<div class="usage-table-scroll"><table><thead><tr><th>peer</th><th>HTTP requests</th><th>uploaded</th><th>downloaded</th><th>active sockets</th></tr></thead><tbody>{peers.map(row=>{const traffic=trafficDirections(row.http,row.websocket);return <tr key={row.peer}><td>{row.peer}</td><td>{integer.format(row.http.requests)}</td><td>{formatBytes(traffic.uploaded)}</td><td>{formatBytes(traffic.downloaded)}</td><td>{integer.format(row.websocket.active_connections)}</td></tr>})}</tbody></table></div>:<p>No peer traffic recorded in this window.</p>}
          </section>
        </>}
      </main>
      <footer><span>Application payload only. HTTP is counted after compression; WebSocket data is counted before per-message compression. Headers, TLS, Tailscale, TCP/IP, retransmits, and radio overhead are excluded.</span></footer>
    </section>
  </div>
}
