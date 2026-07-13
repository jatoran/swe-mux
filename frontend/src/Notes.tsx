import { useEffect, useRef, useState } from 'preact/hooks'
import { api } from './api'
import { useModalFocus } from './modalFocus'

type Note = {
  project:{id:string;label:string;root:string};kind:'spaces'|'sessions';id:string;path:string
  exists:boolean;revision:string;markdown:string;status:string;error?:string
}
type SearchResult = {kind:'spaces'|'sessions';id:string;filename:string;path:string;excerpt:string}
type NoteMode = 'modal'|'pane'
type Props = {
  cwd:string;spaceId:string;sessionId:string|null;terminalSessionId?:string|null;initialKind?:'spaces'|'sessions'
  display?:NoteMode;targetKey:string;mobileActive?:boolean
  onClose:()=>void|Promise<void>;onHide?:()=>void;onPopOut?:()=>void|Promise<void>;onOpenSplit?:()=>void|Promise<void>
  onInsert:(text:string)=>void;onCapture:(targetKey:string)=>void
}

function MarkdownPreview({ markdown }: {markdown:string}) {
  return <div class="note-preview">{markdown.split('\n').map(line => {
    if (line.startsWith('### ')) return <h3>{line.slice(4)}</h3>
    if (line.startsWith('## ')) return <h2>{line.slice(3)}</h2>
    if (line.startsWith('# ')) return <h1>{line.slice(2)}</h1>
    if (line.startsWith('- ')) return <div class="note-list-item">• {line.slice(2)}</div>
    if (line.startsWith('```')) return <div class="note-fence">{line}</div>
    return line ? <p>{line}</p> : <br />
  })}</div>
}

