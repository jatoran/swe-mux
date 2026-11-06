import { dt } from './messages.ts'
import { cellWidth, composerWindow, graphemes, wrappedRows } from './terminalWidth.ts'
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

import { demoRandom } from './determinism.ts'

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

// ------------------------------------------------------------------ composer

/**
 * The bordered prompt box and status line an agent CLI parks at the bottom of
 * its pane, redrawn on every keystroke the way a real TUI does.
 *
 * Fixed width rather than the pane's own, and this is the one deliberate
 * inaccuracy: a real composer spans the terminal, but one byte stream feeds
 * *both* demo surfaces at once (the desktop pane and the phone beside it), so a
 * box sized for either wraps on the other. 44 columns fits the narrowest pane
 * the demo can present and still reads as a composer on the widest.
 */
const BOX_WIDTH = 40
/** Lines the block occupies: two borders, the input row, and the status row. */
export const COMPOSER_HEIGHT = 4

export type ComposerInfo = {
  kind: DemoBackendKind
  model: string
  /** 0..1, as `Session.context_pct` carries it. Shown as the room remaining. */
  contextPct: number
  /** Mid-turn panes swap the shortcut hint for the interrupt hint. */
  working?: boolean
}

/** Visible text, ignoring the SGR escapes the styled halves carry. */
const bare = (text: string): string => text.replace(/\x1b\[[0-9;]*m/g, '')

/** The shape of a session this module needs to draw its composer. Structural
 *  rather than `Session`, so the sim stays free of the app's types. */
export type ComposerSession = {
  backend: string
  model?: string
  context_pct: number
  state: string
}

/** One derivation of a pane's composer, shared by the seed, the spawn route and
 *  the live redraw - so a box can never report a different model or context
 *  than the row above it. */
export function composerInfo(session: ComposerSession): ComposerInfo {
  return {
    kind: demoBackendKind(session.backend),
    model: (session.model || session.backend).replace(/^claude-/, ''),
    contextPct: session.context_pct,
    working: session.state === 'working',
  }
}

export function composerBlock(info: ComposerInfo, buffer: string, cursor = buffer.length): string {
  const inner = BOX_WIDTH - 4
  const accent = info.kind === 'codex' ? CYAN : ORANGE
  const glyph = info.kind === 'codex' ? '›' : '>'
  const placeholder = info.kind === 'codex' ? dt("demo.dd69a639e3") : dt("demo.e7ff85df56")
  // A single-line composer scrolls its own text: keep the tail under the cursor
  // rather than letting a long line break the box.
  const room = inner - 2
  const typed = composerWindow(buffer, cursor, room).text
  // No drawn caret. The block ends by parking the terminal's *own* cursor after the
  // typed text (`composerCaret`), which is what a real CLI does - and drawing a second
  // one here is what put a block glyph in the box while the real cursor blinked on the
  // status line underneath it.
  const body = typed || `${DIM}${placeholder}${RESET}`
  const row = `${accent}${glyph}${RESET} ${body}`
  const rule = '─'.repeat(BOX_WIDTH - 2)

  // While a turn runs the meter is dropped and the interrupt hint has the line to
  // itself - which is both what the real CLIs do and what keeps this row inside a
  // phone-width pane, where hint plus meter would wrap by a character.
  const left = 100 - Math.round(info.contextPct * 100)
  const status = info.working
    ? dt("demo.a38d6c623a", { value1: accent, value2: RESET, value3: DIM, value4: RESET })
    : (() => {
      const hint = dt("demo.21c3d52273", { value1: DIM, value2: RESET })
      const meter = `${DIM}${info.model} · ${left}% ctx${RESET}`
      // Right-aligned against the box, measured on the bare text so the escapes
      // do not count toward the column.
      const gap = Math.max(1, BOX_WIDTH - 2 - cellWidth(hint) - cellWidth(meter))
      return `  ${hint}${' '.repeat(gap)}${meter}`
    })()

  return [
    `${DIM}╭${rule}╮${RESET}`,
    `${DIM}│${RESET} ${row}${' '.repeat(Math.max(0, inner - cellWidth(row)))} ${DIM}│${RESET}`,
    `${DIM}╰${rule}╯${RESET}`,
    status,
  ].join(CRLF)
}

/** Home the cursor and wipe the screen, for a repaint after a resize. */
export const CLEAR_SCREEN = `${ESC}[H${ESC}[2J`

/**
 * How many rows this transcript occupies in a terminal `cols` wide.
 *
 * Used to work out the top padding that pins the composer to the bottom of a pane, so
 * it only has to be right about *authored* content: the demo's transcripts contain no
 * cursor movement. Grapheme widths include Chinese characters, and wrapping leaves a
 * final single cell empty when the next character occupies two cells.
 */
export function renderedRows(transcript: string, cols: number): number {
  if (!transcript) return 0
  const width = Math.max(1, cols)
  // A trailing newline ends the last line rather than starting an empty one.
  const lines = transcript.replace(/\r\n$/, '').split(/\r?\n/)
  return lines.reduce((total, text) => total + wrappedRows(bare(text), width), 0)
}

/**
 * Column, 1-based, of the first character inside the box: the border, a space, the
 * prompt glyph and another space. Where the caret belongs when the box is empty.
 */
const BOX_TEXT_COLUMN = 5

/**
 * Park the terminal's own cursor after the typed text, inside the box.
 *
 * `composerBlock` ends on the status line, three rows below the input row, so the
 * caret has to be walked back up and placed by absolute column. Without this the
 * visitor typed into a box while the cursor blinked on the line under it, which is
 * the one part of a composer nobody can mistake for cosmetic.
 */
export function composerCaret(buffer: string, cursor = buffer.length): string {
  const room = BOX_WIDTH - 4 - 2
  const typed = composerWindow(buffer, cursor, room).caret
  return `${ESC}[2A${ESC}[${BOX_TEXT_COLUMN + typed}G`
}

/** The whole box, with the caret placed inside it. */
export const composerFrame = (info: ComposerInfo, buffer: string, cursor = buffer.length): string =>
  `${composerBlock(info, buffer, cursor)}${composerCaret(buffer, cursor)}`

/**
 * Replace the composer in place: down to the status line, up over the block, clear to
 * the end of the screen, draw it again. Exactly what the real CLIs do on every
 * keystroke, and the reason the demo can show a box the visitor appears to type inside.
 *
 * The leading `\x1b[2B` undoes `composerCaret`: the cursor is parked on the input row,
 * and the erase has to start from the top of the block rather than from wherever the
 * caret happens to be sitting.
 */
export const redrawComposer = (info: ComposerInfo, buffer: string, cursor = buffer.length): string =>
  `${ESC}[2B${ESC}[${COMPOSER_HEIGHT - 1}A\r${ESC}[0J${composerFrame(info, buffer, cursor)}`

/** Wipe the composer without drawing a new one, before appending transcript. */
export const clearComposer = (): string =>
  `${ESC}[2B${ESC}[${COMPOSER_HEIGHT - 1}A\r${ESC}[0J`

/** A line the *user* typed, drawn after that harness's prompt. */
const said = (kind: DemoBackendKind, text: string): string => line(`${promptFor(kind)}${text}`)
/** A line the user typed in anger. Same shape; the colour is the joke. */
const yelled = (kind: DemoBackendKind, text: string): string =>
  line(`${promptFor(kind)}${RED}${text}${RESET}`)

// --------------------------------------------------------------- transcripts

export function claudeScrollback(): string {
  return line(dt("demo.a3d24ef110")) + line()
    + said('claude', dt("demo.2d6c061479")) + line()
    + line(dt("demo.bcaf7d3d9d"))
    + line(dt("demo.906653bdb9"))
    + line(dt("demo.56fe6721e8"))
    + line(dt("demo.d2e24506c8"))
}

export function codexScrollback(): string {
  return line(dt("demo.29367e7ce9")) + line()
    + said('codex', dt("demo.cd93b77079")) + line()
    + line(dt("demo.19bfd032d9"))
    + line(dt("demo.c3c7ffc775"))
    + line(dt("demo.7dc704b877"))
}

export function rageScrollback(): string {
  return line(dt("demo.a3d24ef110")) + line()
    + said('claude', dt("demo.0d9795c6ca")) + line()
    + line(dt("demo.b71906e31c"))
    + line(dt("demo.dc150c2b84"))
    + line(dt("demo.44130da37c"))
}

export function vibeScrollback(): string {
  return line(dt("demo.a3d24ef110")) + line()
    + said('claude', dt("demo.bd9dfe6f1e")) + line()
    + line(dt("demo.8a5db8f0da"))
    + line(dt("demo.e4f5167c0f"))
    + line(dt("demo.eeb7a40996"))
}

export function shellScrollback(): string {
  return (
    line(dt("demo.3d85296710", { value1: DIM, value2: RESET })) +
    line() +
    line(`${SHELL_PROMPT}git status`) +
    line(dt("demo.f38f6a8c0a", { value1: GREEN, value2: RESET })) +
    line(dt("demo.1a186e62cf")) +
    line() +
    line(`  modified:   ${YELLOW}src/cart.js${RESET}`) +
    line(`  modified:   ${YELLOW}tests/checkout.spec.ts${RESET}`) +
    line() +
    SHELL_PROMPT
  )
}

export function awaitingScrollback(): string {
  return line(dt("demo.29367e7ce9")) + line()
    + said('codex', dt("demo.a14b6de130")) + line()
    + line(dt("demo.78ced1dabb"))
    + line(dt("demo.86a334f75d"))
    + line(dt("demo.e4e1ac2634")) + line()
}

/** A pane that is mid-turn: the transcript stops, and the status keeps ticking. */
export function workingScrollback(info: ComposerInfo, task: string): string {
  const b = bullet(info.kind)
  return (
    line(`${info.kind === 'codex' ? `${CYAN}◆${RESET}` : `${ORANGE}✻${RESET}`} ${BOLD}${info.kind === 'codex' ? 'Codex' : 'Claude Code'}${RESET} ${DIM}(demo) - working${RESET}`) +
    line() +
    said(info.kind, task) +
    line() +
    line(dt("demo.082859507c", { value1: b })) +
    line(`${b} ${BOLD}Read${RESET}${DIM}(src/) ⎿ 47 files${RESET}`) +
    line(`${b} ${BOLD}Grep${RESET}${DIM}(coupon) ⎿ 214 matches${RESET}`) +
    line(dt("demo.bc27d8ce3a", { value1: b })) +
    line(`${b} ${BOLD}Bash${RESET}${DIM}(npm test -- --runInBand)${RESET}`) +
    line(`  ${DIM}⎿ running…${RESET}`) +
    line()
  )
}

/** Freshly spawned pane: shorter banner, straight to the composer. */
export function spawnScrollback(info: ComposerInfo): string {
  if (info.kind === 'claude') {
    return (
      line(dt("demo.44f094a1c4", { value1: ORANGE, value2: RESET, value3: BOLD, value4: RESET, value5: DIM, value6: RESET })) +
      line()
    )
  }
  if (info.kind === 'codex') {
    return (
      line(dt("demo.dddc26f6ec", { value1: CYAN, value2: RESET, value3: BOLD, value4: RESET, value5: DIM, value6: RESET })) +
      line()
    )
  }
  // A shell has no composer, and that contrast is worth keeping: it echoes at a
  // prompt exactly as the real one does.
  return line(dt("demo.cde7248d36", { value1: DIM, value2: RESET })) + line() + SHELL_PROMPT
}

// ---------------------------------------------------------------- responders

export type ReplyTool = { id: string; name: string; input?: unknown }

export type Reply = {
  chunks: string[]
  /** ms between chunks */
  pace: number
  /**
   * The same reply as prose, and the tool calls inside it.
   *
   * The drawer's Transcript tab reads merged messages rather than bytes, so a demo
   * that only produced ANSI would leave that tab permanently one turn behind the
   * pane beside it. Deriving both from one authored body is what stops the two
   * surfaces telling different stories about the same turn.
   */
  plain: string
  tools: ReplyTool[]
}

/** Placeholder dialect (`●`, `§bold§`, `¶dim¶`) removed, for the transcript reader. */
const unpaint = (text: string): string =>
  text.replace(/^● /, '').replace(/[§¶]/g, '').trimEnd()

/** Native tool calls an authored body performs, read off its `§Name§(args)` lines. */
function replyTools(body: string[], prefix: string): ReplyTool[] {
  const tools: ReplyTool[] = []
  body.forEach((text, index) => {
    const match = /^●\s+§([A-Za-z]+)§\(([^)]*)\)/.exec(text)
    if (match) tools.push({ id: `${prefix}:${index}`, name: match[1], input: match[2] })
  })
  return tools
}

