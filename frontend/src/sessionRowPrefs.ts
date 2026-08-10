// Persistence bridge and shared clock for the sidebar session row.
//
// The layout blob lives in ONE canonical settings bucket rather than the
// desktop/mobile split the sounds and notification domains use. Those genuinely
// want to differ per device; a row layout does not — the same person wants the
// same information in the same order on both screens. What differs is how much
// of it fits, and that is `mobileFields`, a single flag inside the one blob,
// rather than a second layout to keep in sync by hand.
//
// Two values inside that one blob *are* per device class — `dotSizeDesktop` and
// `dotSizeMobile` — and they are the exception that proves the rule: a physical
// size is the one property a shared layout cannot express, because the screens
// are held at different distances. They stay fields of the single blob rather
// than a second profile bucket, so editing either is one write and either can be
// set from either device.

import { useEffect, useState } from 'preact/hooks'
import { MOBILE_QUERY, currentProfile, rawDomain, saveDomain, type SettingsProfile } from './deviceSettings.ts'
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

/** The indicator size this configuration gives a device class, in CSS pixels. */
export function sessionDotSize(
  config: SessionRowConfig, profile: SettingsProfile = currentProfile(),
): number {
  return profile === 'mobile' ? config.dotSizeMobile : config.dotSizeDesktop
}

let appliedConfig: SessionRowConfig | null = null

/**
 * Write the indicator size for this device's class onto the root element.
 *
 * A custom property rather than a prop because the size is not the indicator's
 * alone: the sidebar's gutter column, the stack thread's x and its break, the
 * title line's height, and the row's own height are all expressed as
 * `--session-dot` in `style.css`. Handing the number to `StateIndicator` would
 * resize the glyph and leave every one of those behind, which is the failure the
 * variable was introduced to prevent. Idempotent; safe to call on every change.
 */
export function applySessionDotSize(config: SessionRowConfig): number {
  appliedConfig = config
  const size = sessionDotSize(config)
  document.documentElement.style.setProperty('--session-dot', `${size}px`)
  return size
}

/**
 * Re-resolve when the device class changes under a live page.
 *
 * A desktop window dragged across the breakpoint adopts the mobile layout, and
 * without this it would keep the desktop size while rendering it — the same
 * reason chrome scale watches the same query.
 */
export function watchSessionDotProfile(): () => void {
  const query = window.matchMedia(MOBILE_QUERY)
  const update = () => { if (appliedConfig) applySessionDotSize(appliedConfig) }
  query.addEventListener('change', update)
  return () => query.removeEventListener('change', update)
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
 * Observed inline size of an element, for width-driven token shedding.
 *
 * A `ResizeObserver` rather than a container query because shedding has to
 * happen in the token list, not in CSS: hiding a token with `display:none`
 * strips the token but leaves the separator JSX already emitted beside it.
 * Quantized to whole pixels so a drag emits one update per pixel at most.
 */
export function useObservedWidth(target: { current: HTMLElement | null }): number {
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = target.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(entries => {
      const size = entries[0]?.contentRect.width
      if (typeof size === 'number') setWidth(Math.round(size))
    })
    observer.observe(element)
    setWidth(Math.round(element.getBoundingClientRect().width))
    return () => observer.disconnect()
  }, [target])
  return width
}

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
