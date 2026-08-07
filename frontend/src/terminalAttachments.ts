export const MAX_ATTACHMENTS_PER_ACTION = 10

export type UploadedTerminalAttachment = {
  id: string
  name: string
  path: string
  relative_path: string
  reference: string
  kind: 'image' | 'file'
  media_type: string
  bytes: number
}

function quotedPath(path: string): string {
  return `"${path.replaceAll('"', '\\"')}"`
}

export function attachmentReferenceText(paths: string[]): string {
  if (paths.length === 0) return ''
  if (paths.length === 1) return `Attached file: ${quotedPath(paths[0])}`
  return `Attached files:\n${paths.map(path => `- ${quotedPath(path)}`).join('\n')}`
}

export function attachmentSafeBroadcast(configured: boolean, attachmentPasteDepth: number): boolean {
  return configured && attachmentPasteDepth === 0
}

export function canInsertTerminalAttachment(state: string, replayReady: boolean): boolean {
  return replayReady && !['starting', 'exited', 'crashed'].includes(state)
}

export function attachmentNeedsManualBracketing(
  nativeImage: boolean,
  agentBackend: boolean,
  bracketedPasteMode: boolean,
): boolean {
  return nativeImage && agentBackend && !bracketedPasteMode
}
