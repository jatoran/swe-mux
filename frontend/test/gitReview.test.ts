import assert from 'node:assert/strict'
import test from 'node:test'
import {
  annotationKey, automaticDiffView, deleteAnnotation, effectiveDiffView,
  extendAnnotationRange, generateReviewPacket, markReviewStale, upsertAnnotation,
  REVIEW_PACKET_MAX_CHARS,
  type AnnotationAnchor,
} from '../src/gitReview.ts'
import type { GitPatchSnapshot, GitProvenance, ReviewFileChange } from '../src/gitWorktrees.ts'

const anchor=(over:Partial<AnnotationAnchor>={}):AnnotationAnchor=>({path:'src/a.ts',side:'new',start:4,end:4,patchHash:'hash-a',...over})

test('annotation anchors stay on one file, side, and frozen patch',()=>{
  assert.deepEqual(extendAnnotationRange(anchor(),anchor({start:8,end:8})),anchor({start:4,end:8}))
  assert.equal(extendAnnotationRange(anchor(),anchor({side:'old'})),null)
  assert.equal(extendAnnotationRange(anchor(),anchor({path:'src/b.ts'})),null)
  assert.equal(extendAnnotationRange(anchor(),anchor({patchHash:'new-hash'})),null)
})

test('annotation keys exclude text and updates have deterministic order',()=>{
  const key=annotationKey(anchor())
  let items=upsertAnnotation([],anchor({path:'z.ts'}),'later')
  items=upsertAnnotation(items,anchor({path:'a.ts',side:'old',start:9,end:7}),'first')
  items=upsertAnnotation(items,anchor({path:'a.ts',side:'old',start:7,end:9}),'edited')
  assert.equal(key.includes('comment'),false)
  assert.deepEqual(items.map(item=>item.anchor.path),['a.ts','z.ts'])
  assert.equal(items[0].text,'edited')
  assert.deepEqual(deleteAnnotation(items,items[0].key).map(item=>item.anchor.path),['z.ts'])
})

test('adaptive diff layout respects an explicit modal-lifetime override',()=>{
  assert.equal(automaticDiffView(899),'unified')
  assert.equal(automaticDiffView(900),'split')
  assert.equal(effectiveDiffView(400,'split'),'split')
  assert.equal(effectiveDiffView(1200,'unified'),'unified')
})

test('only mutable local reviews become stale on Git events',()=>{
  assert.equal(markReviewStale(false,'unstaged'),true)
  assert.equal(markReviewStale(false,'commit'),false)
  assert.equal(markReviewStale(true,'commit'),true)
})

test('review packets are deterministic, bounded, and omit full patches by default',()=>{
  const file:ReviewFileChange={path:'src/a.ts',oldPath:null,status:'M',additions:1,deletions:1,binary:false,submodule:false,currentExists:true}
  const snapshot:GitPatchSnapshot={scope:'unstaged',path:file.path,oldPath:null,worktree:'/repo',commit:null,parent:null,comparisonRef:null,headOid:'head',patchSha256:'hash-a',patch:'diff --git a/src/a.ts b/src/a.ts\n--- a/src/a.ts\n+++ b/src/a.ts\n@@ -3,3 +3,3 @@\n old\n-before\n+after\n tail\n',binary:false,tooLarge:false,unavailableReason:null,additions:1,deletions:1}
  const provenance:GitProvenance={id:'edge',sessionId:'session',sessionName:'Builder',agentRunId:'run',projectId:'project',worktreeRoot:'/repo',commitOid:'a'.repeat(40),parentOids:[],subject:'Add provenance',committedAt:1,previousHead:null,relationship:'created',confidence:'exact',ambiguous:false,source:'session_tool',observedAt:2}
  const context={projectName:'Mux',projectId:'project',repositoryRoot:'/repo',locator:{scope:'unstaged' as const,worktree:'/repo',commit:null,parent:null,comparisonRef:null},headOid:'head',stale:true,files:[file],fileListTruncated:true,snapshots:new Map([[file.path,snapshot]]),annotations:upsertAnnotation([],anchor(),'replace this'),includeFullPatches:false,provenance:[provenance]}
  const first=generateReviewPacket(context)
  const second=generateReviewPacket(context)
  assert.equal(first.text,second.text)
  assert.match(first.text,/Snapshot state: stale/)
  assert.match(first.text,/File list: truncated/)
  assert.match(first.text,/new line 4/)
  assert.match(first.text,/replace this/)
  assert.match(first.text,/Session provenance/)
  assert.match(first.text,/Builder \(session, run run\): created, exact/)
  assert.match(first.text,/Full patches omitted/)
  assert.equal(first.text.includes(snapshot.patch.repeat(2)),false)

  const oversized={...context,includeFullPatches:true,snapshots:new Map([[file.path,{...snapshot,patch:`diff\n${'x'.repeat(REVIEW_PACKET_MAX_CHARS)}`} ]])}
  const bounded=generateReviewPacket(oversized)
  assert.equal(bounded.truncated,true)
  assert.equal(bounded.text.length<=REVIEW_PACKET_MAX_CHARS,true)
  assert.match(bounded.text,/Review packet truncated/)
})
