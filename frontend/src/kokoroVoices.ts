/**
 * Human labels for Kokoro voice ids. The id encodes accent and voice type in
 * its prefix (`af_` = American female, `bm_` = British male, …) and the given
 * name after the underscore; the picker shows both because "af_sky" means
 * nothing until you have memorized the scheme.
 */

const ACCENTS: Record<string, string> = { a: 'American', b: 'British' }
const TYPES: Record<string, string> = { f: 'female', m: 'male' }

export type KokoroVoiceLabel = {
  id: string
  name: string
  flavor: string
}

export function kokoroVoiceLabel(id: string): KokoroVoiceLabel {
  const [prefix, ...rest] = id.split('_')
  const raw = rest.join('_') || id
  const name = raw.charAt(0).toUpperCase() + raw.slice(1)
  const accent = ACCENTS[prefix?.charAt(0) || ''] || ''
  const type = TYPES[prefix?.charAt(1) || ''] || ''
  const flavor = [accent, type].filter(Boolean).join(' ')
  return { id, name, flavor }
}

/** Stable picker order: US female, US male, UK female, UK male, then name. */
export function sortKokoroVoices(ids: string[]): string[] {
  const rank = (id: string) => {
    const prefix = id.split('_')[0] || ''
    const accent = prefix.charAt(0) === 'a' ? 0 : prefix.charAt(0) === 'b' ? 2 : 4
    const type = prefix.charAt(1) === 'f' ? 0 : 1
    return accent + type
  }
  return [...ids].sort((left, right) => rank(left) - rank(right) || left.localeCompare(right))
}
