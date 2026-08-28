# Release: operator-only tasks

Everything here needs a human.
Either it is destructive, it requires an account or a credential no agent holds, or it is a judgement call that has not been made yet.
Agent-owned release-prep work is tracked separately as work packages W1 to W6 (see "Agent work packages" at the end).

Ordering matters in exactly one place and it is the expensive one: **the history rewrite must be the last repository-wide act before the first push**, because it rewrites every ref and orphans every existing worktree and branch.
Do the agent work first, land it, then rewrite, then push.

## 1. Decisions that block other work

These are cheap to make and several tasks are stalled behind them.

- [x] **The GitHub owner.** Decided 2026-08-27: `jatoran`, at `https://github.com/jatoran/swe-mux`, created and empty.
      The `OWNER` placeholders were swept the same day; the only surviving `/OWNER/` string is the guard constant in `site/tools/build.py`, which fails the site build if a placeholder ever reaches a page again and is meant to stay.
      If the project later moves to an organization, GitHub redirects the old path, so this is not a one-way door.
- [ ] **History: rewrite or fresh start.** Task 3 covers the rewrite. Recommendation is rewrite, for the reasons recorded there.
- [ ] **Windows code signing.** Azure Trusted Signing (~$10/month, the cheap modern path), an OV certificate (~$100-400/year, reputation accrues with downloads), or ship unsigned and document the SmartScreen warning on the download page.
      Unsigned PyInstaller executables are the single largest source of "is this malware" reports at launch.
      This can be deferred past the source release but not past the first desktop binary.

## 2. The leaked Tailscale certificate: already rotated, no action needed

Identified 2026-08-27 rather than assumed, and the identification is what closes this item.

`.tmp/tailscale-cert-check/` holds a **Let's Encrypt TLS certificate** for `desktop-dnld8aa.taild42d36.ts.net`, obtained with `tailscale cert`, plus its EC private key.
It is **not** a Tailscale node key, so it carries no device identity and nothing about it can affect tailnet membership or connections.

Tailscale already replaced it on its own:

| | serial | issued | expires |
|---|---|---|---|
| leaked (commit `90bcd45`) | `051328FE...` | 2026-06-25 | 2026-09-23 |
| live on `:443` now | `062C3366...` | 2026-08-24 | 2026-11-22 |

The listener presents the newer certificate, so the leaked key is superseded and expires on its own within weeks.
Its exploitation would require both tailnet access and an active MITM against a name that resolves only inside the tailnet.

- [x] Rotation: done automatically by Tailscale Serve on 2026-08-24. No manual act.
- [ ] Optional and low value: revoke the old certificate with Let's Encrypt.
      It expires 2026-09-23 regardless, and Let's Encrypt retired OCSP in 2025, so revocation is barely checked by anything.

The file is also **no longer on disk**: W1 staged its removal with `git rm --cached`, and the fast-forward that landed that commit applied the deletion to the primary working tree as well.
It remains in history until task 3 runs, which is the only reason the identification above was still possible.

## 3. Git history rewrite

**Run this only after every agent branch has landed on master.**
`git filter-repo` rewrites all refs, which orphans worktrees and invalidates branch state.
Confirm first that `git branch --no-merged master` is empty and every `.claude/worktrees/*` checkout is either landed or expendable.

The measured facts this plan is built on, so it is not re-derived. Two of them were revised on 2026-08-27 after measurement, and both revisions shrink the job:

- The entire private-key surface across 1108 commits is one keypair from one commit (`90bcd45`, `.tmp/tailscale-cert-check/`).
  No Anthropic, Tailscale, AWS, Google, Slack, or GitHub tokens exist anywhere in history.
  The three `ghp_`/`sk-ant-` matches in `tests/` are obviously-synthetic fixtures and are fine to keep.
- **`.git` is 79 MB, not the 205 MB recorded earlier**; Git garbage-collected during the release-prep work.
  79 MB is an unremarkable clone, so dropping the superseded Vite bundles is no longer a reason to run this and the path list below is narrowed to the leak.
