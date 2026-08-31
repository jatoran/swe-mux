import type { ComponentChildren, JSX } from 'preact'

export type DrawerViewTab = {
  id: string
  label: ComponentChildren
  title?: string
}

type Props = {
  active: string
  ariaLabel: string
  items: readonly DrawerViewTab[]
  onSelect: (id: string) => void
  className?: string
}

/** One visual and keyboard contract for every view rail inside the utility drawer. */
export function DrawerViewTabs({ active, ariaLabel, items, onSelect, className }: Props) {
  const focusSelected = (container: HTMLElement) => {
    queueMicrotask(() => container.querySelector<HTMLButtonElement>('[role="tab"][tabindex="0"]')?.focus())
  }
  return <div
    class={`drawer-view-tabs${className ? ` ${className}` : ''}`}
    role="tablist"
    aria-label={ariaLabel}
    style={{ '--drawer-view-count': String(items.length) } as JSX.CSSProperties}
  >
    {items.map((item, index) => <button
      key={item.id}
      type="button"
      role="tab"
      aria-selected={item.id === active}
      class={item.id === active ? 'active' : ''}
      title={item.title}
      tabIndex={item.id === active ? 0 : -1}
      onClick={() => onSelect(item.id)}
      onKeyDown={event => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        const offset = event.key === 'ArrowLeft' ? -1 : 1
        onSelect(items[(index + offset + items.length) % items.length].id)
        focusSelected(event.currentTarget.parentElement as HTMLElement)
      }}
    >{item.label}</button>)}
  </div>
}
