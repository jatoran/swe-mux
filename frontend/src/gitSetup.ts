export type GitSweMuxSetup = {
  show: boolean
  reason: 'available' | 'tracked' | 'already_ignored' | 'disabled' | 'decided'
  decision: 'unseen' | 'keep_visible' | 'ignore_all'
  canIgnore: boolean
  tracked: boolean
}

export function parseGitSweMuxSetup(value: unknown): GitSweMuxSetup | null {
  if (!value || typeof value !== 'object') return null
  const item = value as Record<string, unknown>
  const reasons = new Set(['available', 'tracked', 'already_ignored', 'disabled', 'decided'])
  const decisions = new Set(['unseen', 'keep_visible', 'ignore_all'])
  if (typeof item.show !== 'boolean'
    || typeof item.reason !== 'string' || !reasons.has(item.reason)
    || typeof item.decision !== 'string' || !decisions.has(item.decision)
    || typeof item.can_ignore !== 'boolean') return null
  return {
    show: item.show,
    reason: item.reason as GitSweMuxSetup['reason'],
    decision: item.decision as GitSweMuxSetup['decision'],
    canIgnore: item.can_ignore,
    tracked: item.tracked === true,
  }
}
