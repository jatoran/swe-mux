/**
 * The demo's repository: the worktree map, the commit graph, and the provenance
 * ledger the Git drawer tab draws.
 *
 * One invented history serves all three readings, which is the point - Map, Log and
 * Provenance are three projections of the same commits, and fixtures that disagreed
 * would demonstrate the opposite of what the tab is for. The occupancy column is
 * computed from the live demo sessions rather than written down, so a session the
 * visitor kills leaves its checkout, exactly as it would in the product.
 *
 * Invented, like every demo fixture: no oid, path, name or subject is copied from a
 * real repository.
 */
import {
  DEMO_PROJECT2_ID, DEMO_ROOT, DEMO_ROOT2,
  DEMO_WORKTREE_COUPON, DEMO_WORKTREE_PROFILE,
} from './fixtures.ts'
import { nowSeconds, state } from './store.ts'

const HOUR = 3600

type Commit = {
  oid: string
  parents: string[]
  refs: string[]
  author: string
  /** Hours before "now". */
  age: number
  subject: string
  /** ASCII lane prefix, exactly as `git log --graph` emits it. */
  graph: string
  /** Which checkout the work was done in, for the provenance ledger. */
  worktree: string
  /** Demo session id that made it, or '' for a commit nothing here authored. */
  session: string
  files: Array<{ path: string; status: string; additions: number; deletions: number }>
}

/**
 * The invented history, newest first.
 *
 * Two agent branches off `master`, one of them already merged, which is what gives
 * Log a real fork to draw and Provenance a merge with both an integrator and a
 * branch author - the one case a single attribution slot gets wrong.
 */
const COMMITS: Commit[] = [
  {
    oid: 'a91c4f7e2d3b6180c5a4e9f7b21d8c3a6e5f0917', parents: ['7d2e1b8a', 'c40f9a12'],
    refs: ['HEAD -> feature/faster-cart'], author: 'demo', age: 0.4,
    subject: "Merge branch 'agent/cart-profile' into feature/faster-cart",
    graph: '*   ', worktree: DEMO_ROOT, session: 's-shell',
    files: [
      { path: 'src/cart.js', status: 'M', additions: 6, deletions: 2 },
    ],
  },
  {
    oid: 'c40f9a1233ee7b90d1a86f4c0b73e29d5417ab60', parents: ['7d2e1b8a'],
    refs: ['agent/cart-profile'], author: 'demo', age: 1.1,
    subject: 'Cache the coupon table at boot instead of per request',
    graph: '| * ', worktree: DEMO_WORKTREE_PROFILE, session: 's-codex',
    files: [
      { path: 'src/cart.js', status: 'M', additions: 6, deletions: 2 },
      { path: 'src/coupons.js', status: 'A', additions: 20, deletions: 0 },
    ],
  },
  {
    oid: '7d2e1b8a95c04f31be6a7d20c9f8134e5b0a6d72', parents: ['31bb0c55'],
    refs: [], author: 'demo', age: 2.6,
    subject: 'Await the order request before asserting on the cart badge',
    graph: '* | ', worktree: DEMO_ROOT, session: 's-claude',
    files: [
      { path: 'tests/checkout.spec.ts', status: 'M', additions: 2, deletions: 1 },
    ],
  },
  {
    oid: '31bb0c5548a7e2901d6b3f8c47a05e91b2d7c6f4', parents: ['9f5a72d1'],
    refs: ['master', 'origin/master'], author: 'demo', age: 6.2,
    subject: 'Move the coupon fixture out of the test bundle',
    graph: '* ', worktree: DEMO_ROOT, session: '',
    files: [
      { path: 'tests/fixtures/coupons.json', status: 'D', additions: 0, deletions: 1_204 },
      { path: 'tests/fixtures/coupons.sample.json', status: 'A', additions: 12, deletions: 0 },
    ],
  },
  {
    oid: '9f5a72d16c0b48e3a75f19d2c8340be7215a9f83', parents: ['4c81ea30'],
    refs: [], author: 'demo', age: 27,
    subject: 'Add the checkout smoke spec',
    graph: '* ', worktree: DEMO_ROOT, session: '',
    files: [{ path: 'tests/checkout.spec.ts', status: 'A', additions: 84, deletions: 0 }],
  },
  {
    oid: '4c81ea3071f6b295d38c0a4e71b95f2607d3ac18', parents: [],
    refs: [], author: 'demo', age: 51,
    subject: 'Initial commit',
    graph: '* ', worktree: DEMO_ROOT, session: '',
    files: [{ path: 'README.md', status: 'A', additions: 18, deletions: 0 }],
  },
]

