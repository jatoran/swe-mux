// Which host this frontend is running in, and on which platform.
//
// This is the answer the whole "desktop app or browser?" question turns on, and it
// exists because the two are genuinely different keyboards. The Windows desktop
// shell runs WebView2 with browser accelerator keys disabled (pywebview), so it
// hands the page Ctrl+T, Ctrl+W and Ctrl+Tab - chords no browser tab will ever
// dispatch. Refusing those bindings for everyone, which is what shipped before,
// punished the desktop app for the browser's limits.
//
// The shell publishes a frozen global on its own page (`swe_mux/desktop_shell.py`).
// A browser tab does not have it, and that ABSENCE is the signal rather than a
// missing value: no global means "not the desktop shell".
//
// The platform half matters just as much and is not independent of it today: there
// is no macOS or Linux shell, so a Linux or macOS client is always browser-hosted.
// That is why the Linux defaults problem is a *browser* problem here, and why the
// window-manager reserved table lives beside the browser one rather than under it.

export const DESKTOP_MARKER = '__swemuxDesktopShell'

export type HostKind = 'desktop' | 'browser'
export type PlatformKind = 'win' | 'mac' | 'linux'

export type HostProfile = {
  host: HostKind
  platform: PlatformKind
  /** What the shell said about itself, when there is a shell. */
  shellVersion: string | null
  /** True where the Keyboard Lock API could be offered (Chromium, secure context). */
  keyboardLockAvailable: boolean
}

type DesktopReport = { shell?: unknown; version?: unknown; accelerators?: unknown }

/** The shell's own claim, or null in any browser (and in a shell too old to publish one). */
export function desktopShellReport(
  scope: Record<string, unknown> = globalThis as unknown as Record<string, unknown>,
): { version: string; accelerators: string } | null {
  const raw = scope[DESKTOP_MARKER]
  if (!raw || typeof raw !== 'object') return null
  const value = raw as DesktopReport
  if (value.shell !== 'swe-mux-desktop') return null
  return {
    version: typeof value.version === 'string' ? value.version : '',
    accelerators: typeof value.accelerators === 'string' ? value.accelerators : 'unknown',
  }
}

type PlatformSource = {
  userAgentData?: { platform?: string }
  platform?: string
  userAgent?: string
}

/**
 * win / mac / linux, from the most reliable source the browser offers.
 *
 * `navigator.userAgentData.platform` first because it is the one that is not
 * frozen or lied about; `navigator.platform` next, which is deprecated but still
 * accurate where it exists; the user-agent string last. Anything unrecognised
 * answers `linux`, which is the conservative choice: its window manager grabs the
 * most chords, so an unknown host is told about more conflicts rather than fewer.
 */
export function detectPlatform(source: PlatformSource | undefined = globalThis.navigator): PlatformKind {
  const raw = `${source?.userAgentData?.platform || source?.platform || source?.userAgent || ''}`.toLowerCase()
  if (raw.includes('win')) return 'win'
  if (raw.includes('mac') || raw.includes('iphone') || raw.includes('ipad')) return 'mac'
  return 'linux'
}

/**
 * True where `navigator.keyboard.lock()` could capture browser-reserved chords.
 *
 * Chromium only, and only from JavaScript-initiated fullscreen, with a permission
 * prompt since Chrome 130. Never a default here: it takes Escape and Ctrl+W away
 * from the user's own browser, and the only way out is Chrome's two-second Escape
 * hold. It is offered as an explicit opt-in because swe-mux in a browser is
 * exactly the remote-access case the API was specified for.
 */
export function keyboardLockAvailable(
  scope: { keyboard?: { lock?: unknown } } | undefined = globalThis.navigator as never,
): boolean {
  return typeof scope?.keyboard?.lock === 'function'
}

let cached: HostProfile | null = null

export function hostProfile(): HostProfile {
  if (cached) return cached
  const report = desktopShellReport()
  cached = {
    host: report ? 'desktop' : 'browser',
    platform: detectPlatform(),
    shellVersion: report?.version ?? null,
    keyboardLockAvailable: keyboardLockAvailable(),
  }
  return cached
}

/** Only for tests, which need a profile the real page does not have. */
export function resetHostProfile(next: HostProfile | null = null): void {
  cached = next
}

/** The query a client appends so the daemon resolves for the right keyboard. */
export function hostQuery(profile: HostProfile = hostProfile()): string {
  return `host=${profile.host}&platform=${profile.platform}`
}
