// The two Git readings under the conditions that broke them, which the shared
// `gitMapHarness` cannot produce:
//
//  - A Map row that is genuinely removable (the shared harness's worktree is locked and
//    prunable, so it draws a refusal where the Remove button goes) and whose per-checkout
//    read is *slow*, so the "Reading this worktree…" state is observable at all rather
//    than resolving inside a microtask.
//  - A Log with long subjects, deep lane art, and long ref names - the shape that made
//    the graph wider than its pane. Two short commits on one lane cannot reproduce it.

import { render } from 'preact'
import { useState } from 'preact/hooks'
import { GitTab, type GitView } from '../../src/GitTab'
import { DrawerSegmentControl } from '../../src/DrawerSegmentControl'
import type { Project, Session } from '../../src/types'
import '../../src/style.css'

const project = { id: 'swe-mux', name: 'swe-mux', root: 'D:\\PROJECTS\\swe-mux' } as Project
const response = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })

const worktree = 'D:\\PROJECTS\\swe-mux\\.claude\\worktrees\\git-map-latency'
const worktreeHead = 'c6824e7d0123456789abcdef0123456789abcdef'
const mainHead = '9299950aa1bb2cc3dd4ee5ff6001122334455667'
const clean = { total: 0, additions: 0, deletions: 0, binary_files: 0, files: [], truncated: false }
const counted = (total: number, omitted: boolean) => ({
  total, additions: total * 3, deletions: total, binary_files: 0,
  files: omitted ? [] : Array.from({ length: total }, (_, index) => ({
    path: `frontend/src/file-${index}.ts`, status: 'M', additions: 3, deletions: 1, binary: false, submodule: false,
  })),
  truncated: false, ...(omitted ? { files_omitted: true } : {}),
})

/** How long the per-checkout read is held, so a spec can see the placeholder. */
const DETAIL_DELAY_MS = 1500
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

const row = (omitted: boolean) => ({
  worktree, HEAD: worktreeHead, branch: 'refs/heads/worktree-git-map-latency',
  detached: false, bare: false, locked: null, prunable: null, main: false,
  head_committed_at: 1786800000,
  comparison_counts: { ahead: 4, behind: 0 },
  conflicted: clean, unstaged: counted(6, omitted), staged: clean,
  branch_delta: counted(9, omitted),
})

const mainRow = {
  worktree: project.root, HEAD: mainHead, branch: 'refs/heads/master',
  detached: false, bare: false, locked: null, prunable: null, main: true,
  head_committed_at: 1786700000,
  comparison_counts: { ahead: 0, behind: 0 },
  conflicted: clean, unstaged: clean, staged: clean, branch_delta: clean,
}

const overview = (omitted: boolean) => ({
  repository: { root: project.root, common_dir: 'D:\\PROJECTS\\swe-mux\\.git' },
  comparison: {
    ref: 'master', display: 'master', source: 'local_fallback',
    available: true, reason: null, candidates: ['master'],
  },
  worktrees: [mainRow, row(omitted)],
  detail: omitted ? 'summary' : 'full',
})

// Lane art as deep as a fleet of parallel worktrees actually draws, and subjects as long
// as this repository's own commit messages get.
const LONG = 'Stop the Git tab waiting on Git it has already measured, and say so in the log'
const graphLines = [
  { kind: 'commit', graph: '* | | | | | | ', oid: worktreeHead, parents: [mainHead], refs: ['worktree-git-map-latency', 'origin/worktree-git-map-latency'], author: 'jatoran', committed_at: 1786800000, subject: LONG },
  { kind: 'commit', graph: '|\\ \\ \\ \\ \\ \\ ', oid: 'a'.repeat(40), parents: [mainHead], refs: [], author: 'jatoran', committed_at: 1786790000, subject: `Merge branch 'master' into worktree-queue-readiness-live` },
  { kind: 'commit', graph: '* | | | | | | ', oid: mainHead, parents: [], refs: ['HEAD', 'master'], author: 'jatoran', committed_at: 1786700000, subject: 'Say in the Queue tab whether a target will take a message, and why not' },
]

globalThis.fetch = async input => {
  const url = String(input)
  if(url.startsWith('/api/git/swe-mux-setup?'))return response({show:false,reason:'decided',decision:'keep_visible',can_ignore:false,tracked:false})
  if (url.startsWith('/api/git/worktrees?')) {
    // The Map asks for `detail=summary`; an expanded row asks for one checkout in full,
    // and that is the read this harness holds open.
    if (url.includes('detail=full')) {
      await sleep(DETAIL_DELAY_MS)
      return response({ ...overview(false), worktrees: [row(false)] })
    }
    return response(overview(true))
  }
  if (url.startsWith('/api/git/graph?')) return response({ lines: graphLines, limit: 80, has_more: false })
  if (url.startsWith('/api/git/provenance?')) return response({
    items: [{
      id: 'p-live', session_id: 'session', session_name: 'claude-0e7d93',
      display_name: 'Git map latency', history_id: null, agent_run_id: 'run-live',
      project_id: project.id, worktree_root: worktree,
      commit_oid: worktreeHead, parent_oids: [mainHead], subject: LONG, committed_at: 1786800000,
      relationship: 'created', confidence: 'exact', ambiguous: false, role: 'committer',
      match_method: 'command_range', contributed_paths: ['src/swe_mux/git_review.py'],
      source: 'session_tool', observed_at: 1786800001,
    }],
  })
  if (url.startsWith('/api/land/verify-command')) return response({
    configured: true, source: 'convention', display: '.worktree-verify', digest: 'd1',
    approved: true, previously_approved: true,
    approved_source: '#!/usr/bin/env bash\nexit 0\n', current_source: '#!/usr/bin/env bash\nexit 0\n',
    config_command: '', config_revision: 'r1', config_status: 'ready',
    config_path: 'D:\\PROJECTS\\swe-mux\\.swe-mux\\config.toml',
    script_name: '.worktree-verify', script_present: true, plan: null,
  })
  if (url.startsWith('/api/land')) return response({
    requests: [], hourly_budget: 12, hold_timeout_seconds: 1800, retry_verification: false,
    installed_enabled: true, project_enabled: true, agent_grant: 'draft',
  })
  if (url.startsWith('/api/projects/')) return response({ enabled: [] })
  throw new Error(`Unexpected harness request: ${url}`)
}

// No live session in this checkout: a worktree with occupants refuses removal, and the
// Remove button is what these specs are placing.
const session = {
  id: 'session', name: 'claude-0e7d93', project_id: project.id,
  state: 'running', cwd: project.root, runtime_cwd_live: false,
} as Session

function GitHarness() {
  const [view, setView] = useState<GitView>('map')
  return <aside class="utility-drawer" style="width:100%;height:100dvh;display:flex;flex-direction:column">
    <DrawerSegmentControl
      tab="git"
      active={view}
      context={{ hasTranscript: true, isAgentSession: true }}
      onSelect={id => setView(id as GitView)}
    />
    <GitTab
      view={view} onView={setView}
      project={project} sessions={[session]}
      onOpenFile={() => undefined} onOpenWorktreeFile={() => undefined}
      onProjectUpdated={() => undefined}
      onOpenSession={() => undefined} onOpenHistory={() => undefined}
    />
  </aside>
}

render(<GitHarness />, document.querySelector('#root')!)
