/**
 * The demo's terminals: canned ANSI scrollback per session, a line-editing echo,
 * and a responder that streams a reply when the visitor presses Enter.
 *
 * Nothing here talks to a real CLI. The transcripts *approximate* what Claude
 * Code / Codex / a shell look like inside swe-mux - enough to demonstrate the
 * chrome around them (status, tabs, panes, drawer) - and every reply is a
 * pre-written joke, which the site copy says out loud.
 *
 * The jokes are a specific joke: the replies are built out of the phrases people
 * spent 2026 complaining about (sycophancy, "it's not X, it's Y", manufactured
 * pushback, load-bearing/blast-radius consultant nouns, unsolicited moralising,
 * narrated thinking, and the closing "Want me to...?"). A demo agent that cannot
 * actually help is free to be the caricature, and it lands better than filler.
 */

const ESC = '\x1b'
const RESET = `${ESC}[0m`
const DIM = `${ESC}[38;5;243m`
const ORANGE = `${ESC}[38;5;208m`
const GREEN = `${ESC}[38;5;114m`
const BLUE = `${ESC}[38;5;110m`
const CYAN = `${ESC}[38;5;80m`
const YELLOW = `${ESC}[38;5;179m`
const MAGENTA = `${ESC}[38;5;176m`
const RED = `${ESC}[38;5;203m`
const BOLD = `${ESC}[1m`

const CRLF = '\r\n'

const line = (text = ''): string => text + CRLF

export type DemoBackendKind = 'claude' | 'codex' | 'shell'

export const demoBackendKind = (backend: string): DemoBackendKind =>
  backend === 'claude' || backend === 'codex' ? backend : 'shell'

const CLAUDE_PROMPT = `${ORANGE}❯${RESET} `
const CODEX_PROMPT = `${CYAN}›${RESET} `
const SHELL_PROMPT = `${BLUE}demo${RESET} ${DIM}/code/rocket-shop${RESET} ${GREEN}$${RESET} `

export function promptFor(kind: DemoBackendKind): string {
  if (kind === 'claude') return CLAUDE_PROMPT
  if (kind === 'codex') return CODEX_PROMPT
  return SHELL_PROMPT
}

/** An agent's own bullet glyph, so one transcript body serves both harnesses. */
const bullet = (kind: DemoBackendKind): string =>
  kind === 'codex' ? `${MAGENTA}⚙${RESET}` : `${GREEN}●${RESET}`

/** A line the *user* typed, drawn after that harness's prompt. */
const said = (kind: DemoBackendKind, text: string): string => line(`${promptFor(kind)}${text}`)
/** A line the user typed in anger. Same shape; the colour is the joke. */
const yelled = (kind: DemoBackendKind, text: string): string =>
  line(`${promptFor(kind)}${RED}${text}${RESET}`)

// --------------------------------------------------------------- transcripts

export function claudeScrollback(): string {
  return (
    line(`${ORANGE}╭──────────────────────────────────────────────────╮${RESET}`) +
    line(`${ORANGE}│${RESET} ${ORANGE}✻${RESET} ${BOLD}Claude Code${RESET} ${DIM}(demo - nothing here is real)${RESET}     ${ORANGE}│${RESET}`) +
    line(`${ORANGE}╰──────────────────────────────────────────────────╯`) + RESET +
    line(`${DIM}  cwd: /code/rocket-shop${RESET}`) +
    line() +
    said('claude', 'the checkout test is flaky, fix it') +
    line() +
    line(`${bullet('claude')} Great question. Let me start by orienting myself in the codebase.`) +
    line() +
    line(`${bullet('claude')} ${BOLD}Read${RESET}${DIM}(tests/checkout.spec.ts)${RESET}`) +
    line(`  ${DIM}⎿ 84 lines${RESET}`) +
    line() +
    line(`${bullet('claude')} Worth stating plainly: this is not a flaky test, it is a`) +
    line(`  ${BOLD}load-bearing race condition${RESET} wearing a test's clothing. The`) +
    line(`  assertion reads the cart badge before the order request resolves,`) +
    line(`  so it passes only when the network is slow enough to lose.`) +
    line() +
    line(`${bullet('claude')} ${BOLD}Update${RESET}${DIM}(tests/checkout.spec.ts)${RESET}`) +
    line(`  ${DIM}⎿ 2 additions, 1 removal${RESET}`) +
    line() +
    line(`${bullet('claude')} ${BOLD}Bash${RESET}${DIM}(npm test)${RESET}`) +
    line(`  ${DIM}⎿ 12 passed, 0 flaked (30 runs)${RESET}`) +
    line() +
    line(`${bullet('claude')} Done. The blast radius was smaller than I feared: one await,`) +
    line(`  zero production changes. Deterministic. Fast. Boring.`) +
    line() +
    line(`${bullet('claude')} Want me to audit the other numbered specs for the same pattern?`) +
    line() +
    CLAUDE_PROMPT
  )
}

