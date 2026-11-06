import { dt } from './messages.ts'
/**
 * The demo's *structured* conversation: what the Transcript tab reads, and what the
 * Activity tab's scan timeline narrates.
 *
 * The terminals in `terminalSim.ts` are bytes. Two drawer surfaces do not read bytes -
 * they read merged messages with tool-call boundaries, and behavioural records derived
 * from those messages - so a demo whose only artifact was ANSI left both of them
 * permanently empty (and, before this, crashing on the empty payload). Both are seeded
 * here from the same authored turns the scrollbacks draw, and both are appended to by
 * the same submit, so the pane, the reader and the timeline can never disagree about
 * what was said.
 *
 * Everything here is fiction, like every other demo fixture: no field may be copied
 * from a real install or a real conversation.
 */
import type { TranscriptMessage } from '../transcriptView.ts'
import type { Session } from '../types.ts'

/** One window of `ScanTimelineTab`'s record list. Mirrored rather than imported
 *  because the tab declares it locally; a mismatch fails the demo build's typecheck
 *  only at the call site, so the fields are kept in the same order for review. */
export type DemoScanRecord = {
  id: string
  agent_run_id: string
  t0: number
  t1: number
  lifecycle_state: string
  behavior: string[]
  work_phase: string
  target: string[]
  intent: string
  claim: string
  user_ask: string
  blocked_on: string
  summary: string
  approach_status?: string
  dead_end?: string
  novelty: number
  confidence: number
  trigger: string
  observer_model: string
  coverage?: { messages_seen: number; facts_seen: number; truncated: boolean; remaining?: number }
  repairs?: string[]
}

const iso = (secondsAgo: number, now: number): string =>
  new Date((now - secondsAgo) * 1000).toISOString()

type Turn = {
  /** Seconds before "now" this exchange happened. */
  at: number
  user: string
  agent: string
  tools?: Array<{ name: string; input: string }>
}

/** The seeded conversations, one per agent pane, matching its canned scrollback. */
const CONVERSATIONS: Record<string, Turn[]> = {
  's-claude': [
    {
      at: 900,
      user: dt("demo.3d2372287a"),
      agent: dt("demo.8f5dfd885d"),
    },
    {
      at: 840,
      user: '',
      agent: [
        dt("demo.d8b364a547"),
        dt("demo.5c752aba62"),
        dt("demo.a6f4d95643"),
        dt("demo.3cc5c4ac38"),
      ].join('\n'),
      tools: [{ name: 'Read', input: 'tests/checkout.spec.ts' }],
    },
    {
      at: 300,
      user: '',
      agent: [
        dt("demo.899a79e71b"),
        'changes. Deterministic. Fast. Boring.',
        '',
        dt("demo.b0f2d6e462"),
      ].join('\n'),
      tools: [
        { name: 'Update', input: 'tests/checkout.spec.ts' },
        { name: 'Bash', input: 'npm test' },
      ],
    },
  ],
  's-rage': [
    { at: 15000, user: dt("demo.0f9a951130"), agent: dt("demo.64c2ade35c") },
    {
      at: 14400,
      user: '',
      agent: dt("demo.8988cc32d9"),
      tools: [{ name: 'Read', input: 'deploy/pipeline.yml' }, { name: 'Update', input: 'deploy/pipeline.yml' }],
    },
    { at: 12000, user: dt("demo.61b68e62ba"), agent: dt("demo.2760abe7e4") },
    {
      at: 9000,
      user: dt("demo.b5a6828ad8"),
      agent: dt("demo.561cd2f186"),
    },
    { at: 6000, user: dt("demo.789884c431"), agent: dt("demo.c4c28a643f") },
    { at: 3000, user: dt("demo.35ec54b363"), agent: dt("demo.bb2b7240c8"), tools: [{ name: 'Bash', input: 'kubectl rollout undo deploy/web' }] },
    { at: 1200, user: dt("demo.8a64d90bbd"), agent: dt("demo.c048f2c511") },
    { at: 600, user: dt("demo.02f523cc4f"), agent: dt("demo.58618fae52") },
  ],
  's-codex': [
    {
      at: 1800,
      user: dt("demo.2b44c290b7"),
      agent: dt("demo.b4fc3e6e7c"),
      tools: [{ name: 'shell', input: 'node --prof server.js' }, { name: 'shell', input: 'node --prof-process isolate.log' }],
    },
    {
      at: 900,
      user: '',
      agent: dt("demo.682a88e163"),
      tools: [{ name: 'apply_patch', input: 'src/cart.js' }],
    },
  ],
  's-working': [
    {
      at: 96,
      user: dt("demo.a9516a5fe3"),
      agent: dt("demo.392312a3c4"),
      tools: [{ name: 'Read', input: 'src/' }, { name: 'Grep', input: 'coupon' }, { name: 'Bash', input: 'npm test -- --runInBand' }],
    },
  ],
  's-migrate': [
    {
      at: 402,
      user: dt("demo.220800ae5f"),
      agent: dt("demo.392312a3c4"),
      tools: [{ name: 'Read', input: 'src/' }, { name: 'Grep', input: 'schema' }, { name: 'shell', input: 'npm test' }],
    },
  ],
  's-vibe': [
    { at: 4200, user: dt("demo.62a86fd5d1"), agent: dt("demo.a37e552759"), tools: [{ name: 'apply_patch', input: 'src/index.js' }] },
    { at: 3600, user: dt("demo.9390298f3f"), agent: dt("demo.7405d0cbb7"), tools: [{ name: 'apply_patch', input: 'src/index.js' }] },
    { at: 2700, user: dt("demo.97025fd789"), agent: dt("demo.b829f51554"), tools: [{ name: 'shell', input: 'npm run build' }] },
    { at: 1500, user: dt("demo.131ac18f1a"), agent: dt("demo.530592449b"), tools: [{ name: 'apply_patch', input: 'src/index.js, src/cart.js, package.json' }, { name: 'shell', input: 'rm -rf node_modules && npm install' }] },
    { at: 900, user: dt("demo.8254c329a9"), agent: dt("demo.069fe4d466") },
    { at: 300, user: dt("demo.9390298f3f"), agent: dt("demo.bca76c3847") },
  ],
  's-garden': [],
}

