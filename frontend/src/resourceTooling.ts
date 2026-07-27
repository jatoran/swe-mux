import type { ResourceSnapshot } from './resourceTotals'

/**
 * Language servers are per-session, so N agent sessions open on one repo run N
 * independent indexes of the same code. On a four-session project that was ~1.35 GiB of
 * the 3.3 GiB total -- the largest single line item, and invisible in a flat process
 * list because every copy looks like an ordinary `node.exe` child of its own session.
 * Naming the duplication is the whole point: nothing here reaps anything, it just makes
 * the cost of another concurrent session on the same repo legible before it is paid.
 */
const TOOL_PATTERNS: Array<[RegExp, string]> = [
  [/basedpyright/i, 'basedpyright'],
  [/pyright-?langserver|pyright[\\/]langserver/i, 'pyright'],
  [/typescript-language-server/i, 'typescript-language-server'],
  [/typescript[\\/]lib[\\/]tsserver\.js/i, 'tsserver'],
  [/typingsinstaller/i, 'tsserver typings installer'],
  [/rust-analyzer/i, 'rust-analyzer'],
  [/(^|[\\/\s])gopls(\.exe)?($|\s)/i, 'gopls'],
  [/(^|[\\/\s])clangd(\.exe)?($|\s)/i, 'clangd'],
  [/python-lsp-server|(^|[\\/\s])pylsp($|\s)/i, 'pylsp'],
  [/vscode-(html|css|json|eslint|markdown)-language-server/i, 'vscode language server'],
  [/jdt\.ls|jdtls/i, 'jdtls'],
  [/(^|\s)ruff(\.exe)?\s+server/i, 'ruff server'],
  [/(^|[\\/\s])lua-language-server/i, 'lua-language-server'],
]

export type ToolingGroup = {
  tool: string
  instances: number
  sessions: number
  memory_bytes: number
  /** Memory beyond a single shared instance: what the duplication itself costs. */
  duplicate_bytes: number
}

export const classifyTooling = (command: string): string | null => {
  for (const [pattern, tool] of TOOL_PATTERNS) if (pattern.test(command)) return tool
  return null
}

/**
 * Tooling that more than one session of the same project is running concurrently.
 * A tool confined to a single session is not duplication and is never listed.
 */
export function duplicateToolingGroups(
  snapshot: ResourceSnapshot | null,
  projectId?: string,
): ToolingGroup[] {
  const groups = new Map<string, { sessions: Set<string>; memory: number[] }>()
  for (const group of snapshot?.sessions || []) {
    if (projectId && group.project_id !== projectId) continue
    for (const process of group.processes || []) {
      if (process.exited_at) continue
      const tool = classifyTooling(String(process.command || ''))
      if (!tool) continue
      const entry = groups.get(tool) || { sessions: new Set<string>(), memory: [] }
      entry.sessions.add(group.session_id)
      entry.memory.push(Number(process.memory_bytes) || 0)
      groups.set(tool, entry)
    }
  }
  return [...groups.entries()]
    .filter(([, entry]) => entry.sessions.size > 1)
    .map(([tool, entry]) => {
      const total = entry.memory.reduce((sum, value) => sum + value, 0)
      // One instance would still be paid for if the sessions shared it, so only the
      // remainder is attributable to running them separately. Using the largest
      // instance as the retained one keeps the estimate conservative.
      const retained = entry.memory.length ? Math.max(...entry.memory) : 0
      return {
        tool,
        instances: entry.memory.length,
        sessions: entry.sessions.size,
        memory_bytes: total,
        duplicate_bytes: Math.max(0, total - retained),
      }
    })
    .sort((left, right) => right.duplicate_bytes - left.duplicate_bytes)
}