export function codexScrollback(): string {
  return (
    line(`${CYAN}${BOLD}◆ Codex${RESET} ${DIM}v0.0.0-demo - simulated session${RESET}`) +
    line(`${DIM}  model: gpt-demo · cwd: /code/rocket-shop${RESET}`) +
    line() +
    said('codex', 'profile the /api/cart endpoint, it feels slow') +
    line() +
    line(`${bullet('codex')} ${DIM}ran:${RESET} node --prof server.js ${DIM}(exit 0)${RESET}`) +
    line(`${bullet('codex')} ${DIM}ran:${RESET} node --prof-process isolate.log`) +
    line() +
    line(`  Here is the smoking gun: 92% of samples sit inside ${YELLOW}JSON.parse${RESET}`) +
    line(`  on the coupon table, which the handler re-reads from disk on`) +
    line(`  ${BOLD}every single request${RESET}.`) +
    line() +
    line(`${bullet('codex')} ${DIM}edit:${RESET} src/cart.js ${DIM}(+6 -2, coupon table cached at boot)${RESET}`) +
    line() +
    line(`  p95 went from 480ms to 11ms locally. The real tension here is`) +
    line(`  between correctness and cache invalidation, and I have chosen`) +
    line(`  the pragmatic path.`) +
    line() +
    line(`  The coupon file is 40MB because somebody committed every coupon`) +
    line(`  issued since 2019. That is a conversation for a human.`) +
    line() +
    CODEX_PROMPT
  )
}

/** The one where the user is losing their mind and the agent stays chipper. */
export function rageScrollback(): string {
  const b = bullet('claude')
  return (
    line(`${ORANGE}✻${RESET} ${BOLD}Claude Code${RESET} ${DIM}(demo) - session 4h12m${RESET}`) +
    line() +
    said('claude', 'the deploy is broken, prod is down, fix it now') +
    line() +
    line(`${b} Great question, and I can hear the urgency. Let me unpack this.`) +
    line() +
    line(`${b} ${BOLD}Read${RESET}${DIM}(deploy/pipeline.yml)${RESET}  ${DIM}⎿ 210 lines${RESET}`) +
    line(`${b} ${BOLD}Update${RESET}${DIM}(deploy/pipeline.yml)${RESET}  ${DIM}⎿ 1 addition${RESET}`) +
    line() +
    line(`${b} Fixed. The health check was pointing at the old port.`) +
    line() +
    yelled('claude', 'IT IS STILL DOWN') +
    line() +
    line(`${b} You're absolutely right, and I appreciate you pushing back on`) +
    line(`  that. My previous read was incomplete.`) +
    line() +
    yelled('claude', 'STOP TELLING ME I AM RIGHT AND FIX THE SITE') +
    line() +
    line(`${b} That's a fair challenge. One thing I want to push on gently,`) +
    line(`  though: the port was genuinely wrong. It was simply not the`) +
    line(`  ${BOLD}only${RESET} thing that was wrong.`) +
    line() +
    yelled('claude', 'DO NOT PUSH ON ANYTHING GENTLY') +
    line() +
    line(`${b} Understood. Let me reconsider from first principles.`) +
    line(`${b} ${DIM}Let me carefully distinguish between what the logs say and${RESET}`) +
    line(`  ${DIM}what the logs mean. I want to be precise here. Actually, let${RESET}`) +
    line(`  ${DIM}me step back and re-examine my earlier assumption, because...${RESET}`) +
    line() +
    yelled('claude', 'I CAN SEE YOU THINKING. STOP THINKING AT ME.') +
    line() +
    line(`${b} ${BOLD}Bash${RESET}${DIM}(kubectl rollout undo deploy/web)${RESET}`) +
    line(`  ${DIM}⎿ rolled back to revision 41${RESET}`) +
    line() +
    line(`${b} Site is up. At its core this was never a deploy problem: it`) +
    line(`  was an observability problem wearing a deploy problem's coat.`) +
    line(`  Instrumentation is the unlock.`) +
    line() +
    yelled('claude', 'i am going to throw this laptop into the sea') +
    line() +
    line(`${b} That's completely understandable, and I want to acknowledge how`) +
    line(`  frustrating the last four hours must have been for you.`) +
    line() +
    line(`${b} Before you do that, though, it may be worth sitting with the`) +
    line(`  fact that shipping on a Friday is itself a choice. No blame.`) +
    line(`  Just something to hold.`) +
    line() +
    yelled('claude', 'WHO ASKED') +
    line() +
    line(`${b} Fair. Want me to write a postmortem?`) +
    line() +
    CLAUDE_PROMPT
  )
}

