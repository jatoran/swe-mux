import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'

type Note = {
  project:{id:string;label:string;root:string};kind:'projects'|'spaces'|'sessions';id:string;path:string
  exists:boolean;revision:string;markdown:string;status:string;error?:string
  project_scope_id?:string
  storage?:'app-data'|'project';owner_label?:string
}
type NoteMode = 'modal'|'pane'
type Props = {
  cwd:string;projectScopeId?:string;spaceId:string;sessionId:string|null;terminalSessionId?:string|null;initialKind?:'projects'|'spaces'|'sessions'
  ownerLabel:string;projectLabel?:string
  display?:NoteMode;targetKey:string;mobileActive?:boolean
  onClose:()=>void|Promise<void>;onHide?:()=>void;onPopOut?:()=>void|Promise<void>;onOpenSplit?:()=>void|Promise<void>
  onInsert:(text:string)=>void;onCapture:(targetKey:string)=>void
}

export function Notes({
  cwd, projectScopeId, spaceId, sessionId, terminalSessionId=sessionId, initialKind='spaces', ownerLabel, projectLabel, display='modal', targetKey,
  mobileActive=false, onClose, onHide, onPopOut, onOpenSplit, onInsert, onCapture,
}: Props) {
  const kind = initialKind
  const [note, setNote] = useState<Note | null>(null)
  const [markdown, setMarkdown] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const editor = useRef<HTMLTextAreaElement>(null)
  const panel = useRef<HTMLElement>(null)
  const noteRef = useRef<Note | null>(null)
  const markdownRef = useRef('')
  const dirtyRef = useRef(false)
  const changeVersion = useRef(0)
  const saveRequest = useRef<Promise<boolean> | null>(null)
  const identity = kind === 'projects' ? projectScopeId : kind === 'spaces' ? spaceId : sessionId
  const identityRef = useRef(identity)
  const kindRef = useRef(kind)
  noteRef.current = note
  markdownRef.current = markdown
  dirtyRef.current = dirty
  identityRef.current = identity
  kindRef.current = kind

  const saveNow = async ():Promise<boolean> => {
    if (saveRequest.current) {
      const saved = await saveRequest.current
      return dirtyRef.current && saved ? saveNow() : saved
    }
    const currentNote = noteRef.current
    const currentIdentity = identityRef.current
    if (!dirtyRef.current || !currentNote || !currentIdentity) return true
    const snapshot = {
      cwd, project_scope_id:projectScopeId, kind:kindRef.current, id:currentIdentity, markdown:markdownRef.current,
      revision:currentNote.revision, version:changeVersion.current,
    }
    const request = (async () => {
      setSaving(true);setError('')
      try {
        const saved = await api<Note>('PUT', '/api/notes', {
          cwd:snapshot.cwd,project_scope_id:snapshot.project_scope_id,kind:snapshot.kind,id:snapshot.id,markdown:snapshot.markdown,revision:snapshot.revision,
        })
        if (identityRef.current === snapshot.id && kindRef.current === snapshot.kind) {
          setNote(saved);noteRef.current=saved
          if (changeVersion.current === snapshot.version) {
            setMarkdown(saved.markdown);markdownRef.current=saved.markdown
            setDirty(false);dirtyRef.current=false
          }
        }
        return true
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
        return false
      } finally {
        setSaving(false);saveRequest.current=null
      }
    })()
    saveRequest.current=request
    const saved=await request
    return dirtyRef.current&&saved?saveNow():saved
  }

  const finish = async (action:()=>void|Promise<void>) => {
    if (await saveNow()) await action()
  }
  const closeEditor = () => finish(onClose)
  useModalFocus(panel,()=>void closeEditor(),display==='modal')

  const load = async () => {
    if (!identity) return
    setError('')
    try {
      const scope=projectScopeId?`&project_scope_id=${encodeURIComponent(projectScopeId)}`:''
      const next = await api<Note>('GET', `/api/notes?cwd=${encodeURIComponent(cwd)}&kind=${kind}&id=${encodeURIComponent(identity)}${scope}`)
      setNote(next);noteRef.current=next
      setMarkdown(next.markdown);markdownRef.current=next.markdown
      setDirty(false);dirtyRef.current=false
      if (next.error) setError(next.error)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  useEffect(() => { void load() }, [cwd, projectScopeId, kind, identity])
  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId:string;text:string;targetKey?:string}>).detail
      if (detail.sessionId !== terminalSessionId || detail.targetKey !== targetKey) return
      setMarkdown(current => {
        const next=`${current}${current.endsWith('\n') || !current ? '' : '\n'}${detail.text}\n`
        markdownRef.current=next
        return next
      })
      changeVersion.current+=1;setDirty(true);dirtyRef.current=true
    }
    window.addEventListener('mux:terminal-selection', receive)
    return () => window.removeEventListener('mux:terminal-selection', receive)
  }, [terminalSessionId,targetKey])
  useEffect(() => {
    if (!dirty || !note || !identity) return
    const timer = window.setTimeout(() => void saveNow(), 800)
    return () => clearTimeout(timer)
  }, [dirty,markdown,note?.revision,cwd,kind,identity])

  const insertSelection = () => {
    const input = editor.current
    const selected=input?markdown.slice(input.selectionStart,input.selectionEnd):''
    if (!selected) { setError('Select note text before inserting it into the terminal.'); return }
    onInsert(selected)
  }
  const exportNote = () => {
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }))
    const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${kind}-${identity || 'note'}.md`; anchor.click()
    URL.revokeObjectURL(url)
  }
  const editMarkdown = (value:string) => {
    setMarkdown(value);markdownRef.current=value
    changeVersion.current+=1;setDirty(true);dirtyRef.current=true
  }
  const noteState=error?'error':saving?'saving':dirty?'modified':note?.status||'loading'
  const destination=kind==='spaces'?'swe-mux app data':'project .swe-mux/'
  const statusTitle=`${noteState} · ${destination} · revision::${note?.revision||'none'}${error?` · ${error}`:''}`
  const kindLabel=kind==='projects'?'PROJECT':kind==='spaces'?'SPACE':'SESSION'
  const headerIdentity=kind==='spaces'
    ? `NOTE::SPACE::${ownerLabel} · APP DATA`
    : kind==='sessions'
      ? `NOTE::AGENT-RUN::${ownerLabel}${projectLabel?` · PROJECT::${projectLabel}`:''}`
      : `NOTE::PROJECT::${ownerLabel}`
  const body = <section class={`notes-panel ${display === 'pane' ? `note-pane ${mobileActive?'mobile-active':''}` : 'note-modal'}`} ref={panel} aria-label={`${kindLabel} notes`}>
    <header>
      <div><span>{headerIdentity}</span></div>
      <div class="note-header-actions">
        {display === 'pane' && onHide && <button class="note-pane-back" title="Return to terminal" onClick={onHide}>← terminal</button>}
        {display === 'pane' && onPopOut && <button title="Move note to quick editor" onClick={() => void finish(onPopOut)}>pop out</button>}
        {display === 'modal' && onOpenSplit && <button disabled={!terminalSessionId} title={!terminalSessionId?'A live terminal is required':'Dock beside the terminal'} onClick={() => void finish(onOpenSplit)}>dock right</button>}
        <button aria-label={display === 'pane' ? 'Close note pane' : 'Close notes'} title={display === 'pane' ? 'Close note pane' : 'Close notes'} onClick={() => void closeEditor()}>×</button>
      </div>
    </header>
    <div class="notes-toolbar">
      <button disabled={!terminalSessionId} title="Insert selected note text into the associated terminal" onClick={insertSelection}>insert</button>
      <button disabled={!terminalSessionId} title="Append the associated terminal selection to this note" onClick={() => onCapture(targetKey)}>capture</button>
      <button onClick={exportNote}>export</button><button onClick={() => void load()}>reload</button>
    </div>
    <textarea ref={editor} value={markdown} onInput={event=>editMarkdown(event.currentTarget.value)} placeholder="Write Markdown…" autofocus={display==='modal'} spellcheck />
    <footer class="notes-footer"><span class={`notes-state-light ${noteState}`} role="status" aria-label={statusTitle} title={statusTitle} /></footer>
  </section>

  return display === 'modal'
    ? <div class="notes-layer" role="dialog" aria-modal="true" aria-label={`${kindLabel} note editor`} onMouseDown={event => event.target === event.currentTarget && void closeEditor()}>{body}</div>
    : body
}
