/**
 * The row model behind the modal process inspector.
 *
 * The inspector is the *act* half of process inspection, and it has to carry everything an
 * irreversible action needs to be safe: the full command line, the evidence that attributed the
 * process to a session, the confidence in that evidence, and when it was last verified. Printed
 * unconditionally that is five or six lines per process, so a session with a shell, a conhost,
 * and an agent CLI filled a screen with text nobody reads until the moment they are about to
 * kill something.
 *
 * So the tree draws **one line per process** — identity, live cost, and anything abnormal — and
 * everything else moves behind that line's expander. The rule for what stays on the line is
 * "what you scan for": which process it is, what it is costing, and whether it is in a state you
 * did not expect. The rule for what expands is "what you read before acting". Nothing is
 * dropped; the actions themselves live behind the expander too, which is deliberate, because
 * the evidence you should have read is then on screen at the moment you press Terminate.
 *
 * The collapsed line also stops repeating what its surroundings already say. The executable name
 * is stripped from the command tail, the parent PID is a detail rather than a column because the
 * tree already draws that edge, and `active` — the state of nearly every row — is a coloured dot
 * rather than a badge, so a badge appearing at all means something is wrong.
 *
 * Pure, so the stripping, the abnormal-state rule, and the rollup-suppression rule are testable
 * without a DOM or a daemon.
 */

export type RowListener = { url: string; port: number; host?: string; loopback?: boolean }
export type RowConnection = { remote_host: string; remote_port: number }

/** Structural, so the inspector's richer `ProcessItem` satisfies it without this module
 *  importing a `.tsx` the node test runner cannot load. */
export type RowProcess = {
  pid: number
  parent_pid?: number
  executable: string
  command: string
  started_at?: number
  exited_at?: number
  cpu_pct: number
  memory_bytes: number
  listeners?: RowListener[]
  connections?: RowConnection[]
  conditions?: string[]
  identity_id?: string
  command_hash?: string
  job_assignment?: string
  evidence_state?: string
  evidence_reason?: string
  confidence?: string
  first_seen?: number
  last_seen?: number
  last_verified_at?: number
  exit_evidence?: string
  attribution_version?: number
  attribution_source?: string
}

export type ProcessDetail = { label: string; value: string; title?: string }

export type SessionRollup = {
  processes: number
  cpu_pct: number
  memory_bytes: number
  listeners: number
  connections: number
}

export const memoryLabel = (bytes: number) =>
  bytes >= 1073741824 ? `${(bytes / 1073741824).toFixed(1)} GiB` : `${(bytes / 1048576).toFixed(1)} MiB`

/** Stable across samples, so an expanded row stays expanded through the 2 s refresh. A PID
 *  alone is not stable enough — it is reused — which is why identity wins when it exists. */
export const processRowKey = (process: RowProcess) =>
  process.identity_id || `${process.pid}:${process.started_at || 0}`

export const processState = (process: RowProcess) =>
  process.evidence_state || (process.exited_at ? 'exited' : 'active')

/** `active` is the expected state of nearly every row, so only the others earn a badge. An
 *  `exited` row is already drawn as history, and repeating it as an alarm reads as a problem. */
export const isAbnormalState = (state: string) => state !== 'active' && state !== 'exited'

const commandHead = (command: string) => {
  if (command.startsWith('"')) {
    const close = command.indexOf('"', 1)
    return close === -1 ? command : command.slice(0, close + 1)
  }
  const space = command.indexOf(' ')
  return space === -1 ? command : command.slice(0, space)
}

const baseName = (path: string) => path.replace(/^"|"$/g, '').split(/[\\/]/).pop() || ''

/**
 * The command with its own executable stripped off the front.
 *
 * A row that reads `claude.exe · claude.exe --session-id …` spends its first third saying what
 * the strong text beside it already said, and the arguments — the only part that distinguishes
 * one `node.exe` from another — are the part that gets truncated away.
 */
export function commandTail(process: RowProcess): string {
  const command = (process.command || '').trim()
  if (!command) {
    return process.command_hash ? `fingerprint ${process.command_hash.slice(0, 12)}` : 'command unavailable'
  }
  const head = commandHead(command)
  if (baseName(head).toLowerCase() !== (process.executable || '').toLowerCase()) return command
  return command.slice(head.length).trim() || command
}

/**
 * The live cost of one process, at a width that survives a phone.
 *
 * Zero listeners and zero connections is the normal case, so `net 0L/0C` is a fixed-width way of
 * saying nothing; the counts appear only when there is something to see.
 */
