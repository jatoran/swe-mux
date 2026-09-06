# First run: resumable setup and learning

## What it is

A four-page path to a working session, followed by optional setup and a UI tour.
The browser, desktop WebView, and phone share progress through the daemon.
Getting started remains above Usage in the sidebar until hidden, and Help restores it.

## Core sequence

1. Experience: Just terminals, Agent workspace, or Smart workspace.
2. Agents: detected harnesses, optional login capture, and the default harness or Shell.
3. Projects: background history suggestions, filtering, multi-selection, or a manual folder.
4. Keymap: cards for the shipped presets, an optional shortcut-editor detour, and Start working.

Retained installations first choose Keep settings, Review setup, or Back up and start fresh.
A preset records ordinary settings; it never gates capabilities by its name.
The stable configuration ids remain `terminal`, `deterministic`, and `automations`.

Start working uses the selected Project and harness through the ordinary session-launch path.
When no Project exists, the Project manager remains available to complete that prerequisite.
A completed first-session task prevents finishing optional setup from launching another session.
The fast path defers optional setup and leaves it available in Getting started.
A user who selected Just terminals proceeds directly from Keymap to the workspace or optional extras.

Only one setup, focused guide, or tour surface is active.
Back and Continue later preserve non-secret draft selections.
Unresolved startup requests remain unknown, and failed initial reads retry.
The experience preset is applied only when the chosen tier changes, so returning through an unchanged page cannot erase later customization.

## Agent detection and project discovery

Harness detection starts when setup mounts.
An unanswered detection and a deliberate empty selection remain distinct.
The Agents page offers Check again, installation guidance, and an explicit Shell-only continuation even before detection finishes.
The default harness must belong to the selected set; Shell is always a valid choice.
Selecting no agents writes explicit disablement using the complete registry.

For account-capable harnesses, setup offers Save current login or Sign in and save.
An in-progress login reports where to finish and polls until it settles.
Capture and login use the ordinary provider-account operations.
Model API credentials remain separate from agent account snapshots.

Selected harnesses start background project discovery on the Agents page.
Each harness has its own bounded request, allowing one result set to populate Projects before another finishes.
Selection changes cancel unneeded requests; navigation between setup pages retains completed results.
Discovery reads up to 300 recent transcripts per harness and resolves at most 200 candidate folders, with a 15-second deadline and cooperative cancellation.
It does not import transcripts or register Projects.

Projects offers a filter over names, paths, and harnesses.
Filtering preserves selected folders outside the current result view.
Select visible and Clear selection support bulk choices.
Unavailable folders stay visible and cannot be selected.
A manual folder remains usable while discovery runs or fails.
Continuing registers selected folders explicitly; partial successes survive a later failure and retries do not duplicate them.
POSIX path identity remains case-sensitive, independent of the browser's operating system.
Existing Project files and explicit Project automation choices remain intact.

## Keymap and optional permissions

Keymap cards carry short descriptions and preset-specific warnings.
Customize applies the chosen preset and opens the existing Settings shortcut editor.
Closing Settings resumes Keymap without reapplying the preset over customized bindings.

Automatic-delivery permissions have a separate optional page.
Review first, Deliver when ready, and Extended runs map to the existing supervised, assisted, and autonomous assignments.
Their numeric limits and individual feature switches are available in a disclosure.
Preset assignments come from the daemon; submitted refinements pass through ordinary configuration validation.
Changing delivery permissions is independent of connecting a provider.

Instrumentation and per-harness integration settings retain their existing restart scope.
Setup reports a required daemon reload and never restarts live sessions automatically.

## Model prerequisites and activation

Choosing Smart workspace first applies the model-free base and records pending model features.
Explicitly disabling both model-backed masters removes the provider prerequisite.
The provider page starts with connection controls and then shows two read-only model rows: Cheap and Regular.
Each row names the actual model and input/output prices where the catalog reports them.
Change opens a picker only for that role.
The daily automation limit remains a summary until Adjust is opened.
Unknown cost is stated as unknown.

Model choices carry default scan-timeline and assistant pins along with the Cheap and Regular pair.
Explicit feature overrides remain unchanged and are edited in advanced Settings.
A catalog-less local endpoint resolves every role to its configured single model.
API keys stay in the platform secret store and never enter a setup draft.

Endpoint verification and model-role verification are distinct.
The existing bounded capability probes prove structured output and assistant tool calls without executing a returned tool.
Verification remains bound to the endpoint, credential fingerprint, and effective model ids.
The model configuration operation requires the current config revision.
A changed provider or a failed role check prevents activation.

Finishing provider setup grants only requested model features.
It never reapplies an experience preset or rewrites fleet access, automatic-delivery authority, or unrelated feature choices.
Per-Project permissions, inherited dependencies, budgets, and current-run opt-ins still apply.
Set up later preserves pending model intent without changing the current feature configuration.

