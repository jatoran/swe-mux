/**
 * Deterministic mode: one opt-in switch that makes the whole demo reproducible.
 *
 * The demo is a simulation, and a simulation that draws on the wall clock and on
 * `Math.random` cannot be replayed - which is fine while a visitor is poking at it and
 * fatal for the two things built on top of it: a scripted scenario whose beats must land
 * on the same fixture twice, and a capture rig whose whole value is that its output
 * cannot drift from the product. So `?deterministic=1` (or the `swemux-demo-deterministic`
 * localStorage key) replaces both sources at the root rather than at each call site.
 *
 * Two global overrides and one fixture-time rule, with a different reason for each:
 *
 * - **`Math.random` becomes a seeded PRNG.** Overriding the global rather than threading a
 *   `demoRandom()` through nine modules is deliberate: the demo's non-determinism is
 *   *diffuse* (a joke cursor, a frame id, a spawned pid, a token jitter, a leader-election
 *   tiebreak), and an audit that misses one place fails silently. Replacing the source
 *   catches the places nobody listed.
 * - **The clock is rebased onto a fixed epoch, and keeps running.** Freezing `Date.now()`
 *   outright was tried and is wrong: real code measures *elapsed* time against it, and a
 *   clock whose deltas are always zero disables every one of those. The view mirror would
 *   never leave its post-converge quiet window (`Date.now() < quietUntil` stays true
 *   forever) and the walkthrough's leader election would treat every rival claim as
 *   permanently fresh. Rebasing keeps deltas honest while making the *absolute* values - which is
 *   what every fixture offset and every "3 minutes ago" label is derived from - identical
 *   on every run.
 * - **Generated fixture timestamps come from identity, not elapsed time.** The rebased
 *   clock is still a running clock, so two twenty-five-second scenarios can finish on
 *   adjacent seconds under runner load. A timestamp persisted into the simulated daemon
 *   is therefore derived from the minted id's deterministic ordinal instead.
 *
 * What determinism does NOT buy is pixel-identical video: the scenario runner drives real
 * timers and a recorder samples real frames. What it buys is that every id, every fixture
 * number, every generated name and every rendered timestamp is the same twice, which is
 * the part a capture can be checked against.
 *
 * Persistence is also switched off here (`store.ts` reads `DETERMINISTIC`), because a
 * deterministic run that started from a visitor's saved fleet would not be deterministic,
 * and because a capture must never leave epoch-stamped state behind for the next visitor.
 */

/** The demo's fixed "now" under determinism: 2026-03-14 09:41:00 UTC. */
export const DEMO_EPOCH_MS = Date.UTC(2026, 2, 14, 9, 41, 0)

/**
 * A stable second for fixture data minted under a deterministic `demoId`.
 *
 * The running demo clock is for elapsed-time behavior. Persisted fixture data needs a
 * pure answer, or two equally correct runs whose timers settle one second apart produce
 * different stores. The id already carries the deterministic creation order, so it is
 * the timestamp's complete input rather than another mutable counter.
 */
export function fixtureSeconds(identity: string): number {
  const match = /-d(\d+)$/.exec(identity)
  if (!match) throw new Error(`deterministic fixture id has no ordinal: ${identity}`)
  return Math.floor(DEMO_EPOCH_MS / 1000) + Number(match[1])
}

const FLAG_KEY = 'swemux-demo-deterministic'
const DEFAULT_SEED = 0x5eed1234

function storedFlag(): string | null {
  try { return localStorage.getItem(FLAG_KEY) } catch { return null }
}

function readParams(): { on: boolean; seed: number } {
  let raw: string | null = null
  let seed = DEFAULT_SEED
  try {
    const params = new URLSearchParams(location.search)
    raw = params.get('deterministic')
    const requested = Number(params.get('seed'))
    if (Number.isFinite(requested) && requested !== 0) seed = requested >>> 0
  } catch {
    // A document with no parseable location (a test harness, a data: URL) simply
    // runs in the ordinary, non-deterministic mode.
  }
  if (raw === null) raw = storedFlag()
  return { on: raw === '1' || raw === 'true', seed }
}

