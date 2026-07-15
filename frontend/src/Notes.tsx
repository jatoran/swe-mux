import { useEffect, useRef, useState } from 'preact/hooks'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, placeholder } from '@codemirror/view'
import { api } from './api'
import { useModalFocus } from './modalFocus'
import { noteEditorTheme, wrappedLineIndent } from './noteEditor'

type Note = {
  project:{id:string;label:string;root:string};kind:'projects'|'spaces'|'sessions';id:string;path:string
  exists:boolean;revision:string;markdown:string;status:string;error?:string
  project_scope_id?:string
  storage?:'app-data'|'project';owner_label?:string
  project_label?:string
}
type NoteMode = 'modal'|'pane'
type Props = {
  cwd:string;projectScopeId?:string;spaceId:string;sessionId:string|null;terminalSessionId?:string|null;initialKind?:'projects'|'spaces'|'sessions'
  ownerLabel:string;projectLabel?:string
  display?:NoteMode;targetKey:string;mobileActive?:boolean
  onClose:()=>void|Promise<void>;onHide?:()=>void;onPopOut?:()=>void|Promise<void>;onOpenSplit?:()=>void|Promise<void>
  onBack?:()=>void|Promise<void>;onBrowse?:()=>void
  onInsert:(text:string)=>void;onCapture:(targetKey:string)=>void
}

export function Notes({
  cwd, projectScopeId, spaceId, sessionId, terminalSessionId=sessionId, initialKind='spaces', ownerLabel, projectLabel, display='modal', targetKey,
  mobileActive=false, onClose, onHide, onPopOut, onOpenSplit, onBack, onBrowse, onInsert, onCapture,
}: Props) {
  const kind = initialKind
  const [note, setNote] = useState<Note | null>(null)
  const [markdown, setMarkdown] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const editorHost = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
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
    const instance = view.current
    const range = instance?.state.selection.main
    const selected = instance && range && !range.empty ? instance.state.sliceDoc(range.from, range.to) : ''
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
  const applyEdit = useRef(editMarkdown)
  applyEdit.current = editMarkdown

  // CodeMirror owns the editing surface so soft-wrapped lines can hang-indent to
  // their own leading whitespace; a textarea has no per-line boxes and cannot.
  useEffect(() => {
    if (!editorHost.current) return
    const instance = new EditorView({
      parent: editorHost.current,
      state: EditorState.create({
        doc: markdownRef.current,
        extensions: [
          history(),
          // Tab indents the line by one indent unit (2 spaces) and Shift-Tab dedents,
          // so nesting a bullet is direct. CodeMirror leaves Tab unbound by default
          // because it traps keyboard navigation; defaultKeymap already carries the
          // escape hatch (Ctrl-m, Shift-Alt-m on macOS toggles tab-focus mode).
          // indentWithTab goes last so any future Tab-consuming extension wins.
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          EditorView.lineWrapping,
          wrappedLineIndent,
          noteEditorTheme,
          EditorState.tabSize.of(2),
          placeholder('Write Markdown…'),
          EditorView.contentAttributes.of({ spellcheck: 'true' }),
          EditorView.updateListener.of(update => {
            if (update.docChanged) applyEdit.current(update.state.doc.toString())
          }),
        ],
      }),
    })
    view.current = instance
    if (display === 'modal') instance.focus()
    return () => { instance.destroy();view.current = null }
  }, [])

  // Push externally sourced text (load, capture, note switch) into the editor.
  // Edits that originated in the editor already match, so this cannot loop.
  useEffect(() => {
    const instance = view.current
    if (!instance) return
    const current = instance.state.doc.toString()
    if (current === markdown) return
    instance.dispatch({ changes:{ from:0, to:current.length, insert:markdown } })
  }, [markdown])
  const noteState=error?'error':saving?'saving':dirty?'modified':note?.status||'loading'
  const latestAvailable=/changed externally|revision conflict/i.test(error)
  const destination=kind==='spaces'?'swe-mux app data':'project .swe-mux/'
  const statusTitle=`${noteState} · ${destination} · revision::${note?.revision||'none'}${error?` · ${error}`:''}`
  const kindLabel=kind==='projects'?'PROJECT':kind==='spaces'?'SPACE':'SESSION'
  const resolvedOwner=note?.owner_label||ownerLabel
  const resolvedProject=note?.project_label||projectLabel
  const headerIdentity=kind==='spaces'
    ? `NOTE::SPACE::${resolvedOwner} · APP DATA`
    : kind==='sessions'
      ? `NOTE::AGENT-RUN::${resolvedOwner}${resolvedProject?` · PROJECT::${resolvedProject}`:''}`
      : `NOTE::PROJECT::${resolvedOwner}`
  const body = <section class={`notes-panel ${display === 'pane' ? `note-pane ${mobileActive?'mobile-active':''}` : 'note-modal'}`} ref={panel} aria-label={`${kindLabel} notes`}>
    <header>
      <div><span>{headerIdentity}</span></div>
      <div class="note-header-actions">
        {onBack&&<button title="Return to Notes Index" onClick={()=>void finish(onBack)}>← notes</button>}
        {display==='pane'&&onBrowse&&<button title="Open Notes Index" onClick={onBrowse}>browse</button>}
        {display === 'pane' && onHide && <button class="note-pane-back" title="Return to terminal" onClick={onHide}>← terminal</button>}
        {display === 'pane' && onPopOut && <button title="Move note to quick editor" onClick={() => void finish(onPopOut)}>pop out</button>}
        {display === 'modal' && onOpenSplit && <button title="Dock in the current space" onClick={() => void finish(onOpenSplit)}>dock</button>}
        <button aria-label={display === 'pane' ? 'Close note pane' : 'Close notes'} title={display === 'pane' ? 'Close note pane' : 'Close notes'} onClick={() => void closeEditor()}>×</button>
      </div>
    </header>
    <div class="notes-toolbar">
      <button disabled={!terminalSessionId} title="Insert selected note text into the associated terminal" onClick={insertSelection}>insert</button>
      <button disabled={!terminalSessionId} title="Append the associated terminal selection to this note" onClick={() => onCapture(targetKey)}>capture</button>
      <button onClick={exportNote}>export</button>
      {latestAvailable&&<button title="Replace this draft with the latest note saved on disk" onClick={() => void load()}>load latest</button>}
    </div>
    <div class="notes-editor" ref={editorHost} />
    <footer class="notes-footer"><span class={`notes-state-light ${noteState}`} role="status" aria-label={statusTitle} title={statusTitle} /></footer>
  </section>

  return display === 'modal'
    ? <div class="notes-layer" role="dialog" aria-modal="true" aria-label={`${kindLabel} note editor`} onMouseDown={event => event.target === event.currentTarget && void closeEditor()}>{body}</div>
    : body
}
