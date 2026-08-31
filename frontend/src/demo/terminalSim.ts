/**
 * The demo's terminals: canned ANSI scrollback per backend, a line-editing echo,
 * and a joke responder that streams a reply when the visitor presses Enter.
 *
 * Nothing here talks to a real CLI. The transcripts *approximate* what Claude
 * Code / Codex / a shell look like inside swe-mux — enough to demonstrate the
 * chrome around them (status, tabs, panes, drawer) — and every reply is a
 * pre-written gag, which the site copy says out loud.
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

export function claudeScrollback(): string {
  return (
    line(`${ORANGE}╭──────────────────────────────────────────────────╮${RESET}`) +
    line(`${ORANGE}│${RESET} ${ORANGE}✻${RESET} ${BOLD}Claude Code${RESET} ${DIM}(demo — nothing here is real)${RESET}     ${ORANGE}│${RESET}`) +
    line(`${ORANGE}╰──────────────────────────────────────────────────╯`) + RESET +
    line(`${DIM}  cwd: /code/rocket-shop${RESET}`) +
    line() +
    line(`${CLAUDE_PROMPT}the checkout test is flaky, fix it`) +
    line() +
    line(`${GREEN}●${RESET} I'll look at the failing test before touching anything.`) +
    line() +
    line(`${GREEN}●${RESET} ${BOLD}Read${RESET}${DIM}(tests/checkout.spec.ts)${RESET}`) +
    line(`  ${DIM}⎿ 84 lines${RESET}`) +
    line() +
    line(`${GREEN}●${RESET} Found it: the test asserts the cart badge before the order`) +
    line(`  request resolves, so it passes only when the network is slow`) +
    line(`  enough to lose the race on purpose.`) +
    line() +
    line(`${GREEN}●${RESET} ${BOLD}Update${RESET}${DIM}(tests/checkout.spec.ts)${RESET}`) +
    line(`  ${DIM}⎿ 2 additions, 1 removal${RESET}`) +
    line() +
    line(`${GREEN}●${RESET} ${BOLD}Bash${RESET}${DIM}(npm test)${RESET}`) +
    line(`  ${DIM}⎿ 12 passed, 0 flaked (30 runs)${RESET}`) +
    line() +
    line(`${GREEN}●${RESET} Done — the test now awaits the confirmation screen instead of`) +
    line(`  betting on the fetch losing a footrace. Anything else?`) +
    line() +
    CLAUDE_PROMPT
  )
}

export function codexScrollback(): string {
  return (
    line(`${CYAN}${BOLD}◆ Codex${RESET} ${DIM}v0.0.0-demo — simulated session${RESET}`) +
    line(`${DIM}  model: gpt-demo · cwd: /code/rocket-shop${RESET}`) +
    line() +
    line(`${CODEX_PROMPT}profile the /api/cart endpoint, it feels slow`) +
    line() +
    line(`${MAGENTA}⚙${RESET} ${DIM}ran:${RESET} node --prof server.js ${DIM}(exit 0)${RESET}`) +
    line(`${MAGENTA}⚙${RESET} ${DIM}ran:${RESET} node --prof-process isolate.log`) +
    line() +
    line(`  92% of samples were inside ${YELLOW}JSON.parse${RESET} on the coupon table,`) +
    line(`  which the handler re-reads from disk ${BOLD}per request${RESET}.`) +
    line() +
    line(`${MAGENTA}⚙${RESET} ${DIM}edit:${RESET} src/cart.js ${DIM}(+6 −2, coupon table cached at boot)${RESET}`) +
    line() +
    line(`  p95 went from 480ms to 11ms locally. The coupon file was 40MB`) +
    line(`  because someone committed every coupon since 2019. You may want`) +
    line(`  to talk to someone about that.`) +
    line() +
    CODEX_PROMPT
  )
}

export function shellScrollback(): string {
  return (
    line(`${DIM}demo shell — commands are canned, nothing executes${RESET}`) +
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
      line(`${ORANGE}✻${RESET} ${BOLD}Claude Code${RESET} ${DIM}(demo) — ask me anything, I only tell jokes${RESET}`) +
      line() + CLAUDE_PROMPT
    )
  }
  if (kind === 'codex') {
    return (
      line(`${CYAN}◆${RESET} ${BOLD}Codex${RESET} ${DIM}(demo) — simulated, replies are pre-written${RESET}`) +
      line() + CODEX_PROMPT
    )
  }
  return line(`${DIM}demo shell — try 'git status', 'ls', 'npm test', 'whoami'${RESET}`) + line() + SHELL_PROMPT
}

// ---------------------------------------------------------------- responders

type Reply = { chunks: string[]; /** ms between chunks */ pace: number }

