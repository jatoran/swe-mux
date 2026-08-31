// Measuring which chords this host actually delivers, instead of asserting it.
//
// The shipped table (`swe_mux/keychords.py`) says which chords a browser is
// *expected* to swallow, and it is wrong somewhere by construction. Its predecessor
// refused Ctrl+F as browser-reserved while `Settings.tsx` was intercepting Ctrl+F
// successfully in the same browser, and roughly half the reserved list turned out to
// be chords the page receives perfectly well. Chrome, Firefox and Edge also disagree
// with each other, and none of them documents the set.
//
// So the table is a starting point and this is the correction. The user presses each
// candidate once; a chord that produces a `keydown` here is one the page can have,
// and one that produces silence is one the browser kept. The result is stored per
// device class beside the other UI settings and merged over the table wherever it
// contradicts it.
//
// Two honesty rules, both of which the measurement is worthless without:
//
//  - **Silence is only evidence if the probe was focused and the user really pressed
//    it.** An untested chord is `unknown`, never `blocked`. Absence of a keydown and
//    absence of a keypress are the same signal from outside, so the probe records
//    what was *offered* and what came back, and reports coverage rather than a verdict
//    on chords nobody tried.
//  - **`preventDefault` is part of the measurement.** A chord that arrives and then
//    also navigates the browser away is not usable; the probe suppresses the default
//    and asks the user whether anything else happened, which is the only way to tell
//    "delivered" from "delivered and also acted on".

import { keyChord } from './keys.ts'

export type ProbeVerdict = 'delivered' | 'blocked' | 'unknown'

export type ProbeResult = {
  chord: string
  verdict: ProbeVerdict
  /** When the browser also did its own thing despite `preventDefault`. */
  sideEffect?: boolean
  at: number
}

export type ProbeReport = {
  host: string
  platform: string
  userAgent: string
  results: Record<string, ProbeResult>
  startedAt: number
  finishedAt: number | null
}

/**
 * The chords worth asking about: everything the table calls blocked or contested,
 * which is exactly the set where being wrong costs the user a binding.
 */
export function probeCandidates(policy: {
  browser_unreachable?: string[]
  browser_contested?: Record<string, string>
}): string[] {
  return [
    ...(policy.browser_unreachable || []),
    ...Object.keys(policy.browser_contested || {}),
  ].filter((chord, index, all) => all.indexOf(chord) === index).sort()
}

export function emptyReport(host: string, platform: string, candidates: string[]): ProbeReport {
  return {
    host,
    platform,
    userAgent: typeof navigator === 'undefined' ? '' : navigator.userAgent,
    results: Object.fromEntries(
      candidates.map(chord => [chord, { chord, verdict: 'unknown' as ProbeVerdict, at: 0 }]),
    ),
    startedAt: Date.now(),
    finishedAt: null,
  }
}

/** Fold one observed keystroke into a running report. */
export function recordKeystroke(report: ProbeReport, event: KeyboardEvent): ProbeReport {
  const chord = keyChord(event)
  if (!chord || !(chord in report.results)) return report
  return {
    ...report,
    results: {
      ...report.results,
      [chord]: { chord, verdict: 'delivered', at: Date.now() },
    },
  }
}

/** Mark a chord the user says they pressed and the page never saw. */
export function recordBlocked(report: ProbeReport, chord: string): ProbeReport {
  if (!(chord in report.results)) return report
  return {
    ...report,
    results: { ...report.results, [chord]: { chord, verdict: 'blocked', at: Date.now() } },
  }
}

export function coverage(report: ProbeReport): { tested: number; total: number } {
  const all = Object.values(report.results)
  return { tested: all.filter(item => item.verdict !== 'unknown').length, total: all.length }
}

/**
 * The table, corrected by whatever was measured.
 *
 * Only a *tested* chord moves the answer: `unknown` leaves the shipped verdict
 * standing, because an untried chord is not evidence of anything and treating it as
 * such would quietly hand back every chord the probe was never run against.
 */
export function correctedUnreachable(
  shipped: string[],
  report: ProbeReport | null,
): { unreachable: Set<string>; corrections: string[] } {
  const unreachable = new Set(shipped)
  const corrections: string[] = []
  if (!report) return { unreachable, corrections }
  for (const result of Object.values(report.results)) {
    if (result.verdict === 'delivered' && unreachable.delete(result.chord)) {
      corrections.push(result.chord)
    }
    if (result.verdict === 'blocked' && !unreachable.has(result.chord)) {
      unreachable.add(result.chord)
      corrections.push(result.chord)
    }
  }
  return { unreachable, corrections: corrections.sort() }
}
