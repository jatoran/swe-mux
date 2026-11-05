import { t } from './localization/index.ts'
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
  get ready() { return t("previewCapture.4aab06c7c7") },
  get extra_missing() { return t("previewCapture.f4b7309736") },
  get browser_missing() { return t("previewCapture.5dade93e87") },
}

/**
 * One sentence naming which kind of absent this is and the exact command out of
 * it. `remedy` is null where no command on this machine helps — the packaged
 * desktop app ships no Playwright — and saying so is better than printing an
 * empty "Enable with:", which is what the old copy did whenever the hint was
 * missing.
 */
export function captureUnavailableNote(result: CaptureResult): string {
  const headline = HEADLINE[result.state ?? 'extra_missing'] ?? t("previewCapture.10e0a77fdd")
  const reason = result.reason ? ` ${result.reason}.` : ''
  const remedy = result.remedy
    ? ` Run: ${result.remedy}`
    : t("previewCapture.e79500bc32")
  return `${headline}${reason}${remedy}`
}
