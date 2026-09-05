import { useEffect, useState } from 'preact/hooks'
import { api } from './api'

type Preview={project_defaults?:Record<string,{id:string;label:string;enabled:boolean;previous:boolean}[]>}
/** Both setup and Settings show the daemon's actual assignment before applying it. */
export function ExperiencePreview({tier}:{tier:string}){
  const [payload,setPayload]=useState<Preview|null>(null)
  const [error,setError]=useState('')
  const read=()=>api<Preview>('GET','/api/experience-tiers').then(value=>{setPayload(value);setError('')}).catch(cause=>setError(cause.message))
  useEffect(()=>{void read()},[])
  const rows=payload?.project_defaults?.[tier]
  return <details class="experience-preview"><summary>Preview global automation defaults</summary>
    <p>Projects inherit these defaults unless they explicitly chose otherwise. Applying replaces custom choices for these rows; other automation defaults are preserved.</p>
    {rows?<ul>{rows.map(row=><li key={row.id}><span>{row.label}</span><strong>{row.enabled?'On':'Off'}</strong>{row.enabled!==row.previous&&<small>Changes from {row.previous?'on':'off'}</small>}</li>)}</ul>:<p>{error||'Loading the preset…'} <button type="button" onClick={()=>void read()}>Retry</button></p>}
  </details>
}
