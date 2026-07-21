export type MobileVoiceSetup = {
  status:'ready'|'configured_unverified'|'authorization_required'|'error'
  url?:string|null
  authorization_url?:string|null
  diagnostic:string
  https_port?:number
}

export async function enableMobileVoice():Promise<MobileVoiceSetup>{
  const response=await fetch('/api/remote/mobile-voice/enable',{
    method:'POST',
    headers:{'Content-Type':'application/json','X-Mux-User-Gesture':'mobile-voice-setup'},
    body:'{}',
  })
  const result=await response.json() as MobileVoiceSetup&{error?:string}
  if(!response.ok){
    if(result.authorization_url){location.assign(result.authorization_url);return result}
    throw new Error(result.diagnostic||result.error||'Could not enable secure mobile voice.')
  }
  if(!result.url)throw new Error(result.diagnostic||'Tailscale did not return a secure URL.')
  return result
}

export function mobileVoiceDestination(baseUrl:string,current:Pick<Location,'pathname'|'search'|'hash'>=location):string{
  const target=new URL(baseUrl)
  target.pathname=current.pathname
  target.search=current.search
  target.hash=current.hash
  return target.toString()
}
