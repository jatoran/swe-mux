# Releasing swe-mux

This is the procedure for cutting a swe-mux release.
It describes the **contract** each automated step has to satisfy rather than transcribing the
workflow files, so it stays correct when a workflow is edited.
The workflows themselves live in `.github/workflows/`.

Releases are cut from `master`, the single shared trunk.
There is no release branch and no backport line; `SECURITY.md` records that only the most
recent release is supported.

## Versioning

swe-mux follows Semantic Versioning.
While the major version is `0`, a minor bump may carry a breaking change, and `CHANGELOG.md`
says so explicitly when it does.

### The version is written in several places and nothing keeps them in sync

`pyproject.toml` and `src/swe_mux/__init__.py` are the two that must always move together.
They are not the only ones: the version is also a **string literal** in the routes that report
it, so bumping the package alone leaves a daemon that answers with the previous version and
never fails while doing it.

Every location, verified against the tree:

| Location | Form | What reads it |
|---|---|---|
| `pyproject.toml` | `[project] version` | The wheel and sdist, and therefore PyPI, `uv tool install`, and `pipx`. Authoritative |
| `src/swe_mux/__init__.py` | `__version__` | The configurator's generated inventory, which is what an agent asks (`src/swe_mux/routes/configurator.py`) |
| `src/swe_mux/routes/system.py` | two literals | `GET /api/health`, both the 503 startup body and the ready body. This is what `mux doctor` prints as `swe_mux_version`, and what an in-app update check compares against |
| `src/swe_mux/routes/diagnostics.py` | two literals | The diagnostics export bundle and its capability block - the artifact attached to a bug report |
| `src/swe_mux/mcp.py` | one literal | `serverInfo.version`, advertised to every agent CLI that connects to the mux MCP server |
| `src/swe_mux/provider_accounts.py` | one literal | `clientInfo.version`, sent to a provider endpoint |
| `frontend/package.json` | `version` | Nothing at runtime; it ships in no artifact metadata. Keep it consistent anyway |

Bump all of them in one commit, to one value, and confirm:

```bash
grep -rn '"X\.Y\.Z"' src/ pyproject.toml frontend/package.json
```

**Follow-up owed, and it is the real fix:** the literals should read `swe_mux.__version__`, and
one test should assert `swe_mux.__version__` equals the `[project] version` parsed out of
`pyproject.toml`.
Neither is in place today, which is why this is a checklist rather than a single edit.

## Release procedure

### 1. Prepare the release commit

- Bump the version in every location in the table above.
- Move everything under `## [Unreleased]` in `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD`
  section, dated with the day the tag is cut, and leave `## [Unreleased]` empty above it.
- Update the link references at the foot of `CHANGELOG.md`: point `[Unreleased]` at
  `compare/vX.Y.Z...HEAD` and add a `[X.Y.Z]` release-tag link.
- Confirm no `TODO(release)` placeholder survives in `pyproject.toml`, `CHANGELOG.md`, or
  `SECURITY.md`. The `OWNER` placeholder in every repository URL is resolved once, when the
  repository is published, and must not reach a published artifact.
- Run the full local gate: `.worktree-verify`, or the commands in `CLAUDE.md` § Verification.
- Commit with a DCO sign-off (`git commit -s`), as `CONTRIBUTING.md` requires of every commit.

### 2. Verify the artifact before tagging

Build and inspect the wheel locally.
The tag is the point of no return, because a tag that has published to PyPI cannot be reused.

```bash
npm --prefix frontend ci
npm --prefix frontend run build
uv build
```

Check the built wheel carries, at minimum:

- `License-Expression: Apache-2.0` plus `LICENSE`, `NOTICE`, and `THIRD-PARTY-NOTICES.md` under
  `dist-info/licenses/`.
- The `Project-URL` and `Classifier` lines from `[project]`, with no `OWNER` placeholder left in
  them.
- `swe_mux/static/index.html` and a hashed `swe_mux/static/assets/` bundle.

The last one is the trap.
`npm run build` writes the frontend into `src/swe_mux/static`, whose `index.html` and `assets/`
are **gitignored build output**, so a wheel built from a fresh clone or a worktree that never
ran the frontend build contains the icons, the manifest, the service worker, and the
notification sounds - and no user interface at all.
Nothing in the build fails; the installed daemon simply serves nothing.

