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
  /**
   * Whether this rail currently owns the panel's selection.
   *
   * True everywhere except the Files tab with a file open, where a second rail below this one
   * holds the open documents and the body is showing one of them. Two rails each reporting
   * `aria-selected="true"` over one body is a lie to a screen reader and, with a highlight on
   * both, to everyone else - so the index rail stands down and says what it is: the way back,
   * with `active` still naming which index that would be.
   *
   * Keyboard reach is unaffected. The `active` item keeps `tabIndex=0`, because a rail nobody
   * can tab into is a worse answer than an ambiguous highlight.
   */
  selected?: boolean
}

/** One visual and keyboard contract for every view rail inside the utility drawer. */
export function DrawerViewTabs({ active, ariaLabel, items, onSelect, className, selected = true }: Props) {
  const focusSelected = (container: HTMLElement) => {
    queueMicrotask(() => container.querySelector<HTMLButtonElement>('[role="tab"][tabindex="0"]')?.focus())
  }
  return <div
    class={`drawer-view-tabs${className ? ` ${className}` : ''}${selected ? '' : ' standing-down'}`}
    role="tablist"
    aria-label={ariaLabel}
    style={{ '--drawer-view-count': String(items.length) } as JSX.CSSProperties}
  >
    {items.map((item, index) => <button
      key={item.id}
      type="button"
      role="tab"
      aria-selected={selected && item.id === active}
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
