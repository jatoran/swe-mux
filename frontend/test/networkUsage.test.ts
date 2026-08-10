import assert from 'node:assert/strict'
import test from 'node:test'
import {
  formatByteRate, formatBytes, formatElapsed, snapshotTraffic, trafficDirections,
  type HttpTraffic, type NetworkUsageSnapshot, type WebSocketTraffic,
} from '../src/networkUsage.ts'

const http:HttpTraffic={
  requests:3,request_bytes:100,response_bytes:1000,compressed_responses:2,
  unknown_request_bodies:0,unknown_response_bodies:0,
}
const websocket:WebSocketTraffic={
  connections:2,active_connections:1,received_frames:4,received_bytes:50,
  sent_frames:6,sent_bytes:500,
}

test('traffic directions use the client mobile-data perspective',()=>{
  assert.deepEqual(trafficDirections(http,websocket),{
    uploaded:150,
    downloaded:1500,
    total:1650,
  })
})

test('snapshot totals combine HTTP bodies and WebSocket application frames',()=>{
  const snapshot={totals:{http,websocket}} as NetworkUsageSnapshot
  assert.deepEqual(snapshotTraffic(snapshot),{uploaded:150,downloaded:1500,total:1650})
})

test('bandwidth labels remain compact across byte scales and time windows',()=>{
  assert.equal(formatBytes(0),'0 B')
  assert.equal(formatBytes(1536),'1.5 KiB')
  assert.equal(formatBytes(5*1024*1024),'5 MiB')
  assert.equal(formatByteRate(3*1024,2),'1.5 KiB/s')
  assert.equal(formatElapsed(59),'59s')
  assert.equal(formatElapsed(125),'2m 5s')
  assert.equal(formatElapsed(7380),'2h 3m')
})
