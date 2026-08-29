# The site screenshots, and the environment they are taken in

`site/img/` holds the nine screenshot slots `swemux.dev` owns.
This document is how they are re-recorded after a UI change, and why the environment they are recorded in exists.

## Why there is an environment at all

The captures that used to occupy those filenames were screenshots of a live machine.
Between them they showed a full personal project sidebar, an operator's first name against two provider accounts, those accounts' spend percentages, absolute local paths, and several screens of real transcript prose.
`site/` is the GitHub Pages deploy root, so every file in it is served whether or not a page references it.

They were replaced outright rather than blurred, because a redaction leaves the original bytes in that file's git history and an image on a public domain is scraped faster than it can be withdrawn.
The same reasoning governs every rule below: **an image is a leak nothing later recalls.**

So the shots are taken against a second, synthetic swe-mux install: invented Projects, invented sessions, invented git history, invented notes, invented quota.
Nothing in it is derived from the machine it runs on.

## Running it

Three commands, from the repository root, with the operator's daemon left alone throughout:

```
uv run python trailer/capture_env.py up
uv run --with playwright --with pillow python trailer/capture_site_shots.py
uv run python trailer/capture_env.py down
```

`up` prints the operator daemon's health before and after it starts, and `down` prints it again.
If any of those three reads `False`, stop and find out why before doing anything else.

Shoot one slot while iterating on it:

```
uv run --with playwright --with pillow python trailer/capture_site_shots.py desktop-alerts.webp
```

`--raw-only` writes the full-resolution PNG under `trailer/site-shots/raw/` and leaves `site/img/` untouched, which is what you want while framing.

### Not colliding with the live daemon

The operator's daemon owns **port 8765** and **`~/.mux`**, and both are process-wide singletons; colliding with either disrupts real agent sessions.

`muxd` has no `--data-dir`. It has `--config`, and `supervisor.resolve_data_dir` falls back to the config file's parent directory, so naming the config inside the capture root is what puts the whole install - database, supervisor discovery, shims, logs - somewhere else. `--local-only` clears `tailnet_enabled`, which is what keeps this daemon from reaching for the operator's Tailscale Serve route on 443.

```
uv run muxd --config D:/swemux-capture/data/config.toml --port 8799 --local-only
```

Check the port first (`netstat -ano | grep 8799`) rather than assuming; override with `MUX_CAPTURE_PORT`.

**Never** `muxd --shutdown`, and never a name-matched `taskkill`. Both reap every live session on the machine. `capture_env.py down` terminates only the PID `up` recorded.

### Where it lives

| | default | override |
|---|---|---|
| capture root | `D:/swemux-capture` | `MUX_CAPTURE_ROOT` |
| synthetic checkouts | `<root>/code` | `MUX_CAPTURE_CODE_ROOT` |
| data dir | `<root>/data` | - |
| worktrees | `<root>/worktrees` | - |
| synthetic home | `<root>/home` | - |
| port | 8799 | `MUX_CAPTURE_PORT` |

`up` moves the previous `data/`, `code/`, and `worktrees/` into `<root>/.trash/<timestamp>` rather than deleting them: a shot that came out wrong is usually explained by what the previous run had in its database, and that explanation is gone forever if the directory is removed. Prune `.trash` by hand when it gets large.

The paths are deliberately free of a user directory, because a shell prompt renders one.

## The four things that keep a shot honest

**The environment has no personal data in it, by construction rather than by inspection.**
`capture_env.child_env()` repoints `USERPROFILE`/`HOME` at a synthetic home, which is what makes `ProviderAccountService` read a *fixture* credential instead of the operator's; the first seeded capture rendered the operator's Claude organisation and Codex login as two labelled chips in the sidebar footer, which is the same leak that pulled the originals. The same strip removes every `MUX_*` and `CLAUDE_*` variable, so a capture daemon started from inside a swe-mux session cannot report its sessions to the live install or rename its own panes.

**Every shot is scanned before it is kept.** `capture_site_shots.scan_for_leaks` reads the rendered page and refuses to write anything if it contains the home directory, the account name, or the identity `git config --global` is set to. The list is derived at run time rather than written down here, because a denylist in a public repository is itself a small disclosure and a hand-maintained one goes stale the day the host changes. It is a complement to looking at the file, not a replacement: it cannot see a name rendered into a terminal cell grid, and it cannot judge a picture.

**Look at every file before committing.** The script says so when it finishes and it means it.

**Nothing is fabricated inside the product.** The terminals run real commands against the synthetic repositories - `git log`, `git status`, `git diff --stat`, typed through the app's own terminal - rather than having invented output pasted into a scrollback. The one place data is written directly into the store is `capture_env.seed_store`, which inserts attention items and behavioural records in the exact shape their observers write; both are produced in a live install by a metered model call, and nothing should spend the operator's key to make a screenshot. Every surface then reads them through its ordinary query.

## The scene, slot by slot