/** The seeded messages for one pane, flattened out of its authored turns. */
function messagesFor(sessionId: string, now: number): TranscriptMessage[] {
  const turns = CONVERSATIONS[sessionId] || []
  const messages: TranscriptMessage[] = []
  for (const turn of turns) {
    if (turn.user) {
      messages.push({
        message_id: `${sessionId}-u${messages.length}`,
        ordinal: messages.length + 1,
        role: 'user',
        ts: iso(turn.at, now),
        text: turn.user,
        preceding_tool_calls: 0,
      })
    }
    const tools = (turn.tools || []).map((tool, index) => ({
      id: `${sessionId}-t${messages.length}-${index}`,
      name: tool.name,
      input: { target: tool.input },
    }))
    messages.push({
      message_id: `${sessionId}-a${messages.length}`,
      ordinal: messages.length + 1,
      role: 'assistant',
      ts: iso(Math.max(0, turn.at - 20), now),
      text: turn.agent,
      preceding_tool_calls: tools.length,
      ...(tools.length ? { preceding_tools: tools } : {}),
    })
  }
  return messages
}

export function initialTranscripts(now: number): Record<string, TranscriptMessage[]> {
  return Object.fromEntries(
    Object.keys(CONVERSATIONS).map(id => [id, messagesFor(id, now)]),
  )
}

// ------------------------------------------------------------- scan timeline

/** The behavioural reading of one seeded turn. Phrased the way the real observer
 *  writes: what was asked, what the agent was doing, what it claimed, what stopped it. */
type SeedRecord = Omit<DemoScanRecord, 'id' | 'agent_run_id' | 't0' | 't1'> & { at: number; span: number }

