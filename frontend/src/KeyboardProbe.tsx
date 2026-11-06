import { isComposingKey } from './inputComposition.ts'
import { catalogRevision, rich, t, locale as uiLocale } from './localization/index.ts'
// The control that measures which chords this browser actually hands the page, and
// the opt-in that lets a fullscreen tab take the ones it does not.
//
// Both exist because the shipped table (`swe_mux/keychords.py`) is a claim about
// somebody else's software and is wrong somewhere by construction. Its predecessor
// refused Ctrl+F as browser-reserved while `Settings.tsx` was calling
// `preventDefault` on Ctrl+F successfully in the same browser, and roughly half the
// reserved list turned out to be chords the page receives perfectly well. Chrome,
// Firefox and Edge also disagree with each other and none of them documents the set.
//
// So this asks. The user presses each candidate once; a chord that produces a
// `keydown` here is one the page can have. A chord that produces nothing is
// *offered* as blocked rather than recorded as blocked, because "the browser ate it"
// and "nobody pressed it" are the same signal from in here.

import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { currentProfile, rawDomain, saveDomain } from './deviceSettings.ts'
import { hostProfile } from './hostProfile.ts'
import {
  coverage, emptyReport, probeCandidates, recordBlocked, recordKeystroke,
  type ProbeReport,
} from './hostKeyboardProbe.ts'
import { displayChord } from './keys.ts'

type Props = {
  policy: { browser_unreachable: string[]; browser_contested: Record<string, string> }
  platform: string
}

/** The stored measurement for this device class, if it has ever been run. */
export function storedProbe(): ProbeReport | null {
  const raw = rawDomain(currentProfile(), 'keyboard')?.probe
  return raw && typeof raw === 'object' ? (raw as ProbeReport) : null
}

export function keyboardLockEnabled(): boolean {
  return rawDomain(currentProfile(), 'keyboard')?.lockInFullscreen === true
}

async function persist(patch: Record<string, unknown>): Promise<void> {
  const existing = rawDomain(currentProfile(), 'keyboard') || {}
  await saveDomain(currentProfile(), 'keyboard', { ...existing, ...patch })
}

export function KeyboardProbe({ policy, platform }: Props) {
  const host = hostProfile()
  const candidates = useMemo(() => probeCandidates(policy), [policy, uiLocale.value, catalogRevision.value])
  const [report, setReport] = useState<ProbeReport | null>(storedProbe)
  const [running, setRunning] = useState(false)
  const [index, setIndex] = useState(0)
  const [lock, setLock] = useState(keyboardLockEnabled)
  const surface = useRef<HTMLDivElement>(null)
  const live = useRef<ProbeReport | null>(null)
  live.current = report

  // Capture phase on the window, because half the point is chords the app itself
  // binds: letting them reach the ordinary dispatcher would run a command in the
  // middle of a measurement.
  useEffect(() => {
    if (!running) return
    const onKey = (event: KeyboardEvent) => {
      if (isComposingKey(event)) return

      event.preventDefault()
      event.stopImmediatePropagation()
      if (event.key === 'Escape') { setRunning(false); return }
      const current = live.current
      if (!current) return
      const next = recordKeystroke(current, event)
      if (next !== current) { setReport(next); setIndex(position => Math.min(position + 1, candidates.length - 1)) }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [running, candidates.length])

  const start = () => {
    const fresh = emptyReport(host.host, host.platform, candidates)
    setReport(fresh)
    setIndex(0)
    setRunning(true)
    requestAnimationFrame(() => surface.current?.focus())
  }

  const finish = () => {
    setRunning(false)
    if (report) void persist({ probe: { ...report, finishedAt: Date.now() } })
  }

  const blocked = () => {
    const current = live.current
    if (!current) return
    setReport(recordBlocked(current, candidates[index]))
    setIndex(position => Math.min(position + 1, candidates.length - 1))
  }

  const tested = report ? coverage(report) : { tested: 0, total: candidates.length }

  return <div class="keyboard-probe">
    <div class="keybinding-heading"><div><h3 data-heading-message="heading.8b07606865">{t("heading.8b07606865")}</h3>
      <p>{t("KeyboardProbe.62b3145bb9")}</p></div>
      <button type="button" onClick={running ? finish : start}>{running ? t("KeyboardProbe.5fc0a3943a") : report ? t("KeyboardProbe.31794226d5") : t("KeyboardProbe.ebf940f2b3")}</button></div>

    {host.host === 'desktop' && <p class="keymap-host">{t("KeyboardProbe.8e0325c33c")}</p>}

    {running && <div class="keyboard-probe-surface" ref={surface} tabIndex={-1} role="status" aria-live="polite">
      <p>{rich("KeyboardProbe.cb62b45fad", { value1: <kbd>{displayChord(candidates[index], platform)}</kbd>, value2: index + 1, length: candidates.length })}</p>
      <div>
        <button type="button" onClick={blocked}>{t("KeyboardProbe.5a39f8921c")}</button>
        <button type="button" onClick={() => setIndex(position => Math.min(position + 1, candidates.length - 1))}>{t("KeyboardProbe.28d03596d2")}</button>
        <button type="button" onClick={finish}>{t("KeyboardProbe.6be804aeec")}</button>
      </div>
      <p class="keymap-host">{rich("KeyboardProbe.57ba0605bc", { value1: <strong>{t("KeyboardProbe.089b71123d")}</strong> })}</p>
    </div>}

    {report && !running && <p class="keymap-host">{rich("KeyboardProbe.bb556f45f3", { tested: tested.tested, total: tested.total, value3: (report.finishedAt) ? 'yes' : 'no', length: Object.values(report.results).filter(item => item.verdict === 'delivered').length })}</p>}

    {host.keyboardLockAvailable && host.host === 'browser' && <label class="check keyboard-lock">
      <span>
        <strong>{t("KeyboardProbe.f298db5b36")}</strong>
        <small>{t("KeyboardProbe.620236b9c2")}</small>
      </span>
      <input type="checkbox" checked={lock} onChange={event => {
        const next = event.currentTarget.checked
        setLock(next)
        void persist({ lockInFullscreen: next })
      }} />
    </label>}
  </div>
}
