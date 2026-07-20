export function localPreviewUrl(value:string):string|null{
  try{
    const url=new URL(value)
    if(url.protocol!=='http:'&&url.protocol!=='https:')return null
    const host=url.hostname.toLowerCase()
    if(host==='localhost'||host==='0.0.0.0')url.hostname='127.0.0.1'
    else if(host==='[::]'||host==='::')url.hostname='[::1]'
    else if(host!=='127.0.0.1'&&host!=='[::1]'&&host!=='::1')return null
    url.search='';url.hash=''
    return url.toString()
  }catch{return null}
}
