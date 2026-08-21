export type ResourceSaveIndicator = {
  tone: 'saved' | 'modified' | 'error' | 'pending'
  label: string
}

export function resourceSaveIndicator(status: string): ResourceSaveIndicator {
  if (status === 'modified' || status === 'saving') {
    return { tone: 'modified', label: status === 'saving' ? 'Saving' : 'Modified' }
  }
  // `paused` is not an error, but it is the same kind of fact: this resource is not saving and
  // the user has to know without looking for it.
  if (status === 'paused') return { tone: 'error', label: 'Autosave paused' }
  if (status === 'error' || status === 'conflict' || status === 'deleted' || status === 'read-only' || status === 'malformed') {
    const label = status === 'conflict' ? 'Save conflict'
      : status === 'deleted' ? 'Deleted'
      : status === 'read-only' ? 'Read only'
      : status === 'malformed' ? 'Malformed'
      : 'Error'
    return { tone: 'error', label }
  }
  if (status === 'ready' || status === 'saved' || status === 'idle' || status === 'missing') {
    return { tone: 'saved', label: 'Saved' }
  }
  return { tone: 'pending', label: status === 'loading' ? 'Loading' : status }
}
