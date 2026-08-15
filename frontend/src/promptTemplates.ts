export function renderPromptTemplate(body:string,values:Record<string,string>):string{
  return body.replace(/{{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*}}/g,(_,name:string)=>values[name]??`{{${name}}}`)
}

/** Single-line drawer preview. Templates keep their exact body everywhere else. */
export function promptTemplateExcerpt(body:string,maximum=140):string{
  const compact=body.replace(/\s+/g,' ').trim()
  if(compact.length<=maximum)return compact
  if(maximum<=1)return '…'.slice(0,maximum)
  return `${compact.slice(0,maximum-1).trimEnd()}…`
}
