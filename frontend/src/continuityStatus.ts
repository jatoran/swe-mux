/**
 * Shared, subscribable status for the one-time Continuity WebAssembly
 * initialization started in the bootstrap. The editor waits on the same shared
 * `initialize()` promise internally; this module only surfaces a failure to the
 * app-level banner so a broken WASM load is visible rather than silent.
 */

type Listener = (message: string | null) => void

let failure: string | null = null
const listeners = new Set<Listener>()

export function reportContinuityFailure(message: string): void {
  failure = message
  for (const listener of listeners) listener(failure)
}

export function subscribeContinuityFailure(listener: Listener): () => void {
  listener(failure)
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
