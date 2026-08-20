/**
 * The ranking ladder every typed-filter surface in the app shares.
 *
 * It was written for the Settings index and then wanted by the sidebar's
 * Project/session filter, so it lives here rather than in either of them. Two
 * search boxes with two ladders would disagree about which of two similarly
 * named things is the "best match" - and "best match" is exactly what Enter
 * commits to in the sidebar, so only one answer can be right.
 *
 * The shape of the ladder: an exact name beats a name you typed the start of,
 * which beats a word inside the name, which beats a substring of it, which beats
 * anything found only in the candidate's secondary text. Below all of that sits a
 * span-bounded subsequence pass, which is what makes an abbreviation ("swmx")
 * land without letting five letters scattered across a sentence count as a hit.
 *
 * Pure and DOM-free, so both callers can be unit tested without a renderer.
 */

/** Case-folded, whitespace-collapsed. Both the match key and what queries split on. */
export const normalizeSearchText = (value: string): string =>
  value.toLowerCase().replace(/\s+/g, ' ').trim()

/**
 * Length of the tightest run of `haystack` containing `needle` as a subsequence,
 * or -1. The span is what makes this usable as a fuzzy net: "scrlbck" matching
 * "scrollback bytes" is a real abbreviation, while "sound" matching "set
 * shortcut for open command palette" is five letters scattered over a sentence.
 */
export function subsequenceSpan(haystack: string, needle: string): number {
  if (!needle) return -1
  let best = -1
  for (let start = haystack.indexOf(needle[0]); start >= 0; start = haystack.indexOf(needle[0], start + 1)) {
    let at = start
    let matched = true
    for (let index = 1; index < needle.length; index += 1) {
      at = haystack.indexOf(needle[index], at + 1)
      if (at < 0) { matched = false; break }
    }
    if (!matched) break
    const span = at - start + 1
    if (best < 0 || span < best) best = span
  }
  return best
}

/**
 * How well one already-normalized `term` matches a candidate whose own name is
 * `label` and whose secondary text is `keywords`.
 *
 * Zero means no match, and every caller treats that as disqualifying rather than
 * as a low score: a query term that matches nothing about a candidate means the
 * user was narrowing, not describing.
 */
export function fieldScore(label: string, keywords: string, term: string): number {
  if (!term) return 0
  if (label === term) return 1200
  if (label.startsWith(term)) return 900
  const wordAt = label.indexOf(` ${term}`)
  if (wordAt >= 0) return 760 - Math.min(wordAt, 60)
  const at = label.indexOf(term)
  if (at >= 0) return 620 - Math.min(at, 60)
  const keywordAt = keywords.indexOf(term)
  if (keywordAt >= 0) return 300 - Math.min(keywordAt, 240) / 4
  if (term.length >= 3) {
    const span = subsequenceSpan(label, term)
    if (span > 0 && span <= term.length * 2 + 2) return 140
  }
  return 0
}
