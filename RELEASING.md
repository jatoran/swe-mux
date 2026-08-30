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

**Started at 0.1.3, and the reason it started is the argument for finishing it.**
`src/swe_mux/routes/diagnostics.py` carried two copies of the version and **was never in the
table above**, so the checklist could be followed exactly and still ship a daemon reporting the
previous version through `/api/health` and the doctor export.
`verify_release_unit.py` caught it while the tag was still uncut, which is what that script is
for; the table did not, because a hand-maintained list of copies cannot know about a copy nobody
added to it.
Those two now read `__version__`, so that file needs no row here at all - which is the shape the
remaining locations should end in.
Until then, prefer the `grep` above to the table: it asks the tree rather than a list.

## Release procedure

### 1. Prepare the release commit

- Bump the version in every location in the table above.
- Move everything under `## [Unreleased]` in `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD`
  section, dated with the day the tag is cut, and leave `## [Unreleased]` empty above it.
- Update the link references at the foot of `CHANGELOG.md`: point `[Unreleased]` at
  `compare/vX.Y.Z...HEAD` and add a `[X.Y.Z]` release-tag link.
  Both are load-bearing beyond the changelog page: the GitHub Release body's `**Full changelog**`
  footer is built from the `[X.Y.Z]` reference, so a missing one fails the release job.
- Regenerate the site and commit it in the same commit: `python site/tools/build.py`, then
  `git add site/`.
  `site/changelog/index.html` and its thirty siblings are **committed build output**, and
  `pages.yml` deploys `site/` verbatim without ever running the generator - so a release commit
  that edits `CHANGELOG.md` and not the page publishes a site that does not mention the version a
  user just installed.
  That is exactly what 0.1.3 did: `swemux.dev/changelog/` showed 0.1.2 for a day while 0.1.3 was
  live on PyPI, on GitHub Releases and in `version.json`, and it was repaired by hand afterwards.
  `.worktree-verify` and `ci.yml`'s `site` job both fail on a stale page now, so this step is what
  keeps them green rather than a courtesy.
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

One of the unit script's checks has two correct answers, and the caller picks which.
`changelog-entry` refuses a `## [Unreleased]` section that still holds entries above the version's
own, which is right at the tag and wrong between releases - after a release, `pyproject.toml` still
declares the version just published, and `## [Unreleased]` is where the next version's entries are
supposed to go.
The script cannot tell the two states apart by reading the tree, so `--stage` says which is being
asked, and it defaults to the strict release-time reading.
**A release never passes it.**
The landing gate does, through the test that simulates this repository as its own release, which
is what lets an entry be written between releases instead of being reverted to keep the gate green;
a test asserts that `release.yml` does not.

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
- Create a GitHub Release **whose body is the new `CHANGELOG.md` section**, extracted from the
  tagged revision by `packaging/release_notes.py` and handed to `gh` as `--notes-file`.
  It reads the section with the same parser `verify_release_unit.py` gated the build with, so the
  check that refuses to publish an empty section and the extractor that writes the body cannot
  disagree about where that section begins and ends.
  One line is added: a `**Full changelog**` link to `CHANGELOG.md` at the tag.
  **There is no fallback.** A tag whose section is missing or empty fails the job rather than
  publishing something else, and the notes are applied whether the release is being created or
  was prepared as a draft beforehand.
  This paragraph described behaviour the workflow did not have until 2026-08-29: it used
  `gh release create --generate-notes`, so v0.1.3 published with a body that was one line of
  GitHub's own compare link. `tests/test_release_notes.py` now fails if `--generate-notes` comes
  back.
- Attach every published artifact to that Release: the wheel and sdist, the portable desktop
  archive and its per-file hash manifest, the Windows installer, and `version.json`.
  **The bundle's filename is a contract, not a convenience**: the
  in-app updater recognizes its own platform's artifact by name alone, so it must be built by
  `packaging/package_desktop_release.py` (`swe-mux-<version>-<platform>-<arch>.zip`, one
  top-level `swe-mux/` directory carrying the `bundle.json` the updater reads). An artifact
  named anything else is invisible to every installed copy, which reports "no desktop bundle
  for this platform" rather than installing the wrong thing.
  `release.yml`'s `build-desktop` job produces it, alongside the per-file hash manifest the
  updater plans a delta against and the Windows installer
  (`update_install.release_installer_name`). It builds **three** bundles for that: the app,
  the PTY supervisor, and - since 0.1.4 - `dist/swe-mux-cli`, the console client the installer
  puts on `PATH`. Only the archive and the installer are published; the client bundle ships
  inside the installer and is not a release artifact of its own, so nothing new has to be
  looked up by name (`.docs/design/features/desktop-shell.md`).
- **A release that changes `supervisor.PROTOCOL_VERSION` must say so in its release notes**,
  because the updater refuses to install it: swapping the app bundle alone would leave a
  daemon that cannot talk to the running supervisor, and refreshing the supervisor reaps
  every live session. Such a release is a deliberate, announced, sessions-lost upgrade. The
  updater reads the incoming bundle's declared protocol out of the archive and stops before
  anything is staged; it never decides this quietly.
- Write `version.json` into the static site (latest version, artifact URLs and hashes, and a
  changelog pointer). That file is the in-app update-check endpoint; the site workflow deploys
  `site/` to GitHub Pages from the same repository.

The ordinary `ci` workflow still runs the full gate on Windows and the platform legs on
Linux and macOS; the release workflow does not replace it and does not re-litigate it.

### 5. TestPyPI, which a tag does not do for you

