// Persistence bridge and shared clock for the sidebar session row.
//
// The layout blob lives in ONE canonical settings bucket rather than the
// desktop/mobile split the sounds and notification domains use. Those genuinely
// want to differ per device; a row layout does not — the same person wants the
// same information in the same order on both screens. What differs is how much
// of it fits, and that is `mobileFields`, a single flag inside the one blob,
// rather than a second layout to keep in sync by hand.

import { useEffect, useState } from 'preact/hooks'
import { rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'
import {
  defaultSessionRowConfig, normalizeSessionRowConfig, type SessionRowConfig,
} from './sessionRowConfig.ts'

const ROW_PROFILE: SettingsProfile = 'desktop'

export function loadSessionRowConfig(): SessionRowConfig {
  return normalizeSessionRowConfig(rawDomain(ROW_PROFILE, 'sessionRows'))
}

export async function saveSessionRowConfig(config: SessionRowConfig): Promise<void> {
  await saveDomain(ROW_PROFILE, 'sessionRows', config as unknown as Record<string, unknown>)
}

export async function resetSessionRowConfig(): Promise<void> {
  await saveSessionRowConfig(defaultSessionRowConfig())
}

/** The live configuration, re-read whenever any device edits settings. */
export function useSessionRowConfig(): SessionRowConfig {
  const [config, setConfig] = useState(loadSessionRowConfig)
  useEffect(() => {
    const sync = () => setConfig(loadSessionRowConfig())
    sync()
    window.addEventListener('mux:settings-changed', sync)
    return () => window.removeEventListener('mux:settings-changed', sync)
  }, [])
  return config
}

/**
 * Clock quantum for elapsed durations, milliseconds.
 *
 * Five seconds rather than one because the whole sidebar re-renders on each
 * tick: a `working` row that reads `20s` then `25s` costs a fifth of the renders
 * and loses nothing a human acts on. Ready rows report a *finished* turn's
 * length, which is static, so a settled fleet re-renders on no clock at all.
 */
export const ROW_CLOCK_INTERVAL_MS = 5_000

/**
 * Shared quantized wall clock, in epoch seconds.
 *
 * One timer for the whole sidebar instead of one per row, and stopped entirely
 * while the tab is hidden — a background tab has no rows to age.
 */
export function useRowClock(active = true): number {
  const [now, setNow] = useState(() => Math.floor(Date.now() / 1000))
  useEffect(() => {
    if (!active) return
    let timer: number | undefined
    const tick = () => setNow(Math.floor(Date.now() / 1000))
    const start = () => {
      if (timer !== undefined) return
      tick()
      timer = window.setInterval(tick, ROW_CLOCK_INTERVAL_MS)
    }
    const stop = () => {
      if (timer === undefined) return
      window.clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => (document.hidden ? stop() : start())
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [active])
  return now
}