## Voice, desktop, phone, and tour

Optional extras are focused guides reached from one page or Getting started.
Desktop offers actual shortcut and startup state.
Phone uses the shared Tailscale state, private-access controls, HTTPS setup, QR code, and explicit confirmation that the workspace opened.

Voice separates local reading, local dictation, and optional AI summaries or conversation.
Users choose built-in speech where supported or install local neural speech and recognition models through the existing download panels.
Each panel retains explicit download actions, progress, failure details, and retries.

AI voice controls require verified model readiness.
A configured provider shows inherited models and prices.
Add provider opens the shared connection flow and returns to the same voice choices.
This detour never activates unrelated automations.

Voice completion is an explicit tested outcome, not an enabled config switch.
Read-aloud completion requires successful playback and user confirmation.
Dictation requires an available recognition engine, a real microphone utterance transcribed through the ordinary endpoint, and confirmation of the returned text.
The test sends nothing to an agent.
Capture has bounded timeouts and releases the microphone when canceled or unmounted.
Closing unfinished voice setup preserves choices without marking it complete.

The UI tour is optional and follows real Projects, Run, tabs, splits, resources, Settings, and Help.
It uses an existing Project where available and lets users skip any action step.
Its current step and active, deferred, or completed state persist on the daemon.
Completion, dismissal, collapse, and hiding Getting started remain separate operations.

## Persistence and recovery

`onboarding.json` stores version, revision, installation identity, current page, setup status, tour progress, task lists, section visibility, and a closed non-secret draft.
Drafts include experience, harness choices, Project selections and filter, keymap, delivery refinements, pending model intent, provider form values, and voice choices.
Keys, captured audio, and playback-test confirmation are excluded.
Optimistic browser edits merge with serialized revision-checked saves instead of replacing unrelated progress.
Writes replace the document atomically; malformed state is preserved before a recoverable preferences choice is offered.

Start fresh backs up global configuration, keyboard bindings, and progress under `setup-backups/`.
Projects, repository files, history, accounts, credential stores, and connection identity remain.
Start fresh replays setup against the install that exists; the factory reset ends that install and re-enters this sequence with nothing to replay against (`factory-reset.md`).
The two are not degrees of each other: everything Start fresh deliberately keeps is what a factory reset exists to remove.
Installation-location changes offer retained preferences again.
Help and `swemux setup --restart` provide an explicit restart at any time.

`swemuxd --new-user-profile NAME` selects an isolated local test profile.
It uses separate data and a separate port, disables remote listeners, and cannot be combined with `--config`.
Do not run a daemon from a development worktree.

Progress transitions, backups, discovery outcomes, model choices, verification results, and feature grants enter rotating daemon logs with request correlation.
Credentials are excluded.

## Key files

- `src/swe_mux/onboarding.py`: persistence, closed draft schemas, revisions, and preference backups.
- `src/swe_mux/routes/onboarding.py`: progress, discovery, model configuration, verification, and restricted activation.
- `src/swe_mux/model_setup.py`: model-pair assignment and configuration-bound proof.
- `src/swe_mux/experience_tiers.py`: experience and delivery preset policy.
- `frontend/src/onboarding.ts`: synchronization, serialized saves, and optimistic draft merging.
- `frontend/src/OnboardingFlow.tsx`: page transitions, fast-path completion, and focused guides.
- `frontend/src/HarnessSetup.tsx`, `frontend/src/setupHarnesses.ts`: experience cards, detection, and agent choices.
- `frontend/src/setupDiscovery.ts`, `frontend/src/SetupProjects.tsx`: incremental discovery, filtering, selection, and registration.
- `frontend/src/SetupKeymap.tsx`, `frontend/src/SetupPermissions.tsx`: keymap and automatic-delivery pages.
- `frontend/src/ProviderSetup.tsx`, `frontend/src/SetupModelSummary.tsx`, `frontend/src/setupActivation.ts`: connection, concise model choices, and narrow activation.
- `frontend/src/VoiceSetup.tsx`: acquisition, provider detour, and functional voice checks.
- `frontend/src/GettingStarted.tsx`, `frontend/src/GuidedTutorial.tsx`: ongoing setup and learning.
- `tests/test_onboarding.py`, `frontend/test/renderer/onboarding.spec.ts`: persistence, prerequisites, and user-flow regressions.

## Relates to

- `automation-enablement.md`: inherited defaults, dependency closure, and ceilings.
- `provider-accounts.md`: login capture and account snapshots.
- `keybindings.md`: presets, host conflicts, and shortcut editing.
- `voice.md`: speech engines, downloads, and transcription.
- `desktop-shell.md`, `remote-access.md`: desktop integration and phone access.
- `factory-reset.md`: ending the install and re-entering this sequence from nothing.
