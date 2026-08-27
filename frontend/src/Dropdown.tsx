import { createPortal } from 'preact/compat'
import { useCallback, useLayoutEffect, useRef, useState } from 'preact/hooks'
import {
  DROPDOWN_PRESS_SLOP_PX, dropdownIndexOf, dropdownScrollTop, filterDropdownOptions,
  firstDropdownIndex, isTypeAheadKey, lastDropdownIndex, nextDropdownIndex, nextTypeAhead,
  searchBuffer, typeAheadIndex, type DropdownOption,
} from './dropdownOptions'
import { dropdownStyle, watchDropdownPlacement, type DropdownPlacementOptions } from './dropdownPlacement'
import { useDismissLevel } from './modalFocus'

/**
 * The app's one dropdown, and the only picker any surface should reach for.
 *
 * It replaces `<select>` everywhere, on desktop *and* on a phone, which is the part worth
 * defending because the phone is where a native control is usually the right answer. Here it
 * is not: the platform draws a `<select>` as a full-screen wheel or a floating sheet that
 * borrows none of the app's palette, covers the surface the choice is being made against, and
 * on Android reorders nothing but still cannot be scanned. Every list in swe-mux is short and
 * label-driven, so a listbox drawn in the app's own type reads better on both, and having one
 * implementation is what makes "the list opens where you left it" true everywhere at once
 * rather than three times over.
 *
 * Three behaviours here are the reason it exists rather than being a styling exercise, all
 * three reported against the account-settings model picker:
 *
 *  - **A scroll gesture scrolls.** Choosing happens on `click`, never on `pointerdown`. A touch
 *    that begins on a row and travels is a pan, and the browser withholds the click for exactly
 *    that case; a picker that commits on `pointerdown` instead selects whatever the finger
 *    happened to land on, which is how the model list changed the model whenever it was
 *    scrolled. A movement guard backs the browser up, because a slow drag that ends where it
 *    started still produces a click.
 *  - **It opens where the value is.** The list scrolls the current value to the middle on open,
 *    so a two-hundred-entry catalogue opens at the entry in force rather than at the top.
 *  - **Order is the data's business.** This renders `options` as given. Sorting belongs where
 *    the list is built, so one control cannot impose an order on a list that has a meaningful
 *    one (severity, recency, first-parent).
 *
 * `filter` adds a box at the top of the open list, for the lists a person searches by name
 * rather than scans - every Project picker, and anything else that grows without bound. It is
 * the same control, not a second one: the filter narrows `options` into the rows on screen and
 * everything else (the opening scroll, the arrow walk, the press-slop guard that keeps a pan
 * from choosing) works over those rows unchanged. Type-ahead stands down while it is on,
 * because two mechanisms competing for one highlight is worse than either alone.
 *
 * Form semantics are kept rather than approximated. The trigger is a `<button>`, which is a
 * labelable element, so both `<label for=…>` and a wrapping `<label>` associate exactly as they
 * did with the `<select>` they replaced; `disabled` is the real attribute; and the list is a
 * `role="listbox"` of `role="option"` rows with `aria-activedescendant`, so a screen reader
 * reads a list rather than a pile of buttons.
 */

export type DropdownProps = {
  value: string
  options: readonly DropdownOption[]
  onChange: (value: string) => void
  /** Placed on the trigger, so `<label for=…>` finds it. */
  id?: string
  /** Only when there is no `<label>`; a wrapping or `for=`-associated label is preferred. */
  ariaLabel?: string
  disabled?: boolean
  /** Collapsed text when `value` matches no option. */
  placeholder?: string
  /** Extra classes on the trigger, for a surface with its own width or density rule. */
  class?: string
  title?: string
  /** Marks the trigger for deep links (`settingReveal.ts`). */
  'data-setting'?: string
  /** Widest the list may grow. */
  maxWidth?: DropdownPlacementOptions['maxWidth']
  /** Announced beside the list for a screen reader, when no label is associated. */
  listLabel?: string
  /**
   * Put a filter box at the top of the open list.
   *
   * Opt-in rather than automatic on a row count: a list long enough to want narrowing is not
   * the same list as one long enough to *need* it, and a filter box that appears on its own
   * once a Project is added would move the first row under the finger between two visits.
   * Turn it on for lists a person searches by name - Projects everywhere, and anything else
   * that grows without bound.
   */
  filter?: boolean
  /** Placeholder for the filter box. Defaults to a generic one. */
  filterPlaceholder?: string
}

let nextDropdownId = 0

