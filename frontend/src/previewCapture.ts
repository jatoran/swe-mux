/**
 * The pure half of preview capture: what the daemon's typed unavailable state
 * reads as. Separated from `PreviewPane.tsx` for the reason `processRows.ts` is
 * separated from the panel it draws — the wording is the part that has to be
 * right on a machine that has neither half of the backend installed, and that is
 * exactly the machine no component test runs on.
 *
 * `state` is the discriminator, never the prose: `extra_missing` and
 * `browser_missing` need different commands, and a fresh install can be in
 * either. Collapsing them into one "capture unavailable" is what sent an
 * operator who had already installed the extra to install it again.
 */

export type CaptureState = 'ready' | 'extra_missing' | 'browser_missing'

export type CaptureResult = {
  available: boolean
  path?: string
  region?: boolean
  error?: string
  state?: CaptureState
  reason?: string
  remedy?: string | null
}

const HEADLINE: Record<CaptureState, string> = {
  ready: 'Preview capture is ready.',
  extra_missing: 'Preview capture is not installed.',
  browser_missing: 'Preview capture is installed but has no browser to render with.',
}

/**
 * One sentence naming which kind of absent this is and the exact command out of
 * it. `remedy` is null where no command on this machine helps — the packaged
 * desktop app ships no Playwright — and saying so is better than printing an
 * empty "Enable with:", which is what the old copy did whenever the hint was
 * missing.
 */
export function captureUnavailableNote(result: CaptureResult): string {
  const headline = HEADLINE[result.state ?? 'extra_missing'] ?? 'Capture unavailable.'
  const reason = result.reason ? ` ${result.reason}.` : ''
  const remedy = result.remedy
    ? ` Run: ${result.remedy}`
    : ' No command on this machine enables it.'
  return `${headline}${reason}${remedy}`
}