Two scripts answer most of that list, and they answer different questions.
`packaging/verify_release_artifact.py` reads the wheel alone - the frontend bundle, the licence
files, the licence expression - and never looks at the tag or the source tree.
`packaging/verify_release_unit.py` reads the three together, which is the only way to catch a
disagreement *between* them: a tag that claims a version the tree does not, a version literal in
a reporting route that was never bumped, a changelog entry still under `## [Unreleased]`, a
`[project.urls]` placeholder, a command `README.md` tells a user to run that `[project.scripts]`
no longer declares, or a store whose schema stamp cannot be the version it claims.

```bash
uv run python packaging/verify_release_artifact.py dist/swe_mux-X.Y.Z-py3-none-any.whl
uv run python packaging/verify_release_unit.py --tag vX.Y.Z dist/swe_mux-X.Y.Z-py3-none-any.whl
```

Pass the tag you are **about to** cut.
That is the point at which a mismatch is still fixable, and it is why the second script refuses
to run without a tag rather than reporting a pass it did not earn.
Both run again in `release.yml` before anything is published, so this is a rehearsal rather than
the enforcement.
Neither replaces the `TODO(release)` sweep in step 1: those markers are deliberately still in the
tree, so no gate can require their absence without being red on a healthy checkout.

**Precondition owed before the first publish** (`.docs/development/ROADMAP.md` Phase 11): release
validation must fail on a missing or stale frontend bundle rather than leaving this as a manual
check, and `av` must be out of the wheel's install closure, because `faster-whisper` otherwise
drags 63 MB of GPL FFmpeg onto a user's machine on `pip install swe-mux`.

### 3. Tag

Tags are `vX.Y.Z` - a leading `v`, no prefix, no suffix, matching the `[X.Y.Z]` heading in
`CHANGELOG.md` and every version location in the table above.

```bash
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

Pushing the tag is what starts release automation.
Pushing a commit to `master` does not.

### 4. What CI does on the tag

The release workflow is triggered by a `v*` tag and owns the whole publish.
Its contract:

- Build the sdist and wheel from **the tagged revision only**, with the frontend built in the
  same job so the bundle and the Python source come from one commit.
- Refuse to publish if the artifact fails validation: a version that disagrees with the tag, a
  missing or stale frontend bundle, missing license metadata, or a failing gate.
- Publish to **TestPyPI first**, then to PyPI, through PyPI Trusted Publishing (OIDC). No
  long-lived repository token exists, so there is nothing to rotate or leak.
- Attach the frozen Windows desktop bundle to a GitHub Release whose body is the new
  `CHANGELOG.md` section.
- Write `version.json` into the static site (latest version, artifact URLs and hashes, and a
  changelog pointer). That file is the in-app update-check endpoint; the site workflow deploys
  `site/` to GitHub Pages from the same repository.

The ordinary `ci` workflow still runs the full gate on Windows and the platform legs on
Linux and macOS; the release workflow does not replace it and does not re-litigate it.

### 5. TestPyPI before PyPI, every time

The TestPyPI upload is not a formality, and it is the only chance to catch a metadata or
packaging defect before a version number is permanently consumed.

Install the TestPyPI artifact into a clean environment on a machine with no source checkout,
falling back to PyPI for dependencies TestPyPI does not carry:

```bash
uv tool install --index-url https://test.pypi.org/simple/ \
  --index-strategy unsafe-best-match swe-mux==X.Y.Z
mux --help
muxd --local-only
```

Confirm before promoting to PyPI:

- `muxd --local-only` starts and serves a working interface at `http://127.0.0.1:8765`, which is
  the check that proves the frontend bundle shipped.
- `mux doctor` runs and prints the new version as `swe_mux_version`, which proves the
  `routes/system.py` literals were bumped.
- The configurator's reported version matches the installed distribution version, which proves
  `__init__.py` was bumped. This is the version-drift check, made on a real install rather than
  by reading the source.

Only then promote the same commit's artifacts to PyPI.

### 6. After publishing

- Open the PyPI project page and confirm the README renders, and that the classifiers and
  project URLs are the ones from `[project]`.
- Confirm the GitHub Release carries the changelog section and the desktop artifact.
- Confirm the site's changelog page and `version.json` show the new version, and that a running
  older install offers the update banner.

## What is not automated, on purpose

- Choosing the version number.
- Writing the changelog entry. It is written from what changed for a user, not generated from
  commit subjects.
- Rewriting git history. History rewriting is an operator act and is denied to agents
  deliberately (`.docs/development/ROADMAP.md` Phase 11, repository publication).
- Supervisor updates. A release that changes the PTY supervisor's protocol reaps every live
  session on update, and its release notes must say so rather than leaving the updater to
  surprise the operator (`CLAUDE.md` § Supervisor change).
