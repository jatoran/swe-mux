import assert from 'node:assert/strict'
import test from 'node:test'
import type { PaneLayout } from '../src/layout.ts'
import type { Project, Session } from '../src/types.ts'
import {
  adjacentVoiceSession, buildVoiceNavigationIndex, projectAtVoiceNumber, sessionAtVoiceNumber,
} from '../src/voiceNavigation.ts'

const project=(id:string):Project=>({id,name:id,root:`D:/${id}`,position:0} as Project)
const session=(id:string,projectId:string,createdAt:number,pending=false):Session=>({
  id,name:id,project_id:projectId,created_at:createdAt,pending,
} as Session)
const layout=(...ids:string[]):PaneLayout=>({
  version:7,
  root:ids.length?{
    type:'stack',id:'stack',active_child_id:ids[0],
    children:ids.map(id=>({type:'leaf',kind:'terminal',id})),
  }:null,
})

const projects=[project('project-b'),project('project-a')]
const sessions=[
  session('old-unpanned','project-b',1),
  session('pane-second','project-b',2),
  session('pane-first','project-b',3),
  session('pending','project-b',0,true),
  session('other-project','project-a',1),
]
const buildIndex=()=>buildVoiceNavigationIndex(projects,sessions,item=>item.id==='project-b'
  ?layout('pane-first','pane-second')
  :layout('other-project'))

test('spoken numbering follows pane order first, then age, and skips pending sessions', () => {
  const index=buildIndex()
  assert.deepEqual(index.projects.map(item=>item.id),['project-b','project-a'])
  assert.equal(index.projectNumberById.get('project-a'),2)
  assert.deepEqual(index.sessionsByProject.get('project-b')?.map(item=>item.id),[
    'pane-first','pane-second','old-unpanned',
  ])
  assert.deepEqual(index.sessionAddressById.get('pane-second'),{projectNumber:1,sessionNumber:2})
  assert.deepEqual(index.sessionAddressById.get('other-project'),{projectNumber:2,sessionNumber:1})
  assert.equal(index.sessionAddressById.has('pending'),false)
})

test('a spoken number resolves within its project, or to nothing', () => {
  const index=buildIndex()
  assert.equal(projectAtVoiceNumber(index,1)?.id,'project-b')
  assert.equal(projectAtVoiceNumber(index,3),null)
  assert.equal(sessionAtVoiceNumber(index,'project-b',2)?.id,'pane-second')
  assert.equal(sessionAtVoiceNumber(index,'project-a',2),null)
})

test('next/previous walks the panes and stops rather than wrapping or crossing projects', () => {
  const index=buildIndex()
  assert.equal(adjacentVoiceSession(index,'project-b','pane-first',1)?.id,'pane-second')
  assert.equal(adjacentVoiceSession(index,'project-b','pane-second',-1)?.id,'pane-first')
  assert.equal(adjacentVoiceSession(index,'project-b','old-unpanned',1),null)
  assert.equal(adjacentVoiceSession(index,'project-b','other-project',1),null)
})