/** The prose half of an authored body: the lines that are not a tool invocation. */
const replyProse = (body: string[]): string =>
  body
    .filter(text => !/^●\s+§[A-Za-z]+§\(/.test(text) && !/^\s*¶/.test(text))
    .map(unpaint)
    .join('\n')
    .trim()

/** Agent replies, written entirely out of 2026's most-complained-about tells. */
const AGENT_EXAMPLES: string[][] = [
  [dt("demo.6de1fc1498"), dt("demo.cf4e1e5278"), dt("demo.beb93e4f28")],
  [dt("demo.53472eb63f"), dt("demo.8b13d283f4")],
]

const BUSY_REPLIES: string[][] = [[dt("demo.eaa557dbc1"), dt("demo.ee64b32e44")]]

const SHELL_CANNED: Record<string, string[]> = {
  'git status': [
    dt("demo.f38f6a8c0a", { value1: GREEN, value2: RESET }),
    dt("demo.6d6e51eec6", { value1: DIM, value2: RESET }),
  ],
  ls: ['README.md   package.json   src/   tests/   coupons-since-2019.json'],
  dir: ['README.md   package.json   src/   tests/   coupons-since-2019.json'],
  'npm test': [
    `${DIM}> rocket-shop@1.0.0 test${RESET}`,
    '',
    dt("demo.e9ad59d838", { value1: GREEN, value2: RESET, value3: DIM, value4: RESET }),
    dt("demo.792af1b604", { value1: GREEN, value2: RESET, value3: DIM, value4: RESET }),
    dt("demo.e609db8500", { value1: GREEN, value2: RESET, value3: DIM, value4: RESET }),
    '',
    `${GREEN}12 passing${RESET}`,
  ],
  // The preview scenario's own command. A listener is the one thing a shell can produce
  // that the rest of swe-mux reacts to, so the banner names the port the demo's invented
  // dev-server process is already reported on (`fleetFixtures.ts`) rather than a new one.
  'npm run dev': [
    `${DIM}> rocket-shop@1.0.0 dev${RESET}`,
    '',
    `  ${GREEN}ready${RESET} in 412 ms`,
    '',
    `  ${BOLD}local${RESET}   http://127.0.0.1:5173/`,
    dt("demo.5fef7ea6e4", { value1: DIM, value2: RESET }),
  ],
  whoami: ['demo-user'],
  pwd: ['/code/rocket-shop'],
  uptime: [dt("demo.64d99b49fc")],
  sudo: [dt("demo.e112d55fb4")],
  vim: [dt("demo.6e9fceaac1")],
}

function shellReply(command: string): string[] {
  const trimmed = command.trim()
  if (!trimmed) return []
  const canned = SHELL_CANNED[trimmed] ?? SHELL_CANNED[trimmed.split(/\s+/)[0]]
  if (canned) return canned
  return [dt("demo.f62cd76c05", { value1: trimmed.split(/\s+/)[0], value2: DIM, value3: RESET })]
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

// Where the joke pools start, so two visitors in a row do not read the same first reply.
// The demo's own stream (`determinism.ts`) rather than the global one: which joke a pane
// tells ends up in the scrollback and in the Transcript tab, so it is fixture data, and a
// capture has to be able to reproduce it.
let exampleCursor = Math.floor(demoRandom() * AGENT_EXAMPLES.length)
let busyCursor = Math.floor(demoRandom() * BUSY_REPLIES.length)

/** The refusal a busy pane answers with, instead of running the responder. */
export function busyReply(kind: DemoBackendKind): Reply {
  busyCursor = (busyCursor + 1) % BUSY_REPLIES.length
  const source = BUSY_REPLIES[busyCursor]
  const body = source.map(text => line(paint(kind, text)))
  return {
    chunks: [line(), ...body, line()],
    pace: 160,
    plain: replyProse(source),
    tools: [],
  }
}

/**
 * A reply somebody wrote, painted for a harness.
 *
 * Same derivation as the joke pool: one authored body in the placeholder dialect yields
 * the ANSI the pane streams, the prose the Transcript tab reads, and the tool calls
 * between them. Scenarios hand their lines here rather than assembling escape sequences,
 * so a scripted turn cannot end up telling the reader a different story from the pane.
 */
export function authoredReply(kind: DemoBackendKind, body: string[], pace = 190): Reply {
  return {
    chunks: [line(), ...body.map(text => line(paint(kind, text))), line()],
    pace,
    plain: replyProse(body),
    tools: replyTools(body, `scripted-${body.length}`),
  }
}

export function buildReply(kind: DemoBackendKind, input: string): Reply {
  if (kind === 'shell') {
    const body = shellReply(input)
    return {
      chunks: [...body.map(text => line(text)), promptFor(kind)],
      pace: 30,
      plain: body.map(text => bare(text)).join('\n'),
      tools: [],
    }
  }
  exampleCursor = (exampleCursor + 1) % AGENT_EXAMPLES.length
  const source = AGENT_EXAMPLES[exampleCursor]
  const joke = source.map(text => line(paint(kind, text)))
  // No trailing prompt: an agent pane's caller redraws the composer once the
  // last chunk has landed, which is what puts the box back under the reply.
  return {
    chunks: [line(), ...joke, line()],
    pace: 220,
    plain: replyProse(source),
    tools: replyTools(source, `joke-${exampleCursor}`),
  }
}

/**
 * Per-session line editing state. Echo is decided here so backspace behaves;
 * the caller owns writing the returned echo bytes and firing the responder.
 */
export type LineState = { buffer: string; cursor?: number; pending?: string; pasting?: boolean }

export type InputResult = { echo: string; submitted: string | null }

export function consumeInput(stateRef: LineState, data: string): InputResult {
  let echo = ''
  let submitted: string | null = null
  let cursor = Math.min(stateRef.cursor ?? stateRef.buffer.length, stateRef.buffer.length)
  const insert = (text: string): void => {
    stateRef.buffer = stateRef.buffer.slice(0, cursor) + text + stateRef.buffer.slice(cursor)
    cursor += text.length
    echo += text
  }
  const back = (): number => Math.max(0, cursor - (graphemes(stateRef.buffer.slice(0, cursor)).at(-1)?.length ?? 1))
  const eraseWord = (): void => {
    const prefix = stateRef.buffer.slice(0, cursor)
    const start = prefix.replace(/\S+\s*$/, '').length
    stateRef.buffer = prefix.slice(0, start) + stateRef.buffer.slice(cursor)
    cursor = start
    echo += '\b \b'
  }
  const input = (stateRef.pending ?? '') + data
  stateRef.pending = ''
  for (let i = 0; i < input.length;) {
    if (input[i] === '\x1b') {
      const rest = input.slice(i)
      if (rest === '\x1b' || /^\x1b\[[0-9;?]*$/.test(rest)) { stateRef.pending = rest; break }
      const csi = /^\x1b\[([0-9;?]*)([A-Za-z~])/.exec(rest)
      if (csi) {
        const [raw, parameters, key] = csi
        if (raw === '\x1b[200~') stateRef.pasting = true
        else if (raw === '\x1b[201~') stateRef.pasting = false
        else if (key === 'u' && /^13;(2|5)$/.test(parameters)) insert('\n')
        else if (key === 'u' && parameters === '127;5') eraseWord()
        else if (key === 'D') { cursor = back(); echo += raw }
        else if (key === 'C') { cursor = Math.min(stateRef.buffer.length, cursor + (graphemes(stateRef.buffer.slice(cursor))[0]?.length ?? 0)); echo += raw }
        else if (key === 'H') { cursor = 0; echo += raw }
        else if (key === 'F') { cursor = stateRef.buffer.length; echo += raw }
        else if (raw === '\x1b[3~') { stateRef.buffer = stateRef.buffer.slice(0, cursor) + stateRef.buffer.slice(cursor + (graphemes(stateRef.buffer.slice(cursor))[0]?.length ?? 0)); echo += raw }
        i += raw.length; continue
      }
      if (rest.startsWith('\x1b\r')) insert('\n')
      else if (rest.startsWith('\x1b\x7f') || rest.startsWith('\x1b\b')) eraseWord()
      i += 2; continue
    }
    const char = String.fromCodePoint(input.codePointAt(i)!)
    i += char.length
    if (stateRef.pasting) { insert(char === '\r' ? '\n' : char); continue }
    if (char === '\r') {
      if (submitted === null) { submitted = stateRef.buffer; stateRef.buffer = ''; cursor = 0; echo += CRLF }
    } else if (char === '\n') insert('\n')
    else if (char === '\x17') eraseWord()
    else if (char === '\x7f' || char === '\b') {
      const start = back()
      stateRef.buffer = stateRef.buffer.slice(0, start) + stateRef.buffer.slice(cursor)
      cursor = start; echo += '\b \b'
    } else if (char === '\x03') {
      stateRef.buffer = ''; cursor = 0; echo += `^C${CRLF}`; submitted = submitted ?? ''
    } else if (char === '\x01') { cursor = 0; echo += '\r' }
    else if (char === '\x05') { cursor = stateRef.buffer.length; echo += '\r' }
    else if (char >= ' ' || char === '\t') insert(char)
  }
  stateRef.cursor = cursor
  return { echo, submitted }
}
