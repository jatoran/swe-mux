# First run: resumable setup and learning

## What it is

One machine-owned setup sequence, followed by a UI tour and optional first steps.
The browser, desktop WebView, and phone share progress through the daemon.
Getting started remains above Usage in the sidebar until explicitly hidden, and Help restores it.

## Sequence

1. Existing preferences, when retained data or a different installation is detected.
2. Experience tier, theme, keyboard preset, and optional granular refinements.
3. Provider and model setup when the selected features need language models.
4. Harness detection, default harness, fleet access, and optional account capture.
5. Project folders, discovered from native history or selected manually.
6. Desktop integration, using actual shortcut and startup registration state.
7. A choice to start the UI tour, launch a session, or explore independently.

Only one setup or tour surface is active at a time.
An unresolved startup request is unknown, never evidence that setup completed.
Failed initial reads retry with bounded delays, and successful event reconnection refreshes config and progress.
Continue later preserves the current page and non-secret experience and harness selections.
The sidebar resumes the sequence without covering the workspace until selected.
Opening a focused phone, voice, model, or desktop guide suppresses the tour.

## Experience presets

A tier assigns ordinary settings and never acts as a runtime capability gate.
Every switch stays editable through its owning settings or automation surface.

- Pure terminal disables instrumentation, fleet surfaces, and the tier-managed automation defaults.
- Deterministic enables the model-free fleet layer and the recommended project-memory defaults with their dependency closure.
- Automations includes Deterministic and the model-backed starting set, with the corresponding master switches.

The presets also assign their managed global automation ceilings.
`automation_project_defaults` is merged over existing entries, replacing only the preset's own inventory.
Explicit Project decisions continue to outrank inherited defaults, subject to the global ceiling.
Unrelated automation defaults are preserved.
Setup and Settings render the daemon's preview of labels, resulting values, and differences before applying a preset.
Reapplying the current tier is allowed because its individual settings may have changed.

Autonomy remains a separate axis: supervised, assisted, or autonomous.
Selecting model-backed features does not itself select unattended agent authority.
Instrumentation changes may require a daemon reload; setup reports this and never restarts live sessions itself.

## Model prerequisites

Selecting Automations records intent before enabling its masters.
The provider page uses the same connection controls, credential operation, model routing table, and budget controls as Settings.
API keys stay in the platform secret store and never enter the progress draft.
An authenticated harness account is separate from the model API provider.
A compatible local server may require no key.

Endpoint verification is followed by explicit approval of cheap, standard, scan timeline, and assistant model roles.
OpenRouter suggestions use the existing scan and assistant defaults only when the catalog lists them.
Other feature overrides remain available in a disclosure.
A server with no model catalog resolves every role to its single configured model.

The role check sends at most six distinct structured-output probes and one tool-call probe.
No returned tool call executes.
A failed role check leaves model-backed activation unavailable.
Successful verification is bound to the endpoint, credential fingerprint, and role model ids in `model-setup-verification.json`.
Changing one invalidates that proof for a later tier application.
Model settings changing during verification refuse approval rather than stamping a different configuration.

Continue with Deterministic applies that preset explicitly and leaves model setup available in Getting started.
The project creation form and grant gates offer guided model setup before permitting model-dependent activation.
Per-project permission, current-run scan opt-ins, budgets, and authority checks still apply after setup.

## Projects and accounts

Detection checks the registry of supported harnesses.
The user chooses a default harness for Run, with the first detected harness suggested and the choice corrected when it is deselected.
For harnesses whose account manager supports capture, an external system login offers Save current login.
An already-saved account is identified, and an unreadable or absent login is stated without attempting capture.

Project discovery reads native harness history independently of swe-mux's Project registry.
It aggregates working folders, resolves repository roots, groups linked worktrees under the common repository, and orders by recent activity.
The scan is bounded to recent transcripts and at most 200 candidate working folders, with a 45-second request deadline and cooperative cancellation.
Unavailable folders remain visible and cannot be selected.
Registration is explicit, and successfully added folders remain registered if another selection fails.
Project files and their explicit automation choices are not overwritten by discovery.

