import { isAgentBackend } from './harnessRegistry.ts'
import {
  railItemVisible, railPadSlotItemIds, railPayload, resolveRailItems, type RailBackend, type RailConfig,
  type RailDevice, type RailItem,
} from './commandRail.ts'

export type RailVoiceRequest = {
  action: 'insertText' | 'pasteText' | 'sendKey'
  text?: string
  submit?: boolean
}

export type RailVoiceEntry = {
  item: RailItem
  phrases: string[]
  request: RailVoiceRequest
}

const cleanPhrases = (phrases: readonly string[]): string[] => [...new Set(phrases
  .map(phrase => phrase.trim())
  .filter(Boolean))]

function explicitPhrases(item: RailItem): string[] {
  return cleanPhrases(item.voicePhrases || [])
}

function itemPhrases(item: RailItem): string[] {
  const explicit = explicitPhrases(item)
  if (explicit.length) return explicit
  if (item.type !== 'skill' && item.type !== 'slash') return []
  const name = (item.text || '').trim().replace(/^[/$]/, '')
  return name ? cleanPhrases([name, `run ${name}`]) : []
}

function itemRequest(item: RailItem, backend: RailBackend): RailVoiceRequest | null {
  if (item.type === 'key' && item.bytes) return { action: 'sendKey', text: item.bytes }
  if (item.type === 'action' && item.action === 'paste') return { action: 'pasteText' }
  if (item.type === 'skill' || item.type === 'slash') {
    if (!isAgentBackend(backend)) return null
    // A name-derived alias may insert a command for editing, but may not run an
    // arbitrary configured macro. Submitted entries need an explicit opt-in.
    if (item.submit && !explicitPhrases(item).length) return null
    const text = railPayload(item, backend)
    return text ? { action: 'insertText', text, submit: !!item.submit } : null
  }
  return null
}

/**
 * Resolve the voice-safe subset of Actions currently visible to a session.
 *
 * Placement remains authoritative: removing an item from both surfaces removes its
 * spoken alias too. Duplicate placements collapse to one command. UI-only,
 * destructive, literal-text, and prompt items never cross this adapter.
 *
 * **A pad is a placement.** Its slots hold ordinary catalog items, so the actions inside
 * one are walked exactly as though they sat on the rail themselves - otherwise the
 * default rail, which folds the four arrows into a pad, would silently retire "press up"
 * as a spoken command. The pad chip itself is never voiced: it is a container with no
 * behaviour of its own, and `itemRequest` declines it along with every other non-key.
 */
export function resolveRailVoiceEntries(
  config: RailConfig,
  context: { device: RailDevice; backend: RailBackend },
): RailVoiceEntry[] {
  const seen = new Set<string>()
  const byId = new Map(config.items.map(item => [item.id, item]))
  const placed = resolveRailItems(config, 'strip', context).map(entry => entry.item)
  const entries = placed.flatMap(item => [
    item,
    ...railPadSlotItemIds(item)
      .map(id => byId.get(id))
      .filter((slot): slot is RailItem => !!slot && railItemVisible(slot, context.backend)),
  ])
  const result: RailVoiceEntry[] = []
  for (const item of entries) {
    if (seen.has(item.id)) continue
    seen.add(item.id)
    const phrases = itemPhrases(item)
    const request = itemRequest(item, context.backend)
    if (!phrases.length || !request) continue
    result.push({ item, phrases, request })
  }
  return result
}