const GARDEN_COMMITS: Commit[] = [
  {
    oid: 'be31d0a9744c8f2016b5e3a7c9df8412a06b5e37', parents: ['12ac5f80'],
    refs: ['HEAD -> main', 'origin/main'], author: 'demo', age: 3.5,
    subject: 'Water the memes on a schedule',
    graph: '* ', worktree: DEMO_ROOT2, session: 's-garden',
    files: [{ path: 'src/water.js', status: 'M', additions: 9, deletions: 3 }],
  },
  {
    oid: '12ac5f8033b7e1d640a9c2f85e07b3164d9a7c02', parents: [],
    refs: [], author: 'demo', age: 30,
    subject: 'Plant the garden',
    graph: '* ', worktree: DEMO_ROOT2, session: '',
    files: [{ path: 'README.md', status: 'A', additions: 4, deletions: 0 }],
  },
]

const commitsFor = (projectId: string): Commit[] =>
  projectId === DEMO_PROJECT2_ID ? GARDEN_COMMITS : COMMITS

/** Expand the abbreviated parent ids the table is written with. */
function fullParents(commit: Commit, all: Commit[]): string[] {
  return commit.parents.map(parent =>
    all.find(item => item.oid.startsWith(parent))?.oid ?? parent)
}

const changeSummary = (files: Commit['files'], omit = false) => ({
  total: files.length,
  additions: files.reduce((sum, file) => sum + file.additions, 0),
  deletions: files.reduce((sum, file) => sum + file.deletions, 0),
  binary_files: 0,
  files: omit ? [] : files.map(file => ({
    path: file.path, old_path: null, status: file.status,
    additions: file.additions, deletions: file.deletions,
    binary: false, submodule: false, current_exists: file.status !== 'D',
  })),
  truncated: false,
  files_omitted: omit,
})

// ------------------------------------------------------------------ worktrees

type TreeSpec = {
  path: string
  branch: string
  main: boolean
  head: string
  ahead: number
  behind: number
  unstaged: Commit['files']
  staged: Commit['files']
  branchDelta: Commit['files']
}

const ROCKET_TREES: TreeSpec[] = [
  {
    path: DEMO_ROOT, branch: 'feature/faster-cart', main: true, head: COMMITS[0].oid,
    ahead: 3, behind: 0,
    unstaged: [
      { path: 'src/cart.js', status: 'M', additions: 12, deletions: 4 },
      { path: 'tests/checkout.spec.ts', status: 'M', additions: 6, deletions: 2 },
    ],
    staged: [],
    branchDelta: [
      { path: 'src/cart.js', status: 'M', additions: 26, deletions: 8 },
      { path: 'src/coupons.js', status: 'A', additions: 20, deletions: 0 },
      { path: 'tests/checkout.spec.ts', status: 'M', additions: 2, deletions: 1 },
      { path: 'tests/fixtures/coupons.json', status: 'D', additions: 0, deletions: 1_204 },
      { path: 'tests/fixtures/coupons.sample.json', status: 'A', additions: 12, deletions: 0 },
    ],
  },
  {
    path: DEMO_WORKTREE_COUPON, branch: 'agent/coupon-table', main: false,
    head: '5ea70c1938bd42f60a8c37e19b5d0247fa3c6e81', ahead: 3, behind: 1,
    unstaged: [
      { path: 'src/coupons.js', status: 'M', additions: 44, deletions: 18 },
      { path: 'src/cart.js', status: 'M', additions: 9, deletions: 3 },
      { path: 'scripts/import-coupons.mjs', status: 'A', additions: 96, deletions: 0 },
    ],
    staged: [
      { path: 'docs/coupons.md', status: 'A', additions: 38, deletions: 0 },
    ],
    branchDelta: [
      { path: 'src/coupons.js', status: 'M', additions: 128, deletions: 40 },
      { path: 'src/cart.js', status: 'M', additions: 22, deletions: 12 },
      { path: 'scripts/import-coupons.mjs', status: 'A', additions: 96, deletions: 0 },
      { path: 'docs/coupons.md', status: 'A', additions: 38, deletions: 0 },
    ],
  },
  {
    path: DEMO_WORKTREE_PROFILE, branch: 'agent/cart-profile', main: false,
    head: COMMITS[1].oid, ahead: 1, behind: 0,
    unstaged: [{ path: 'src/cart.js', status: 'M', additions: 12, deletions: 4 }],
    staged: [],
    branchDelta: [
      { path: 'src/cart.js', status: 'M', additions: 6, deletions: 2 },
      { path: 'src/coupons.js', status: 'A', additions: 20, deletions: 0 },
    ],
  },
]

