import { rich, t } from './localization/index.ts'
import { useEffect, useState } from 'preact/hooks'
import { subscribeContinuityFailure } from './continuityStatus'

/** App-level banner shown when the note editor's WebAssembly engine fails to load. */
export function ContinuityBanner() {
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => subscribeContinuityFailure(setMessage), [])
  if (!message) return null
  return (
    <div class="broadcast-banner continuity-banner" role="alert">
      <strong>{t("ContinuityBanner.ddb0f58437")}</strong>
      <span>{rich("ContinuityBanner.2e77672fbe", { message: message })}</span>
    </div>
  )
}
