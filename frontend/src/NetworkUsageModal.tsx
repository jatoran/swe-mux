import { numberFormatter, locale as uiLocale } from './localization/index.ts'
import { rich, t } from './localization/index.ts'
import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import {
  formatByteRate, formatBytes, formatElapsed, payloadKindLabel, snapshotTraffic, trafficDirections,
  type NetworkUsageSnapshot,
} from './networkUsage'

const REFRESH_MS=3000

const integer=numberFormatter()

// The Bandwidth segment of the Resources modal. It was a modal of its own until the
// consolidation put the four metered things swe-mux reports - processes, bandwidth, disk,
// tokens - behind one dialog instead of four app-menu rows. What is left here is the
// surface; `ResourcesModal.tsx` owns the dialog, the focus trap, and the close control.
export function NetworkUsageView() {
  const [snapshot,setSnapshot]=useState<NetworkUsageSnapshot|null>(null)
  const [refreshing,setRefreshing]=useState(true)
  const [resetting,setResetting]=useState(false)
  const [confirmReset,setConfirmReset]=useState(false)
  const [error,setError]=useState('')
  const [updatedAt,setUpdatedAt]=useState<number|null>(null)
  const sequence=useRef(0)

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
  const sentPayloads=[...(snapshot?.websocket_sent_payloads||[])].sort((left,right)=>right.bytes-left.bytes)
  const peers=[...(snapshot?.peers||[])].sort((left,right)=>trafficDirections(right.http,right.websocket).total-trafficDirections(left.http,left.websocket).total)

  return <>
      <div class="network-usage-actions">
        <div><strong>{snapshot ? t("NetworkUsageModal.a04af35164", { value1: formatElapsed(snapshot.uptime_seconds) }) : t("NetworkUsageModal.096e0dea14")}</strong><span>{snapshot ? t("NetworkUsageModal.4e8ed74ac8", { value1: new Date(snapshot.started_at*1000).toLocaleString(uiLocale.value) }) : t("NetworkUsageModal.520375fa10")}{updatedAt ? t("NetworkUsageModal.1381120916", { value1: new Date(updatedAt).toLocaleTimeString(uiLocale.value) }) : ''}</span></div>
        <div class="resource-view-actions">
          <button disabled={refreshing||resetting} onClick={()=>void refresh()}>{refreshing ? t("NetworkUsageModal.eb50add3d3") : t("NetworkUsageModal.d6cc0a088c")}</button>
          <button class={confirmReset?'confirming':''} disabled={resetting||!snapshot} onClick={()=>void reset()}>{resetting ? t("NetworkUsageModal.b6980e7b7f") : confirmReset ? t("NetworkUsageModal.20eb3a560d") : t("NetworkUsageModal.0893bb8e2a")}</button>
        </div>
      </div>
      {error && <div class="usage-error" role="alert">{error}</div>}
      <main>
        {!snapshot ? <div class="usage-empty"><strong>{error ? t("NetworkUsageModal.3cd9185e77") : t("NetworkUsageModal.a49dbdb2cc")}</strong><p>{error || t("NetworkUsageModal.c739d67024")}</p></div> : <>
          <section class="network-usage-summary" aria-label={t("NetworkUsageModal.9ef82b6aa5")}>
            <article><span>{t("NetworkUsageModal.b7a8a844a6")}</span><strong>{formatBytes(totals?.downloaded||0)}</strong><small>{rich("NetworkUsageModal.6dee468a52", { value1: formatByteRate(totals?.downloaded||0,snapshot.uptime_seconds) })}</small></article>
            <article><span>{t("NetworkUsageModal.3cef1b245d")}</span><strong>{formatBytes(totals?.uploaded||0)}</strong><small>{rich("NetworkUsageModal.6dee468a52", { value1: formatByteRate(totals?.uploaded||0,snapshot.uptime_seconds) })}</small></article>
            <article><span>{t("NetworkUsageModal.63251978ea")}</span><strong>{formatBytes(totals?.total||0)}</strong><small>{rich("NetworkUsageModal.81808577d4", { value1: formatBytes(snapshot.uptime_seconds>0?(totals?.total||0)/snapshot.uptime_seconds*3600:0) })}</small></article>
            <article><span>{t("NetworkUsageModal.e38a439109")}</span><strong>{integer.format(snapshot.totals.http.requests)}</strong><small>{rich("NetworkUsageModal.3b9d51d5a3", { value1: integer.format(snapshot.totals.http.compressed_responses) })}</small></article>
            <article><span>{t("NetworkUsageModal.84d553b05c")}</span><strong>{integer.format(snapshot.totals.websocket.received_frames+snapshot.totals.websocket.sent_frames)}</strong><small>{rich("NetworkUsageModal.3d80cfdd30", { active_connections: snapshot.totals.websocket.active_connections })}</small></article>
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="NetworkUsageModal.4e1fa6bf0e">{t("NetworkUsageModal.4e1fa6bf0e")}</h3>
            {routes.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("NetworkUsageModal.5b7e6bf2dc")}</th><th>{t("NetworkUsageModal.8a84e406c0")}</th><th>{t("NetworkUsageModal.ec72420df5")}</th><th>{t("NetworkUsageModal.3cef1b245d")}</th><th>{t("NetworkUsageModal.b7a8a844a6")}</th><th>{t("NetworkUsageModal.9da308c2e4")}</th></tr></thead><tbody>{routes.map(row=><tr key={`${row.method}:${row.route}`}><td>{row.method}</td><td title={row.route}>{row.route}</td><td>{integer.format(row.requests)}</td><td>{formatBytes(row.request_bytes)}</td><td>{formatBytes(row.response_bytes)}</td><td>{integer.format(row.compressed_responses)}</td></tr>)}</tbody></table></div> : <p>{t("NetworkUsageModal.86d69d9c8e")}</p>}
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="NetworkUsageModal.f1d554ffe2">{t("NetworkUsageModal.f1d554ffe2")}</h3>
            {channels.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("NetworkUsageModal.69e36568cd")}</th><th>{t("NetworkUsageModal.1e5fac8674")}</th><th>{t("NetworkUsageModal.9687961165")}</th><th>{t("NetworkUsageModal.3cef1b245d")}</th><th>{t("NetworkUsageModal.b7a8a844a6")}</th><th>{t("NetworkUsageModal.594cfd2607")}</th></tr></thead><tbody>{channels.map(row=><tr key={row.channel}><td>{row.channel}</td><td>{integer.format(row.connections)}</td><td>{integer.format(row.active_connections)}</td><td>{formatBytes(row.received_bytes)}</td><td>{formatBytes(row.sent_bytes)}</td><td>{integer.format(row.received_frames+row.sent_frames)}</td></tr>)}</tbody></table></div> : <p>{t("NetworkUsageModal.66cc082505")}</p>}
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="NetworkUsageModal.9017547cf7">{t("NetworkUsageModal.9017547cf7")}</h3>
            {sentPayloads.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("NetworkUsageModal.2ffc1d0638")}</th><th>{t("NetworkUsageModal.69e36568cd")}</th><th>{t("NetworkUsageModal.6a9cac583d")}</th><th>{t("NetworkUsageModal.594cfd2607")}</th><th>{t("NetworkUsageModal.b7a8a844a6")}</th><th>{t("NetworkUsageModal.cf73706160")}</th></tr></thead><tbody>{sentPayloads.map(row=><tr key={`${row.peer}:${row.channel}:${row.kind}`}><td>{row.peer}</td><td>{row.channel}</td><td>{payloadKindLabel(row.kind)}</td><td>{integer.format(row.frames)}</td><td>{formatBytes(row.bytes)}</td><td>{formatBytes(row.frames?row.bytes/row.frames:0)}</td></tr>)}</tbody></table></div> : <p>{t("NetworkUsageModal.ced079e960")}</p>}
          </section>

          <section class="network-usage-table">
            <h3 data-heading-message="NetworkUsageModal.64b769bcaf">{t("NetworkUsageModal.64b769bcaf")}</h3>
            {peers.length ? <div class="usage-table-scroll"><table><thead><tr><th>{t("NetworkUsageModal.2ffc1d0638")}</th><th>{t("NetworkUsageModal.e38a439109")}</th><th>{t("NetworkUsageModal.3cef1b245d")}</th><th>{t("NetworkUsageModal.b7a8a844a6")}</th><th>{t("NetworkUsageModal.3f60b7ec8e")}</th></tr></thead><tbody>{peers.map(row=>{const traffic=trafficDirections(row.http,row.websocket);return <tr key={row.peer}><td>{row.peer}</td><td>{integer.format(row.http.requests)}</td><td>{formatBytes(traffic.uploaded)}</td><td>{formatBytes(traffic.downloaded)}</td><td>{integer.format(row.websocket.active_connections)}</td></tr>})}</tbody></table></div> : <p>{t("NetworkUsageModal.929ac1af41")}</p>}
          </section>
        </>}
      </main>
      <footer><span>{t("NetworkUsageModal.b0cc9f331b")}</span></footer>
  </>
}
