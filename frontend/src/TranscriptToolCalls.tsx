import { useState } from 'preact/hooks'
import { transcriptToolInputText, type TranscriptToolCall } from './transcriptView'

/**
 * The tool calls at one seam of a conversation, each a disclosure that starts closed.
 *
 * Two callers with two shapes. History hands over calls with their inputs. The drawer's
 * reader hands over names only - inputs were nearly all of its payload, for a disclosure
 * almost nobody opens - plus `load`, which reads this seam's inputs the first time one
 * of its calls is opened. Either way an input's `<pre>` exists only while its call is
 * open: a long session's tool calls toggled on drew every input as preformatted text.
 */
export function TranscriptToolCalls({ calls, load }: {
  calls: TranscriptToolCall[]
  /** Reads these calls' inputs; given when `calls` arrive without them. */
  load?: () => Promise<TranscriptToolCall[]>
}) {
  const [open, setOpen] = useState<string[]>([])
  const [loaded, setLoaded] = useState<TranscriptToolCall[] | null>(null)
  const [reading, setReading] = useState<'idle' | 'loading' | 'failed'>('idle')
  if (!calls.length) return null
  const inputs = new Map((loaded || []).map(call => [call.id, call.input]))
  const fetchInputs = () => {
    if (!load || loaded || reading === 'loading') return
    setReading('loading')
    load()
      .then(result => { setLoaded(result); setReading('idle') })
      .catch(() => setReading('failed'))
  }
  return <section class="transcript-tool-calls" aria-label={`${calls.length} tool call${calls.length === 1 ? '' : 's'}`}>
    {calls.map(call => {
      const lazy = !!load && call.input === undefined
      const input = transcriptToolInputText(lazy ? inputs.get(call.id) : call.input)
      if (!lazy && !input) {
        return <div class="transcript-tool-call transcript-tool-call-empty" key={call.id}>{call.name}</div>
      }
      const isOpen = open.includes(call.id)
      return <details class="transcript-tool-call"
        key={call.id}
        open={isOpen}
        onToggle={event => {
          const now = (event.currentTarget as HTMLDetailsElement).open
          setOpen(current => now
            ? (current.includes(call.id) ? current : [...current, call.id])
            : current.filter(id => id !== call.id))
          if (now && lazy) fetchInputs()
        }}
      >
        <summary>{call.name}</summary>
        {isOpen && (lazy && !loaded
          ? <p class="transcript-tool-call-status">{reading === 'failed'
            ? 'This input could not be read. Close and reopen to try again.'
            : 'Reading input…'}</p>
          : <pre>{input || '(no input)'}</pre>)}
      </details>
    })}
  </section>
}
