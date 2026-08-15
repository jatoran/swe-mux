export const UI_BUILD_META_NAME = 'ui-build'

export function normalizeUiBuildId(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const candidate = value.trim().toLowerCase()
  return /^[0-9a-f]{64}$/.test(candidate) ? candidate : null
}

export function loadedUiBuildId(documentValue: Pick<Document, 'querySelector'> = document): string | null {
  const content = documentValue.querySelector<HTMLMetaElement>(`meta[name="${UI_BUILD_META_NAME}"]`)?.content
  return normalizeUiBuildId(content)
}

export function uiUpdateRequired(loaded: string | null, served: unknown): boolean {
  const next = normalizeUiBuildId(served)
  return loaded !== null && next !== null && loaded !== next
}

export function uiUpdateReloadReady(
  updateAvailable: boolean,
  visibility: DocumentVisibilityState,
): boolean {
  return updateAvailable && visibility === 'hidden'
}
