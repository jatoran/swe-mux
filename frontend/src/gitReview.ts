import {
  provenanceAmbiguityNote,
  provenanceRoleLabel,
  type GitPatchSnapshot,
  type GitProvenance,
  type ReviewFileChange,
} from './gitWorktrees.ts'

export type GitReviewScope='unstaged'|'staged'|'conflicted'|'branch'|'commit'
export type AnnotationSide='old'|'new'
export type AnnotationAnchor={path:string;side:AnnotationSide;start:number;end:number;patchHash:string}
export type GitAnnotation={key:string;anchor:AnnotationAnchor;text:string}
export type ReviewLocator={
  scope:GitReviewScope
  worktree:string|null
  commit:string|null
  parent:string|null
  comparisonRef:string|null
}
export type ReviewPacketContext={
  projectName:string
  projectId:string
  repositoryRoot:string
  locator:ReviewLocator
  headOid:string|null
  stale:boolean
  files:ReviewFileChange[]
  fileListTruncated:boolean
  snapshots:Map<string,GitPatchSnapshot>
  annotations:GitAnnotation[]
  includeFullPatches:boolean
  provenance?:GitProvenance[]
}

export const REVIEW_PACKET_MAX_CHARS=200_000
export const INLINE_DIFF_ROW_LIMIT=500
export const SPLIT_LAYOUT_MIN_WIDTH=900

export function annotationKey(anchor:AnnotationAnchor):string {
  return `${encodeURIComponent(anchor.path)}:${anchor.side}:${anchor.start}-${anchor.end}:${anchor.patchHash}`
}

export function normalizeAnchor(anchor:AnnotationAnchor):AnnotationAnchor|null {
  if(!anchor.path||!anchor.patchHash||!Number.isInteger(anchor.start)||!Number.isInteger(anchor.end)||anchor.start<1||anchor.end<1)return null
  return {...anchor,start:Math.min(anchor.start,anchor.end),end:Math.max(anchor.start,anchor.end)}
}

export function extendAnnotationRange(first:AnnotationAnchor,next:AnnotationAnchor):AnnotationAnchor|null {
  if(first.path!==next.path||first.side!==next.side||first.patchHash!==next.patchHash)return null
  return normalizeAnchor({...first,start:Math.min(first.start,next.start),end:Math.max(first.end,next.end)})
}

export function upsertAnnotation(items:GitAnnotation[],anchor:AnnotationAnchor,text:string):GitAnnotation[] {
  const normalized=normalizeAnchor(anchor),trimmed=text.trim()
  if(!normalized||!trimmed)return items
  const key=annotationKey(normalized)
  return [...items.filter(item=>item.key!==key),{key,anchor:normalized,text:trimmed}].sort(compareAnnotations)
}

export function deleteAnnotation(items:GitAnnotation[],key:string):GitAnnotation[] {
  return items.filter(item=>item.key!==key)
}

export function compareAnnotations(first:GitAnnotation,second:GitAnnotation):number {
  return first.anchor.path.localeCompare(second.anchor.path)||first.anchor.side.localeCompare(second.anchor.side)||first.anchor.start-second.anchor.start||first.anchor.end-second.anchor.end||first.key.localeCompare(second.key)
}

export function automaticDiffView(width:number):'split'|'unified' {
  return width>=SPLIT_LAYOUT_MIN_WIDTH?'split':'unified'
}

export function effectiveDiffView(width:number,manual:'split'|'unified'|null):'split'|'unified' {
  return manual||automaticDiffView(width)
}

export function markReviewStale(current:boolean,scope:GitReviewScope):boolean {
  return current||scope!=='commit'
}

export function patchRequestQuery(projectId:string,locator:ReviewLocator,path:string,expectedHead?:string|null):string {
  const query=new URLSearchParams({project_id:projectId,scope:locator.scope,path})
  if(locator.worktree)query.set('worktree',locator.worktree)
  if(locator.commit)query.set('commit',locator.commit)
  if(locator.parent)query.set('parent',locator.parent)
  if(expectedHead)query.set('expected_head',expectedHead)
  return query.toString()
}

function lineExcerpt(patch:string,anchor:AnnotationAnchor):string {
  const lines=patch.split('\n')
  let oldLine=0,newLine=0
  const picked:string[]=[]
  let within=false
  for(const line of lines){
    const header=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line)
    if(header){oldLine=Number(header[1]);newLine=Number(header[2]);within=false;continue}
    if(line.startsWith('---')||line.startsWith('+++'))continue
    const sideLine=anchor.side==='old'?oldLine:newLine
    const belongs=anchor.side==='old'?line[0]!=='+' : line[0]!=='-'
    if(belongs&&sideLine>=Math.max(1,anchor.start-2)&&sideLine<=anchor.end+2){picked.push(line);within=true}
    else if(within&&picked.length)break
    if(line[0]!=='+'&&line[0]!=='\\')oldLine+=1
    if(line[0]!=='-'&&line[0]!=='\\')newLine+=1
    if(picked.length>=40)break
  }
  return picked.join('\n')||'(matching hunk context unavailable)'
}

