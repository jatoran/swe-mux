import { t } from './localization/index.ts'
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
  return <details class="experience-preview"><summary>{t("ExperiencePreview.e05e8dffc2")}</summary>
    <p>{t("ExperiencePreview.1964547d14")}</p>
    {rows ? <ul>{rows.map(row=><li key={row.id}><span>{row.label}</span><strong>{row.enabled ? t("ExperiencePreview.1300117561") : t("ExperiencePreview.ca7981b46e")}</strong>{row.enabled!==row.previous && <small>{t("ExperiencePreview.b2f872f672", { value1: (row.previous) ? 'yes' : 'no' })}</small>}</li>)}</ul> : <p>{error || t("ExperiencePreview.c76578f4c4")} <button type="button" onClick={()=>void read()}>{t("ExperiencePreview.942087cc2d")}</button></p>}
  </details>
}
