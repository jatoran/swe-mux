import type { DropdownOption } from './dropdownOptions'

/**
 * One order for Projects, and one way to offer them.
 *
 * Every surface outside the sidebar lists Projects to be *found*, not to be read in a
 * meaningful sequence: a filter dropdown in Automations, a scope picker in Settings, a
 * history filter. Registry order is meaningless to a reader and Project position is the
 * sidebar's own arrangement, so both leave the list unscannable the moment there are more
 * than a handful. Name order is the only one somebody can predict without looking.
 *
 * The sidebar is the deliberate exception and does not use any of this: its order is the
 * operator's own drag arrangement, which is the whole point of it.
 */

/**
 * Name order as a reader expects it: case- and accent-insensitive, and numeric-aware so
 * `phase-9` precedes `phase-10` instead of following it.
 */
export const compareProjectNames = (left: string, right: string): number =>
  left.localeCompare(right, undefined, { sensitivity: 'base', numeric: true })

/** A copy of `items` in name order. Never sorts in place - several callers pass a prop. */
export function byProjectName<T>(items: readonly T[], name: (item: T) => string): T[] {
  return [...items].sort((left, right) => compareProjectNames(name(left), name(right)))
}

/**
 * Project rows for {@link import('./Dropdown').Dropdown}, in name order.
 *
 * `detail` carries the root path when there is one, because two checkouts of the same repo
 * are two Projects with the same name and the path is the only thing that tells them apart -
 * and because the filter searches `detail`, so those two stay reachable by typing.
 */
export function projectDropdownOptions<T>(
  projects: readonly T[],
  read: (item: T) => { value: string; label: string; detail?: string },
): DropdownOption[] {
  return byProjectName(projects.map(read), option => option.label)
}
