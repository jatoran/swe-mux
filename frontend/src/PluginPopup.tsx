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
  return <div class="modal-layer plugin-popup-layer" role="dialog" aria-modal="true" aria-label="Plugin popup">
    <div class="modal plugin-popup-modal">
      <header>
        <strong>{title}</strong>
        <div class="plugin-popup-actions">
          <button disabled={docking} onClick={onDock}>{docking?'Docking…':'Keep as Project tab'}</button>
          <button aria-label="Close plugin popup" disabled={docking} onClick={onClose}>×</button>
        </div>
      </header>
      <div class="plugin-popup-terminal">{children}</div>
    </div>
  </div>
}
