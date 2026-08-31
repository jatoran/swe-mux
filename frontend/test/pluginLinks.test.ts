import assert from 'node:assert/strict'
import test from 'node:test'
import { invalidatePluginLinks, routePluginLink } from '../src/pluginLinks.ts'

test('plugin link routing discovers and invokes the first matching handler',async t=>{
  const original=globalThis.fetch
  t.after(()=>{globalThis.fetch=original})
  const calls:Array<{path:string;body:unknown}>=[]
  globalThis.fetch=(async(input:URL|string|Request,init?:RequestInit)=>{
    const path=String(input)
    if(path==='/api/plugins/link-handlers')return new Response(JSON.stringify([
      {plugin_id:'example.links',id:'github',title:'GitHub',pattern:'^https://github\\.com/',action:'inspect'},
    ]),{status:200,headers:{'Content-Type':'application/json'}})
    calls.push({path,body:JSON.parse(String(init?.body||'{}'))})
    return new Response(JSON.stringify({outcome:'succeeded'}),{status:200,headers:{'Content-Type':'application/json'}})
  }) as typeof fetch
  invalidatePluginLinks()
  assert.equal(await routePluginLink('https://github.com/jatoran/swe-mux',{project_id:'p1',session_id:'s1'}),true)
  assert.deepEqual(calls,[{
    path:'/api/plugins/example.links/links/github',
    body:{project_id:'p1',session_id:'s1',context:'session',url:'https://github.com/jatoran/swe-mux'},
  }])
})

test('plugin link routing leaves unmatched URLs to the browser',async t=>{
  const original=globalThis.fetch
  t.after(()=>{globalThis.fetch=original})
  globalThis.fetch=(async()=>new Response(JSON.stringify([
    {plugin_id:'example.links',id:'github',title:'GitHub',pattern:'^https://github\\.com/',action:'inspect'},
  ]),{status:200,headers:{'Content-Type':'application/json'}})) as typeof fetch
  invalidatePluginLinks()
  assert.equal(await routePluginLink('https://example.com',{project_id:'p1',session_id:'s1'}),false)
})
