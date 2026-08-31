// Kept in step with `COMMAND_GROUPS` in `swe_mux/keybindings.py`, which decides
// both the Settings grouping and the leader tree's group letters.
export type CommandCategory = 'session' | 'pane' | 'project' | 'terminal' | 'view' | 'input' | 'clipboard' | 'git' | 'history' | 'voice' | 'notes'

/**
 * Palette prefixes: one palette, four scopes.
 *
 * Typing `@` in the palette narrows to sessions, `#` to Projects, `:` to files and
 * `>` back to commands. It is VS Code's model and it exists because the palette
 * scored only command label/id/category, so "go to that session" - the single most
 * common navigation in a fleet UI - could not be answered by the palette at all.
 */
export type PaletteScope = 'commands' | 'sessions' | 'projects' | 'files'

export const PALETTE_PREFIXES: Record<string, PaletteScope> = {
  '>': 'commands',
  '@': 'sessions',
  '#': 'projects',
  ':': 'files',
}

/** The scope a query asks for, and the query with its prefix removed. */
export function paletteScope(query: string, fallback: PaletteScope = 'commands'): { scope: PaletteScope; term: string } {
  const scope = PALETTE_PREFIXES[query[0]]
  return scope ? { scope, term: query.slice(1) } : { scope: fallback, term: query }
}

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

// Both moved out when bindings stopped being a flat chord→command map: chord
// spelling belongs with the tokenizer that produces chords (`keys.ts`) and the
// command→chord lookup belongs with the trie that holds them (`keymap.ts`).
// Re-exported here because this is where every consumer already looks.
export { bindingFor } from './keymap.ts'
// Imported as well as re-exported: `shortcutMatches` below needs it in local scope,
// which `export … from` does not provide.
import { displayChord } from './keys.ts'
export { displayChord }

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
 * answers both questions a reader brings to a 200-row table: "where is the shortcut
 * for X" and "what owns this chord". The chord is matched in both spellings — stored
 * (`ctrl+shift+p`) and displayed (`Ctrl+Shift+P`) — so neither typing habit misses,
 * and an unbound row matches `not set` exactly as it reads on screen.
 *
 * `platform` because the displayed half is platform-dependent: a macOS reader sees
 * `⌘⇧P` and would otherwise be searching a spelling their screen never shows.
 *
 * Every term must match, so extra words narrow, which is the rule the panel's own
 * search already follows.
 */
export function shortcutMatches(
  command: { id: string; label: string; category: string },
  chord: string | undefined,
  query: string,
  platform = 'win',
): boolean {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!terms.length) return true
  const haystack = [
    command.label, command.id, commandCategoryLabel(command.category),
    chord || UNBOUND_CHORD, displayChord(chord, platform),
  ].join(' ').toLowerCase()
  return terms.every(term => haystack.includes(term))
}