const GARDEN_TREES: TreeSpec[] = [
  {
    path: DEMO_ROOT2, branch: 'main', main: true, head: GARDEN_COMMITS[0].oid,
    ahead: 0, behind: 0, unstaged: [], staged: [], branchDelta: [],
  },
]

const treesFor = (projectId: string): TreeSpec[] =>
  projectId === DEMO_PROJECT2_ID ? GARDEN_TREES : ROCKET_TREES

/** `GET /api/git/worktrees`. `detail=summary` withholds file lists, exactly as the
 *  daemon does, so an expanded row really does fetch its own reading. */
export function worktreesPayload(projectId: string, detail: string, worktree: string): unknown {
  const summary = detail !== 'full'
  const commits = commitsFor(projectId)
  const trees = treesFor(projectId)
    .filter(tree => !worktree || tree.path === worktree)
  const root = projectId === DEMO_PROJECT2_ID ? DEMO_ROOT2 : DEMO_ROOT
  return {
    repository: { root, common_dir: `${root}/.git` },
    comparison: {
      ref: 'master', display: 'origin/master', source: 'origin_head',
      available: true, reason: null, candidates: ['origin/master', 'master'],
    },
    worktrees: trees.map(tree => ({
      worktree: tree.path,
      HEAD: tree.head,
      branch: `refs/heads/${tree.branch}`,
      detached: false,
      bare: false,
      main: tree.main,
      head_committed_at: nowSeconds() - Math.round((commits[0]?.age ?? 1) * HOUR),
      comparison_counts: { ahead: tree.ahead, behind: tree.behind },
      unstaged: changeSummary(tree.unstaged, summary),
      staged: changeSummary(tree.staged, summary),
      conflicted: changeSummary([], summary),
      branch_delta: changeSummary(tree.branchDelta, summary),
    })),
  }
}

/** `GET /api/git/graph`. */
export function graphPayload(projectId: string, limit: number, grep: string, author: string): unknown {
  const all = commitsFor(projectId)
  const filtered = grep || author
    ? all.filter(commit =>
      (!grep || commit.subject.toLowerCase().includes(grep.toLowerCase()))
      && (!author || commit.author.toLowerCase().includes(author.toLowerCase())))
    : all
  const now = nowSeconds()
  const lines = filtered.slice(0, limit).map(commit => ({
    kind: 'commit',
    // Git only draws lanes for a contiguous walk, so a filtered read carries none -
    // the same rule the real endpoint follows, and the reason `filtered` exists.
    graph: grep || author ? '' : commit.graph,
    oid: commit.oid,
    parents: fullParents(commit, all),
    refs: commit.refs,
    author: commit.author,
    committed_at: now - Math.round(commit.age * HOUR),
    subject: commit.subject,
  }))
  return {
    lines,
    limit,
    has_more: filtered.length > limit,
    filtered: Boolean(grep || author),
  }
}

/** `GET /api/git/commits/{oid}/changes`. */
export function commitChangesPayload(projectId: string, oid: string): unknown {
  const all = commitsFor(projectId)
  const commit = all.find(item => item.oid === oid || item.oid.startsWith(oid))
  if (!commit) return null
  const parents = fullParents(commit, all)
  return {
    commit: commit.oid,
    parent: parents[0] ?? null,
    parents,
    parent_label: parents.length > 1 ? 'first parent' : parents.length ? 'parent' : 'root commit',
    message: `${commit.subject}\n\nInvented commit body, written for the demo.`,
    summary: changeSummary(commit.files),
  }
}

// ----------------------------------------------------------------- provenance

const sessionLabel = (id: string): string =>
  state.sessions.find(item => item.id === id)?.name || id

/**
 * `GET /api/git/provenance`: the durable ledger, plus the daemon's own per-commit
 * summary and the reference-move records that belong to a checkout rather than to
 * any session.
 */
