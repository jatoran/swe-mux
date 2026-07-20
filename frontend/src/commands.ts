export type CommandCategory = 'session' | 'pane' | 'project' | 'terminal' | 'view' | 'input' | 'clipboard' | 'git' | 'history' | 'voice'

export type Command = {
  id: string
  label: string
  category: CommandCategory
  available: boolean
  disabledReason?: string
  run: () => void
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
