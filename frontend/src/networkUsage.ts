export type HttpTraffic = {
  requests:number
  request_bytes:number
  response_bytes:number
  compressed_responses:number
  unknown_request_bodies:number
  unknown_response_bodies:number
}

export type WebSocketTraffic = {
  connections:number
  active_connections:number
  received_frames:number
  received_bytes:number
  sent_frames:number
  sent_bytes:number
}

export type NetworkUsageSnapshot = {
  started_at:number
  uptime_seconds:number
  measurement:{http:string;websocket:string}
  totals:{http:HttpTraffic;websocket:WebSocketTraffic}
  peers:Array<{peer:string;http:HttpTraffic;websocket:WebSocketTraffic}>
  http_routes:Array<{method:string;route:string} & HttpTraffic>
  websocket_channels:Array<{channel:string} & WebSocketTraffic>
  websocket_sent_payloads:Array<{peer:string;channel:string;kind:string;frames:number;bytes:number}>
}

export type TrafficDirections = {
  uploaded:number
  downloaded:number
  total:number
}

export function trafficDirections(http:HttpTraffic,websocket:WebSocketTraffic):TrafficDirections {
  const uploaded=http.request_bytes+websocket.received_bytes
  const downloaded=http.response_bytes+websocket.sent_bytes
  return{uploaded,downloaded,total:uploaded+downloaded}
}

export function snapshotTraffic(snapshot:NetworkUsageSnapshot):TrafficDirections {
  return trafficDirections(snapshot.totals.http,snapshot.totals.websocket)
}

export function formatBytes(bytes:number):string {
  if(!Number.isFinite(bytes)||bytes<=0)return'0 B'
  const units=['B','KiB','MiB','GiB','TiB']
  const index=Math.min(Math.floor(Math.log(bytes)/Math.log(1024)),units.length-1)
  const value=bytes/1024**index
  const maximumFractionDigits=value<10?2:value<100?1:0
  return`${value.toLocaleString(undefined,{maximumFractionDigits})} ${units[index]}`
}

export function formatByteRate(bytes:number,seconds:number):string {
  if(!Number.isFinite(seconds)||seconds<=0)return'0 B/s'
  return`${formatBytes(bytes/seconds)}/s`
}

export function formatElapsed(seconds:number):string {
  const whole=Math.max(0,Math.floor(Number.isFinite(seconds)?seconds:0))
  if(whole<60)return`${whole}s`
  if(whole<3600)return`${Math.floor(whole/60)}m ${whole%60}s`
  if(whole<86400)return`${Math.floor(whole/3600)}h ${Math.floor((whole%3600)/60)}m`
  return`${Math.floor(whole/86400)}d ${Math.floor((whole%86400)/3600)}h`
}

export function payloadKindLabel(kind:string):string {
  return kind.replaceAll('_',' ')
}
