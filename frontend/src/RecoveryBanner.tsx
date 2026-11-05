import { t } from './localization/index.ts'
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
      <span>{t("RecoveryBanner.6c5cb8259e")}</span>
      <button type="button" onClick={() => rendererRecovery.resumeAll(previewIds)}>{t("RecoveryBanner.726ef317f6")}</button>
      <button type="button" aria-label={t("RecoveryBanner.48845bff33")} onClick={() => rendererRecovery.dismiss()}>×</button>
    </div>
  )
}
