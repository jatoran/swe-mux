# Release: operator-only tasks

Everything here needs a human.
Either it is destructive, it requires an account or a credential no agent holds, or it is a judgement call that has not been made yet.
Agent-owned release-prep work is tracked separately as work packages W1 to W6 (see "Agent work packages" at the end).

Ordering matters in exactly one place and it is the expensive one: **the history rewrite must be the last repository-wide act before the first push**, because it rewrites every ref and orphans every existing worktree and branch.
Do the agent work first, land it, then rewrite, then push.

## 1. Decisions that block other work

These are cheap to make and several tasks are stalled behind them.

- [ ] **The GitHub owner.** Personal account or a new organization.
      Recommendation: a new org, because it costs nothing, makes the project look like a project rather than a side folder, and transfers to a company later without moving the URL that every blog post and awesome-list entry will already point at.
      Agents have written `https://github.com/OWNER/swe-mux` with `TODO(release): OWNER` markers throughout; find them with `grep -rn "TODO(release): OWNER"` and replace once decided.
- [ ] **History: rewrite or fresh start.** Task 3 covers the rewrite. Recommendation is rewrite, for the reasons recorded there.
- [ ] **Windows code signing.** Azure Trusted Signing (~$10/month, the cheap modern path), an OV certificate (~$100-400/year, reputation accrues with downloads), or ship unsigned and document the SmartScreen warning on the download page.
      Unsigned PyInstaller executables are the single largest source of "is this malware" reports at launch.
      This can be deferred past the source release but not past the first desktop binary.

## 2. Rotate the leaked Tailscale certificate

Do this first and independently of everything else.
`.tmp/tailscale-cert-check/key.pem` is a real EC private key that has been in git objects since commit `90bcd45`.
Removing it from history does not un-expose it.

- [ ] Revoke and reissue the Tailscale cert for the affected host.
- [ ] Confirm nothing in the running install still references the old keypair.

Agent W1 untracks the file at HEAD; that is not a substitute for rotation.

## 3. Git history rewrite

**Run this only after every agent branch has landed on master.**
`git filter-repo` rewrites all refs, which orphans worktrees and invalidates branch state.
Confirm first that `git branch --no-merged master` is empty and every `.claude/worktrees/*` checkout is either landed or expendable.

The measured facts this plan is built on, so it is not re-derived:

- The entire private-key surface across 1078 commits is one keypair from one commit (`90bcd45`, `.tmp/tailscale-cert-check/`).
  No Anthropic, Tailscale, AWS, Google, Slack, or GitHub tokens exist anywhere in history.
  The three `ghp_`/`sk-ant-` matches in `tests/` are obviously-synthetic fixtures and are fine to keep.
- `.git` is 205 MB, dominated by roughly forty superseded 1.2 MB Vite bundles and 1.1 MB WASM blobs under `src/swe_mux/static/assets/`, committed before that path was gitignored.
- The historical `.test-tmp-identity/**/*.db` and `*.jsonl` blobs contain synthetic test data, not real transcripts. They are clutter, not a leak.

Recommended single pass, which fixes the leak and the bloat together:

```
git clone --no-local D:/PROJECTS/swe-mux D:/PROJECTS/swe-mux-clean
cd D:/PROJECTS/swe-mux-clean
git filter-repo \
  --path .tmp --path .verify --path .trash \
  --path src/swe_mux/static/assets \
  --path .test-tmp-identity \
  --path test-tmp-codex \
  --invert-paths
```

`git filter-repo` is a separate install (`uv tool install git-filter-repo` or `pip install git-filter-repo`); it is not bundled with Git.
The `--no-local` clone is deliberate: it forces a real object copy so the source repository is never the thing being rewritten.

- [ ] Verify after the rewrite, before trusting it:
      - `git log --all --oneline -S'-----BEGIN' --pickaxe-regex` returns nothing
      - `git ls-files | grep -E '^[.](tmp|verify|trash)/'` returns nothing
      - `du -sh .git` is dramatically smaller (expect well under 100 MB)
      - the working tree still builds: `uv sync --extra desktop --extra voice-local`, `npm --prefix frontend ci`, `npm --prefix frontend run build`, then `.worktree-verify`
- [ ] Keep the original `D:/PROJECTS/swe-mux` untouched until the rewritten clone is confirmed good and pushed. It is the only backup.
- [ ] Decide what the live daemon runs from afterwards. The primary checkout is a running application with worktrees, `.swe-mux/` state, and a frozen `dist/`; swapping its git history is not a neutral act. Simplest safe path: push the clean clone, then re-point or re-clone the working checkout once the public repo exists.

The alternative, a fresh single-commit history, is faster and equally safe but discards 1078 commits of visible activity that a launch benefits from.
Only choose it if the rewrite verification above turns up something unexpected.

## 4. Create and configure the public repository

- [ ] Create the repo under the chosen owner. Do not initialize it with any files.
- [ ] Push the cleaned history.
- [ ] Set the description and topics for discovery: `claude-code`, `ai-agents`, `agent-orchestration`, `coding-agents`, `codex`, `terminal-multiplexer`, `self-hosted`.
- [ ] Enable **secret scanning** and **push protection** (Settings, Code security). Expect the synthetic `ghp_`/`sk-ant-` test fixtures to be flagged; dismiss them as test data rather than editing the tests.
- [ ] Enable Dependabot security updates. Leave version updates off initially or it will open a lot of noise on day one.
- [ ] Enable private security advisories, so `SECURITY.md` points at something real.
- [ ] Add branch protection on `master` only after the first CI run is green, so a red required check does not lock you out of your own repository.

