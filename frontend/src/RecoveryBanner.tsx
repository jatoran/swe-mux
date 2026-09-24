import { useEffect, useState } from 'preact/hooks'
import { rendererRecovery } from './rendererRecovery'

/** Says why this page came up with Preview documents paused, and lets the operator
 *  load them all at once. Renders nothing on an ordinary page load. */
export function RecoveryBanner({ previewIds }: { previewIds: string[] }) {
  const [notice, setNotice] = useState(() => rendererRecovery.current())
  useEffect(() => rendererRecovery.subscribe(() => setNotice(rendererRecovery.current())), [])
  if (!notice) return null
  return (
    <div class="recovery-banner" role="status" aria-live="polite" data-testid="recovery-banner">
      <strong>{notice.message}</strong>
      <span>Previews stay paused until you load them, so the tab that caused this cannot do it again.</span>
      <button type="button" onClick={() => rendererRecovery.resumeAll(previewIds)}>Load previews</button>
      <button type="button" aria-label="Dismiss" onClick={() => rendererRecovery.dismiss()}>×</button>
    </div>
  )
}
