import { t } from './localization/index.ts'
import type { ComponentChildren } from 'preact'

export function PluginPopup({
  title,
  docking,
  onDock,
  onClose,
  children,
}: {
  title: string
  docking: boolean
  onDock: () => void
  onClose: () => void
  children: ComponentChildren
}) {
  return <div class="modal-layer plugin-popup-layer" role="dialog" aria-modal="true" aria-label={t("PluginPopup.1a9e515fd1")}>
    <div class="modal plugin-popup-modal">
      <header>
        <strong>{title}</strong>
        <div class="plugin-popup-actions">
          <button disabled={docking} onClick={onDock}>{docking ? t("PluginPopup.77ac1e59b5") : t("PluginPopup.acc0edc6a9")}</button>
          <button aria-label={t("PluginPopup.909565482d")} disabled={docking} onClick={onClose}>×</button>
        </div>
      </header>
      <div class="plugin-popup-terminal">{children}</div>
    </div>
  </div>
}
