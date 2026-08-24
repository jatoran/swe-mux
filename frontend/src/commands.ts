export type CommandCategory = 'session' | 'pane' | 'project' | 'terminal' | 'view' | 'input' | 'clipboard' | 'git' | 'history' | 'voice'

export type VoiceCommandResult = {
  detail: string
  speech?: string
  /** Full response shown in Talk history when it differs from the short status line. */
  transcript?: string
}

export type CommandVoice = {
  /** Deterministic spoken aliases. `{text}` captures the remaining utterance. */
  phrases: string[]
  execute?: (text: string) => VoiceCommandResult | Promise<VoiceCommandResult>
}

export type Command = {
  id: string
  label: string
  category: CommandCategory
  available: boolean
  disabledReason?: string
  run: () => unknown | Promise<unknown>
  voice?: CommandVoice
}

export type CommandRunResult = 'ran' | 'disabled' | 'unknown'

export function runCommand(commands: Command[], id: string): CommandRunResult {
  const command = commands.find(item => item.id === id)
  if (!command) return 'unknown'
  if (!command.available) return 'disabled'
  command.run()
  return 'ran'
}

function fuzzyScore(label: string, query: string): number | null {
  const haystack = label.toLowerCase()
  const needle = query.trim().toLowerCase()
  if (!needle) return 0
  const direct = haystack.indexOf(needle)
  if (direct >= 0) return direct
  let cursor = 0
  let score = 0
  for (const character of needle) {
    const found = haystack.indexOf(character, cursor)
    if (found < 0) return null
    score += found - cursor
    cursor = found + 1
  }
  return score + 100
}

/** Shared empty result, so a closed palette allocates nothing. */
const NO_COMMANDS: Command[] = []

/**
 * The palette's result list - and, while the palette is closed, no work at all.
 *
 * `searchCommands` fuzzy-scores every command in the registry: a string build and a
 * sort over hundreds of entries. Its only consumer is the palette's result list, and
 * it used to be recomputed on every render of the composition root, including every
 * five-second sidebar clock tick. Gating lives here rather than at the call site so
 * the renderer harness exercises the same function the app does.
 */
export function paletteResults(open: boolean, commands: Command[], query: string): Command[] {
  return open ? searchCommands(commands, query) : NO_COMMANDS
}

export function searchCommands(commands: Command[], query: string): Command[] {
  return commands
    .map((command, index) => ({ command, index, score: fuzzyScore(`${command.label} ${command.id} ${command.category}`, query) }))
    .filter((item): item is {command:Command;index:number;score:number} => item.score !== null)
    .sort((left, right) => left.score - right.score || left.index - right.index)
    .map(item => item.command)
}

export function bindingFor(commandId: string, bindings: Record<string, string>): string | undefined {
  return Object.entries(bindings).find(([, id]) => id === commandId)?.[0]
}

export function displayChord(chord?: string): string {
  return chord?.split('+').map(part => part === 'ctrl' ? 'Ctrl' : part === 'alt' ? 'Alt' : part === 'shift' ? 'Shift' : part === 'meta' ? 'Meta' : part.length === 1 ? part.toUpperCase() : part).join(' ') || ''
}
