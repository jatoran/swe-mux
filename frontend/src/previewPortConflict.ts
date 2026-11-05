import { t } from './localization/index.ts'
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
  const command = listener.command || listener.executable || t("previewPortConflict.5e695195cb")
  return command.length > COMMAND_CHARS ? `${command.slice(0, COMMAND_CHARS - 1)}…` : command
}

const describeRelation = (listener: PortConflictListener): string => {
  if (listener.owner) return t("previewPortConflict.9cca661d57")
  if (listener.evidence_state === 'suspected_orphan' || listener.evidence_state === 'escaped') {
    return t("previewPortConflict.87b31a7fb3")
  }
  if (listener.session_id) return t("previewPortConflict.d885d2d3c4")
  return t("previewPortConflict.c1a5e637b0")
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
    headline = t("previewPortConflict.52d815d5cd", { value1: port, value2: answering.pid, value3: answering.executable || 'unknown' })
  } else if (conflict.answering_is_owner === true) {
    severity = 'shared'
    headline = t("previewPortConflict.507f001f17", { value1: port, value2: others, value3: (others === 1) ? 'yes' : 'no' })
  } else {
    severity = 'unknown'
    headline = t("previewPortConflict.2ba3893eb1", { value1: port, value2: conflict.listeners.length })
  }
  return {
    severity,
    headline,
    get remedy() { return t("previewPortConflict.da3cc55509") },
    listeners: conflict.listeners.map(item => ({
      pid: item.pid,
      label: describeProcess(item),
      detail: describeRelation(item),
      answering: item.pid === conflict.answering_pid,
      owner: item.owner,
    })),
  }
}