export function Notes({
  cwd, spaceId, sessionId, terminalSessionId=sessionId, initialKind='spaces', display='modal', targetKey,
  mobileActive=false, onClose, onHide, onPopOut, onOpenSplit, onInsert, onCapture,
}: Props) {
  const [kind, setKind] = useState<'spaces'|'sessions'>(initialKind)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [note, setNote] = useState<Note | null>(null)
  const [markdown, setMarkdown] = useState('')
  const [view, setView] = useState<'edit'|'preview'>('edit')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const editor = useRef<HTMLTextAreaElement>(null)
  const panel = useRef<HTMLElement>(null)
  const noteRef = useRef<Note | null>(null)
  const markdownRef = useRef('')
  const dirtyRef = useRef(false)
  const changeVersion = useRef(0)
  const saveRequest = useRef<Promise<boolean> | null>(null)
  const identity = selectedId || (kind === 'spaces' ? spaceId : sessionId)
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
      cwd, kind:kindRef.current, id:currentIdentity, markdown:markdownRef.current,
      revision:currentNote.revision, version:changeVersion.current,
    }
    const request = (async () => {
      setSaving(true);setError('')
      try {
        const saved = await api<Note>('PUT', '/api/project/notes', {
          cwd:snapshot.cwd,kind:snapshot.kind,id:snapshot.id,markdown:snapshot.markdown,revision:snapshot.revision,
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

  useEffect(() => { setKind(initialKind); setSelectedId(null) }, [initialKind,spaceId,sessionId])

  const load = async () => {
    if (!identity) return
    setError('')
    try {
      const next = await api<Note>('GET', `/api/project/notes?cwd=${encodeURIComponent(cwd)}&kind=${kind}&id=${encodeURIComponent(identity)}`)
      setNote(next);noteRef.current=next
      setMarkdown(next.markdown);markdownRef.current=next.markdown
      setDirty(false);dirtyRef.current=false
      if (next.error) setError(next.error)
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  useEffect(() => { void load() }, [cwd, kind, identity])
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

  const changeScope = async (nextKind:'spaces'|'sessions') => {
    if (nextKind === kind || !(await saveNow())) return
    setKind(nextKind);setSelectedId(null);setResults([])
  }
  const openResult = async (result:SearchResult) => {
    if (!(await saveNow())) return
    setKind(result.kind);setSelectedId(result.id);setResults([])
  }
  const insertSelection = () => {
    const input = editor.current
    const selected = input ? markdown.slice(input.selectionStart, input.selectionEnd) : ''
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

  const body = <section class={`notes-panel ${display === 'pane' ? `note-pane ${mobileActive?'mobile-active':''}` : 'note-modal'}`} ref={panel} aria-label={`${kind === 'spaces' ? 'Space' : 'Session'} notes`}>
    <header>
      <div><span>NOTE::{kind === 'spaces' ? 'SPACE' : 'SESSION'}::{note?.project.label || 'loading'}</span><strong title={note?.path || cwd}>{note?.path || cwd}</strong></div>
      <div class="note-header-actions">
        {display === 'pane' && onHide && <button class="note-pane-back" title="Return to terminal" onClick={onHide}>← terminal</button>}
        {display === 'pane' && onPopOut && <button title="Move note to quick editor" onClick={() => void finish(onPopOut)}>pop out</button>}
        {display === 'modal' && onOpenSplit && <button disabled={!terminalSessionId} title={!terminalSessionId?'A live terminal is required':'Dock beside the terminal'} onClick={() => void finish(onOpenSplit)}>dock right</button>}
        <button aria-label={display === 'pane' ? 'Close note pane' : 'Close notes'} title={display === 'pane' ? 'Close note pane' : 'Close notes'} onClick={() => void closeEditor()}>×</button>
      </div>
    </header>
    <nav aria-label="Note controls">
      {display === 'modal' && <><button class={kind === 'spaces' ? 'active' : ''} onClick={() => void changeScope('spaces')}>Space note</button><button disabled={!sessionId} class={kind === 'sessions' ? 'active' : ''} onClick={() => void changeScope('sessions')}>Session note</button></>}
      <button class={view === 'edit' ? 'active' : ''} onClick={() => setView('edit')}>Edit</button><button class={view === 'preview' ? 'active' : ''} onClick={() => setView('preview')}>Preview</button>
    </nav>
    <div class="notes-toolbar">
      {display === 'modal' && <input placeholder="Search project notes…" onInput={async event => setResults((await api<{items:SearchResult[]}>('GET', `/api/project/notes/search?cwd=${encodeURIComponent(cwd)}&q=${encodeURIComponent(event.currentTarget.value)}`)).items)} />}
      <button disabled={!terminalSessionId || view !== 'edit'} title="Insert selected note text into the associated terminal" onClick={insertSelection}>insert</button>
      <button disabled={!terminalSessionId} title="Append the associated terminal selection to this note" onClick={() => onCapture(targetKey)}>capture</button>
      <button onClick={exportNote}>export</button><button onClick={() => void load()}>reload</button>
    </div>
    <div class={`notes-results ${results.length?'':'empty'}`}>{results.map(result => <button title={result.path} onClick={()=>void openResult(result)}><strong>{result.filename}</strong><span>{result.excerpt}</span></button>)}</div>
    <div class="notes-meta">{error && <div class="notes-status error" role="alert">{error}</div>}<div class="notes-status">{saving ? 'saving…' : dirty ? 'modified' : note?.status || 'loading'} · revision::{note?.revision || 'none'}{!note?.exists ? ' · first save creates .swe-mux/' : ''}</div></div>
    {view === 'edit' ? <textarea ref={editor} value={markdown} onInput={event => editMarkdown(event.currentTarget.value)} placeholder="# Project notes" autofocus={display==='modal'} /> : <MarkdownPreview markdown={markdown} />}
  </section>

  return display === 'modal'
    ? <div class="notes-layer" role="dialog" aria-modal="true" aria-label="Project notes" onMouseDown={event => event.target === event.currentTarget && void closeEditor()}>{body}</div>
    : body
}