## 5. GitHub Pages and the domain

Agent W2 writes the deploy workflow; the account-side setup is yours.

- [ ] Settings, Pages, Source: **GitHub Actions** (not a branch).
- [ ] Add `swemux.dev` as the custom domain.
- [ ] DNS at the registrar: apex `A` records to GitHub's four Pages addresses, plus a `www` `CNAME` to `<owner>.github.io`.
- [ ] Wait for the certificate, then enable **Enforce HTTPS**.
- [ ] Fill the three `data-todo` placeholders in `site/index.html` (docs, blog, repository URLs) once the owner is known. Verify with `grep -rn data-todo site/`.
- [ ] Re-run the site's own gates after editing: `node site/tools/check.mjs` and `python site/tools/contrast.py`.

## 6. PyPI

- [ ] Register the `swe-mux` name on PyPI. Do this early; names are first-come.
- [ ] Configure **Trusted Publishing** (PyPI, Publishing, add a GitHub publisher naming the owner, repo, and the release workflow filename W2 creates). No long-lived API token is stored anywhere.
- [ ] Do the same on TestPyPI, and validate an alpha there before the first real publish.

## 7. Screenshots and demo assets

`site/img/desktop-workspace.webp` currently leaks real data: your full project list (`cmr-capture-manager`, `continuity`, `workout-plan`, `augur-engine`, `valadezvalor.com`, and others), the name `ADAM`, and account spend percentages.
The mobile shots that were checked are clean, but every image needs review before publishing.

- [ ] Stand up the capture environment. This needs no VM: a second daemon with its own `data_dir` and port (the isolated-daemon pattern already used on 8799) plus a handful of synthetic projects with invented names.
- [ ] Audit all eleven files in `site/img/` and recapture any that show real project names, personal names, spend figures, or unrelated paths.
- [ ] Record the launch assets from that environment: one 60 to 90 second hero video, plus feature GIFs for the orchestrator fan-out, the land queue landing branches serially, phone and voice control, the status board, and a session-preserving redeploy.
- [ ] Drop them into the `TODO(release)` markers the agents left in `README.md` and `.docs/marketing/`.
- [ ] Keep the capture scripts and scene notes beside the assets so they can be re-recorded after UI changes rather than reconstructed from memory.

## 8. Clean-machine validation

Nothing here can be delegated to an agent, because it needs a machine that is not this one.

- [ ] Windows Sandbox or a Hyper-V VM: install from the published artifacts only, with no source checkout and no Node. Confirm `muxd` starts, the bundled UI opens, a shell spawns, an agent promotes, and shutdown leaks no processes.
  (Docker Desktop cannot host a GUI Windows session, and macOS cannot be licensed onto non-Apple hardware, so macOS coverage comes from CI runners and borrowed hardware.)
- [ ] Upgrade and uninstall paths on that same machine.
- [ ] The second-physical-machine trial (the CMR laptop): install, set up Tailscale, install the PWA, and record every gap another person would hit.
- [ ] Browser pass: Chrome and Edge as primary, then Firefox, then mobile Safari and Chrome. Document what is supported rather than implying universality.
- [ ] Confirm the Tailscale setup flow shows the phone URL with a copy button and the PWA install instruction, rather than assuming the operator derives it.

## 9. Known gaps deliberately not assigned to an agent

Recorded so they are decisions rather than oversights.

- **`mux doctor` requires a running daemon.** It issues `GET /api/diagnostics/doctor`, so it cannot diagnose the single most likely new-user failure, which is the daemon not starting. Worth a daemon-less local mode before launch, but it is a feature change rather than release prep.
- **Phase 16 first-run blockers are still open** (mobile tour stranding at step 10 of 14, the unskippable provider login at step 5, and the stacked harness dialog over the tour blur). These break a brand-new user's guided first run and should land before any announcement, though not necessarily before the repository is public.
- **`site/index.html` is 883 lines of hand-authored markup** with its own check tooling. It is in good shape; it needs the placeholder URLs and the screenshot recapture, not a rewrite.

## Agent work packages

Running in parallel worktrees, each committing to its own branch and stopping short of landing.
The orchestrating session lands them serially.

| ID | Scope | Owns |
|---|---|---|
| W1 | Untrack `.tmp/`, `.verify/`, `.trash/`, stray root files | git index, `.gitignore` |
| W2 | CI marker-drift fix, release workflow, Pages deploy workflow | `.github/workflows/` |
| W3 | `[project.urls]`, classifiers, CHANGELOG, SECURITY, RELEASING | `pyproject.toml` `[project]`, new root docs |
| W4 | Release artifact validation gate for the frontend bundle | `packaging/verify_release_artifact.py` + tests |
| W5 | Remove GPL `av` from the wheel install closure | dependencies, `[tool.uv]`, `license_audit.py` |
| W6 | Public-facing README rewrite | `README.md` |
