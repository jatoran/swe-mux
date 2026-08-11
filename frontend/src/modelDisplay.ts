export type ModelDisplayPrefix = Readonly<{
  raw: string
  display: string
}>

// Prefix mappings compact stable model families while retaining version and
// snapshot suffixes. Add a mapping here instead of teaching individual views
// about provider naming conventions.
export const MODEL_DISPLAY_PREFIXES: readonly ModelDisplayPrefix[] = [
  { raw: 'anthropic/claude-sonnet', display: 'sonnet' },
  { raw: 'anthropic/claude-haiku', display: 'haiku' },
  { raw: 'anthropic/claude-opus', display: 'opus' },
  { raw: 'moonshotai/kimi', display: 'kimi' },
  { raw: 'openai/codex', display: 'codex' },
  { raw: 'openai/gpt', display: 'gpt' },
  { raw: 'claude-sonnet', display: 'sonnet' },
  { raw: 'claude-haiku', display: 'haiku' },
  { raw: 'claude-opus', display: 'opus' },
]

const ORDERED_MODEL_DISPLAY_PREFIXES = [...MODEL_DISPLAY_PREFIXES]
  .sort((left,right)=>right.raw.length-left.raw.length)

export function displayModelName(model: string): string {
  const normalized = model.toLowerCase()
  for (const mapping of ORDERED_MODEL_DISPLAY_PREFIXES) {
    if (normalized === mapping.raw) return mapping.display
    if (normalized.startsWith(`${mapping.raw}-`)) {
      return `${mapping.display}${model.slice(mapping.raw.length)}`
    }
  }
  return model
}