export function processMetrics(process: RowProcess): string {
  const parts = [`${process.cpu_pct.toFixed(1)}%`, memoryLabel(process.memory_bytes)]
  const listeners = process.listeners?.length || 0
  const connections = process.connections?.length || 0
  if (listeners || connections) parts.push([listeners && `${listeners}L`, connections && `${connections}C`].filter(Boolean).join('/'))
  return parts.join(' · ')
}

const NETWORK_SAMPLE = 4

const networkDetail = (process: RowProcess): string => {
  const listeners = process.listeners || []
  const connections = process.connections || []
  const parts: string[] = []
  if (listeners.length) {
    const shown = listeners.slice(0, NETWORK_SAMPLE).map(listener => listener.url || `:${listener.port}`)
    parts.push(`listening ${shown.join(', ')}${listeners.length > NETWORK_SAMPLE ? ` +${listeners.length - NETWORK_SAMPLE} more` : ''}`)
  }
  if (connections.length) {
    const shown = connections.slice(0, NETWORK_SAMPLE).map(item => `${item.remote_host}:${item.remote_port}`)
    parts.push(`connected ${shown.join(', ')}${connections.length > NETWORK_SAMPLE ? ` +${connections.length - NETWORK_SAMPLE} more` : ''}`)
  }
  return parts.join(' · ')
}

const defaultTime = (epoch: number) => new Date(epoch * 1000).toLocaleString()

/**
 * Everything the collapsed line left out, in the order you read it before acting: what it is,
 * where it came from, why we believe it belongs to this session, and how fresh that belief is.
 *
 * Empty fields are omitted rather than rendered as `—`, because a labelled blank costs a line
 * and tells you the same thing as its absence.
 */
export function processDetails(process: RowProcess, formatTime: (epoch: number) => string = defaultTime): ProcessDetail[] {
  const details: ProcessDetail[] = []
  const command = (process.command || '').trim()
  details.push(command
    ? { label: 'command', value: command }
    : { label: 'command', value: process.command_hash ? `fingerprint ${process.command_hash.slice(0, 12)}` : 'unavailable' })
  details.push({ label: 'parent', value: process.parent_pid ? `PID ${process.parent_pid}` : 'none observed' })
  details.push({
    label: 'evidence',
    value: `${process.evidence_reason || 'live observation'} · confidence ${process.confidence || 'high'}`,
  })
  details.push({
    label: 'attribution',
    value: `${process.attribution_source || 'unknown'} v${process.attribution_version || 1} · ${process.job_assignment || 'job assignment unknown'}`,
  })
  const verified = process.last_verified_at || process.last_seen
  if (verified) details.push({ label: 'checked', value: formatTime(verified) })
  if (process.first_seen) {
    details.push({
      label: 'seen',
      value: `first ${formatTime(process.first_seen)} · last ${formatTime(process.last_seen || process.first_seen)}`,
    })
  }
  if (process.exited_at) {
    details.push({
      label: 'exited',
      value: `${formatTime(process.exited_at)}${process.exit_evidence ? ` · ${process.exit_evidence}` : ''}`,
    })
  }
  const network = networkDetail(process)
  if (network) details.push({ label: 'network', value: network })
  const conditions = process.conditions || []
  if (conditions.length) details.push({ label: 'warnings', value: conditions.join(' · ') })
  return details
}

/**
 * The per-session rollup, or `null` when it would only restate the single row beneath it.
 *
 * A heading reading `1 proc · CPU 2.5% · 571.0 MiB` above a row reading `2.5% · 571.0 MiB` is
 * the same measurement printed twice; the rollup earns its place only once it aggregates.
 */
export function sessionRollup(processes: RowProcess[]): SessionRollup | null {
  const live = processes.filter(process => !process.exited_at)
  if (live.length < 2) return null
  return {
    processes: live.length,
    cpu_pct: live.reduce((total, process) => total + process.cpu_pct, 0),
    memory_bytes: live.reduce((total, process) => total + process.memory_bytes, 0),
    listeners: live.reduce((total, process) => total + (process.listeners?.length || 0), 0),
    connections: live.reduce((total, process) => total + (process.connections?.length || 0), 0),
  }
}

export function rollupLabel(rollup: SessionRollup): string {
  const parts = [
    `${rollup.processes} proc`,
    `CPU ${rollup.cpu_pct.toFixed(1)}%`,
    memoryLabel(rollup.memory_bytes),
  ]
  if (rollup.listeners || rollup.connections) {
    parts.push([rollup.listeners && `${rollup.listeners}L`, rollup.connections && `${rollup.connections}C`].filter(Boolean).join('/'))
  }
  return parts.join(' · ')
}