| slot | scene |
|---|---|
| `desktop-workspace.webp` | atlas-api, two panes over one Project (the split is the Project's stored layout, seeded in `seed_fleet`), `git log`/`git status` in the left pane and `git diff`/`git branch -vv` in the right, Notes drawer open on the seeded note. The only shot allowed to include chrome. |
| `desktop-alerts.webp` | Alerts → Now, sidebar collapsed, drawer widened to fill the crop, held-back digest expanded so the shot carries a suppressed item *and its reason* rather than a count. |
| `desktop-git.webp` | Git → Map: `main` plus two checked-out worktrees, each 1 ahead and 1 behind. The trunk gets one commit *after* the branches are cut, so the counts have something to say in both directions. |
| `desktop-insight.webp` | Activity → Timeline on the one real agent session (`Ingest receipt contract`): five phase-labelled behavioural records with a dead-end and a blocked badge, the budget line, and the this-run toggle. Needs the `agent-run` flow below; skipped by the default run. |
| `desktop-notes.webp` | Notes → the seeded note, scrolled past the opening paragraph so headings, a nested ordered list, and the checkbox rows are all in one frame. |
| `mobile-session.webp` | A phone, on a session of its own (`Ingest throughput bench`), with output typed *at phone width*. |
| `mobile-nav.webp` | The navigation overlay, four Projects expanded with session rows, status dots, and elapsed times. |
| `mobile-notes.webp` | The same note in the phone's side panel. |
| `mobile-alerts.webp` | The Alerts tab in the phone's side panel. |

## The one shot that needs a real agent run: `desktop-insight.webp`

Activity's **Timeline** segment is gated on `hasHarnessTranscript(backend)`, and every seeded session here is a shell, so the segment is not offered at all.
The way through is one real, bounded, read-only claude run in the synthetic `atlas-api` checkout, which is an operator's decision rather than an agent's because it spends real subscription quota.
It is implemented (2026-08-28) and skipped by the default shoot:

```
uv run python trailer/capture_env.py up --claude-config
uv run python trailer/capture_env.py agent-run
uv run --with playwright --with pillow python trailer/capture_site_shots.py desktop-insight.webp
```

What `agent-run` does, and why each half is shaped the way it is:

- **The daemon gets `CLAUDE_CONFIG_DIR` pointed at the real `~/.claude`** (`up --claude-config`), for its *discovery* half only: `harness._claude_data_home` reads the daemon's environment to find the run's transcript under `<dir>/projects`.
- **The agent session gets the operator's real home per-session** (`USERPROFILE`/`HOME` in the spawn `env`), because the CLI's account state lives in `~/.claude.json` and its credential in `~/.claude/.credentials.json`, and any token refresh then writes back to the operator's own files rather than to a stray copy.
  `CLAUDE_CONFIG_DIR` is masked with an **empty string** in the same spawn env: per-session env can override but never unset, the CLI treats empty as unset, and a CLI that *does* see `CLAUDE_CONFIG_DIR` keeps its account state in `<dir>/.claude.json` - which is not where the real state lives, so it opens a sign-in screen over a perfectly valid credential.
  Both halves were measured on 2026-08-28; the failed shapes cost a run each.
- **The trust dialog's default answer is "No, exit".** A bare Enter at that dialog confirms the exit and crashes the session; `agent-run` sends arrow-down then Enter, which is a no-op in an already-trusted prompt's empty composer.
- **One anodyne, read-only prompt** goes through `POST /api/sessions/{sid}/input`, the app's own path, and the turn is waited out on `last_turn_ms`.
- **The seeded `SCANS` are re-keyed** from `capture-run-atlas-api` onto the run's real `agent_run_id`, so the records attach to the one session whose Timeline segment exists.
- **The whole enablement chain has to be green** or the panel renders the opt-in screen instead of the records (`ScanTimelineTab` gates on `global_enabled && project_enabled`): the `scan_timeline_enabled` install switch, a *configured* OpenRouter key (presence is all `llm_readiness` checks; `agent-run` stores a placeholder that authenticates against nothing, so any scan it lets through 401s at zero cost), and the Project opt-in at `atlas-api/.swe-mux/config.toml` - which must carry `version = 1` and the explicit dependency closure (`raw_store`, `tier0`, `scan_timeline`), because the parser rejects an unversioned file and the resolver treats an absent dependency as off.
- **A scan attempt is the skip-reason reset.** A scan tried before the Project opt-in landed leaves a terminal "not permitted for this Project" reason in service memory, and the panel prints it in red over the records; `_scan` clears it on entry, so `agent-run` ends with one deliberate scan that then fails harmlessly on the placeholder key.

Two cautions that survive the implementation:

- **Only the Timeline segment is cleared for this shot.** With the real config dir visible to the daemon, the drawer's Actions → Skills panel lists the operator's real global skills by name, and the Agent tab reads their real CLI configuration. Frame nothing but the drawer on the Timeline segment.
- Do not reach for the shortcut of spawning a session with `backend: "claude"` and a shell executable. It makes the segment appear, and it makes the UI assert something false about what is running, which is exactly the mockup the site's own rules forbid.

## Known cosmetic artifacts

- The quota chips read **`stale`**. The poll loop runs once about two seconds after start regardless of configuration, dials the provider with the fixture credential, fails, and marks the retained numbers stale. `provider_quota_poll_minutes = 1440` stops it happening *again* mid-shoot but cannot stop the first one. The percentages themselves are the seeded fixture and are correct.
- The Git map's landing strip reads **`Not configured`** for `.worktree-verify`, which is true: the synthetic Projects have no verification command. The brief's "commit provenance column" lives on the Git tab's **Provenance** segment and needs agent-authored commits, so the Map is what this environment can show.
- `mobile-nav.webp` has no model names beside the session rows. Shell sessions have no model.

## Geometry, and why it is not a preference

`site/index.html` gives every image an explicit `width`/`height`, and `site/tools/check.mjs` asserts the page does not overflow at several widths, so **a shot delivered at a different aspect ratio is a layout regression rather than a different-looking picture.** The two shapes are 2100x1275 (desktop, 28:17) and 1206x2622 (mobile, 201:437); that table is maintained in `site/tools/placeholders.py`.

Three geometries, because one does not fit:

| | viewport | scale | drawer | result |
|---|---|---|---|---|
| hero | 1400x850 | 2 | 560 | 2800x1700, downscaled 0.75 |
| panel | 900x430 | 3 | 668 | clip is ~700x425 CSS = 2100x1275 native |
| wide panel | 1400x638 | 2 | 1010 | clip is ~1050x637.5 CSS = 2100x1275 native |
| mobile | 402x874 | 3 | - | 1206x2622 native, no resampling |

Nothing is ever enlarged; `finish` refuses rather than shipping a soft image, with a four-pixel tolerance for the rounding a half-pixel CSS height produces.

The panel geometry exists because the first attempt reused the hero's viewport and produced a legible-but-tiny panel adrift in an empty frame. The wide-panel geometry exists because the note editor sets its own type at a fixed size, so at the panel geometry a note is three enormous lines.

## Things that look like they should work and do not

Each of these cost a round trip and is written down so the next person does not pay for it again.

- **The vertical `utility-rail` is rendered only while the drawer is closed**, and clicking one of its icons opens the drawer on the *previously selected* tab rather than on the icon that was clicked. `open_drawer_tab` uses the rail to open and the drawer's own strip to choose, and asserts the heading afterwards - the failure it exists for produced a Notes panel in the Alerts slot.
- **Which drawer tab is showing is persisted per Project on the server**, so it is whatever the previous shot left behind, and pressing the tab that is already showing closes the drawer. Both `open_drawer_tab` and `mobile_drawer_tab` are loops that check before they click, for that reason. Seeding `mux.drawer.tab.v1` in `localStorage` does not fix it: the migration lands on the unscoped presentation rather than on the Project's.
- **Splitting a session that is already a tab is a no-op** (`splitView` returns the layout unchanged when the leaf is already in it), and every freshly spawned session is already a tab. The hero's two-pane layout is set through `PATCH /api/projects/{id}` with the v1 layout form, whose whole content is an ordered list of session ids.
- **A PTY is shared and is resized by whichever client is attached.** A pane written at 1400 CSS px and re-attached at 402 reflows its scrollback into itself, and no later write repairs what is already in the buffer. Every scene command list opens with `cls`, and the typing happens inside the shot, at the width that shot will use.
- **The note write field is `markdown`.** `body` is accepted and ignored, and leaves an empty note that photographs as the editor with nothing in it.
- **Wait for the fleet, not for a clock.** The sidebar renders empty for a moment on a cold load; a fixed sleep photographed "Create your first Project" once.
- The third tab is off-screen on a phone, so a mobile shot selects its session through the navigation overlay rather than the tab rail.

## Relationship to the trailer scripts

`capture_live_ui.py` and `render_feature_cut.py` in this folder record the **live** daemon on 8765 and are for the feature trailer; their footage is explicitly not cleared for publication (see `README.md`). The two scripts here are the opposite: they never touch 8765, and everything they record is intended to be published.

The hero video and the short looping demos that `.docs/development/RELEASE_MANUAL_TASKS.md` § 7 also asks for are built, in this same environment, and they have their own brief: [`HERO.md`](HERO.md).
Everything on this page applies to them unchanged - the synthetic install, the leak scan, the by-eye review - with one addition that only moving pictures need.

**A still can be looked at once; a loop has to be checked frame by frame, and one crop there is a redaction rather than a composition.**
The beats that film real claude sessions carry the CLI's statusline, which renders the operator's actual 5-hour and weekly subscription spend as digits *inside a terminal cell grid* - the one place `scan_for_leaks` cannot reach, because it reads the DOM.
`encode_loops.py` crops that band off, and `--frames` on both encoders dumps the *encoded* file for the review, because reviewing the raw take proves nothing about what shipped.
The loops are cut from the hero's own takes rather than shot separately, so a UI change cannot leave the page and the film disagreeing about what the product looks like.
