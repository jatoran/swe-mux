// Pure geometry for the desktop navigation sidebar. Pointer handlers stay in App,
// while these boundaries remain independently testable and consistent with the grid.

export const SIDEBAR_MIN_WIDTH = 190
export const SIDEBAR_MAX_WIDTH = 480
export const SIDEBAR_DEFAULT_WIDTH = 254
export const SIDEBAR_WIDTH_KEY = 'mux.sidebar.width.v1'
export const SIDEBAR_COLLAPSED_WIDTH = 40
export const SIDEBAR_RESIZER_WIDTH = 4

// Collapse only after travelling beyond the minimum. Reopening uses a different
// boundary so a pointer hovering around one pixel cannot make the sidebar chatter.
export const SIDEBAR_COLLAPSE_WIDTH = 150
export const SIDEBAR_REOPEN_WIDTH = 170

export function clampSidebarWidth(value: number): number {
  if (!Number.isFinite(value)) return SIDEBAR_DEFAULT_WIDTH
  return Math.max(SIDEBAR_MIN_WIDTH, Math.min(SIDEBAR_MAX_WIDTH, value))
}

/** Read the device-local sidebar width without accepting stale out-of-range values. */
export function storedSidebarWidth(value: string | null): number {
  const stored = Number(value)
  return Number.isFinite(stored) && stored >= SIDEBAR_MIN_WIDTH && stored <= SIDEBAR_MAX_WIDTH
    ? stored
    : SIDEBAR_DEFAULT_WIDTH
}

/** Resolve one frame of a reversible drag-collapse gesture with hysteresis. */
export function dragCollapsedAtWidth(
  rawWidth: number,
  currentlyCollapsed: boolean,
  collapseWidth: number,
  reopenWidth: number,
): boolean {
  if (!Number.isFinite(rawWidth)) return currentlyCollapsed
  return currentlyCollapsed ? rawWidth < reopenWidth : rawWidth <= collapseWidth
}

/** Translate one semantic open/close command to the active responsive presentation. */
export function navigationSidebarCommandState(mobile: boolean, open: boolean): {
  mobileOpen: boolean | null
  desktopCollapsed: boolean | null
} {
  return mobile
    ? { mobileOpen: open, desktopCollapsed: null }
    : { mobileOpen: null, desktopCollapsed: !open }
}
