/**
 * Resolution plan for an assistant-dispatched UI command (Phase 10.6).
 *
 * The assistant's `run_ui_command` hands this device a phrase like "open
 * project workout-plan". Resolution runs the same deterministic ladder spoken
 * input uses — registry aliases, the closed query grammar, the fuzzy pass —
 * with two deliberate differences from the live microphone path:
 *
 * - the `{text}` catch-all is excluded, because for a dispatched command it is
 *   a black hole: it matches anything and turns "move to project X" into a
 *   voice lookup instead of a failure the assistant can react to;
 * - there is no assistant fallback, because the assistant is the caller.
 *
 * Pure so the ladder is testable without a workspace.
 */
import { searchCommands, type Command } from './commands.ts'
import { normalizeSpokenText, resolveVoiceIntent } from './voiceIntents.ts'
import { resolveVoiceFuzzy } from './voiceFuzzy.ts'
import { parseVoiceQuery, type VoiceQuery } from './voiceQueries.ts'

export type UiCommandPlan =
  | { kind: 'command'; command: Command; captured: string }
  | { kind: 'query'; query: VoiceQuery }
  | { kind: 'none'; candidates: string[] }

/** A command whose only trigger is the bare `{text}` slot is a catch-all. */
function isCatchAll(command: Command): boolean {
  const phrases = command.voice?.phrases || []
  return phrases.length > 0 && phrases.every(phrase => phrase.trim() === '{text}')
}

export function planUiCommand(commands: Command[], text: string): UiCommandPlan {
  const registry = commands.filter(command => !isCatchAll(command))
  const resolution = resolveVoiceIntent(registry, text)
  if (resolution.match) {
    return { kind: 'command', command: resolution.match.command, captured: resolution.match.text }
  }
  // The closed query grammar owns navigation ("open project X", "session N",
  // "go to next session") and the deterministic lookups; it resolves entity
  // references with its own candidate answers, which is exactly what the
  // assistant needs relayed on a miss.
  const query = parseVoiceQuery(text)
  if (query) return { kind: 'query', query }
  const fuzzy = resolveVoiceFuzzy(registry, text)
  if (fuzzy) return { kind: 'command', command: fuzzy.command, captured: '' }
  const normalized = normalizeSpokenText(text)
  const byLabel = registry.filter(
    command => command.available && normalizeSpokenText(command.label) === normalized,
  )
  if (byLabel.length === 1) return { kind: 'command', command: byLabel[0], captured: '' }
  const suggestions = [
    ...resolution.candidates.map(candidate => candidate.command.label),
    ...searchCommands(registry.filter(command => command.available), text)
      .slice(0, 3)
      .map(command => command.label),
  ]
  return { kind: 'none', candidates: [...new Set(suggestions)].slice(0, 6) }
}
