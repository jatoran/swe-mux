import { t } from './localization/index.ts'
export type ResourceSaveIndicator = {
  tone: 'saved' | 'modified' | 'error' | 'pending'
  label: string
}

export function resourceSaveIndicator(status: string): ResourceSaveIndicator {
  if (status === 'modified' || status === 'saving') {
    return { tone: 'modified', label: status === 'saving' ? t("resourceSaveIndicator.096b73624b") : t("resourceSaveIndicator.e8ce5dcaf4") }
  }
  // `paused` is not an error, but it is the same kind of fact: this resource is not saving and
  // the user has to know without looking for it.
  if (status === 'paused') return { tone: 'error', get label() { return t("resourceSaveIndicator.19a9d16dc8") } }
  if (status === 'error' || status === 'conflict' || status === 'deleted' || status === 'read-only' || status === 'malformed') {
    const label = status === 'conflict' ? t("resourceSaveIndicator.ab18116a57")
      : status === 'deleted' ? 'Deleted'
      : status === 'read-only' ? t("resourceSaveIndicator.8ac7673530")
      : status === 'malformed' ? 'Malformed'
      : 'Error'
    return { tone: 'error', label }
  }
  if (status === 'ready' || status === 'saved' || status === 'idle' || status === 'missing') {
    return { tone: 'saved', get label() { return t("resourceSaveIndicator.b5c120b316") } }
  }
  return { tone: 'pending', label: status === 'loading' ? t("resourceSaveIndicator.dc380888c4") : status }
}
