/**
 * What a Preview pane says when its port is held by more than one process.
 *
 * On Windows a server that sets `SO_REUSEADDR` - every Python standard-library server
 * does - binds a port another process already listens on without any error, and the
 * kernel then hands each connection to one of them. The server started second runs
 * perfectly and never receives a request, so its Preview shows the other process's
 * page with nothing on screen to say so (2026-09-26: a task's app rendered an orphaned
 * `http.server`'s directory listing). The daemon reports every listener and which one
 * accepted a probe connection; this turns that into the words the pane draws.
 *
 * Pure, and free of `.tsx` imports, so the node test runner can load it.
 */

import type { PortConflict, PortConflictListener } from './processFleet'

export type PortConflictSeverity = 'wrong_server' | 'shared' | 'unknown'

export type PortConflictNotice = {
  severity: PortConflictSeverity
  headline: string
  remedy: string
  listeners: Array<{ pid: number; label: string; detail: string; answering: boolean; owner: boolean }>
}

const COMMAND_CHARS = 120

const describeProcess = (listener: PortConflictListener): string => {
  const command = listener.command || listener.executable || 'unknown process'
  return command.length > COMMAND_CHARS ? `${command.slice(0, COMMAND_CHARS - 1)}…` : command
}

const describeRelation = (listener: PortConflictListener): string => {
  if (listener.owner) return "this preview's session"
  if (listener.evidence_state === 'suspected_orphan' || listener.evidence_state === 'escaped') {
    return 'left behind by a session that has ended'
  }
  if (listener.session_id) return 'another session'
  return 'not started by any live swe-mux session'
}

/** The notice for a shared port, or `null` when there is nothing to say. */
export function portConflictNotice(conflict: PortConflict | null | undefined): PortConflictNotice | null {
  if (!conflict || conflict.listeners.length < 2) return null
  const port = conflict.port
  const others = conflict.listeners.filter(item => !item.owner).length
  const answering = conflict.listeners.find(item => item.pid === conflict.answering_pid)
  let severity: PortConflictSeverity
  let headline: string
  if (conflict.answering_is_owner === false && answering) {
    severity = 'wrong_server'
    headline = `Port ${port} is shared - this preview is showing PID ${answering.pid} (${answering.executable || 'unknown'}), not this session's server.`
  } else if (conflict.answering_is_owner === true) {
    severity = 'shared'
    headline = `Port ${port} is also held by ${others} other process${others === 1 ? '' : 'es'}. This preview reaches this session's server now, but the OS chooses per connection.`
  } else {
    severity = 'unknown'
    headline = `Port ${port} is held by ${conflict.listeners.length} processes, and swe-mux could not tell which one this preview reaches.`
  }
  return {
    severity,
    headline,
    remedy: 'Stop the other process, or run this server on a free port. On Windows a Python server can bind a port that is already in use without reporting an error.',
    listeners: conflict.listeners.map(item => ({
      pid: item.pid,
      label: describeProcess(item),
      detail: describeRelation(item),
      answering: item.pid === conflict.answering_pid,
      owner: item.owner,
    })),
  }
}