export function provenancePayload(projectId: string, subject: string): unknown {
  const now = nowSeconds()
  const commits = commitsFor(projectId)
    .filter(commit => !subject || commit.subject.toLowerCase().includes(subject.toLowerCase()))
  const items: Record<string, unknown>[] = []
  const summaries: Record<string, unknown>[] = []

  for (const commit of commits) {
    const committedAt = now - Math.round(commit.age * HOUR)
    const merge = commit.parents.length > 1
    const row = (session: string, role: string, relationship: string, confidence: string) => {
      const id = `prov-${commit.oid.slice(0, 8)}-${role}`
      items.push({
        id,
        session_id: session,
        session_name: sessionLabel(session),
        display_name: sessionLabel(session),
        history_id: null,
        agent_run_id: session ? `run-${session}` : null,
        project_id: projectId,
        worktree_root: commit.worktree,
        commit_oid: commit.oid,
        parent_oids: commit.parents,
        subject: commit.subject,
        committed_at: committedAt,
        previous_head: commit.parents[0] ?? null,
        relationship,
        confidence,
        ambiguous: confidence === 'ambiguous',
        role,
        match_method: role === 'contributor' ? 'observed write' : 'session tool call',
        contributed_paths: role === 'contributor' ? commit.files.map(file => file.path) : [],
        source: role === 'observer' ? 'git_monitor' : 'session_tool',
        observed_at: committedAt + 2,
      })
      return { id, session_id: session }
    }

    const committer = commit.session
      ? row(commit.session, merge ? 'integrator' : 'committer', merge ? 'merged' : 'created', 'exact')
      : null
    // A landing merge has more than one true answer: the session that ran the merge
    // made the commit, and the session whose branch it carries wrote the work. Both
    // are on the ledger, which is the whole reason `branch_authors` exists.
    const branchAuthors = merge ? [row('s-codex', 'branch_author', 'authored_branch', 'correlated')] : []
    const contributors = !merge && commit.session
      ? [row(commit.session, 'contributor', 'contributed', 'correlated')]
      : []
    // Everyone else who merely had the checkout open when the reference moved. Kept
    // out of the summary on purpose: occupancy is not authorship.
    for (const session of state.sessions) {
      if (session.project_id !== projectId) continue
      if (session.id === commit.session) continue
      if ((session.runtime_cwd || session.cwd) !== commit.worktree) continue
      row(session.id, 'observer', 'observed', 'ambiguous')
    }

    summaries.push({
      commit_oid: commit.oid,
      subject: commit.subject,
      committed_at: committedAt,
      worktree_root: commit.worktree,
      committer: merge ? null : committer,
      integrator: merge ? committer : null,
      branch_authors: branchAuthors,
      contributors,
      attribution: commit.session ? 'exact' : 'ambiguous',
    })
  }

  return {
    items,
    commits: summaries,
    ref_moves: commits.slice(0, 3).map((commit, index) => ({
      id: `move-${commit.oid.slice(0, 8)}`,
      project_id: projectId,
      worktree_root: commit.worktree,
      commit_oid: commit.oid,
      previous_head: commit.parents[0] ?? commit.oid,
      kind: commit.parents.length > 1 ? 'merged' : index === 0 ? 'fast_forward' : 'created',
      commit_count: commit.parents.length > 1 ? 2 : 1,
      authored_count: 1,
      subject: commit.subject,
      committed_at: now - Math.round(commit.age * HOUR),
      observed_at: now - Math.round(commit.age * HOUR) + 3,
    })),
    permitted: true,
  }
}

/** `GET /api/git/swe-mux-setup`: nothing to offer, which is a real answer. */
export function sweMuxSetupPayload(): unknown {
  return { show: false, reason: 'already_ignored', decision: 'keep_visible', can_ignore: false, tracked: false }
}

/** `GET /api/land?project_id=`: one finished request, so the strip has a trail. */
// `landPayload` used to live here as a constant with one finished row in it. It moved to
// `controlPlane.ts` when the land queue became state: the interesting thing about a
// landing is the sequence queued → reconciling → verifying → landed, and a builder that
// invented its own row could never show a second one arriving beside the first.

/**
 * `GET /api/land/verify-command`.
 *
 * The field names are `parseLandVerifyCommand`'s, and they were not: this answered
 * `command`/`grant`/`approved_digest`, none of which that parser reads, so every field it
 * cares about fell back to the empty gate and the Git tab told every visitor "No
 * verification command - a land here would be refused rather than run" while the land
 * scenario beside it narrated the gate passing. A payload whose *keys* are wrong is worse
 * than a missing route, because it answers 200 and the surface renders a confident
 * falsehood; `demoDirector.test.ts` now parses this with the app's own parser rather than
 * trusting the shape.
 */
export function verifyCommandPayload(): unknown {
  return {
    configured: true,
    source: 'repository',
    display: '.worktree-verify',
    digest: 'demo-digest',
    approved: true,
    previously_approved: true,
    approved_source: '.worktree-verify',
    current_source: '.worktree-verify',
    config_command: '',
    config_revision: 'demo',
    config_status: 'clean',
    config_path: '',
    script_name: '.worktree-verify',
    script_present: true,
    plan: null,
    verify_grant: 'granted',
    provenance: null,
    // Granted, and authored by this machine, which is the posture the demo's own Project
    // is in: the gate runs without stopping to ask before every land.
    runs_without_approval: true,
  }
}
