export const CONTEXT_MENU_WIDTH = 300

export function clampContextMenuLeft(
  pointerX: number,
  viewportWidth: number,
  preferredWidth = CONTEXT_MENU_WIDTH,
  gutter = 4,
): number {
  const availableWidth = Math.max(0, viewportWidth - gutter * 2)
  const renderedWidth = Math.min(preferredWidth, availableWidth)
  return Math.max(gutter, Math.min(pointerX, viewportWidth - renderedWidth - gutter))
}
