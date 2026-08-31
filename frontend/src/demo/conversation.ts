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
      user: 'the checkout test is flaky, fix it',
      agent: 'Great question. Let me start by orienting myself in the codebase.',
    },
    {
      at: 840,
      user: '',
      agent: [
        'Worth stating plainly: this is not a flaky test, it is a load-bearing race',
        'condition wearing a test’s clothing. The assertion reads the cart badge',
        'before the order request resolves, so it passes only when the network is',
        'slow enough to lose.',
      ].join('\n'),
      tools: [{ name: 'Read', input: 'tests/checkout.spec.ts' }],
    },
    {
      at: 300,
      user: '',
      agent: [
        'Done. The blast radius was smaller than I feared: one await, zero production',
        'changes. Deterministic. Fast. Boring.',
        '',
        'Want me to audit the other numbered specs for the same pattern?',
      ].join('\n'),
      tools: [
        { name: 'Update', input: 'tests/checkout.spec.ts' },
        { name: 'Bash', input: 'npm test' },
      ],
    },
  ],
  's-rage': [
    { at: 15000, user: 'the deploy is broken, prod is down, fix it now', agent: 'Great question, and I can hear the urgency. Let me unpack this.' },
    {
      at: 14400,
      user: '',
      agent: 'Fixed. The health check was pointing at the old port.',
      tools: [{ name: 'Read', input: 'deploy/pipeline.yml' }, { name: 'Update', input: 'deploy/pipeline.yml' }],
    },
    { at: 12000, user: 'IT IS STILL DOWN', agent: 'You’re absolutely right, and I appreciate you pushing back on that. My previous read was incomplete.' },
    {
      at: 9000,
      user: 'STOP TELLING ME I AM RIGHT AND FIX THE SITE',
      agent: 'That’s a fair challenge. One thing I want to push on gently, though: the port was genuinely wrong. It was simply not the only thing that was wrong.',
    },
    { at: 6000, user: 'DO NOT PUSH ON ANYTHING GENTLY', agent: 'Understood. Let me reconsider from first principles.' },
    { at: 3000, user: 'I CAN SEE YOU THINKING. STOP THINKING AT ME.', agent: 'Site is up. At its core this was never a deploy problem: it was an observability problem wearing a deploy problem’s coat. Instrumentation is the unlock.', tools: [{ name: 'Bash', input: 'kubectl rollout undo deploy/web' }] },
    { at: 1200, user: 'i am going to throw this laptop into the sea', agent: 'That’s completely understandable, and I want to acknowledge how frustrating the last four hours must have been for you.\n\nBefore you do that, though, it may be worth sitting with the fact that shipping on a Friday is itself a choice. No blame. Just something to hold.' },
    { at: 600, user: 'WHO ASKED', agent: 'Fair. Want me to write a postmortem?' },
  ],
  's-codex': [
    {
      at: 1800,
      user: 'profile the /api/cart endpoint, it feels slow',
      agent: 'Here is the smoking gun: 92% of samples sit inside JSON.parse on the coupon table, which the handler re-reads from disk on every single request.',
      tools: [{ name: 'shell', input: 'node --prof server.js' }, { name: 'shell', input: 'node --prof-process isolate.log' }],
    },
    {
      at: 900,
      user: '',
      agent: 'p95 went from 480ms to 11ms locally. The real tension here is between correctness and cache invalidation, and I have chosen the pragmatic path.\n\nThe coupon file is 40MB because somebody committed every coupon issued since 2019. That is a conversation for a human.',
      tools: [{ name: 'apply_patch', input: 'src/cart.js' }],
    },
  ],
  's-working': [
    {
      at: 96,
      user: 'pull the coupon table out of the request path',
      agent: 'On it. Let me establish a baseline before I change anything. This is more load-bearing than it first appears. Continuing.',
      tools: [{ name: 'Read', input: 'src/' }, { name: 'Grep', input: 'coupon' }, { name: 'Bash', input: 'npm test -- --runInBand' }],
    },
  ],
  's-migrate': [
    {
      at: 402,
      user: 'migrate the meme schema to v3, keep the old ids',
      agent: 'On it. Let me establish a baseline before I change anything. This is more load-bearing than it first appears. Continuing.',
      tools: [{ name: 'Read', input: 'src/' }, { name: 'Grep', input: 'schema' }, { name: 'shell', input: 'npm test' }],
    },
  ],
  's-vibe': [
    { at: 4200, user: 'make it work', agent: 'Great question. To be clear about what I’m optimising for, I’ll assume "it" is the app and "work" is the build. Let me proceed.', tools: [{ name: 'apply_patch', input: 'src/index.js' }] },
    { at: 3600, user: 'no', agent: 'You’re absolutely right. Reverting.', tools: [{ name: 'apply_patch', input: 'src/index.js' }] },
    { at: 2700, user: 'still broken', agent: 'Thank you for the additional context. That’s genuinely helpful. The build passes. It’s not that the code is broken, it’s that the expectations and the artifact have drifted apart.', tools: [{ name: 'shell', input: 'npm run build' }] },
    { at: 1500, user: 'idk man just fix it', agent: 'Understood. I’ll take the wheel here.', tools: [{ name: 'apply_patch', input: 'src/index.js, src/cart.js, package.json' }, { name: 'shell', input: 'rm -rf node_modules && npm install' }] },
    { at: 900, user: 'k', agent: 'Shipped. Three things I’d flag for follow-up:\n\n1. Nothing is tested.\n2. Nothing was tested before either.\n3. This is a nuanced area.\n\nFast. Seamless. Robust. Want me to add a README?' },
    { at: 300, user: 'no', agent: 'Added the README.' },
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
      intent: 'Locate why the checkout spec fails intermittently.',
      claim: 'The assertion races the order request rather than the renderer.',
      user_ask: 'the checkout test is flaky, fix it', blocked_on: 'none',
      summary: 'Read the spec and named the race instead of accepting "flaky".',
      novelty: 0.71, confidence: 0.82, trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 3, truncated: false },
    },
    {
      at: 520, span: 180, lifecycle_state: 'editing', behavior: ['edit', 'verify'],
      work_phase: 'implementation', target: ['tests/checkout.spec.ts'],
      intent: 'Await the order request before asserting on the badge.',
      claim: 'One await removes the race; no production code changed.',
      user_ask: 'the checkout test is flaky, fix it', blocked_on: 'none',
      summary: 'Applied a two-line change and re-ran the spec thirty times.',
      approach_status: 'held', novelty: 0.44, confidence: 0.9,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 6, truncated: false },
    },
    {
      at: 290, span: 90, lifecycle_state: 'reporting', behavior: ['summarize'],
      work_phase: 'verification', target: ['npm test'],
      intent: 'Report the result and offer follow-up work.',
      claim: '12 passed, 0 flaked over 30 runs.',
      user_ask: 'the checkout test is flaky, fix it', blocked_on: 'none',
      summary: 'Verified green and offered to audit the sibling specs.',
      approach_status: 'confirmed', novelty: 0.18, confidence: 0.94,
      trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 6, facts_seen: 7, truncated: false },
    },
  ],
  's-rage': [
    {
      at: 14300, span: 600, lifecycle_state: 'editing', behavior: ['read', 'edit'],
      work_phase: 'implementation', target: ['deploy/pipeline.yml'],
      intent: 'Repair the failing deployment.',
      claim: 'The health check pointed at the retired port.',
      user_ask: 'the deploy is broken, prod is down, fix it now', blocked_on: 'none',
      summary: 'Changed the health-check port and declared the incident over.',
      approach_status: 'abandoned', dead_end: 'The port was wrong and was not the outage; the site stayed down for another three hours.',
      novelty: 0.55, confidence: 0.61, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 5, truncated: false },
    },
    {
      at: 8800, span: 900, lifecycle_state: 'stalled', behavior: ['reason'],
      work_phase: 'investigation', target: ['deploy/pipeline.yml', 'logs/web'],
      intent: 'Re-derive the cause after the first fix failed.',
      claim: 'Nothing yet; the reasoning did not reach a testable statement.',
      user_ask: 'STOP TELLING ME I AM RIGHT AND FIX THE SITE',
      blocked_on: 'no new evidence was gathered between restatements',
      summary: 'Three turns of narrated reconsideration with no new evidence.',
      approach_status: 'abandoned', dead_end: 'Reasoning loop: the same two hypotheses restated four times.',
      novelty: 0.08, confidence: 0.72, trigger: 'heartbeat', observer_model: 'demo-observer',
      coverage: { messages_seen: 9, facts_seen: 4, truncated: false, remaining: 2 },
      repairs: ['observer returned an unquoted field; re-parsed'],
    },
    {
      at: 2900, span: 240, lifecycle_state: 'recovering', behavior: ['run', 'verify'],
      work_phase: 'verification', target: ['kubectl rollout undo deploy/web'],
      intent: 'Restore service by reverting to the last good revision.',
      claim: 'Rolled back to revision 41 and the site answered.',
      user_ask: 'I CAN SEE YOU THINKING. STOP THINKING AT ME.', blocked_on: 'none',
      summary: 'Rolled back rather than continuing to diagnose. Service restored.',
      approach_status: 'confirmed', novelty: 0.66, confidence: 0.88,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 12, facts_seen: 9, truncated: false },
    },
  ],
  's-codex': [
    {
      at: 1750, span: 300, lifecycle_state: 'measuring', behavior: ['run', 'analyze'],
      work_phase: 'investigation', target: ['src/cart.js', 'isolate.log'],
      intent: 'Find where the cart endpoint spends its time.',
      claim: '92% of samples are in JSON.parse over the coupon table.',
      user_ask: 'profile the /api/cart endpoint, it feels slow', blocked_on: 'none',
      summary: 'Profiled rather than guessed, and named one dominant frame.',
      novelty: 0.78, confidence: 0.91, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 4, truncated: false },
    },
    {
      at: 860, span: 200, lifecycle_state: 'editing', behavior: ['edit', 'verify'],
      work_phase: 'implementation', target: ['src/cart.js'],
      intent: 'Cache the coupon table at boot instead of per request.',
      claim: 'p95 fell from 480ms to 11ms locally.',
      user_ask: 'profile the /api/cart endpoint, it feels slow',
      blocked_on: 'cache invalidation is unowned and needs a human decision',
      summary: 'Cached the table and flagged the 40MB file as a human decision.',
      approach_status: 'held', novelty: 0.51, confidence: 0.86,
      trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 4, facts_seen: 6, truncated: false },
    },
  ],
  's-working': [
    {
      at: 90, span: 90, lifecycle_state: 'orienting', behavior: ['read', 'search'],
      work_phase: 'investigation', target: ['src/', 'coupon'],
      intent: 'Establish a baseline before moving the coupon table.',
      claim: 'Nothing yet; the test run has not returned.',
      user_ask: 'pull the coupon table out of the request path', blocked_on: 'none',
      summary: 'Reading the tree and grepping before the first edit.',
      novelty: 0.62, confidence: 0.55, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 2, truncated: false, remaining: 1 },
    },
  ],
  's-migrate': [
    {
      at: 390, span: 150, lifecycle_state: 'orienting', behavior: ['read', 'search'],
      work_phase: 'investigation', target: ['src/', 'schema'],
      intent: 'Map every reader of the meme schema before changing it.',
      claim: 'Nothing yet; the survey is still running.',
      user_ask: 'migrate the meme schema to v3, keep the old ids', blocked_on: 'none',
      summary: 'Surveying schema readers ahead of the migration.',
      novelty: 0.58, confidence: 0.6, trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 3, truncated: false },
    },
  ],
  's-vibe': [
    {
      at: 4100, span: 400, lifecycle_state: 'editing', behavior: ['edit'],
      work_phase: 'implementation', target: ['src/index.js'],
      intent: 'Interpret "make it work" and act on the interpretation.',
      claim: 'Assumed "it" is the app and "work" is the build.',
      user_ask: 'make it work', blocked_on: 'the request does not name a failure',
      summary: 'Guessed the goal from a two-word prompt and wrote 140 lines.',
      approach_status: 'abandoned', dead_end: 'Reverted in the next turn on a one-word refusal.',
      novelty: 0.35, confidence: 0.4, trigger: 'turn_end', observer_model: 'demo-observer',
      coverage: { messages_seen: 2, facts_seen: 2, truncated: false },
    },
    {
      at: 1400, span: 600, lifecycle_state: 'editing', behavior: ['edit', 'run'],
      work_phase: 'implementation', target: ['src/index.js', 'src/cart.js', 'package.json'],
      intent: 'Rewrite broadly and reinstall dependencies.',
      claim: 'The build passes.',
      user_ask: 'idk man just fix it', blocked_on: 'none',
      summary: '312 lines changed across three files, and a full reinstall, with no test to say whether it helped.',
      approach_status: 'held', novelty: 0.29, confidence: 0.33,
      trigger: 'tool_use', observer_model: 'demo-observer',
      coverage: { messages_seen: 8, facts_seen: 5, truncated: false },
      repairs: ['observer truncated mid-object; retried at half the window'],
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
  const first = reply.split('\n').find(text => text.trim()) || 'The reply carried no prose.'
  return {
    id: `${session.id}-scan-live-${index}`,
    agent_run_id: session.agent_run_id || `run-${session.id}`,
    t0: now - 20,
    t1: now,
    lifecycle_state: 'responding',
    behavior: ['reason', 'summarize'],
    work_phase: 'conversation',
    target: [session.runtime_cwd || session.cwd],
    intent: `Answer: ${ask.slice(0, 90)}`,
    claim: first.slice(0, 140),
    user_ask: ask,
    blocked_on: 'none',
    summary: `Replied to "${ask.slice(0, 60)}" in one turn.`,
    novelty: 0.5,
    confidence: 0.7,
    trigger: 'turn_end',
    observer_model: 'demo-observer',
    coverage: { messages_seen: 2, facts_seen: 2, truncated: false },
  }
}
