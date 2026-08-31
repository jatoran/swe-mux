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

/** Whether a native image reference must carry the paste wrapper by hand.
 *
 * Same rule, and the same reasoning, as `pasteNeedsManualBracketing`: xterm's mirror of
 * the child's bracketed-paste mode is a guess that goes stale in both directions, while
 * an agent CLI holds the mode on for its whole life. The wrapper is what makes the CLI
 * read the reference as a pasted image rather than as typed text, so it is sent on the
 * harness trait alone. */
export function attachmentNeedsManualBracketing(
  nativeImage: boolean,
  agentBackend: boolean,
): boolean {
  return nativeImage && agentBackend
}
