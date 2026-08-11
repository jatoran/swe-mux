export type ResourceSaveIndicator = {
  tone: 'saved' | 'modified' | 'error' | 'pending'
  label: string
}

export function resourceSaveIndicator(status: string): ResourceSaveIndicator {
  if (status === 'modified' || status === 'saving') {
    return { tone: 'modified', label: status === 'saving' ? 'Saving' : 'Modified' }
  }
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