- **The leak sits at commit 14 of 1108** (2026-07-20, eight days after the first commit), so any filter rewrites 1094 commits regardless of how narrow the path list is.
  The scope of the rewrite is therefore fixed and total; only the payload changes.
  `filter-repo` preserves messages, authors, and dates, so the published history still shows 1108 commits across six weeks - only the SHAs differ.
- Every worktree and branch is expendable, verified rather than assumed: all 59 worktrees are clean and all 160 branches are merged into `master`.
  Nothing is lost by orphaning them, which is what the rewrite does.
- The historical `.test-tmp-identity/**/*.db` and `*.jsonl` blobs contain synthetic test data, not real transcripts. They are clutter, not a leak.

Recommended pass, narrowed to the leak and the scratch directories:

```
git clone --no-local D:/PROJECTS/swe-mux D:/PROJECTS/swe-mux-clean
cd D:/PROJECTS/swe-mux-clean
git filter-repo \
  --path .tmp --path .verify --path .trash \
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

- [x] Repo created at `https://github.com/jatoran/swe-mux`, empty, no initial files.
- [ ] **Do not push yet.** The leaked keypair is untracked at HEAD but is still in the history, so the first push publishes it in the object graph regardless of what the working tree looks like.
      Task 3 (the rewrite) has to happen first, and task 3 has to happen after the last agent branch lands.
      Nothing is lost by the wait: an empty repository is not a signal anyone reads.
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

## 8.5 First public CI run: two findings

The repository went public on 2026-08-27 and CI ran on a machine that was not the dev host for the first time.
It found two real things, which is what that run is for.

**Fixed: the main `mypy` pass was host-dependent.**
`[tool.mypy]` inherited its platform from the host, so `uv run mypy` asked a different question on every runner and could not be green on all of them at once.
Five modules reach Win32 directly (`desktop.py`, `agent_launcher.py`, `ghost_windows.py`, `file_manager.py`, `timer_resolution.py`) and are absent from the per-platform `ignore_errors` override, because on Windows they typecheck normally.
Both POSIX legs failed with the same 35 errors, reproduced locally with `uv run mypy --platform linux`.
Fixed by pinning `platform = "win32"`, which keeps those five fully checked everywhere rather than silencing them; the `--platform linux` pass in `mypy-platform.toml` still owns the POSIX side.

**Open, and deliberately not guessed at: one supervisor test fails on the GitHub Windows runner.**
`test_pty_supervisor.py::test_supervisor_process_outlives_client_and_reaps_on_command` asserts the supervisor anchors its cwd in the data dir, which exists so a supervisor spawned from `dist/` cannot lock the app tree against a rebuild.
On the runner its cwd was the Temp **root** (`C:\Users\runneradmin\AppData\Local\Temp`) rather than the `tmp_path` data dir - not a parent, and several levels up, so this is not a short-name or symlink artifact.
Everything else passed: 5446 of 5448, and the run has passed every local and worktree gate many times.

What is known and what is not, kept separate on purpose:

- `supervisor.py` does `os.chdir(data_dir)` and logs a warning on `OSError`. That warning does **not** appear in the CI log, but the supervisor writes to its own log file rather than stdout, so its absence proves nothing.
- The invariant the assertion protects is still satisfied: the Temp root is not `dist/`, so nothing is locked against a rebuild. The assertion is stricter than the property it guards.
- ~~The remaining hypothesis is that `data_dir` resolves differently on the runner from an empty config file, but the test connects successfully on `tmp_path`, which argues against it. This was not resolved.~~ **Refuted 2026-08-27 (W16).** See below.

### W16, 2026-08-27: the `data_dir` hypothesis is dead, and the instrument is in

**Proved from the code, not guessed.**
`main()` computes `data_dir` exactly once and hands that same object to all three consumers: `_setup_logging`, `os.chdir`, and `_run` -> `SupervisorServer(config_path, data_dir)` -> `_write_discovery` -> `discovery_path(self.data_dir)`.
Discovery and chdir therefore cannot disagree; there is no second value to diverge.
`resolve_data_dir` on an empty config file falls through `tomllib.loads("") == {}` to `config_path.parent`, which for `--config <tmp_path>/config.toml` is `tmp_path`.

