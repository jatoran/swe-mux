import { transcriptToolInputText, type TranscriptToolCall } from './transcriptView'

export function TranscriptToolCalls({ calls }: { calls: TranscriptToolCall[] }) {
  if (!calls.length) return null
  return <section class="transcript-tool-calls" aria-label={`${calls.length} tool call${calls.length === 1 ? '' : 's'}`}>
    {calls.map(call => {
      const input = transcriptToolInputText(call.input)
      return input
        ? <details class="transcript-tool-call" key={call.id}>
          <summary>{call.name}</summary>
          <pre>{input}</pre>
        </details>
        : <div class="transcript-tool-call transcript-tool-call-empty" key={call.id}>{call.name}</div>
    })}
  </section>
}