const AGENT_JOKES: string[][] = [
  [
    `${GREEN}●${RESET} Excellent question. Let me consult the codebase.`,
    `${GREEN}●${RESET} ${BOLD}Read${RESET}${DIM}(src/everything.js)${RESET}`,
    `  ${DIM}⎿ 40,000 lines (this is why we can't have nice things)${RESET}`,
    `${GREEN}●${RESET} I've thought about it carefully and the answer is: it's DNS.`,
    `  It's always DNS. Even here, in a demo, with no network. DNS.`,
  ],
  [
    `${GREEN}●${RESET} On it. Spinning up 14 subagents.`,
    `  ${DIM}⎿ 13 of them are arguing about tabs vs spaces${RESET}`,
    `  ${DIM}⎿ 1 of them fixed your issue and refuses to say which one it was${RESET}`,
    `${GREEN}●${RESET} Task complete. Please clap.`,
  ],
  [
    `${GREEN}●${RESET} ${BOLD}Bash${RESET}${DIM}(rm -rf node_modules && npm install)${RESET}`,
    `  ${DIM}⎿ downloading the internet… 1.2GB${RESET}`,
    `${GREEN}●${RESET} I didn't fix the bug, but node_modules is fresh and that`,
    `  feels like progress. The bug is now a "known issue", which is a`,
    `  much more comfortable category for everyone involved.`,
  ],
  [
    `${GREEN}●${RESET} I've analyzed the request. It touches the legacy billing code.`,
    `${GREEN}●${RESET} ${DIM}The legacy billing code has analyzed me back.${RESET}`,
    `${GREEN}●${RESET} We've agreed not to make eye contact. Trying a different file.`,
    `${GREEN}●${RESET} Fixed. Two tests were harmed in the making of this reply,`,
    `  and both had it coming.`,
  ],
  [
    `${GREEN}●${RESET} This is a demo, so between us: I'm not actually running.`,
    `  The real thing manages live Claude Code and Codex sessions,`,
    `  survives daemon restarts, and pages your phone when an agent`,
    `  needs an approval. I, meanwhile, know exactly five jokes.`,
    `${GREEN}●${RESET} This was joke five. It's mostly true, which is the worst kind.`,
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
}

function shellReply(command: string): string[] {
  const trimmed = command.trim()
  if (!trimmed) return []
  const canned = SHELL_CANNED[trimmed] ?? SHELL_CANNED[trimmed.split(/\s+/)[0]]
  if (canned) return canned
  return [`demo-shell: ${trimmed.split(/\s+/)[0]}: command not found ${DIM}(this shell only pretends to work)${RESET}`]
}

let jokeCursor = Math.floor(Math.random() * AGENT_JOKES.length)

export function buildReply(kind: DemoBackendKind, input: string): Reply {
  if (kind === 'shell') {
    const body = shellReply(input)
    return { chunks: [...body.map(text => line(text)), promptFor(kind)], pace: 30 }
  }
  jokeCursor = (jokeCursor + 1) % AGENT_JOKES.length
  const joke = AGENT_JOKES[jokeCursor]
  const styled = kind === 'codex'
    ? joke.map(text => text.replace(new RegExp(`\\${ESC}\\[38;5;114m●`, 'g'), `${MAGENTA}⚙`))
    : joke
  return {
    chunks: [line(), ...styled.map(text => line(text)), line(), promptFor(kind)],
    pace: 220,
  }
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
