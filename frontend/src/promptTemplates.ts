import type { ProjectBackend } from './types'

/** One template as the daemon lists it. `key` is the `scope:id` pair an Action
 *  button stores; `project_id` names the Project a `project`-scoped template
 *  belongs to, which is not necessarily the focused one in the management view. */
export type PromptTemplate = {
  id: string; key: string; scope: 'global' | 'project'; title: string; body: string
  tags: string[]; variables: string[]; backends: ProjectBackend[]
  created_at: number; updated_at: number; revision: string
  favorite: boolean; last_used_at?: number | null; use_count: number; conflict: boolean
  project_id?: string | null; project_name?: string | null
}

/** Stable identity for rendering and selection. `key` alone repeats when two
 *  Projects hold copies of one template file, which only the widened management
 *  listing can show. */
export function promptTemplateRef(item: PromptTemplate): string {
  return `${item.project_id || ''}|${item.key}`
}

const PLACEHOLDER=/{{\s*([A-Za-z][A-Za-z0-9_]{0,63})\s*}}/g

export function renderPromptTemplate(body:string,values:Record<string,string>):string{
  return body.replace(PLACEHOLDER,(_,name:string)=>values[name]??`{{${name}}}`)
}

/** Placeholder names in a body, in the daemon's order (sorted, de-duplicated).
 *  Mirrors `PROMPT_VARIABLE` in `prompt_library.py`: the server derives the saved
 *  `variables` list the same way, so an editor can show the fields a body will get
 *  before it has been saved. */
export function promptTemplateVariables(body:string):string[]{
  const names=new Set<string>()
  for(const match of body.matchAll(PLACEHOLDER))names.add(match[1])
  return [...names].sort()
}

/** Single-line drawer preview. Templates keep their exact body everywhere else. */
export function promptTemplateExcerpt(body:string,maximum=140):string{
  const compact=body.replace(/\s+/g,' ').trim()
  if(compact.length<=maximum)return compact
  if(maximum<=1)return '…'.slice(0,maximum)
  return `${compact.slice(0,maximum-1).trimEnd()}…`
}
