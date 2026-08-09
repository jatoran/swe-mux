import type { Command } from './commands.ts'

export type VoiceIntentCandidate = {
  command: Command
  phrase: string
  text: string
  confidence: number
}

export type VoiceIntentResolution = {
  match: VoiceIntentCandidate | null
  candidates: VoiceIntentCandidate[]
  confidence: number
}

const NUMBER_WORDS: Record<string, string> = {
  zero: '0', one: '1', two: '2', three: '3', four: '4', five: '5',
  six: '6', seven: '7', eight: '8', nine: '9', ten: '10',
}

const FILLER = /^(?:please|could you|would you|can you|hey|okay|ok)\s+/i

export function normalizeSpokenText(value: string): string {
  let text = value.toLowerCase().replace(/[’']/g, '').replace(/[^a-z0-9{}]+/g, ' ').trim()
  while (FILLER.test(text)) text = text.replace(FILLER, '')
  return text.split(/\s+/).map(token => NUMBER_WORDS[token] || token).join(' ')
}

export function extractWakeIntent(value: string, wakeWords: string[]): string | null {
  const text = normalizeSpokenText(value)
  for (const word of wakeWords) {
    const wake = normalizeSpokenText(word)
    if (text === wake) return ''
    if (text.startsWith(`${wake} `)) return text.slice(wake.length + 1).trim()
  }
  return null
}

function phraseMatch(spoken: string, phrase: string): { text: string; confidence: number } | null {
  const marker = '{text}'
  const normalized = normalizeSpokenText(phrase)
  const markerAt = normalized.indexOf(marker)
  if (markerAt < 0) return spoken === normalized ? { text: '', confidence: 1 } : null
  const before = normalized.slice(0, markerAt).trim()
  const after = normalized.slice(markerAt + marker.length).trim()
  if (before && spoken !== before && !spoken.startsWith(`${before} `)) return null
  const remainder = before ? spoken.slice(before.length).trim() : spoken
  if (after && remainder !== after && !remainder.endsWith(` ${after}`)) return null
  const text = after ? remainder.slice(0, remainder.length - after.length).trim() : remainder
  if (!text) return null
  // A catch-all `{text}` query is the final deterministic grammar fallback.
  // Literal slot templates such as `new Claude in Alpha {text}` must outrank it.
  return { text, confidence: before || after ? .98 : .9 }
}

/** Resolve only aliases declared by the command registry. No action exists here. */
export function resolveVoiceIntent(commands: Command[], spoken: string): VoiceIntentResolution {
  const normalized = normalizeSpokenText(spoken)
  const candidates: VoiceIntentCandidate[] = []
  for (const command of commands) {
    if (!command.available || !command.voice) continue
    for (const phrase of command.voice.phrases) {
      const result = phraseMatch(normalized, phrase)
      if (result) candidates.push({ command, phrase, ...result })
    }
  }
  const bestByCommand = new Map<string, VoiceIntentCandidate>()
  for (const candidate of candidates) {
    const current = bestByCommand.get(candidate.command.id)
    if (!current || candidate.confidence > current.confidence) bestByCommand.set(candidate.command.id, candidate)
  }
  const ranked = [...bestByCommand.values()].sort((left, right) =>
    right.confidence - left.confidence || left.command.label.localeCompare(right.command.label))
  const confidence = ranked[0]?.confidence || 0
  const tied = ranked.filter(candidate => candidate.confidence >= confidence - .01)
  return { match: tied.length === 1 ? tied[0] : null, candidates: tied, confidence }
}

export function numberedCandidates(candidates: VoiceIntentCandidate[], limit = 4): string {
  return candidates.slice(0, limit).map((candidate, index) => `${index + 1}. ${candidate.command.label}`).join(' ')
}

export function selectNumberedCandidate(candidates: VoiceIntentCandidate[], spoken: string): VoiceIntentCandidate | null {
  const match = normalizeSpokenText(spoken).match(/^(?:option\s+)?([1-9]|10)$/)
  if (!match) return null
  return candidates[Number(match[1]) - 1] || null
}