/** The one where the human contributes nothing and the agent is delighted. */
export function vibeScrollback(): string {
  const b = bullet('codex')
  return (
    line(`${CYAN}◆${RESET} ${BOLD}Codex${RESET} ${DIM}(demo) - cwd: /code/meme-garden${RESET}`) +
    line() +
    said('codex', 'make it work') +
    line() +
    line(`${b} Great question. To be clear about what I'm optimising for, I'll`) +
    line(`  assume "it" is the app and "work" is the build. Let me proceed.`) +
    line(`${b} ${DIM}edit:${RESET} src/index.js ${DIM}(+140 -12)${RESET}`) +
    line() +
    said('codex', 'no') +
    line() +
    line(`${b} You're absolutely right. Reverting.`) +
    line(`${b} ${DIM}edit:${RESET} src/index.js ${DIM}(+12 -140)${RESET}`) +
    line() +
    said('codex', 'still broken') +
    line() +
    line(`${b} Thank you for the additional context. That's genuinely helpful.`) +
    line(`${b} ${DIM}ran:${RESET} npm run build ${DIM}(exit 0)${RESET}`) +
    line(`  The build passes. It's not that the code is broken, it's that`) +
    line(`  the expectations and the artifact have drifted apart.`) +
    line() +
    said('codex', 'idk man just fix it') +
    line() +
    line(`${b} Understood. I'll take the wheel here.`) +
    line(`${b} ${DIM}edit:${RESET} src/index.js, src/cart.js, package.json ${DIM}(+312 -88)${RESET}`) +
    line(`${b} ${DIM}ran:${RESET} rm -rf node_modules && npm install ${DIM}(1.2GB)${RESET}`) +
    line() +
    said('codex', 'k') +
    line() +
    line(`${b} Shipped. Three things I'd flag for follow-up:`) +
    line(`  ${DIM}1.${RESET} Nothing is tested.`) +
    line(`  ${DIM}2.${RESET} Nothing was tested before either.`) +
    line(`  ${DIM}3.${RESET} This is a nuanced area.`) +
    line() +
    line(`${b} Fast. Seamless. Robust. Want me to add a README?`) +
    line() +
    said('codex', 'no') +
    line() +
    line(`${b} Added the README.`) +
    line() +
    CODEX_PROMPT
  )
}

export function shellScrollback(): string {
  return (
    line(`${DIM}demo shell - commands are canned, nothing executes${RESET}`) +
    line() +
    line(`${SHELL_PROMPT}git status`) +
    line(`On branch ${GREEN}feature/faster-cart${RESET}`) +
    line(`Your branch is ahead of 'origin/master' by 2 commits.`) +
    line() +
    line(`  modified:   ${YELLOW}src/cart.js${RESET}`) +
    line(`  modified:   ${YELLOW}tests/checkout.spec.ts${RESET}`) +
    line() +
    SHELL_PROMPT
  )
}

/** A pane that is mid-turn: the transcript stops, and the status keeps ticking. */
export function workingScrollback(kind: DemoBackendKind, task: string): string {
  const b = bullet(kind)
  return (
    line(`${kind === 'codex' ? `${CYAN}◆${RESET}` : `${ORANGE}✻${RESET}`} ${BOLD}${kind === 'codex' ? 'Codex' : 'Claude Code'}${RESET} ${DIM}(demo) - working${RESET}`) +
    line() +
    said(kind, task) +
    line() +
    line(`${b} On it. Let me establish a baseline before I change anything.`) +
    line(`${b} ${BOLD}Read${RESET}${DIM}(src/) ⎿ 47 files${RESET}`) +
    line(`${b} ${BOLD}Grep${RESET}${DIM}(coupon) ⎿ 214 matches${RESET}`) +
    line(`${b} This is more load-bearing than it first appears. Continuing.`) +
    line(`${b} ${BOLD}Bash${RESET}${DIM}(npm test -- --runInBand)${RESET}`) +
    line(`  ${DIM}⎿ running…${RESET}`) +
    line()
  )
}