**Read this before trusting the heading it used to carry.**
This section said "TestPyPI before PyPI, every time", and the automation has never done that.
The `publish-testpypi` job in `release.yml` carries an `if:` condition that requires the run to
have come from a `workflow_dispatch` whose `index` input is testpypi, so **a `v*` tag skips it
and publishes straight to PyPI**.
Both 0.1.1 and 0.1.2 went out that way on 2026-08-28, and the job reported `skipped` rather than
failing, so nothing drew attention to it.

That is a real gap and it is stated rather than closed, because closing it is a decision:
either make the tag path run TestPyPI first and gate PyPI on it, or accept that the tag is the
rehearsal and stop claiming otherwise.
Until one of those happens, a TestPyPI validation is a **manual act you perform before tagging**,
using the `workflow_dispatch` path with `index: testpypi`.

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

## What the first two releases actually taught, 2026-08-28

Recorded so the next person does not rediscover them. Both were found by releasing, not by
reading, and neither could have failed the local gate.

**A release can succeed at PyPI and fail as a release.**
0.1.1 published to PyPI and then `build-desktop` failed, which skipped `github-release` and
`update-manifest`. The result was a version live on PyPI with **no GitHub Release and no
refreshed `version.json`** - so every installed copy was still being told 0.1.0 was current
while PyPI served 0.1.1. The publish jobs are independent of the desktop job, so "PyPI worked"
is not "the release worked". Check the whole run, not the package.

The repair is a new version, not a retag. A tag that has published to PyPI cannot be reused, so
0.1.2 carried 0.1.1's contents plus the artifacts it failed to build. A PyPI version with no
matching GitHub Release is untidy and harmless; leave it and say so in the changelog.

**The installer's source path is resolved against the `.iss` file, not the working directory.**
`build-desktop` failed on its first ever run because `build_installer.py` passed
`/DAppSource=dist` - a relative path - and ISCC resolves a relative `Source:` against the
script's own directory, so `[Files]` searched `packaging/installer/dist/` for a bundle at the
repository root. `cwd=ROOT` on the subprocess looks like it should prevent that and does not.
The `.iss` header had documented `AppSource` as absolute all along.
`tests/test_windows_installer.py` now fails when any `/D` path define is relative. Note what the
suite still cannot do: it reads the `.iss` as source text and never compiles it.

**That gap is now half closed, and it is worth knowing which half.** `ci.yml`'s
`installer-cycle` job compiles the installer on every push - real ISCC, real client bundle,
stubs for the app and supervisor bundles it asks nothing about - and then runs the whole
install → upgrade → uninstall cycle against `HKCU\Environment\Path`
(`packaging/installer/verify_path_cycle.ps1`). So a compile error, a duplicated PATH entry on
upgrade, or a `%USERPROFILE%` flattened by an uninstall all fail before a tag. What still
happens for the first time on a tag is the compile over the **real** 120 MB app bundle: a
`[Files]` path that only exists in a real build, or an `lzma2/max` pass over several hundred
megabytes, is not exercised by the cycle job.

**A push-triggered Pages deploy can publish content older than itself.**
`pages.yml` stages its bytes at checkout and deploys them after the `pages` concurrency queue
clears. On v0.1.2 the release commit's Pages run restored `version.json` at 00:30:57, when the
newest release was still v0.1.0; `release.yml` published the correct 0.1.2 manifest at 00:37:25;
the queued Pages deploy finished at 00:38:56 and overwrote it. The site advertised 0.1.0 while
PyPI served 0.1.2.

The `concurrency` group does not prevent this - it serialises the two deployments, and the loser
is whichever *finishes* first, not whichever holds newer content. `pages.yml` now also triggers
on `workflow_run` of `release`, so the last deployment is the newest one. **After any release,
confirm `https://swemux.dev/version.json` actually names the new version**, rather than assuming
the job that wrote it was the job that won.

## What 0.1.3 taught, 2026-08-29

Both of these were repaired by hand after the release, and both had the same shape: a document
described the behaviour the project wanted, nothing executed that description, and the gap was
invisible because the wrong outcome is indistinguishable from the right one unless you go and look.

**A release body assembled by GitHub is still a release body.**
`release.yml` ran `gh release create --generate-notes`, so v0.1.3 published with a body that was
literally the compare link, while section 4 above had been stating the contract as "a Release
whose body is the new `CHANGELOG.md` section" since before 0.1.0.
Nothing was red and nothing could be: generated notes are well-formed.
The notes are now extracted by `packaging/release_notes.py` and passed as `--notes-file`, with no
fallback, and `tests/test_release_notes.py` fails if `--generate-notes` returns.

**A generated artifact that is committed is only as fresh as the commit that regenerated it.**
The 0.1.3 release commit updated `CHANGELOG.md` and not `site/changelog/index.html`, which is
generated from it, so `swemux.dev/changelog/` advertised 0.1.2 for a day.
`check_changelog.py` passed throughout and was right to: it asks whether `CHANGELOG.md` carries a
dated entry per released version, which it did.
Nothing asked whether the artifact still matched its source, and CI ran no site checks at all.
It does now (`ci.yml`'s `site` job), the landing gate does too
(`tests/test_site_artifacts.py`), and section 1 makes the regenerate part of the release commit.
Note what neither of them checks: a hand-edited `site/index.html`, which has no generator, and
anything in `site/` that `build.py` does not write.

## What is not automated, on purpose

- Choosing the version number.
- Writing the changelog entry. It is written from what changed for a user, not generated from
  commit subjects.
- Rewriting git history. History rewriting is an operator act and is denied to agents
  deliberately (`.docs/development/ROADMAP.md` Phase 11, repository publication).
- Supervisor updates. A release that changes the PTY supervisor's protocol reaps every live
  session on update, and its release notes must say so rather than leaving the updater to
  surprise the operator (`CLAUDE.md` § Supervisor change).