**Proved from the CI run already in hand.**
The client reads the discovery file at `data_dir / "supervisor.json"` derived from the *same* value, on a `tmp_path` that is fresh per test, so no stale supervisor could have written it.
The failing run got past `_connect_with_retries(tmp_path)` to reach line 353, so on that runner the supervisor's `data_dir` **was** `tmp_path`.
The chdir target was therefore `tmp_path`, and "`data_dir` resolved elsewhere" is not the explanation.

**What that leaves, and why none of it can be settled from the source.**
A plain `OSError` from `os.chdir` cannot produce the observed value either: the test launches the supervisor with `cwd=str(tmp_path)`, so a failed anchor leaves the cwd at `tmp_path`, not at the Temp root.
Something therefore moved the cwd to the Temp root, or `psutil` reported a value the supervisor does not hold.
`os.chdir` appears exactly twice in `src/swe_mux`, and the other one is in the POSIX fork child (`pty_backend_posix.py`), so no swe-mux Python code is a candidate.
The remaining candidates are native: the `_winpty` extension around `PTY()` creation or `spawn`, or `psutil`'s PEB read of a 64-bit target.
Neither is decidable by reading this repository, which is why nothing was patched.

**The instrument, and exactly what the next red run will say.**
`tests/test_pty_supervisor.py` now samples the supervisor's raw, *unresolved* cwd at three points and, on a mismatch, raises a report instead of a bare comparison.
The report is built in-process rather than uploaded, so the answer travels with the failure rather than with the run.
It resolves the question in one pass:

- The **timeline** (`connected` -> `spawned` -> `client-aborted`) says *when*. `connected` is taken before any pseudoconsole exists, so a bad value there means the anchor never held, and a value that is right at `connected` and wrong at `spawned` convicts the ConPTY spawn.
- **`cwd per the supervisor itself`** says *whether psutil is telling the truth*. It spawns a session with `cwd=None`, which the supervisor passes through as a NULL `lpCurrentDirectory`, so `cmd /c cd` prints the supervisor's own working directory. Agreement with psutil means the supervisor really is mis-anchored; disagreement means the assertion has been reading a bad instrument and the invariant was never violated.
- The **raw, unresolved** strings say *provenance*. The runner's `%TEMP%` is the 8.3 form `C:\Users\RUNNER~1\AppData\Local\Temp`, and `.resolve()` erases that distinction, so the short form points at something that read the environment variable while the long form points at something that did not.
- The **supervisor console output and `supervisor.log`** are both included, which closes the evidence gap named above: if `os.chdir` did raise, its warning and traceback are now in the failure text.

Two supporting changes.
The test now drains the supervisor's stdout on a thread from the moment it launches; it previously left a `PIPE` unread for the whole test, which is a 64 KB deadlock waiting for a chatty run as well as the reason the console log was unavailable.
`.github/workflows/ci.yml` gained an `if: failure()` pair of steps that copy every `supervisor*.log` and `crash.log` out of the runner's `pytest-of-*` trees and upload them as a `supervisor-logs` artifact, which covers the failure modes that produce no assertion message at all (a connect timeout, a hung spawn).
The copy runs after the suite, so the path lengths the test actually used are untouched.

**Still open, and still not to be guessed at.**
Do not weaken the assertion to make the badge green, and do not edit `supervisor.py` on a hypothesis: a supervisor change reaps every live session and needs the deliberate out-of-band update flow in the root `CLAUDE.md`.
If the probe and psutil disagree on the next red run, the fix is in the test's instrument and no supervisor change is warranted.
If they agree, the timeline names the culprit and a supervisor-side fix can be written against evidence.

## 9. Known gaps deliberately not assigned to an agent

Recorded so they are decisions rather than oversights.

