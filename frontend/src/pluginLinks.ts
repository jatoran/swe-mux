import { api } from './api.ts'

export type PluginLinkHandler={plugin_id:string;id:string;title:string;pattern:string;action:string}

let cached:PluginLinkHandler[]=[]
let loadedAt=0

export async function pluginLinkHandlers():Promise<PluginLinkHandler[]> {
  if(Date.now()-loadedAt<30_000)return cached
  cached=await api<PluginLinkHandler[]>('GET','/api/plugins/link-handlers')
  loadedAt=Date.now()
  return cached
}

export async function routePluginLink(
  uri:string,
  context:{project_id:string;session_id:string},
):Promise<boolean>{
  const handlers=await pluginLinkHandlers()
  const handler=handlers.find(item=>{
    try{return new RegExp(item.pattern).test(uri)}catch{return false}
  })
  if(!handler)return false
  await api('POST',`/api/plugins/${handler.plugin_id}/links/${handler.id}`,{
    ...context,context:'session',url:uri,
  })
  return true
}

export function invalidatePluginLinks():void {loadedAt=0}