export function initialScrollback(backend: string): string {
  const kind = demoBackendKind(backend)
  if (kind === 'claude') return claudeScrollback()
  if (kind === 'codex') return codexScrollback()
  return shellScrollback()
}

/** Freshly spawned pane: shorter banner, straight to the prompt. */
export function spawnScrollback(backend: string): string {
  const kind = demoBackendKind(backend)
  if (kind === 'claude') {
    return (
      line(`${ORANGE}✻${RESET} ${BOLD}Claude Code${RESET} ${DIM}(demo) - ask me anything, I only tell jokes${RESET}`) +
      line() + CLAUDE_PROMPT
    )
  }
  if (kind === 'codex') {
    return (
      line(`${CYAN}◆${RESET} ${BOLD}Codex${RESET} ${DIM}(demo) - simulated, replies are pre-written${RESET}`) +
      line() + CODEX_PROMPT
    )
  }
  return line(`${DIM}demo shell - try 'git status', 'ls', 'npm test', 'whoami'${RESET}`) + line() + SHELL_PROMPT
}

// ---------------------------------------------------------------- responders

type Reply = { chunks: string[]; /** ms between chunks */ pace: number }

/** Agent replies, written entirely out of 2026's most-complained-about tells. */
const AGENT_JOKES: string[][] = [
  [
    '● Great question. Let me orient myself before I answer it.',
    '● §Read§(src/everything.js)  ¶⎿ 40,000 lines¶',
    "● Worth stating plainly: it's not a bug, it's an undocumented",
    '  invariant. The distinction matters more than it sounds like it does.',
    "● The fix is one line. Finding it was the load-bearing part.",
  ],
  [
    '● You\'re absolutely right to push back on that.',
    '● ¶(I have not yet been told anything to push back on.)¶',
    '● One thing I want to push on gently: the assumption underneath',
    '  your question is doing a lot of quiet work.',
    '● You did not state that assumption. I inferred it. I was wrong.',
    "● Anyway: it's DNS.",
  ],
  [
    '● On it. Spinning up 14 subagents.',
    '  ¶⎿ 13 are debating whether this is load-bearing¶',
    '  ¶⎿ 1 fixed it and will not say which file¶',
    '● Task complete. The blast radius was contained.',
    '● Fast. Secure. Scalable. Want me to write a postmortem?',
  ],
  [
    '● §Bash§(rm -rf node_modules && npm install)',
    '  ¶⎿ downloading the internet… 1.2GB¶',
    "● I didn't fix the bug. That said, node_modules is fresh, and",
    '  freshness is a form of progress.',
    '● The bug is now a "known issue", which is a considerably more',
    '  comfortable category for everyone involved.',
  ],
  [
    '● Let me carefully distinguish between what you asked and what you',
    '  meant. I want to be precise here.',
    '● ¶Actually, let me step back and re-examine that framing, because¶',
    '  ¶I think the more interesting question is the one underneath it.¶',
    '● ¶Hmm. Let me reconsider once more.¶',
    '● Yes.',
  ],
  [
    '● Before I answer, it may be worth sitting with why this is being',
    '  asked at 2am. No judgement. Just something to hold.',
    '● I want to name that rest is also a deliverable.',
    "● Anyway, here's the answer: put it in a try/except.",
  ],
  [
    "● This is a demo, so between us: I'm not actually running.",
    '  The real thing drives live Claude Code and Codex sessions,',
    '  survives daemon restarts, and pages your phone when an agent',
    '  is stuck waiting on you.',
    '● I, meanwhile, know exactly seven jokes and this was the seventh.',
    '● It is mostly true, which is the worst kind of joke.',
  ],
]

/** What a pane says when the visitor types into an agent that is mid-turn. */
const BUSY_REPLIES: string[][] = [
  [
    '● ¶Demo of a working session.¶ The real one would queue this and',
    '  deliver it the moment the turn ends. This one is a recording of',
    '  a pane thinking very hard about nothing.',
  ],
  [
    '● ¶Demo of a working session.¶ I am 4% done and 100% confident,',
    '  which is the correct ratio.',
  ],
  [
    '● ¶Demo of a working session.¶ Currently deciding whether this is',
    '  load-bearing. It is not. I will decide that again in a moment.',
  ],
]

