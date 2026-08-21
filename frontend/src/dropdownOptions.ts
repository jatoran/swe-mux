/**
 * The pure half of the app's dropdown: what an option is, and how a keyboard moves over a list
 * of them.
 *
 * It lives apart from the component for the usual reason — the interesting rules here are
 * arithmetic over an array and are worth testing without a DOM — and for one specific to this
 * control. A native `<select>` shipped these behaviours for free (skip a disabled entry, wrap
 * at the ends, jump on a typed prefix, treat a repeated letter as "next match" rather than a
 * two-letter prefix), and a custom listbox that gets any of them wrong is a downgrade nobody
 * asked for. Writing them down as functions is what makes "the replacement still does what the
 * native control did" a checkable claim.
 */

export type DropdownOption = {
  value: string
  /** What the row and the collapsed trigger read as. */
  label: string
  /** A second, dimmer run on the row: an id, a price, a path. Never shown collapsed. */
  detail?: string
  /** Native `title` for a row whose label ellipsizes. */
  title?: string
  disabled?: boolean
}

/** How long a type-ahead buffer survives between keystrokes, matching the platform's own. */
export const DROPDOWN_TYPEAHEAD_MS = 800

/**
 * How far a pointer may travel between press and release and still count as a press on a row.
 *
 * The rail uses the same number for the same reason (`RAIL_PAN_SLOP_PX`), kept separate
 * because the two controls are unrelated and retuning one must not silently retune the other.
 */
export const DROPDOWN_PRESS_SLOP_PX = 6

/** Index of `value`, or `-1` when the current value is not in the list. */
export const dropdownIndexOf = (options: readonly DropdownOption[], value: string): number =>
  options.findIndex(option => option.value === value)

/**
 * The next selectable index `step` away from `from`, wrapping, skipping disabled rows.
 *
 * `from` of `-1` means "nothing is active yet", so a first ArrowDown lands on the first row
 * rather than the second. The walk is bounded by the list length, so a list that is entirely
 * disabled returns `-1` instead of spinning.
 */
export function nextDropdownIndex(options: readonly DropdownOption[], from: number, step: number): number {
  if (!options.length) return -1
  const start = from < 0 ? (step > 0 ? -1 : 0) : from
  for (let hop = 1; hop <= options.length; hop += 1) {
    const index = (((start + hop * step) % options.length) + options.length) % options.length
    if (!options[index].disabled) return index
  }
  return -1
}

/** First selectable index, or `-1`. */
export const firstDropdownIndex = (options: readonly DropdownOption[]): number =>
  options.findIndex(option => !option.disabled)

/** Last selectable index, or `-1`. */
export function lastDropdownIndex(options: readonly DropdownOption[]): number {
  for (let index = options.length - 1; index >= 0; index -= 1) if (!options[index].disabled) return index
  return -1
}

/**
 * Where a typed buffer lands, searched from just after `from` so the list cycles rather than
 * sticking on the first match.
 *
 * A prefix match is preferred over a substring one, and the whole list is prefix-scanned before
 * any substring is considered — otherwise typing `s` in a list holding "Session" and "Disabled"
 * could stop on "Disabled", which is not what any select does. Returns `-1` when nothing matches,
 * which the caller reads as "leave the highlight alone" rather than "select nothing".
 */
export function typeAheadIndex(options: readonly DropdownOption[], buffer: string, from: number): number {
  const needle = buffer.trim().toLocaleLowerCase()
  if (!needle) return -1
  const rotated: number[] = []
  for (let hop = 1; hop <= options.length; hop += 1) rotated.push((from + hop) % options.length)
  // `from` itself is checked last, so a buffer that still matches the active row keeps it when
  // it is the only match, and moves off it the moment a second row matches too.
  const scan = (test: (label: string) => boolean): number => {
    for (const index of rotated) {
      const option = options[index]
      if (!option.disabled && test(option.label.toLocaleLowerCase())) return index
    }
    return -1
  }
  const prefix = scan(label => label.startsWith(needle))
  return prefix >= 0 ? prefix : scan(label => label.includes(needle))
}

/**
 * The buffer a keystroke produces: it extends while the typing is continuous, and a gap longer
 * than {@link DROPDOWN_TYPEAHEAD_MS} starts a fresh one.
 */
export const nextTypeAhead = (buffer: string, key: string, sinceLastMs: number): string =>
  sinceLastMs > DROPDOWN_TYPEAHEAD_MS ? key : buffer + key

/**
 * Whether a key is type-ahead rather than a command.
 *
 * One printable character, no modifier that would make it a chord. Space is excluded because a
 * listbox uses it to choose, exactly as a native select does.
 */
export const isTypeAheadKey = (key: string, modified: boolean): boolean =>
  !modified && key.length === 1 && key !== ' ' && !!key.trim()

/**
 * The buffer to actually search on.
 *
 * One letter pressed repeatedly is "the next row starting with that letter", not a two-letter
 * prefix — the platform rule, and the reason a list of eight entries called `Session …` is
 * still walkable by pressing `s` eight times. A native select makes the same trade, and the
 * cost is the same one: a genuine `ss` prefix is unreachable by typing. Collapsing here rather
 * than in {@link nextTypeAhead} keeps the buffer the user actually typed available for the
 * highlight, which is the only other thing that reads it.
 */
export const searchBuffer = (buffer: string): string =>
  buffer.length > 1 && buffer.split('').every(character => character === buffer[0]) ? buffer[0] : buffer

/**
 * Where the list should be scrolled so `index` is visible, given the list's own metrics.
 *
 * Returned as an absolute `scrollTop` rather than applied with `scrollIntoView`, for two
 * reasons that both bit the model picker. `scrollIntoView` scrolls *every* scrollable ancestor,
 * so opening a dropdown low in the Settings panel scrolled the panel itself and moved the
 * trigger out from under its own list. And on open the wanted position is not "nearest" but
 * "roughly centred", so the rows either side of the current value are visible and the list
 * reads as a place in a catalogue rather than as a list that happens to start here.
 */
export function dropdownScrollTop(
  { itemTop, itemHeight, viewHeight, scrollHeight, scrollTop }: {
    itemTop: number
    itemHeight: number
    viewHeight: number
    scrollHeight: number
    scrollTop: number
  },
  mode: 'centre' | 'nearest' = 'nearest',
): number {
  const maximum = Math.max(0, scrollHeight - viewHeight)
  if (mode === 'centre') {
    return Math.max(0, Math.min(maximum, Math.round(itemTop - viewHeight / 2 + itemHeight / 2)))
  }
  if (itemTop < scrollTop) return Math.max(0, Math.min(maximum, itemTop))
  const itemBottom = itemTop + itemHeight
  if (itemBottom > scrollTop + viewHeight) return Math.max(0, Math.min(maximum, itemBottom - viewHeight))
  return Math.max(0, Math.min(maximum, scrollTop))
}
