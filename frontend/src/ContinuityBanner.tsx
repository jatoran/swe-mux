import { useEffect, useState } from 'preact/hooks'
import { subscribeContinuityFailure } from './continuityStatus'

/** App-level banner shown when the note editor's WebAssembly engine fails to load. */
export function ContinuityBanner() {
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => subscribeContinuityFailure(setMessage), [])
  if (!message) return null
  return (
    <div class="broadcast-banner continuity-banner" role="alert">
      <strong>Note editor engine unavailable</strong>
      <span>The Continuity WebAssembly engine failed to initialize: {message}. Notes cannot be edited until the app is reloaded.</span>
    </div>
  )
}
