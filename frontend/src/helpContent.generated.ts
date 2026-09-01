// GENERATED FILE - do not edit by hand.
// Regenerate: node frontend/scripts/build-help-content.mts
// Source of truth: .docs/design/features/ (see HELP_SECTIONS in that script).
// frontend/test/helpTopics.test.ts fails when this file drifts from those docs.
import type { HelpDocContent } from './helpTopics.ts'

export const HELP_DOC_CONTENT: HelpDocContent[] = [
  {
    "topic": "scan-timeline",
    "doc": ".docs/design/features/scan-timeline.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "The scan timeline is a read-only, run-scoped semantic index over bounded transcript deltas and deterministic Tier 0 facts. It produces a readable behavioral history without writing to a PTY, changing agent state, or ranking attention. It is the Tier 1 substrate for dead-end memory and later cross-session semantic consumers."
          }
        ]
      },
      {
        "heading": "Authorization and lifetime",
        "blocks": [
          {
            "kind": "p",
            "text": "Three independent gates must all be open before a scan can call OpenRouter:"
          },
          {
            "kind": "ul",
            "items": [
              "The global scan_timeline_enabled master switch is on. It is the scan row's Global switch on the Automation Policy matrix; the Automation workspace shows its state and links there.",
              "The Project enables scan_timeline and its raw_store and tier0 dependencies.",
              "The current agent_run_id is enabled from that session's Timeline drawer tab."
            ]
          },
          {
            "kind": "p",
            "text": "The Timeline tab's off state names whichever of the first two gates is closed and links to that exact switch (setting-links.md), rather than describing where it lives."
          },
          {
            "kind": "p",
            "text": "The Timeline drawer exposes the Project permission directly. Turning it on also enables the required dependencies and creates a blank .swe-mux/project-context.md if needed. Turning it off disables Scan timeline and consumers that require it. Changing Project permission never authorizes a current run and never starts a backfill."
          },
          {
            "kind": "p",
            "text": "The run gate defaults off. It belongs to one provider conversation, not the persistent terminal session. /clear, /new, another conversation rollover, session exit, and session crash disable the old run and never authorize the successor. A rollover writes a visible boundary record and resets the transcript cursor, continuity window, and novelty comparison."
          },
          {
            "kind": "p",
            "text": "That boundary is right as a cost decision and, repeated per conversation, is pure friction for a Project that has already decided it wants a timeline. scan_timeline_auto_enable in the Project config answers \"yes, always\" once. Since 2026-08-31 it inherits like an opt-in rather than being written per Project: unset means follow Config.scan_timeline_auto_enable_default, so an operator answers it once for the install and a Project overrides only where it disagrees (automation-enablement.md). It was previously written into every Project the creation form armed, which is exactly why nobody could change their mind about it in one place. It only ever creates a grant for a run that has no row at all: a run the human switched off, or one an ended session disabled, stays off, because an off switch that re-arms itself is not an off switch. The snapshot reports auto_enable but never applies it - a read that started scanning would make opening the drawer a spending decision - so a brand-new run reads as off until its first trigger arms it. Turning the Project's permission off clears the flag, so re-permitting later does not silently re-arm every conversation."
          }
        ]
      }
    ]
  },
  {
    "topic": "git",
    "doc": ".docs/design/features/git.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "ul",
            "items": [
              "Attached sessions poll the latest accepted live cwd (or spawn cwd until live telemetry is available) for HEAD, branch, dirty count, upstream divergence, linked-worktree identity, working-tree root, lines changed against HEAD, and lines and files changed against the comparison ref.",
              "User-initiated worktree API wraps git worktree without performing other mutating git operations.",
              "Durable provenance connects a commit to the session and agent run whose evidence observed or created it without changing the repository."
            ]
          }
        ]
      }
    ]
  },
  {
    "topic": "prompt-queue",
    "doc": ".docs/design/features/prompt-queue.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "Durable, ordered messages staged against a target agent run, delivered through one typed operation, surviving daemon and browser restarts without duplicate delivery. Roadmap Phase 4. The storage model is mailbox-shaped, so Phase 5's senders (agent messages, remote devices) and the control-plane queue-draft channel (CONTROL_PLANE_ROADMAP.md §13) are new callers of the same typed operations, not new delivery paths."
          },
          {
            "kind": "p",
            "text": "Phase 4 delivers only on an explicit user act. Phase 5 adds two bounded callers on top, documented separately: auto-delivery.md (who else may press send, and under what gate) and agent-messaging.md (who else may put a message in a queue). The auto-delivery install master is off by default; once enabled, each live Claude/Codex conversation gets a bounded default-on grant that can be turned off for that conversation. Agent-authored queueing stays separately opt-in."
          }
        ]
      }
    ]
  },
  {
    "topic": "scheduled-runs",
    "doc": ".docs/design/features/scheduled-runs.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "A schedule does one of two things in one Project on its own, on a cron expression, on an interval, or once at a time."
          },
          {
            "kind": "ul",
            "items": [
              "Start a new session (action: spawn). The Run menu's launch, deferred: the same SpawnRequest (project, backend, launch profile, cwd, name, seed_text), plus a trigger and an owner.",
              "Reopen an existing conversation (action: resume). The History browser's Resume button, deferred: a conversation named by its history run id, plus a trigger and an owner."
            ]
          },
          {
            "kind": "p",
            "text": "Either carries a prompt, and optionally a queue of messages behind it."
          },
          {
            "kind": "p",
            "text": "That framing is the authorization argument. A schedule is a user-authored deferred press of a button the author could have pressed themselves, so it inherits their authority. It is not the decision-gated \"model-authored action selection, autonomous worker spawning\" in ../../development/ROADMAP.md: no model chooses to create, edit, or fire one. What it did owe that gate is the rest of the checklist, and those are the guards in this document."
          }
        ]
      }
    ]
  },
  {
    "topic": "attention-ranking",
    "doc": ".docs/design/features/attention-ranking.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "The layer that decides which of many concurrent sessions actually needs the human, and when. Every earlier control-plane layer writes findings; this one routes them. Roadmap Phase 6.5, control-plane build-order steps 6 and 7 (../../development/CONTROL_PLANE_ROADMAP.md §6.7, §6.8, §14)."
          },
          {
            "kind": "p",
            "text": "It produces no session writes. Routing a finding to a channel, and holding a channel back, is the entire output."
          }
        ]
      }
    ]
  },
  {
    "topic": "agent-environment",
    "doc": ".docs/design/features/agent-environment.md",
    "sections": [
      {
        "heading": "Purpose",
        "blocks": [
          {
            "kind": "p",
            "text": "Agent Environment is a session-selected, read-only inventory of the Claude Code or Codex CLI behind the focused terminal. It answers which runtime options, tools, skills, MCP servers, plugins, hooks, agents, policies, and feature overrides can be discovered without changing the CLI. It is not an execution surface and is not proof that a configured extension connected successfully."
          },
          {
            "kind": "p",
            "text": "The utility drawer owns a separate Agent tab titled Agent Environment immediately after Transcript. Instructions and memory are the Agent tab's third segment, Instructions, titled Instructions & Memory in its body; it was a separate Project-scoped Context tab until the drawer consolidation. It carries no availability gate, so a shell session focused on the Agent tab still reaches it while Config and Tools — which read a live harness inventory — drop out. Commands remains the action surface for inserting a skill or command into the focused terminal."
          }
        ]
      }
    ]
  },
  {
    "topic": "processes-and-previews",
    "doc": ".docs/design/features/processes-and-previews.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "ul",
            "items": [
              "Per-session descendant attribution and bounded resource/listener snapshots.",
              "Explicit preview leaves for a detected or user-approved literal-loopback development server.",
              "Static document previews: a directory of the Project checkout served by the daemon itself, through the same registry and the same /preview/<id>/ route, with no process and no port."
            ]
          }
        ]
      }
    ]
  },
  {
    "topic": "project-resources",
    "doc": ".docs/design/features/project-resources.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "Safe access to Project-owned notes, the global Scratchpad, a bounded Project file tree, revision-checked text editing, ignore patterns, host file-manager reveal, and leased filesystem watches. Editable resources are pane tabs alongside terminals and previews. The file tree and notes collection are utility-drawer tabs."
          }
        ]
      }
    ]
  },
  {
    "topic": "transcript-branches",
    "doc": ".docs/design/features/transcript-branches.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "ul",
            "items": [
              "A Claude transcript is an append-only DAG, not a list of turns. parentUuid names the record a record answers. A retry, a /rewind, or a resend after a failed request appends a new sibling under the same parent; the previous attempt stays in the file forever.",
              "The live branch is the ancestry of the newest record. The file is append-only, so the last record is by construction on the branch still being written, and the ancestors of that record are exactly the nodes whose subtree contains it.",
              "An abandoned record is one the live branch does not reach. It was never sent to the provider and the CLI stops showing it the moment the conversation branches away.",
              "Every reader in mux read these files in file order until this was built, so a prompt resent eight times through an outage was eight prompts to the Transcript tab, to history search, and to every consumer of the indexing parse. Measured across 60 recent transcripts of one machine (2026-08-18): 37 held off-branch records; 236 records and 36 conversational messages in total."
            ]
          }
        ]
      }
    ]
  },
  {
    "topic": "project-actions",
    "doc": ".docs/design/features/project-actions.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "The Project-level Run menu is the single launch surface for a new Claude, Codex, shell, custom terminal, worktree session, or an explicitly selected repository task. It imports tasks from the Project root and opens every resulting process as an ordinary Project-owned terminal tab."
          },
          {
            "kind": "p",
            "text": "The worktree launcher is an explicit Git operation rather than a Project Action. It creates a named branch below the configured global worktree root through POST /api/git/worktrees, closes the launcher once that durable operation succeeds, then bootstraps and starts the selected backend through POST /api/git/worktrees/session. The browser creates and focuses a client-only pending session at the worktree path on submit, before the checkout exists, as an ordinary tab in the focused pane. Creation failures return to the still-open launcher rather than a toast, because they are what the operator can correct there. The pending row and its leaf are replaced in place by the daemon session when setup and spawn finish. Moving elsewhere during setup is respected: completion updates the pending location without reclaiming focus or the pane's active tab. Its suggested checkout path is grouped by Project and branch below worktree_root, which defaults to <data_dir>/worktrees and is editable in Settings under Git. The resulting absolute path remains editable before creation, and changing the setting does not move existing worktrees. Whitespace entered in the branch field becomes -, keeping the Git branch and suggested filesystem path aligned."
          }
        ]
      }
    ]
  },
  {
    "topic": "keybindings",
    "doc": ".docs/design/features/keybindings.md",
    "sections": [
      {
        "heading": "What it is",
        "blocks": [
          {
            "kind": "p",
            "text": "The keyboard surface: a chord vocabulary that names physical keys, one-to-three-chord sequences behind a leader, a rule list that can be scoped to a host, a platform and a focus context, five shipped presets that are data rather than code, and a per-host capability report that says what a chord will actually do instead of refusing it."
          },
          {
            "kind": "p",
            "text": "The scale it answers: 214 bindable commands. Before this there were 26 default bindings and no way to reach the rest without opening the palette."
          }
        ]
      },
      {
        "heading": "Hosts, platforms, and the one hard refusal",
        "blocks": [
          {
            "kind": "p",
            "text": "A rule may carry host (desktop | browser), platform (win | mac | linux) and when. Resolution happens in the daemon, not the browser, for the same reason the experience-tier assignment does: a browser-computed answer would be a second copy of the policy and the copy is what drifts. The client states what it is (GET /api/keybindings?host=…&platform=…, frontend/src/hostProfile.ts) and gets one answer computed for that keyboard."
          },
          {
            "kind": "p",
            "text": "The desktop shell publishes window.__swemuxDesktopShell on its own page (desktop_permissions.shell_report), and its absence is the signal: a browser tab never has it. The fact it carries is the one the keyboard needs - production WebView2 runs with pywebview's browser accelerators disabled, so that window receives Ctrl+T, Ctrl+W and Ctrl+Tab where no browser tab will."
          },
          {
            "kind": "p",
            "text": "Four tables say what a chord costs, and only the first is a refusal:"
          },
          {
            "kind": "p",
            "text": "| Table | What it means | Refused? | |---|---|---| | APPLICATION_RESERVED | the fixed UI-scale controls | yes, at every position in a sequence | | BROWSER_UNREACHABLE | the page never receives the keydown | no - reported, and live in the desktop app | | BROWSER_CONTESTED | the page receives it and can suppress the browser's own meaning | no - reported with what it costs | | WM_RESERVED[platform] | the compositor takes it first | no - reported per platform | | TERMINAL_RESERVED | what a shell in a pane means by it | no - reported, and scopable away with when |"
          },
          {
            "kind": "p",
            "text": "Plus the AltGr hazard, which is any chord holding both Ctrl and Alt without Meta."
          }
        ]
      }
    ]
  }
]