function comparisonDescription(locator:ReviewLocator):string {
  if(locator.scope==='commit')return locator.parent
    ? `commit ${locator.commit} compared with parent ${locator.parent}`
    : `initial commit ${locator.commit}`
  if(locator.scope==='branch')return `worktree HEAD compared with merge base of ${locator.comparisonRef||'(unavailable comparison ref)'}`
  return `${locator.scope} changes in the selected worktree`
}

export function generateReviewPacket(context:ReviewPacketContext):{text:string;truncated:boolean} {
  const lines=[
    '# Git review packet',
    '',
    `Project: ${context.projectName} (${context.projectId})`,
    `Repository: ${context.repositoryRoot}`,
    ...(context.locator.worktree?[`Worktree: ${context.locator.worktree}`]:[]),
    `Scope: ${context.locator.scope}`,
    `Comparison: ${comparisonDescription(context.locator)}`,
    ...(context.locator.comparisonRef?[`Comparison ref: ${context.locator.comparisonRef}`]:[]),
    ...(context.locator.commit?[`Commit: ${context.locator.commit}`]:[]),
    ...((context.locator.scope==='commit')?[`Parent: ${context.locator.parent||'(initial commit)'}`]:[]),
    ...(context.headOid?[`HEAD: ${context.headOid}`]:[]),
    `Snapshot state: ${context.stale?'stale - changes updated after this review opened':'current when loaded'}`,
    `File list: ${context.fileListTruncated?'truncated to the bounded API result':'complete'}`,
    '',
  ]
  if(context.provenance?.length){
    lines.push('## Session provenance','')
    for(const item of context.provenance){
      const files=item.contributedPaths.length?` [${item.contributedPaths.slice(0,10).join(', ')}${item.contributedPaths.length>10?', …':''}]`:''
      lines.push(`- ${item.sessionName} (${item.sessionId}${item.agentRunId?`, run ${item.agentRunId}`:''}): ${provenanceRoleLabel(item)}, ${item.confidence}${item.ambiguous?` (${provenanceAmbiguityNote(item)})`:''}${files}`)
    }
    lines.push('')
  }
  const annotations=[...context.annotations].sort(compareAnnotations)
  if(!annotations.length)lines.push('No annotations.','')
  let activePath=''
  for(const annotation of annotations){
    if(annotation.anchor.path!==activePath){activePath=annotation.anchor.path;lines.push(`## ${activePath}`,'')}
    const snapshot=context.snapshots.get(activePath)
    const range=annotation.anchor.start===annotation.anchor.end?String(annotation.anchor.start):`${annotation.anchor.start}-${annotation.anchor.end}`
    lines.push(`- ${annotation.anchor.side} line${annotation.anchor.start===annotation.anchor.end?'':'s'} ${range}`)
    lines.push(`  Patch SHA-256: ${annotation.anchor.patchHash}`)
    lines.push(`  Comment: ${annotation.text}`)
    lines.push('')
    lines.push('```diff',snapshot?.patch?lineExcerpt(snapshot.patch,annotation.anchor):'(patch not loaded)','```','')
  }
  const loaded=[...context.snapshots.entries()].sort(([first],[second])=>first.localeCompare(second))
  lines.push('## Loaded snapshots','')
  for(const [path,snapshot] of loaded){
    lines.push(`- ${path}: ${snapshot.patchSha256}${snapshot.tooLarge?' (oversized)':''}${snapshot.unavailableReason?` (${snapshot.unavailableReason})`:''}`)
  }
  if(context.files.length>loaded.length)lines.push(`- ${context.files.length-loaded.length} file patch(es) were not loaded.`)
  if(context.includeFullPatches){
    lines.push('','## Full loaded patches','')
    for(const [path,snapshot] of loaded){
      if(!snapshot.patch)continue
      lines.push(`### ${path}`,'','```diff',snapshot.patch,'```','')
    }
  }else lines.push('','Full patches omitted. Enable "Include full loaded patches" to add bounded loaded patches.')
  const text=lines.join('\n')
  if(text.length<=REVIEW_PACKET_MAX_CHARS)return {text,truncated:false}
  const notice='\n\n[Review packet truncated at the size limit. Some loaded patch material was omitted.]\n'
  return {text:text.slice(0,REVIEW_PACKET_MAX_CHARS-notice.length)+notice,truncated:true}
}
