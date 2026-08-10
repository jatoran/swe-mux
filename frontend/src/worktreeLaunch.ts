const WINDOWS_RESERVED = /[<>:"/\\|?*\u0000-\u001f]+/g

export function normalizeWorktreeBranchInput(value: string): string {
  return value.replace(/\s+/g, '-')
}

function safeLeaf(value: string, fallback: string): string {
  return value
    .trim()
    .replace(WINDOWS_RESERVED, '-')
    .replace(/[\s.]+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    || fallback
}

export function worktreePathForBranch(
  worktreeRoot: string,
  projectName: string,
  projectId: string,
  branch: string,
): string {
  const root = worktreeRoot.trim().replace(/[\\/]+$/, '')
  if (!root) return ''
  const separator = root.includes('\\') && !root.includes('/') ? '\\' : '/'
  const project = safeLeaf(projectName, 'project')
  const identity = safeLeaf(projectId, 'unknown').slice(0, 8).replace(/-+$/, '') || 'unknown'
  const branchLeaf = safeLeaf(branch, 'worktree')
  return [root, `${project}-${identity}`, branchLeaf].join(separator)
}