export function Dropdown({
  value, options, onChange, id, ariaLabel, disabled, placeholder, class: className, title,
  'data-setting': dataSetting, maxWidth, listLabel, filter, filterPlaceholder,
}: DropdownProps) {
  const trigger = useRef<HTMLButtonElement>(null)
  // The scrolling, positioned panel. Without a filter it *is* the listbox, which is the DOM
  // every other surface in the app already has; with one it is a shell around the filter box
  // and the listbox, because an `<input>` is not a legal child of `role="listbox"`.
  const list = useRef<HTMLDivElement>(null)
  // Whichever node actually holds the option rows, for lookups by row index.
  const rowsHost = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const press = useRef<{ x: number; y: number } | null>(null)
  const typed = useRef({ buffer: '', at: 0 })
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  // A control disabled while its list is up is closed, not open-with-nothing-drawn: everything
  // below reads this rather than `open`, so the panel, the listeners, and what the trigger
  // announces cannot disagree.
  const expanded = open && !disabled
  // The rows actually on screen. Identical to `options` unless a filter is typed, and every
  // index below - the selection, the highlight, the arrow walk, the opening scroll - is an
  // index into *this*, so filtering needs no second coordinate system to map back through.
  // `options` is still what the trigger reads and what reserves its width, because the
  // collapsed control must not change size as the open list is narrowed.
  const rows = filter ? filterDropdownOptions(options, query) : options
  const selectedIndex = dropdownIndexOf(rows, value)
  const [activeIndex, setActiveIndex] = useState(selectedIndex)
  // Stable for the component's life: it names the list and its rows for `aria-controls` and
  // `aria-activedescendant`, both of which have to keep pointing at the same nodes across
  // re-renders. Derived from `id` when the caller gave one, so the ids read as a set.
  const generated = useRef('')
  if (!generated.current) generated.current = `dropdown-${(nextDropdownId += 1)}`
  const listId = `${id || generated.current}-list`
  // Off `options`, not `rows`: the collapsed trigger states the value in force, which a filter
  // that happens to exclude it must not blank.
  const selected = options.find(option => option.value === value) || null

  // Written straight onto the node rather than through state, and the two reasons are worth
  // keeping. A state round-trip means the panel's *first* layout has no `max-height`, so it
  // is briefly as tall as its content and therefore not scrollable — and the opening scroll
  // below, measuring that layout, computed a maximum of zero and left a two-hundred-entry
  // catalogue at the top. It also saves a render per placement, and placement re-runs on
  // every scroll and viewport change while the list is open.
  const place = useCallback(() => {
    const panel = list.current
    if (!panel || !trigger.current) return
    // Both are cleared first: a flip between above and below leaves the other inset behind,
    // which would pin the panel to two edges and stretch it.
    panel.style.removeProperty('top')
    panel.style.removeProperty('bottom')
    for (const [property, value] of Object.entries(dropdownStyle(trigger.current, { maxWidth }))) {
      panel.style.setProperty(property, value)
    }
  }, [maxWidth])

  // One layout effect, in this order and before paint: place the panel, then scroll it to the
  // value in force, then take focus. The list is never drawn at the top-left corner on its way
  // to the trigger, and the scroll measures the box the reader will actually see.
  //
  // Opening at the current value is what makes a long catalogue read as a position in a list
  // rather than as a list that happens to start here.
  useLayoutEffect(() => {
    if (!expanded) return
    place()
    const container = list.current
    // By index rather than by selector: the rows are the list's own children in order, and
    // `id` is caller-supplied, so a selector would need escaping to survive a setting name.
    const row = selectedIndex >= 0
      ? rowsHost.current?.children[selectedIndex] as HTMLElement | undefined
      : undefined
    if (container && row) {
      container.scrollTop = dropdownScrollTop({
        itemTop: row.offsetTop,
        itemHeight: row.offsetHeight,
        viewHeight: container.clientHeight,
        scrollHeight: container.scrollHeight,
        scrollTop: container.scrollTop,
      }, 'centre')
    }
    // A filtering list puts the caret in its own box: the point of the box is that typing
    // narrows the list, and focus on the listbox would send the first keystroke to type-ahead
    // instead. Everything the listbox answers to - arrows, Enter, Escape - is forwarded from
    // the input, so the keyboard route is unchanged either way.
    if (filter) search.current?.focus()
    else container?.focus()
    return watchDropdownPlacement(place)
    // Deliberately keyed on opening alone: this is the opening scroll, and re-running it when
    // the highlight moves would fight the arrow-key effect below for the same scrollTop.
  }, [expanded, place])

  // Keeping the highlight on screen as the arrows move it, without `scrollIntoView`: that
  // scrolls every scrollable ancestor, and the panel behind the list is one of them, so it
  // would drag the trigger out from under its own list. Before paint, so the row is already
  // where it belongs rather than arriving there a frame later.
  useLayoutEffect(() => {
    const container = list.current
    if (!expanded || !container || activeIndex < 0) return
    const row = rowsHost.current?.children[activeIndex] as HTMLElement | undefined
    if (!row) return
    container.scrollTop = dropdownScrollTop({
      itemTop: row.offsetTop,
      itemHeight: row.offsetHeight,
      viewHeight: container.clientHeight,
      scrollHeight: container.scrollHeight,
      scrollTop: container.scrollTop,
    })
  }, [expanded, activeIndex])

  // Closing on a press outside, but never on a press inside the `<label>` this control belongs
  // to: a wrapping label forwards its own click to the button, so closing here and reopening
  // there made a click on the label text flicker the list instead of toggling it.
  //
  // A *layout* effect, so the listener exists the moment the list does. A deferred effect
  // leaves a window — one frame, but a real one — in which the list is on screen and a press
  // outside it does nothing, which is a control that will not close if you are quick.
  useLayoutEffect(() => {
    if (!expanded) return
    const label = trigger.current?.closest('label')
    const closeOutside = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (list.current?.contains(target) || trigger.current?.contains(target)) return
      if (label && target && label.contains(target)) return
      setOpen(false)
    }
    window.addEventListener('pointerdown', closeOutside, true)
    return () => window.removeEventListener('pointerdown', closeOutside, true)
  }, [expanded])

  const openList = () => {
    if (disabled) return
    typed.current = { buffer: '', at: 0 }
    // Every open starts with the whole list: a query left over from last time would hide the
    // value in force, and "it opens where the value is" is the behaviour this control exists
    // for. `rows` is therefore `options` at this moment, so the index is right for both.
    setQuery('')
    const from = dropdownIndexOf(options, value)
    setActiveIndex(from >= 0 ? from : firstDropdownIndex(options))
    setOpen(true)
  }
  const close = (refocus = true) => {
    setOpen(false)
    setQuery('')
    if (refocus) trigger.current?.focus()
  }
  const choose = (index: number) => {
    const option = rows[index]
    if (!option || option.disabled) return
    if (option.value !== value) onChange(option.value)
    close()
  }
  const move = (step: number) => setActiveIndex(current => {
    const next = nextDropdownIndex(rows, current, step)
    return next < 0 ? current : next
  })
  // Narrowing the list moves every row, so the highlight goes to the best remaining match
  // rather than staying on whichever row now happens to sit at the old index.
  const retype = (next: string) => {
    setQuery(next)
    setActiveIndex(firstDropdownIndex(filterDropdownOptions(options, next)))
  }

  // The platform back gesture and the mobile back swipe, which reach every open level through
  // the stack rather than through a key. Escape is handled in `onKeyDown` instead: the list has
  // focus while it is open, so it can close itself and stop the key rather than depending on a
  // window-level handler that only exists inside the app shell.
  useDismissLevel(() => close(), expanded, 'dropdown')

  const onKeyDown = (event: KeyboardEvent) => {
    const modified = event.altKey || event.ctrlKey || event.metaKey
    if (!expanded) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp' || event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        openList()
      }
      return
    }
    if (event.key === 'ArrowDown') { event.preventDefault(); move(1); return }
    if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); return }
    if (event.key === 'Home') { event.preventDefault(); setActiveIndex(firstDropdownIndex(rows)); return }
    if (event.key === 'End') { event.preventDefault(); setActiveIndex(lastDropdownIndex(rows)); return }
    if (event.key === 'Enter') { event.preventDefault(); choose(activeIndex); return }
    // Space chooses from the listbox, exactly as a native select does - but inside the filter
    // box it is a character in a Project name, so it must reach the input untouched.
    if (event.key === ' ') { if (filter) return; event.preventDefault(); choose(activeIndex); return }
    // Escape and Tab both leave without choosing. Escape is stopped as well as prevented, so a
    // dropdown open inside a modal closes only itself rather than also reaching the window
    // handler that pops the next level; Tab has to close here and then be allowed to move
    // focus, so it is deliberately neither.
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); return }
    if (event.key === 'Tab') { setOpen(false); return }
    // A filtering list has a better answer than type-ahead for every printable key: the key
    // goes into the box and narrows the list, which is both more visible and not limited to
    // a prefix. Running both would fight over the highlight.
    if (filter || !isTypeAheadKey(event.key, modified)) return
    event.preventDefault()
    const now = Date.now()
    const buffer = nextTypeAhead(typed.current.buffer, event.key, now - typed.current.at)
    typed.current = { buffer, at: now }
    const found = typeAheadIndex(rows, searchBuffer(buffer), activeIndex)
    if (found >= 0) setActiveIndex(found)
  }

  const label = selected ? selected.label : placeholder || ''
  // The label the trigger reserves room for, so it keeps a `<select>`'s constant width rather
  // than resizing to whatever is chosen — a toolbar full of these would otherwise reflow on
  // every change. The longest label by character count rather than by measured width: an exact
  // answer means rendering every option, which a two-hundred-entry list cannot afford, and the
  // app's surfaces are monospaced or near enough that counting is right or one glyph short.
  const widest = options.reduce(
    (longest, option) => (option.label.length > longest.length ? option.label : longest),
    placeholder || '',
  )

  const optionRows = <>
    {rows.map((option, index) => <div
        id={`${listId}-${index}`}
        // Keyed by position as well as value, because two rows legitimately share a value:
        // a list whose "none" row and whose sole real row are both the empty string is a
        // shape several surfaces render, and duplicate keys reconcile into one another.
        key={`${index}:${option.value}`}
        class={`dropdown-option${index === activeIndex ? ' active' : ''}${option.disabled ? ' disabled' : ''}`}
        role="option"
        // By index rather than by value, for the same reason: exactly one row is the
        // selection, and it is the first match, which is what a `<select>` resolves to.
        aria-selected={index === selectedIndex}
        aria-disabled={option.disabled || undefined}
        data-value={option.value}
        title={option.title}
        onPointerDown={event => { press.current = { x: event.clientX, y: event.clientY } }}
        // Mouse only: a finger panning the list would otherwise drag the highlight along with
        // it, which reads as the list picking something while it is being scrolled — the very
        // impression this control exists to remove.
        onPointerMove={event => { if (event.pointerType === 'mouse' && !option.disabled) setActiveIndex(index) }}
        onClick={event => {
          // The browser withholds a click when a touch pans, so this guard is belt-and-braces
          // for the slow drag that ends within a few pixels of where it began — and for a mouse
          // drag out of the list and back, which does still click.
          const from = press.current
          press.current = null
          if (from && Math.hypot(event.clientX - from.x, event.clientY - from.y) > DROPDOWN_PRESS_SLOP_PX) return
          choose(index)
        }}
      >
        <span class="dropdown-option-label">{option.label}</span>
        {option.detail && <span class="dropdown-option-detail">{option.detail}</span>}
        <span class="dropdown-option-check" aria-hidden="true">{index === selectedIndex ? '✓' : ''}</span>
    </div>)}
    {!rows.length && <div class="dropdown-empty">{query.trim() ? 'No matches' : 'No options'}</div>}
  </>

  // Two shapes, because an `<input>` is not a legal child of `role="listbox"`. Without a
  // filter the panel *is* the listbox, which is the exact DOM every existing surface and
  // spec already has. With one, the panel is a shell: a filter box behaving as the ARIA
  // 1.2 combobox that owns the highlight, over a listbox of the rows. The panel stays the
  // positioned, scrolling element either way, so placement and the scroll maths do not
  // learn about the difference.
  const panel = expanded ? createPortal(
    filter
      // No `onKeyDown` on the shell: the input carries it and the event bubbles, so a
      // handler here too would run every arrow twice.
      ? <div ref={list} class="dropdown-list dropdown-list-filtered">
        <div class="dropdown-filter">
          <input
            ref={search}
            type="text"
            role="combobox"
            value={query}
            placeholder={filterPlaceholder || 'Type to filter…'}
            aria-label={`Filter ${listLabel || ariaLabel || 'options'}`}
            aria-autocomplete="list"
            aria-expanded
            aria-controls={listId}
            aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
            autocomplete="off"
            spellcheck={false}
            onInput={event => retype(event.currentTarget.value)}
            onKeyDown={onKeyDown}
          />
        </div>
        <div
          ref={rowsHost}
          id={listId}
          class="dropdown-rows"
          role="listbox"
          aria-label={listLabel || ariaLabel || undefined}
        >{optionRows}</div>
      </div>
      : <div
        ref={element => { list.current = element; rowsHost.current = element }}
        id={listId}
        class="dropdown-list"
        role="listbox"
        tabIndex={-1}
        aria-label={listLabel || ariaLabel || undefined}
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        onKeyDown={onKeyDown}
      >{optionRows}</div>,
    document.body,
  ) : null

  return <>
    <button
      ref={trigger}
      id={id}
      type="button"
      class={`dropdown-trigger${expanded ? ' open' : ''}${className ? ` ${className}` : ''}`}
      disabled={disabled}
      title={title}
      data-setting={dataSetting}
      data-value={value}
      aria-label={ariaLabel}
      aria-haspopup="listbox"
      aria-expanded={expanded}
      aria-controls={expanded ? listId : undefined}
      onClick={() => (expanded ? close(false) : openList())}
      onKeyDown={onKeyDown}
    >
      <span class="dropdown-value">{label}</span>
      <span class="dropdown-sizer" aria-hidden="true">{widest}</span>
      <span class="dropdown-chevron" aria-hidden="true">{expanded ? '▴' : '▾'}</span>
    </button>
    {panel}
  </>
}
