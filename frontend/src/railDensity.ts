/**
 * Rail density: how tightly the Action rail packs, per device class.
 *
 * The rail is the one piece of chrome that is *always* on screen under every terminal, and
 * on a desktop with four panes open it is four rails. Comfortable is what it has always
 * been; Compact and Dense trade the reach that sizing buys for terminal rows, which is the
 * trade a desktop wants and a phone mostly does not — hence a per-device-class value rather
 * than one number, exactly as `uiScale.ts` splits chrome scale.
 *
 * This module owns the *choice* and nothing else. The numbers live in `style.css` as one
 * variable group per step, because they are five related lengths that have to move together
 * and because the mobile floor is a different set rather than a scaled desktop one. What
 * crosses the boundary is a single `data-rail-density` attribute on the root element, and
 * Comfortable removes it — so a device that never opted in, and a browser whose daemon never
 * answered, both render the stylesheet's own `:root` values and are indistinguishable from a
 * build without this feature.
 */
import { MOBILE_QUERY, currentProfile, type SettingsProfile } from './deviceSettings.ts'

/** Mirrors `RAIL_DENSITIES` in `config.py`. Anything else is snapped back to comfortable. */
export const RAIL_DENSITIES = ['comfortable', 'compact', 'dense'] as const

export type RailDensity = (typeof RAIL_DENSITIES)[number]

export const DEFAULT_RAIL_DENSITY: RailDensity = 'comfortable'

const LABELS: Record<RailDensity, string> = {
  comfortable: 'Comfortable  (today’s spacing)',
  compact: 'Compact',
  dense: 'Dense',
}

export const railDensityLabel = (density: RailDensity): string => LABELS[density]

const CONFIG_KEY: Record<SettingsProfile, 'rail_density_desktop' | 'rail_density_mobile'> = {
  desktop: 'rail_density_desktop',
  mobile: 'rail_density_mobile',
}

export const railDensityConfigKey = (
  profile: SettingsProfile,
): 'rail_density_desktop' | 'rail_density_mobile' => CONFIG_KEY[profile]

/** A config value as a known step; anything unrecognised — including a daemon older than
 *  this build, which sends neither key — resolves to comfortable. */
export function railDensityFrom(config: Record<string, unknown>, profile: SettingsProfile): RailDensity {
  const raw = config[CONFIG_KEY[profile]]
  return RAIL_DENSITIES.find(step => step === raw) ?? DEFAULT_RAIL_DENSITY
}

let current: Record<string, unknown> | null = null

/** Write this device class's density onto the root element. Idempotent. */
export function applyRailDensity(config: Record<string, unknown>): RailDensity {
  current = config
  const density = railDensityFrom(config, currentProfile())
  writeRailDensity(density)
  return density
}

function writeRailDensity(density: RailDensity): void {
  const root = document.documentElement
  if (density === DEFAULT_RAIL_DENSITY) root.removeAttribute('data-rail-density')
  else root.setAttribute('data-rail-density', density)
}

/**
 * Re-resolve when the device class itself changes under a live page.
 *
 * A desktop window dragged past the breakpoint adopts the mobile layout and the mobile
 * variable group; without this it would keep the desktop *step* while rendering the mobile
 * numbers, which is the one combination nobody chose.
 */
export function watchRailDensityProfile(onChange?: (density: RailDensity) => void): () => void {
  const query = window.matchMedia(MOBILE_QUERY)
  const update = () => { if (current) onChange?.(applyRailDensity(current)) }
  query.addEventListener('change', update)
  return () => query.removeEventListener('change', update)
}
