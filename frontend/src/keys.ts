export function keyChord(event: KeyboardEvent): string {
  const parts: string[] = []
  if (event.ctrlKey) parts.push('ctrl')
  if (event.shiftKey) parts.push('shift')
  if (event.altKey) parts.push('alt')
  if (event.metaKey) parts.push('meta')
  parts.push(event.key.toLowerCase())
  return parts.join('+')
}

/** True for Tab/Shift+Tab focus traversal, excluding app-level modified chords. */
export function isFocusTraversalKey(
  event: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'altKey' | 'metaKey'>,
): boolean {
  return event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey
}
