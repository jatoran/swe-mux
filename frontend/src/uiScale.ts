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
 * The terminal follows it too, but not through CSS: xterm owns its own font and
 * derives the cell grid from it, so the scale is handed to `TerminalPane` as a
 * number and multiplied into its base font size there. That does change the
 * columns and rows this device proposes — a bigger font means fewer of both — and
 * that proposal feeds cross-device viewport arbitration. Which is the correct
 * result rather than a hazard: it is what a smaller window already does, and a
 * device rendering type it cannot fit is exactly what arbitration exists to
 * catch. Only the Continuity note editor is still excluded, having its own
 * `--continuity-*` properties that are already configurable.
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

const CONFIG_KEY: Record<SettingsProfile, 'ui_scale_desktop' | 'ui_scale_mobile'> = {
  desktop: 'ui_scale_desktop',
  mobile: 'ui_scale_mobile',
}

export const uiScaleConfigKey = (profile: SettingsProfile): 'ui_scale_desktop' | 'ui_scale_mobile' =>
  CONFIG_KEY[profile]

export type UiScaleIntent = 'increase' | 'decrease' | 'reset'

type UiScaleKeyboardInput = Pick<
  KeyboardEvent,
  'altKey' | 'code' | 'ctrlKey' | 'key' | 'metaKey' | 'shiftKey'
>

/**
 * Classify the fixed browser-style scale keys before an editor or xterm can consume them.
 *
 * `Ctrl+=` is accepted as `Ctrl++`, matching browser zoom on keyboards where `+` shares
 * the equals key. `code` keeps the classification stable across Shift and keyboard
 * layouts; `key` is the fallback used by synthetic events and older engines.
 */
export function uiScaleKeyboardIntent(event: UiScaleKeyboardInput): UiScaleIntent | null {
  if (!event.ctrlKey || event.altKey || event.metaKey) return null
  if (!event.shiftKey && (event.code === 'Digit0' || event.code === 'Numpad0' || event.key === '0')) {
    return 'reset'
  }
  if (
    event.code === 'Equal'
    || event.code === 'NumpadAdd'
    || event.key === '='
    || event.key === '+'
  ) return 'increase'
  if (
    !event.shiftKey
    && (event.code === 'Minus' || event.code === 'NumpadSubtract' || event.key === '-')
  ) return 'decrease'
  return null
}

/** Resolve one discrete step, clamped at the published endpoints. */
export function uiScaleForIntent(scale: UiScale, intent: UiScaleIntent): UiScale {
  if (intent === 'reset') return DEFAULT_UI_SCALE
  const currentIndex = UI_SCALE_STEPS.indexOf(scale)
  const offset = intent === 'increase' ? 1 : -1
  const nextIndex = Math.max(0, Math.min(UI_SCALE_STEPS.length - 1, currentIndex + offset))
  return UI_SCALE_STEPS[nextIndex]
}

export const UI_SCALE_WHEEL_THRESHOLD_PX = 50
export const UI_SCALE_WHEEL_GESTURE_GAP_MS = 250

type UiScaleWheelInput = Pick<WheelEvent, 'deltaMode' | 'deltaY' | 'timeStamp'>

/**
 * Turn a high-resolution Ctrl+wheel stream into discrete scale steps.
 *
 * A mouse notch normally clears the threshold in one event. Trackpad/pinch streams
 * accumulate until they do, reset after a pause, and discard accumulated travel when
 * direction reverses. One unusually large event still produces only one step.
 */
export function createUiScaleWheelIntent() {
  let accumulated = 0
  let lastAt = Number.NEGATIVE_INFINITY
  return (event: UiScaleWheelInput): UiScaleIntent | null => {
    const unit = event.deltaMode === 1 ? 40 : event.deltaMode === 2 ? 800 : 1
    const delta = event.deltaY * unit
    if (!Number.isFinite(delta) || delta === 0) return null
    if (
      event.timeStamp - lastAt > UI_SCALE_WHEEL_GESTURE_GAP_MS
      || (accumulated !== 0 && Math.sign(accumulated) !== Math.sign(delta))
    ) accumulated = 0
    lastAt = event.timeStamp
    accumulated += delta
    if (Math.abs(accumulated) < UI_SCALE_WHEEL_THRESHOLD_PX) return null
    const intent = accumulated < 0 ? 'increase' : 'decrease'
    accumulated = 0
    return intent
  }
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

/**
 * A base size in px at the current scale, for the surfaces CSS cannot reach.
 *
 * Two decimals, not whole pixels: the steps land on 9.9 / 12.1 / 13.75 / 15.4, and
 * rounding those to integers would make 1.1 and 1.25 render identically in the
 * terminal while the chrome beside it moved — `--ui-font-size` is a raw `calc()` and
 * does no rounding. xterm accepts fractional sizes and derives cell metrics from
 * them, so matching it exactly is what keeps the two surfaces the same size.
 */
export const scaledFontSize = (basePx: number, scale: number): number =>
  Math.round(basePx * scale * 100) / 100

let current: Record<string, unknown> | null = null

/** Write the scale for this device's class. Idempotent; safe to call on every config
 *  apply. Returns the resolved step, because the terminal needs it as a number rather
 *  than as a custom property. */
export function applyUiScale(config: Record<string, unknown>): UiScale {
  current = config
  const scale = uiScaleFrom(config, currentProfile())
  writeUiScale(scale)
  return scale
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
export function watchUiScaleProfile(onChange?: (scale: UiScale) => void): () => void {
  const query = window.matchMedia(MOBILE_QUERY)
  const update = () => { if (current) onChange?.(applyUiScale(current)) }
  query.addEventListener('change', update)
  return () => query.removeEventListener('change', update)
}