## First steps and tour

First steps include a Project, a first session, desktop integration, phone access, voice, and deferred model setup when relevant.
Worktrees live under Explore more, alongside documentation and the live website demo.
Tasks are not drawn in the central empty workspace or duplicated on setup's final page.
Completion, dismissal, collapsing the section, and hiding the section are distinct operations.
Dismissed tasks can be restored.

The UI tour follows setup and teaches navigation, Run, tabs, splits, resources, Settings, and Help.
It omits the account configuration step after setup and uses an existing Project when available.
Its current step and active, deferred, or completed status persist on the daemon.
Browser-local tutorial storage is not the application authority.
Phone connection is a focused guide with Tailscale state, private access enablement, HTTPS setup, a QR code, refresh, and explicit user confirmation that the workspace opened.
Voice uses the existing guided voice setup.

## Retained data and fresh preferences

The first use of the progress schema offers existing users a retained-preferences choice once.
Subsequent upgrades in the same installation leave progress intact.
A changed installation location offers the choice again.
A reinstall into the identical location cannot always be distinguished from an upgrade by a wheel-installed application.
Help and `swemux setup --restart` therefore provide the explicit route at any time.

Start fresh backs up global configuration, keyboard bindings, and progress into a uniquely named `setup-backups/` directory before resetting preferences.
Projects, repository files, history, account snapshots, credential stores, and connection identity are retained.
Keyboard bindings reset to the shipped preset.
Restart-scoped preferences are reported and take effect at a later reload.
The operation uses revision checks; a stale client cannot reset preferences after another client changes setup.

`swemuxd --new-user-profile NAME` selects a named profile under `~/.mux-test-profiles/`, separate from the ordinary data directory.
It disables remote listeners and uses a separate local port, or the explicitly supplied `--port`.
Names are restricted to letters, digits, underscores, and dashes and cannot be combined with `--config`.
This supports manual new-user testing without removing `.mux`.
Do not run a daemon from a development worktree.

## Persistence and diagnostics

`onboarding.json` stores schema version, optimistic revision, installation identity, current page, setup status, tour status and step, section visibility, task lists, and a closed non-secret draft.
Writes replace the document atomically.
Unknown fields, malformed values, and stale revisions are refused.
Unreadable progress is preserved before a recoverable existing-preferences page is offered.
State transitions, backups, discovery outcomes, and model verification results enter the daemon's rotating logs with request correlation.
Credential material is excluded.

## Key files

- `src/swe_mux/onboarding.py`: progress persistence, validation, installation identity, and preference backups.
- `src/swe_mux/routes/onboarding.py`: progress, reset, project discovery, and model-role verification routes.
- `src/swe_mux/model_setup.py`: configuration-bound verification proof.
- `src/swe_mux/experience_tiers.py`, `src/swe_mux/routes/settings.py`: preset policy, preview, prerequisites, and application.
- `frontend/src/onboarding.ts`: client synchronization, retry, and revision handling.
- `frontend/src/OnboardingFlow.tsx`, `frontend/src/HarnessSetup.tsx`: setup orchestration and experience/harness pages.
- `frontend/src/ProviderSetup.tsx`, `frontend/src/ExperiencePreview.tsx`: controls shared with Settings.
- `frontend/src/SetupAccounts.tsx`, `frontend/src/SetupProjects.tsx`, `frontend/src/DesktopSetup.tsx`: focused setup steps.
- `frontend/src/GettingStarted.tsx`, `frontend/src/GuidedTutorial.tsx`, `frontend/src/ConnectPhone.tsx`: ongoing learning surfaces.
- `tests/test_onboarding.py`, `frontend/test/renderer/onboarding.spec.ts`: persistence, prerequisites, and user-flow regressions.

## Relates to

- `automation-enablement.md`: inherited defaults, dependency closure, and global ceilings.
- `provider-accounts.md`: existing login capture and account snapshots.
- `desktop-shell.md`: native shell, shortcut registration, and startup behavior.
- `remote-access.md`: supported private phone connectivity.
- `setting-links.md`: in-place grants and links to owning editors.