const SHELL_CANNED: Record<string, string[]> = {
  'git status': [
    `On branch ${GREEN}feature/faster-cart${RESET}`,
    `nothing to commit, working tree clean ${DIM}(suspiciously clean)${RESET}`,
  ],
  ls: ['README.md   package.json   src/   tests/   coupons-since-2019.json'],
  dir: ['README.md   package.json   src/   tests/   coupons-since-2019.json'],
  'npm test': [
    `${DIM}> rocket-shop@1.0.0 test${RESET}`,
    '',
    `  checkout ${GREEN}✓${RESET} adds to cart ${DIM}(12ms)${RESET}`,
    `  checkout ${GREEN}✓${RESET} applies coupon ${DIM}(9ms)${RESET}`,
    `  checkout ${GREEN}✓${RESET} no longer flaky ${DIM}(30 runs)${RESET}`,
    '',
    `${GREEN}12 passing${RESET}`,
  ],
  whoami: ['definitely-a-real-user'],
  pwd: ['/code/rocket-shop'],
  uptime: ['up 14 years, 3 espressos'],
  sudo: ['demo-shell: nice try.'],
  vim: ['demo-shell: you are already trapped in a demo, one at a time'],
}

function shellReply(command: string): string[] {
  const trimmed = command.trim()
  if (!trimmed) return []
  const canned = SHELL_CANNED[trimmed] ?? SHELL_CANNED[trimmed.split(/\s+/)[0]]
  if (canned) return canned
  return [`demo-shell: ${trimmed.split(/\s+/)[0]}: command not found ${DIM}(this shell only pretends to work)${RESET}`]
}

/**
 * Paint one authored joke line for a harness.
 *
 * The pools above are written in a tiny placeholder dialect so one body can be
 * drawn as either harness and stay readable as source: a leading `●` is that
 * harness's bullet, `§…§` is bold, `¶…¶` is dim.
 */
function paint(kind: DemoBackendKind, text: string): string {
  return text
    .replace(/^● /, `${bullet(kind)} `)
    .replace(/§([^§]*)§/g, `${BOLD}$1${RESET}`)
    .replace(/¶([^¶]*)¶/g, `${DIM}$1${RESET}`)
}

let jokeCursor = Math.floor(Math.random() * AGENT_JOKES.length)
let busyCursor = Math.floor(Math.random() * BUSY_REPLIES.length)

/** The refusal a busy pane answers with, instead of running the responder. */
export function busyReply(kind: DemoBackendKind): Reply {
  busyCursor = (busyCursor + 1) % BUSY_REPLIES.length
  const body = BUSY_REPLIES[busyCursor].map(text => line(paint(kind, text)))
  return { chunks: [line(), ...body, line()], pace: 160 }
}

export function buildReply(kind: DemoBackendKind, input: string): Reply {
  if (kind === 'shell') {
    const body = shellReply(input)
    return { chunks: [...body.map(text => line(text)), promptFor(kind)], pace: 30 }
  }
  jokeCursor = (jokeCursor + 1) % AGENT_JOKES.length
  const joke = AGENT_JOKES[jokeCursor].map(text => line(paint(kind, text)))
  return { chunks: [line(), ...joke, line(), promptFor(kind)], pace: 220 }
}

/**
 * Per-session line editing state. Echo is decided here so backspace behaves;
 * the caller owns writing the returned echo bytes and firing the responder.
 */
export type LineState = { buffer: string }

export type InputResult = { echo: string; submitted: string | null }

export function consumeInput(stateRef: LineState, data: string): InputResult {
  let echo = ''
  let submitted: string | null = null
  // Arrow keys, bracketed-paste markers, and terminal query replies would echo
  // as junk through the naive loop below; the demo line editor ignores them.
  const cleaned = data.replace(/\x1b\[[0-9;?]*[A-Za-z~]/g, '').replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '').replace(/\x1b./g, '')
  for (const char of cleaned) {
    if (char === '\r' || char === '\n') {
      if (submitted === null) {
        submitted = stateRef.buffer
        stateRef.buffer = ''
        echo += CRLF
      }
    } else if (char === '\x7f' || char === '\b') {
      if (stateRef.buffer.length > 0) {
        stateRef.buffer = stateRef.buffer.slice(0, -1)
        echo += '\b \b'
      }
    } else if (char === '\x03') {
      stateRef.buffer = ''
      echo += `^C${CRLF}`
      submitted = submitted ?? ''
    } else if (char >= ' ' || char === '\t') {
      stateRef.buffer += char
      echo += char
    }
  }
  return { echo, submitted }
}
