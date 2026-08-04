import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitDiffView } from '../../src/GitDiffView'
import { GitReviewModal } from '../../src/GitReviewModal'
import type { AnnotationAnchor, GitAnnotation } from '../../src/gitReview'
import type { Project } from '../../src/types'
import '../../src/style.css'

const patch='diff --git a/example.ts b/example.ts\n--- a/example.ts\n+++ b/example.ts\n@@ -1,2 +1,2 @@\n-const value = 1\n+const value = 2\n export default value\n'
const annotation:GitAnnotation={key:'saved',anchor:{path:'example.ts',side:'new',start:1,end:1,patchHash:'hash'},text:'Saved widget'}
const project={id:'project',name:'Harness',root:'/repo'} as Project
const file={path:'example.ts',oldPath:null,status:'M',additions:1,deletions:1,binary:false,submodule:false,currentExists:true}

globalThis.fetch=async()=>new Response(JSON.stringify({
  scope:'unstaged',path:'example.ts',worktree:'/repo',commit:null,parent:null,comparison_ref:null,
  head_oid:'a'.repeat(40),patch_sha256:'hash',patch,binary:false,too_large:false,
  unavailable_reason:null,additions:1,deletions:1,
}),{status:200,headers:{'Content-Type':'application/json'}})

function Harness(){
  const [view,setView]=useState<'unified'|'split'>('unified')
  const [clicked,setClicked]=useState('')
  const [selected,setSelected]=useState<AnnotationAnchor|null>(null)
  const [modal,setModal]=useState(false)
  return <><button id="toggle" onClick={()=>setView(value=>value==='unified'?'split':'unified')}>{view}</button><button id="open-modal" onClick={()=>setModal(true)}>modal</button><output id="clicked">{clicked}</output><GitDiffView patch={patch} viewType={view} wrap={false} annotations={[annotation]} selectedAnchor={selected} onGutter={(side,line)=>{setClicked(`${side}:${line}`);setSelected({path:'example.ts',side,start:line,end:line,patchHash:'hash'})}} renderWidget={(items,anchor)=><div class="test-widget">{items.map(item=>item.text).join(',')}{anchor?' composer':''}</div>}/>{modal&&<GitReviewModal project={project} repositoryRoot="/repo" files={[file]} locator={{scope:'unstaged',worktree:'/repo',commit:null,parent:null,comparisonRef:null}} initialPath="example.ts" truncated={false} onClose={()=>setModal(false)} onOpenFile={()=>undefined} onSendToAgent={()=>undefined}/>}</>
}

render(<Harness/>,document.querySelector('#root')!)