const { on, seed } = readParams()

/** Whether this frame is running deterministically. Read by the store (persistence),
 *  the fleet fixtures (the process wobble) and the capture rig's assertions. */
export const DETERMINISTIC = on

/** The PRNG seed in force. Exposed so a capture can record what it captured under. */
export const DEMO_SEED = seed

/**
 * A small, fast, well-distributed PRNG. Mulberry32: 32 bits of state, one multiply
 * chain per draw, and no dependency - which matters because this replaces `Math.random`
 * for the whole page, including inside the app under test.
 */
export function mulberry32(state: number): () => number {
  let value = state >>> 0
  return () => {
    value = (value + 0x6d2b79f5) >>> 0
    let t = value
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * A monotonic counter, for ids that must not carry a timestamp.
 *
 * `store.demoId` used to mix `Date.now()` into every id it minted, which is a perfectly
 * good uniqueness source and a terrible reproducibility one: two runs a second apart
 * produced different session ids, so a scenario that named a spawned pane could not name
 * it twice. Under determinism ids are the counter alone.
 */
let counter = 0
export const nextOrdinal = (): number => (counter += 1)

/** Reset the id counter. Only the unit suite calls this; a page never does. */
export const resetOrdinals = (): void => { counter = 0 }

/**
 * The platform's real `Math.random`, captured before it is replaced.
 *
 * Deliberately still available, and there is exactly one caller: a frame's own identity
 * on the BroadcastChannel. Both frames use that id to ignore their own echoes, so under
 * a shared seed they would mint the *same* id and each would then discard everything the
 * other said - two frames that agree perfectly and mirror nothing. Frame identity is a
 * uniqueness requirement rather than a fixture, so it is the one draw that stays random
 * on purpose.
 */
export const trueRandom: () => number = Math.random.bind(Math)

/**
 * A PRNG stream only the demo's own fixtures draw from.
 *
 * Overriding `Math.random` is necessary but not sufficient, and the gap is subtle enough
 * that it took a determinism test to find: a seeded global is only reproducible if the
 * *sequence of draws* is, and the app draws from it too (`randomId.ts` mints ids on boot
 * and on interaction). Those draws are scheduled against real timers and real fetches, so
 * the demo's Nth draw is not the same value twice - which showed up as a spawned session
 * getting a different pid on the second run of the same scenario, and nothing else.
 *
 * So the app keeps the seeded global (its ids are then stable-ish and, more to the point,
 * never leak a real entropy source into a capture), and anything that becomes *fixture
 * data* draws here instead, where the only caller is the demo and the order is the
 * scenario's own.
 */
const demoStream = mulberry32((seed ^ 0x9e3779b9) >>> 0)
export const demoRandom = (): number => (DETERMINISTIC ? demoStream() : Math.random())

if (DETERMINISTIC) {
  Math.random = mulberry32(seed)

  // Rebased rather than frozen - see the header. `origin` is captured here rather than
  // assuming `performance.now()` is 0 at module evaluation, because this module is
  // evaluated after the document has already been parsing for a while.
  const origin = typeof performance === 'object' ? performance.now() : 0
  const elapsed = (): number =>
    typeof performance === 'object' ? Math.round(performance.now() - origin) : 0
  const clock = (): number => DEMO_EPOCH_MS + elapsed()

  const RealDate = Date
  // A Proxy rather than a subclass: `Date`'s constructor overloads cannot be spread
  // through a `super(...)` call without losing their types, and a proxy leaves
  // `instanceof`, `Date.parse`, `Date.UTC` and every prototype method untouched.
  const DemoDate = new Proxy(RealDate, {
    construct(target, args) {
      return args.length === 0
        ? Reflect.construct(target, [clock()])
        : Reflect.construct(target, args)
    },
    get(target, property, receiver) {
      if (property === 'now') return clock
      return Reflect.get(target, property, receiver)
    },
  })
  ;(globalThis as { Date: DateConstructor }).Date = DemoDate as DateConstructor
}
