import { t } from './localization/index.ts'
import type { JSX } from 'preact'

type Props = {
  projectName?: string
  mobile: boolean
  expanded: boolean
  order: number
  onOpen: (element: HTMLButtonElement) => void
}

/** Desktop pane-local entry to the Project Run menu. */
export function PaneRunTrigger({ projectName, mobile, expanded, order, onOpen }: Props) {
  if (mobile || !projectName) return null
  return <button
    type="button"
    class="pane-run-trigger"
    data-tutorial="pane-run"
    aria-label={t("PaneRunTrigger.23af12f561", { projectName: projectName })}
    aria-haspopup="menu"
    aria-expanded={expanded}
    title={t("PaneRunTrigger.23af12f561", { projectName: projectName })}
    style={{ order } as JSX.CSSProperties}
    onClick={event => onOpen(event.currentTarget)}
  >+</button>
}
