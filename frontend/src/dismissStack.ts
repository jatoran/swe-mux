// The app's single answer to "what does back mean right now".
//
// Every dismissable level — a modal, a menu, a slide-in panel, a drill-down inside a
// modal — registers here while it is open. One `pop()` dismisses exactly one level,
// so Escape, the Android system back gesture, and the mobile swipe-back can all be
// wired to the same call instead of each carrying its own ladder of conditionals.
// Three components had already grown that ladder by hand (`AutomationDashboard`'s
// paired `useModalFocus` gates, `ProjectRunMenu`'s nested Escape ternary, and
// `App`'s eighteen-setter Escape handler), which is what motivated one store.
//
// Pure and DOM-free so the ordering rules can be unit-tested directly.

export type DismissEntry = {
  /** Close this one level. Called by `pop()`; may be guarded (see `pop`). */
  dismiss: () => void
  /** Refuse to be dismissed, and absorb the pop rather than letting it fall through. */
  blocking?: boolean
  /** Registered inactive; `setActive` flips it without changing stack position. */
  active?: boolean
  /** Diagnostics only — names the level in the trace ring. */
  label?: string
}

export type PopResult = 'popped' | 'blocked' | 'pending' | 'empty'

export type DismissTraceEvent = {
  at: number
  action: 'register' | 'unregister' | 'activate' | 'deactivate' | 'pop'
  label: string
  result?: PopResult
  depth: number
}

export const DISMISS_TRACE_LIMIT = 64

type Registered = {
  id: number
  seq: number
  active: boolean
  blocking: boolean
  label: string
  dismiss: () => void
}

export type DismissStack = ReturnType<typeof createDismissStack>

export function createDismissStack(now: () => number = () => Date.now()) {
  const entries = new Map<number, Registered>()
  const listeners = new Set<() => void>()
  const trace: DismissTraceEvent[] = []
  let nextId = 1
  let nextSeq = 1
  // Set by `pop()` and cleared by the next structural change. See `pop`.
  let pending: number | null = null

  const activeEntries = () => [...entries.values()].filter(entry => entry.active)
  const depth = () => activeEntries().length

  const record = (action: DismissTraceEvent['action'], label: string, result?: PopResult) => {
    trace.push({ at: now(), action, label, result, depth: depth() })
    if (trace.length > DISMISS_TRACE_LIMIT) trace.splice(0, trace.length - DISMISS_TRACE_LIMIT)
  }

  const notify = () => { for (const listener of [...listeners]) listener() }

  /**
   * Push a level. Returns the id used to unregister or gate it.
   *
   * Position is temporal: the most recently registered active level pops first. A
   * drill-down inside a modal therefore needs no knowledge of its own depth — it
   * registers after its parent and so sits above it.
   */
  const register = (entry: DismissEntry): number => {
    const id = nextId++
    entries.set(id, {
      id,
      seq: nextSeq++,
      active: entry.active !== false,
      blocking: entry.blocking === true,
      label: entry.label || 'unnamed',
      dismiss: entry.dismiss,
    })
    pending = null
    record('register', entry.label || 'unnamed')
    notify()
    return id
  }

  const unregister = (id: number): void => {
    const entry = entries.get(id)
    if (!entry) return
    entries.delete(id)
    pending = null
    record('unregister', entry.label)
    notify()
  }

  /**
   * Gate a level without moving it.
   *
   * Deliberately not "unregister then re-register": `AutomationDashboard` toggles its
   * parent gate off while a child is open, and a re-register would land the parent
   * back on top of the stack — above levels that opened after it.
   */
  const setActive = (id: number, active: boolean): void => {
    const entry = entries.get(id)
    if (!entry || entry.active === active) return
    entry.active = active
    pending = null
    record(active ? 'activate' : 'deactivate', entry.label)
    notify()
  }

  /**
   * Dismiss the top active level.
   *
   * The entry is left registered: its owner unregisters on unmount, and a *guarded*
   * close (Settings opening its Save/Discard decision instead of closing) legitimately
   * leaves the level up with a new level above it. To keep a double-fire from closing
   * two levels, the popped id is remembered and a second `pop()` is inert until the
   * stack changes shape — any register, unregister, or gate flip clears it. A dismiss
   * that changed nothing therefore leaves back inert, which matches what the user sees.
   */
  const pop = (): PopResult => {
    const stack = activeEntries().sort((left, right) => left.seq - right.seq)
    const top = stack[stack.length - 1]
    if (!top) { record('pop', 'none', 'empty'); return 'empty' }
    if (top.blocking) { record('pop', top.label, 'blocked'); return 'blocked' }
    if (pending === top.id) { record('pop', top.label, 'pending'); return 'pending' }
    pending = top.id
    record('pop', top.label, 'popped')
    top.dismiss()
    return 'popped'
  }

  const subscribe = (listener: () => void): (() => void) => {
    listeners.add(listener)
    return () => { listeners.delete(listener) }
  }

  return {
    register,
    unregister,
    setActive,
    pop,
    depth,
    subscribe,
    /** Top active level's label, or null. Diagnostics only. */
    topLabel: () => {
      const stack = activeEntries().sort((left, right) => left.seq - right.seq)
      return stack.length ? stack[stack.length - 1].label : null
    },
    /** Recent transitions, oldest first. Bounded by DISMISS_TRACE_LIMIT. */
    trace: (): DismissTraceEvent[] => [...trace],
  }
}

/** The app-wide stack. Tests build their own with `createDismissStack`. */
export const dismissStack = createDismissStack()