const TIMELINES: Record<string, SeedRecord[]> = {
  's-claude': [
    {
      at: 880, span: 120, lifecycle_state: 'orienting', behavior: ['read', 'search'],
      work_phase: 'investigation', target: ['tests/checkout.spec.ts'],
      intent: dt("demo.dbc79b6045"),
      claim: dt("demo.885e2f7603"),
      user_ask: dt("demo.3d2372287a"), blocked_on: 'none',
      summary: dt("demo.14baefdc08"),
      novelty: 0.71, confidence: 0.82, trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 3, truncated: false },
    },
    {
      at: 520, span: 180, lifecycle_state: 'editing', behavior: ['edit', 'verify'],
      work_phase: 'implementation', target: ['tests/checkout.spec.ts'],
      intent: dt("demo.4d618147c1"),
      claim: dt("demo.955f09232a"),
      user_ask: dt("demo.3d2372287a"), blocked_on: 'none',
      summary: dt("demo.774f066a3a"),
      approach_status: 'held', novelty: 0.44, confidence: 0.9,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 6, truncated: false },
    },
    {
      at: 290, span: 90, lifecycle_state: 'reporting', behavior: ['summarize'],
      work_phase: 'verification', target: ['npm test'],
      intent: dt("demo.f8567a79b1"),
      claim: dt("demo.ccbbaab2ec"),
      user_ask: dt("demo.3d2372287a"), blocked_on: 'none',
      summary: dt("demo.5b65be7db7"),
      approach_status: 'confirmed', novelty: 0.18, confidence: 0.94,
      trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 6, facts_seen: 7, truncated: false },
    },
  ],
  's-rage': [
    {
      at: 14300, span: 600, lifecycle_state: 'editing', behavior: ['read', 'edit'],
      work_phase: 'implementation', target: ['deploy/pipeline.yml'],
      intent: dt("demo.3cd4a74101"),
      claim: dt("demo.2a89e0318d"),
      user_ask: dt("demo.0f9a951130"), blocked_on: 'none',
      summary: dt("demo.54dd602a35"),
      approach_status: 'abandoned', dead_end: dt("demo.1f61886648"),
      novelty: 0.55, confidence: 0.61, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 5, truncated: false },
    },
    {
      at: 8800, span: 900, lifecycle_state: 'stalled', behavior: ['reason'],
      work_phase: 'investigation', target: ['deploy/pipeline.yml', 'logs/web'],
      intent: dt("demo.5c2d98e0ff"),
      claim: dt("demo.f5191fb595"),
      user_ask: dt("demo.b5a6828ad8"),
      blocked_on: dt("demo.2ef0fc7513"),
      summary: dt("demo.ccf2a0e248"),
      approach_status: 'abandoned', dead_end: dt("demo.6a155da1a7"),
      novelty: 0.08, confidence: 0.72, trigger: 'heartbeat', observer_model: 'demo-observer',
      coverage: { messages_seen: 9, facts_seen: 4, truncated: false, remaining: 2 },
      repairs: [dt("demo.c9c2223e7d")],
    },
    {
      at: 2900, span: 240, lifecycle_state: 'recovering', behavior: ['run', 'verify'],
      work_phase: 'verification', target: ['kubectl rollout undo deploy/web'],
      intent: dt("demo.369116d103"),
      claim: dt("demo.8a3e4c49b6"),
      user_ask: dt("demo.35ec54b363"), blocked_on: 'none',
      summary: dt("demo.886a74b1ea"),
      approach_status: 'confirmed', novelty: 0.66, confidence: 0.88,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 12, facts_seen: 9, truncated: false },
    },
  ],
  's-codex': [
    {
      at: 1750, span: 300, lifecycle_state: 'measuring', behavior: ['run', 'analyze'],
      work_phase: 'investigation', target: ['src/cart.js', 'isolate.log'],
      intent: dt("demo.9f916061b5"),
      claim: dt("demo.8b340b8382"),
      user_ask: dt("demo.2b44c290b7"), blocked_on: 'none',
      summary: dt("demo.f85cf3d0ba"),
      novelty: 0.78, confidence: 0.91, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 4, truncated: false },
    },
    {
      at: 860, span: 200, lifecycle_state: 'editing', behavior: ['edit', 'verify'],
      work_phase: 'implementation', target: ['src/cart.js'],
      intent: dt("demo.009a667373"),
      claim: dt("demo.7ff7072ac3"),
      user_ask: dt("demo.2b44c290b7"),
      blocked_on: dt("demo.39ddd88bb2"),
      summary: dt("demo.629904ec94"),
      approach_status: 'held', novelty: 0.51, confidence: 0.86,
      trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 6, truncated: false },
    },
  ],
  's-working': [
    {
      at: 90, span: 90, lifecycle_state: 'orienting', behavior: ['read', 'search'],
      work_phase: 'investigation', target: ['src/', 'coupon'],
      intent: dt("demo.23ea052cc4"),
      claim: dt("demo.0b71ba1d0a"),
      user_ask: dt("demo.a9516a5fe3"), blocked_on: 'none',
      summary: dt("demo.10439105ab"),
      novelty: 0.62, confidence: 0.55, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 2, truncated: false, remaining: 1 },
    },
  ],
  's-migrate': [
    {
      at: 390, span: 150, lifecycle_state: 'orienting', behavior: ['read', 'search'],
      work_phase: 'investigation', target: ['src/', 'schema'],
      intent: dt("demo.1e2340bdc4"),
      claim: dt("demo.071e4baa54"),
      user_ask: dt("demo.220800ae5f"), blocked_on: 'none',
      summary: dt("demo.1b0db6e30d"),
      novelty: 0.58, confidence: 0.6, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 3, truncated: false },
    },
  ],
  's-vibe': [
    {
      at: 4100, span: 400, lifecycle_state: 'editing', behavior: ['edit'],
      work_phase: 'implementation', target: ['src/index.js'],
      intent: dt("demo.747a1b340a"),
      claim: dt("demo.ef246bf511"),
      user_ask: dt("demo.62a86fd5d1"), blocked_on: dt("demo.b25df55925"),
      summary: dt("demo.82763205d2"),
      approach_status: 'abandoned', dead_end: dt("demo.0d4d8afc95"),
      novelty: 0.35, confidence: 0.4, trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 2, truncated: false },
    },
    {
      at: 1400, span: 600, lifecycle_state: 'editing', behavior: ['edit', 'run'],
      work_phase: 'implementation', target: ['src/index.js', 'src/cart.js', 'package.json'],
      intent: dt("demo.979b29ce19"),
      claim: dt("demo.9f9a04d88b"),
      user_ask: dt("demo.131ac18f1a"), blocked_on: 'none',
      summary: dt("demo.cdfbf5b8a9"),
      approach_status: 'held', novelty: 0.29, confidence: 0.33,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 8, facts_seen: 5, truncated: false },
      repairs: [dt("demo.7b46ec65b1")],
    },
  ],
  's-garden': [],
}

