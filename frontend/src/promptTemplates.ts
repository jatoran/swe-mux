export function renderPromptTemplate(body:string,values:Record<string,string>):string{
  return body.replace(/{{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*}}/g,(_,name:string)=>values[name]??`{{${name}}}`)
}
