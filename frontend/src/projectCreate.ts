// Folder and identity helpers for project creation. Policy remains inherited.
export type InitScript = {id:string;label:string;command:string;default_enabled?:boolean}

export type ProjectCreateDraft = {
  mode:'existing'|'new'
  name:string
  root:string
  parent:string
  folder:string
  folderTouched:boolean
  group_id:string
}

export const emptyProjectCreateDraft=():ProjectCreateDraft=>({
  mode:'existing',name:'',root:'',parent:'',folder:'',folderTouched:false,group_id:'',
})

/** The registration payload intentionally has no settings or automation overrides. */
export function projectCreateRequest(draft:ProjectCreateDraft) {
  return {name:draft.name.trim(),root:projectCreateRoot(draft),group_id:draft.group_id||null,create_missing:draft.mode==='new'}
}

// Windows is the primary platform and its separator is also the one a drive-letter
// path implies, so it is the fallback when the parent carries no separator of its own.
export function pathSeparator(path:string):string {
  if(path.includes('\\'))return '\\'
  if(path.includes('/'))return '/'
  return '\\'
}

export function joinPath(parent:string, name:string):string {
  const base=parent.trim().replace(/[\\/]+$/,'')
  const leaf=name.trim().replace(/^[\\/]+/,'')
  if(!base)return leaf
  if(!leaf)return base
  // A bare drive letter needs its separator back: `D:` and `D:\` mean different things.
  const separator=/^[A-Za-z]:$/.test(base)?'\\':pathSeparator(parent)
  return `${base}${separator}${leaf}`
}

// A Project name is free text; a folder name is not. Characters Windows rejects
// outright become hyphens rather than being dropped, so two distinct names cannot
// silently collapse into the same folder.
export function suggestFolderName(name:string):string {
  return name.trim()
    .replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g,'-')
    .replace(/\s+/g,'-')
    .replace(/-{2,}/g,'-')
    .replace(/^[-.]+|[-. ]+$/g,'')
}

export function parentPath(path:string):string {
  const trimmed=path.trim().replace(/[\\/]+$/,'')
  const index=Math.max(trimmed.lastIndexOf('\\'),trimmed.lastIndexOf('/'))
  if(index<=0)return ''
  const parent=trimmed.slice(0,index)
  // A drive-letter parent keeps its separator: `D:` alone is a relative path.
  return /^[A-Za-z]:$/.test(parent)?`${parent}\\`:parent
}

/**
 * The parent directory holding the most registered project roots — the Settings
 * placeholder for the assistant's new-project location. Case-insensitive count
 * (Windows is the primary platform); ties keep the first-seen spelling.
 */
export function commonestParent(roots:string[]):string {
  const counts=new Map<string,{count:number;value:string}>()
  for(const root of roots){
    const parent=parentPath(root)
    if(!parent)continue
    const key=parent.toLowerCase()
    const entry=counts.get(key)
    if(entry)entry.count+=1
    else counts.set(key,{count:1,value:parent})
  }
  let best='',bestCount=0
  for(const {count,value} of counts.values()){
    if(count>bestCount){best=value;bestCount=count}
  }
  return best
}

export function projectCreateFolder(draft:ProjectCreateDraft):string {
  return draft.folderTouched?draft.folder.trim():suggestFolderName(draft.name)
}

/** The exact canonical root the daemon will be asked to register. */
export function projectCreateRoot(draft:ProjectCreateDraft):string {
  if(draft.mode==='existing')return draft.root.trim()
  const folder=projectCreateFolder(draft)
  return draft.parent.trim()&&folder?joinPath(draft.parent,folder):''
}

export function projectCreateReady(draft:ProjectCreateDraft):boolean {
  return !!draft.name.trim()&&!!projectCreateRoot(draft)
}

/** Init scripts start unchecked unless their definition opts in. */
export function defaultInitScriptSelection(scripts:InitScript[]):string[] {
  return scripts.filter(script=>script.default_enabled).map(script=>script.id)
}