- **PyAV is out of this project's closure, but not out of a downstream `pip install`.**
  W5 landed the fix and proved by measurement that transcription works with no `av` installed at all, so swe-mux imports the real package on no code path.
  The limit is structural rather than an oversight: a uv override governs this repository's resolution, its lockfile, its builds, and the license gate, but it is not carried in the published wheel's `Requires-Dist`.
  A downstream `pip install swe-mux[voice-local]` therefore still resolves `faster-whisper`'s own declared `av>=11`.
  There is no PEP 508 mechanism to exclude a transitive dependency from published metadata; closing the last inch needs `faster-whisper` to make `av` optional upstream, or an override on the installing side.
  What remains for that user is disk size and diligence, not function.
  Decide before publishing whether to document this in `SECURITY.md`/`RELEASING.md` or to raise it upstream with `faster-whisper`.
- **A desktop redeploy is owed before release, though not before landing.**
  The running frozen app still carries the old inline `av` stub, including a defect W5 found and fixed: refusing every attribute meant `repr()` of the stub module raised, so any log line or traceback mentioning `av` raised from inside the stub and buried the real diagnostic.
  Run `uv run python packaging/redeploy_desktop.py` from the primary checkout once the release branch settles.

- ~~**`mux doctor` requires a running daemon.**~~ **Closed 2026-08-27 (W10).** It now falls back to a local report (`src/swe_mux/doctor_local.py`) when the daemon is unreachable, covering the install-integrity faults that stop a daemon starting: the Python floor, the package's import graph, the config file, the frontend bundle in the installed package, the data directory, `mux.db`, the configured port, the host PTY backend, the frozen supervisor bundle, prerequisites, harness detection, and each optional extra.
  A daemon that answers is byte-for-byte unaffected.
  The status vocabulary gained `unchecked` so a check that did not run reads as neither healthy nor unavailable, and exit codes compose the existing two: `1` for a failing local check, `3` (daemon unreachable) for a clean degraded report, never `0`.
  Contract: `design/interfaces.md`, "`mux doctor` without a running daemon".
- **Phase 16 first-run blockers are still open** (mobile tour stranding at step 10 of 14, the unskippable provider login at step 5, and the stacked harness dialog over the tour blur). These break a brand-new user's guided first run and should land before any announcement, though not necessarily before the repository is public.
- **`site/index.html` is 883 lines of hand-authored markup** with its own check tooling. It is in good shape; it needs the placeholder URLs and the screenshot recapture, not a rewrite.

## Agent work packages

All six ran in parallel worktrees and **all six are landed on master** as of 2026-08-27.

| ID | Scope | Outcome |
|---|---|---|
| W1 | Untrack `.tmp/`, `.verify/`, `.trash/`, stray root files | Landed. The leaked keypair is untracked at HEAD and still on disk; history removal remains task 3. |
| W2 | CI marker-drift fix, release workflow, Pages deploy workflow | Landed. `ci.yml` was missing `not live_edge_tts` and would have gone red on all three runners the first time it ran in public. |
| W3 | `[project.urls]`, classifiers, CHANGELOG, SECURITY, RELEASING | Landed. Verified present in a built wheel's METADATA. |
| W4 | Release artifact validation gate for the frontend bundle | Landed. Verified to fail on four separate stripped wheels and to exit non-zero. |
| W5 | Remove GPL `av` from the wheel install closure | Landed, with the scope limit recorded above. |
| W6 | Public-facing README rewrite | Landed. |

Post-landing gate on master, all green: 5316 passed / 17 skipped, ruff clean, mypy clean over 224 files, frontend `tsc` / `check:tests` / `npm test` all exit 0, and a wheel built from master passes `verify_release_artifact.py`.

One pre-existing flake was found and fixed while landing.
`test_redeploy_endpoint.py::test_a_scan_older_than_the_window_is_run_again` failed intermittently under load because the bundle-holder scan reused a cached scan whenever `now - started <= window`, and Windows advances `time.monotonic()` in 15.625 ms steps.
Two back-to-back calls with `max_age=0` land on an identical reading, so a caller asking for no caching silently got some.
The comparison is now half-open, so `max_age=0` means "never reuse" regardless of clock resolution.

## Sync command

Use the full form; the short one silently strips PyInstaller and breaks the desktop build:

```
uv sync --extra desktop --extra voice-local --group dev --group package
```
