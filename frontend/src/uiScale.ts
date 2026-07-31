/**
 * Chrome scale: one multiplier on the size of every non-terminal surface.
 *
 * The whole UI renders at a single font size. `style.css` forces
 * `font-size:var(--ui-font-size)` onto every element inside `.app-shell` that is
 * not the terminal, with an `!important` that beats the ~165 per-rule font sizes
 * written before it. That makes chrome type one number — and this module is what
 * lets the user move it.
 *
 * It is a *scale*, not a font size, because moving type alone is what wrecks the
 * layout: text grows inside rows and bars whose heights are fixed px, so labels
 * clip and two-line rows overflow. The same `--ui-scale` therefore multiplies
 * `--ui-font-size` and the set of row/bar heights that hold a line of chrome
 * text (see the `calc(... * var(--ui-scale))` declarations in `style.css`), so a
 * row grows with the text in it. Paddings, borders, icon sizes, and the 44 px
 * touch floors deliberately stay fixed: they are not type-derived, and leaving
 * them still is what keeps a scaled-up UI looking dense rather than inflated.
 *
 * Excluded on purpose: the terminal (its own `BASE_FONT_SIZE`, and it feeds
 * cross-device viewport arbitration) and the Continuity note editor (its own
 * `--continuity-*` properties, already configurable).
 *
 * The value is split desktop/mobile because the same UI is driven from a browser
 * and a phone, and one number cannot say "the phone is too small but the desktop
 * is fine". The split uses the same breakpoint as the workspace projection and
 * the device-class settings profiles, so a desktop window dragged narrow adopts
 * the mobile value live.
 */
import { MOBILE_QUERY, currentProfile, type SettingsProfile } from './deviceSettings.ts'

/** Mirrors `UI_SCALES` in `config.py`. Anything else is snapped back to 1. */
export const UI_SCALE_STEPS = [0.9, 1.0, 1.1, 1.25, 1.4] as const

export type UiScale = (typeof UI_SCALE_STEPS)[number]

export const DEFAULT_UI_SCALE: UiScale = 1.0

/** The chrome font size, in px, that a scale resolves to. Also the label copy. */
export const UI_SCALE_BASE_PX = 11

export const uiScaleLabel = (scale: UiScale): string =>
  `${Math.round(scale * 100)}%  (${+(UI_SCALE_BASE_PX * scale).toFixed(1)}px)`

const CONFIG_KEY: Record<SettingsProfile, string> = {
  desktop: 'ui_scale_desktop',
  mobile: 'ui_scale_mobile',
}

/**
 * A config value as a known step. Floats do not compare exactly across a TOML →
 * JSON → JS round trip, so this snaps to the nearest step within a tolerance
 * rather than testing membership, and falls back to 1 for anything else —
 * including a daemon older than this build, which sends neither key.
 */
export function uiScaleFrom(config: Record<string, unknown>, profile: SettingsProfile): UiScale {
  const raw = config[CONFIG_KEY[profile]]
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return DEFAULT_UI_SCALE
  return UI_SCALE_STEPS.find(step => Math.abs(step - raw) < 1e-9) ?? DEFAULT_UI_SCALE
}

let current: Record<string, unknown> | null = null

/** Write the scale for this device's class. Idempotent; safe to call on every config apply. */
export function applyUiScale(config: Record<string, unknown>): void {
  current = config
  writeUiScale(uiScaleFrom(config, currentProfile()))
}

function writeUiScale(scale: UiScale): void {
  const root = document.documentElement.style
  // 1 is the stylesheet's own `:root` value, so releasing the property rather
  // than restating it keeps the default path identical to a build without this
  // feature — including when the daemon is unreachable and no config ever loads.
  if (scale === DEFAULT_UI_SCALE) root.removeProperty('--ui-scale')
  else root.setProperty('--ui-scale', String(scale))
}

/**
 * Re-resolve when the device class itself changes. A desktop browser dragged
 * across the breakpoint switches which config key applies, and without this it
 * would keep the desktop scale while rendering the mobile layout.
 */
export function watchUiScaleProfile(): () => void {
  const query = window.matchMedia(MOBILE_QUERY)
  const update = () => { if (current) applyUiScale(current) }
  query.addEventListener('change', update)
  return () => query.removeEventListener('change', update)
}
