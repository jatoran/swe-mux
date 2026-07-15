export type ListenerLike = { host: string; port: number; loopback: boolean; url: string }
export type ProcessWithListeners = { pid: number; exited_at?: number; listeners?: ListenerLike[] }
export type DetectedServer = { pid: number; host: string; port: number; url: string }

/**
 * The servers a session is actually running, derived from its process snapshot.
 *
 * Listening on a port is the only reliable signal that a child is a *server*. An
 * agent's process tree is otherwise mostly bookkeeping (`cmd`, `python`,
 * `claude.exe`), and no amount of age or liveness filtering separates those from
 * something worth surfacing, so this keys on listeners alone.
 *
 * Only loopback listeners are returned: a preview can bridge nothing else, so a
 * non-loopback listener would be an unactionable row. (The daemon reports a wildcard
 * bind at its loopback address, so a server on 0.0.0.0 arrives here as 127.0.0.1.)
 * Results are deduped by port, preferring the IPv4 loopback when a server binds both
 * stacks because it is the more broadly reachable of the two, and ordered by port so
 * rows do not move between samples.
 */
export function detectedServers<T extends ProcessWithListeners>(processes: T[]): DetectedServer[] {
  const byPort = new Map<number, DetectedServer>()
  for (const process of processes) {
    if (process.exited_at) continue
    for (const listener of process.listeners || []) {
      if (!listener.loopback) continue
      if (byPort.get(listener.port)?.host === '127.0.0.1') continue
      byPort.set(listener.port, {
        pid: process.pid,
        host: listener.host,
        port: listener.port,
        url: listener.url,
      })
    }
  }
  return [...byPort.values()].sort((first, second) => first.port - second.port)
}