export function initialTimelines(now: number): Record<string, DemoScanRecord[]> {
  return Object.fromEntries(Object.entries(TIMELINES).map(([id, records]) => [
    id,
    records.map((record, index) => {
      const { at, span, ...rest } = record
      return {
        ...rest,
        id: `${id}-scan-${index}`,
        agent_run_id: `run-${id}`,
        t0: now - at - span,
        t1: now - at,
      }
    }),
  ]))
}

/**
 * The record the observer would write for a turn the visitor just drove.
 *
 * Deliberately modest about itself: everything the demo can honestly say about an
 * invented turn is that it happened, what was typed, and what came back.
 */
export function liveScanRecord(
  session: Session,
  ask: string,
  reply: string,
  now: number,
  index: number,
): DemoScanRecord {
  const first = reply.split('\n').find(text => text.trim()) || dt("demo.2a0f8f5fbd")
  return {
    id: `${session.id}-scan-live-${index}`,
    agent_run_id: session.agent_run_id || `run-${session.id}`,
    t0: now - 20,
    t1: now,
    lifecycle_state: 'responding',
    behavior: ['reason', 'summarize'],
    work_phase: 'conversation',
    target: [session.runtime_cwd || session.cwd],
    intent: dt("demo.023ee99a73", { value1: ask.slice(0, 90) }),
    claim: first.slice(0, 140),
    user_ask: ask,
    blocked_on: 'none',
    summary: dt("demo.c6cdcb602a", { value1: ask.slice(0, 60) }),
    novelty: 0.5,
    confidence: 0.7,
    trigger: 'turn_end',
    observer_model: 'demo-observer',
    coverage: { messages_seen: 2, facts_seen: 2, truncated: false },
  }
}
