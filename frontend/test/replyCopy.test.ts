import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ReplyCopyRequest, type ReplySnapshot } from '../src/replyCopy.ts'

const session = {id:'pane',native_session_id:'conversation',agent_run_id:'run',turn_epoch:1}
const answer = (text='final'): ReplySnapshot => ({...session,session_id:session.id,message_id:'m',turn_id:'t',revision:'revision',text,previous_answer:false})
const deferred = () => {
  let resolve!: (value: ReplySnapshot) => void
  const promise=new Promise<ReplySnapshot>(done=>{resolve=done})
  return {promise,resolve}
}

test('each click requests a fresh answer even after a successful copy',async()=>{
  const request=new ReplyCopyRequest()
  assert.equal((await request.load(session,async()=>answer('first')))?.text,'first')
  assert.equal((await request.load(session,async()=>answer('second')))?.text,'second')
})

test('an older response cannot overwrite a newer copy request',async()=>{
  const request=new ReplyCopyRequest(), old=deferred(), fresh=deferred()
  const first=request.load(session,()=>old.promise)
  const second=request.load(session,()=>fresh.promise)
  fresh.resolve(answer('final'))
  assert.equal((await second)?.text,'final')
  old.resolve(answer('progress'))
  assert.equal(await first,null)
})

test('rewind, conversation switch and unmount retire in-flight copy requests',async()=>{
  for(const update of [()=>({...session,turn_epoch:2}),()=>({...session,native_session_id:'rewound'}),()=>null]){
    const request=new ReplyCopyRequest(), pending=deferred()
    const result=request.load(session,()=>pending.promise)
    const next=update()
    if(next)request.observe(next);else request.cancel()
    pending.resolve(answer('old branch'))
    assert.equal(await result,null)
  }
})

test('server identity must match and failed refresh never falls back to old text',async()=>{
  const request=new ReplyCopyRequest()
  await request.load(session,async()=>answer())
  await assert.rejects(request.load(session,async()=>({...answer(),agent_run_id:'different'})),/conversation changed/)
  await assert.rejects(request.load(session,async()=>{throw new Error('unavailable')}),/unavailable/)
  await assert.rejects(request.load(session,async()=>answer('')),/No completed/)
})
