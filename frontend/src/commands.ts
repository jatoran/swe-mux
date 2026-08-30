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

/**
 * A command category as a heading rather than as the id it is stored as.
 *
 * Both catalogs spell a category as one lowercase word, so capitalising is the whole
 * rule and a table of ten entries would only be a second place to forget one. It is a
 * function rather than a CSS `text-transform` because the Settings search index reads
 * the *text* of the heading it files a shortcut under, and "Input · view" is not a
 * place a reader recognises. The two catalogs do not agree on the set — the daemon's
 * `KEYBINDING_COMMANDS` has `notes`, `CommandCategory` has `git` and `history` — which
 * is why this takes a string and never asserts membership.
 */
export function commandCategoryLabel(category: string): string {
  return category ? category[0].toUpperCase() + category.slice(1) : ''
}

/** What the Settings shortcut row shows when nothing is bound to its command. */
export const UNBOUND_CHORD = 'not set'

/**
 * Whether one keyboard-shortcut row survives Settings' shortcut filter.
 *
 * The haystack is everything the row shows plus the ids behind it, so the filter
 * answers both questions a reader brings to a 110-row table: "where is the shortcut
 * for X" and "what owns this chord". The chord is matched in both spellings — stored
 * (`ctrl+shift+p`) and displayed (`Ctrl Shift P`) — so neither typing habit misses,
 * and an unbound row matches `not set` exactly as it reads on screen.
 *
 * Every term must match, so extra words narrow, which is the rule the panel's own
 * search already follows.
 */
export function shortcutMatches(
  command: { id: string; label: string; category: string }, chord: string | undefined, query: string,
): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return true
  const haystack = [
    command.label, command.id, commandCategoryLabel(command.category),
    chord || UNBOUND_CHORD, displayChord(chord),
  ].join(' ').toLowerCase()
  return terms.every(term => haystack.includes(term))
}
