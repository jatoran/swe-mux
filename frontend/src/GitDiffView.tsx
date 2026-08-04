import type { ComponentType } from 'preact'
import { Diff, getChangeKey, parseDiff, type ChangeData, type HunkData } from 'react-diff-view'
import 'react-diff-view/style/index.css'
import type { AnnotationAnchor, AnnotationSide, GitAnnotation } from './gitReview'

export type GitDiffViewProps={
  patch:string
  viewType:'unified'|'split'
  wrap:boolean
  rowLimit?:number
  annotations?:GitAnnotation[]
  selectedAnchor?:AnnotationAnchor|null
  onGutter?:(side:AnnotationSide,line:number,shift:boolean)=>void
  renderWidget?:(annotations:GitAnnotation[],anchor:AnnotationAnchor|null)=>preact.ComponentChildren
}

function lineFor(change:ChangeData,side:AnnotationSide):number|null {
  if(change.type==='insert')return side==='new'?change.lineNumber:null
  if(change.type==='delete')return side==='old'?change.lineNumber:null
  return side==='old'?change.oldLineNumber:change.newLineNumber
}

function slicedHunks(hunks:HunkData[],limit:number):{hunks:HunkData[];omitted:boolean} {
  let remaining=limit,omitted=false
  const result:HunkData[]=[]
  for(const hunk of hunks){
    if(remaining<=0){omitted=true;break}
    if(hunk.changes.length<=remaining){result.push(hunk);remaining-=hunk.changes.length;continue}
    result.push({...hunk,changes:hunk.changes.slice(0,remaining)})
    remaining=0;omitted=true
  }
  return {hunks:result,omitted}
}

export function GitDiffView(props:GitDiffViewProps) {
  const files=parseDiff(props.patch,{nearbySequences:'zip'})
  if(!files.length)return <p class="git-diff-state">The patch contains no renderable hunks.</p>
  return <div class={`git-diff-render ${props.wrap?'wrap':'nowrap'}`}>
    {files.map((file,fileIndex)=>{
      const sliced=slicedHunks(file.hunks,props.rowLimit??Number.MAX_SAFE_INTEGER)
      const widgets:Record<string,preact.ComponentChildren>={}
      for(const hunk of sliced.hunks)for(const change of hunk.changes){
        const key=getChangeKey(change)
        const lineAnnotations=(props.annotations||[]).filter(annotation=>{
          const line=lineFor(change,annotation.anchor.side)
          return line!==null&&annotation.anchor.end===line
        })
        const selected=props.selectedAnchor&&(['old','new'] as const).some(side=>lineFor(change,side)===props.selectedAnchor?.end&&side===props.selectedAnchor.side)
        if(lineAnnotations.length||selected)widgets[key]=props.renderWidget?.(lineAnnotations,selected?props.selectedAnchor||null:null)||null
      }
      const DiffComponent=Diff as unknown as ComponentType<Record<string,unknown>>
      return <div class="git-diff-file" key={`${file.oldPath}:${file.newPath}:${fileIndex}`}>
        <DiffComponent
          hunks={sliced.hunks}
          viewType={props.viewType}
          diffType={file.type}
          optimizeSelection
          widgets={widgets}
          renderGutter={({change,side,renderDefault}:any)=>{
            const line=lineFor(change,side)
            return line===null?renderDefault():<button class="git-diff-gutter-button" aria-label={`Annotate ${side} line ${line}`} onClick={(event:MouseEvent)=>{event.preventDefault();event.stopPropagation();props.onGutter?.(side,line,event.shiftKey)}}>{renderDefault()}</button>
          }}
        />
        {sliced.omitted&&<p class="git-diff-omitted">Additional hunks omitted from this preview.</p>}
      </div>
    })}
  </div>
}

export default GitDiffView
