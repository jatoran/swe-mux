import { api, type ApiOptions } from './api.ts'
import type { HarnessRegistryPayload } from './harnessRegistry.ts'
import type { Preview } from './processFleet.ts'
import type { Project, ProjectGroup, Session } from './types.ts'

/**
 * The fleet snapshot: the five daemon GETs the composition root re-reads on an
 * interval, on visibility, on every events-socket reconnect, and after every
 * mutation that can change what the fleet holds.
 *
 * Every slice is optional because a cycle applies what it got. The five are
 * independent reads of independent registries, so one failing endpoint must not
 * discard the other four - that is what a fail-fast `Promise.all` used to do,
 * turning a single transient 500 into a whole missed cycle for the sidebar, the
 * Project list, the previews and the harness registry alike.
 */
export type FleetSlices = {
  sessions?: Session[]
  projects?: Project[]
  previews?: { items: Preview[] }
  groups?: ProjectGroup[]
  harnesses?: HarnessRegistryPayload
}

export type FleetSliceName = keyof FleetSlices

export type FleetSliceFailure = { slice: FleetSliceName; cause: unknown }

export type FleetFetchOutcome = { slices: FleetSlices; failures: FleetSliceFailure[] }

/**
 * The deadline every fleet GET carries, per `api.ts`'s own rule: a request a view
 * cannot render without always sets one, because a fetch issued while a dormant
 * PWA is waking can hang forever instead of failing. These five had none, and the
 * in-flight dedupe below then pinned every future refresh behind the hung promise
 * until the page was reloaded.
 *
 * Generous rather than tight: a cold daemon enumerating a large fleet is slow, not
 * broken, and a deadline that fires on a slow-but-working daemon would trade a rare
 * freeze for a routine empty cycle.
 */
export const FLEET_REFRESH_TIMEOUT_MS = 20_000

/**
 * How long one whole cycle may run before the controller stops waiting for it.
 *
 * The per-request deadline above bounds the fetches; this bounds everything else -
 * an application step that never returns, a browser that suspended the tab mid-cycle
 * and lost the continuation. Comfortably above the request deadline so a cycle whose
 * requests are merely timing out finishes on its own rather than being abandoned.
 */
export const FLEET_REFRESH_STALL_MS = 45_000

type RequestFn = <T>(method: string, path: string, body?: unknown, options?: ApiOptions) => Promise<T>

const SLICE_PATHS: Record<FleetSliceName, string> = {
  sessions: '/api/sessions',
  projects: '/api/projects',
  previews: '/api/previews',
  groups: '/api/project-groups',
  harnesses: '/api/harnesses',
}

const SLICE_NAMES = Object.keys(SLICE_PATHS) as FleetSliceName[]

/**
 * Read all five slices concurrently, each under its own deadline, and report both
 * what arrived and what did not.
 *
 * Never rejects: a caller applies the slices it was handed and decides for itself
 * what a failure list means, which is the whole point of the split.
 */
export async function fetchFleetSlices(
  options: ApiOptions = {},
  request: RequestFn = api,
): Promise<FleetFetchOutcome> {
  const requestOptions: ApiOptions = { ...options, timeoutMs: options.timeoutMs ?? FLEET_REFRESH_TIMEOUT_MS }
  const settled = await Promise.allSettled(
    SLICE_NAMES.map(name => request<unknown>('GET', SLICE_PATHS[name], undefined, requestOptions)),
  )
  const slices: Record<string, unknown> = {}
  const failures: FleetSliceFailure[] = []
  settled.forEach((result, index) => {
    const name = SLICE_NAMES[index]
    if (result.status === 'fulfilled') slices[name] = result.value
    else failures.push({ slice: name, cause: result.reason })
  })
  return { slices: slices as FleetSlices, failures }
}

/** The first failure's message, for the one-line error the composition root shows. */
export function describeFleetFailures(failures: readonly FleetSliceFailure[]): string {
  const first = failures[0]
  if (!first) return ''
  const message = first.cause instanceof Error ? first.cause.message : String(first.cause)
  return failures.length > 1 ? `${message} (${failures.length} of ${SLICE_NAMES.length} fleet reads failed)` : message
}

export type FleetRefreshController = {
  /**
   * Run a refresh cycle, or join the next one.
   *
   * The returned promise always resolves - never rejects - once a cycle that
   * *began at or after this call* has concluded. A caller arriving while a cycle
   * is already in flight is deliberately not handed that cycle's promise: its
   * snapshot was taken before whatever the caller just changed, so awaiting it
   * returns pre-mutation data and the UI paints the state the operator just left.
   */
  refresh(): Promise<void>
  /** Abandon anything in flight and drop the dedupe. For teardown. */
  reset(): void
  /** Whether a cycle is running. Diagnostics and tests. */
  pending(): boolean
}

type Deferred = { promise: Promise<void>; resolve: () => void }

function makeDeferred(): Deferred {
  let resolve: () => void = () => {}
  const promise = new Promise<void>(settle => { resolve = settle })
  return { promise, resolve }
}

export type FleetRefreshOptions = {
  stallMs?: number
  /** Called when a cycle outran `stallMs` and was abandoned. */
  onStall?: (stallMs: number) => void
}

/**
 * The in-flight dedupe for fleet refreshes, with the two properties the old
 * inline version lacked.
 *
 * **Abandonable.** A cycle that outruns `stallMs` is aborted and forgotten, so a
 * request that never settles costs one slow cycle rather than every refresh for
 * the life of the page. The old dedupe stored the hung promise and handed it to
 * the interval, the visibility handler and the socket reconnect alike; nothing
 * ever cleared it.
 *
 * **Post-mutation honest.** Callers that arrive mid-cycle queue a follow-up and
 * are given *its* promise, so `await refresh()` after a POST observes the POST.
 * Follow-ups coalesce: many callers during one cycle share one queued cycle.
 */
export function createFleetRefreshController(
  run: (signal: AbortSignal) => Promise<void>,
  options: FleetRefreshOptions = {},
): FleetRefreshController {
  const stallMs = options.stallMs ?? FLEET_REFRESH_STALL_MS
  let generation = 0
  let active: { generation: number; controller: AbortController; settled: Deferred; timer: ReturnType<typeof setTimeout> } | null = null
  let queued: Deferred | null = null

  const conclude = (id: number) => {
    if (!active || active.generation !== id) return
    clearTimeout(active.timer)
    const settled = active.settled
    active = null
    settled.resolve()
    if (queued) start()
  }

  function start(): Promise<void> {
    const settled = queued ?? makeDeferred()
    queued = null
    generation += 1
    const id = generation
    const controller = new AbortController()
    const timer = setTimeout(() => {
      // The cycle may still be running; aborting is what makes it stop applying,
      // and dropping it from `active` is what unpins every later refresh.
      controller.abort()
      options.onStall?.(stallMs)
      conclude(id)
    }, stallMs)
    active = { generation: id, controller, settled, timer }
    let started: Promise<void>
    try {
      started = run(controller.signal)
    } catch (cause) {
      started = Promise.reject(cause)
    }
    void started.catch(() => {}).then(() => conclude(id))
    return settled.promise
  }

  return {
    refresh: () => {
      if (!active) return start()
      queued = queued ?? makeDeferred()
      return queued.promise
    },
    reset: () => {
      if (active) {
        clearTimeout(active.timer)
        active.controller.abort()
        const settled = active.settled
        active = null
        settled.resolve()
      }
      const pending = queued
      queued = null
      pending?.resolve()
    },
    pending: () => active !== null,
  }
}
