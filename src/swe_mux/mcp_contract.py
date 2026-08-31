"""Shared declarations for the closed mux MCP capability surface."""

from __future__ import annotations

READ_TOOL_NAMES = (
    "list_sessions",
    "get_session",
    "read_transcript",
    "search_history",
    "memory_sources",
    "read_memory",
    "project_notes",
    "read_project_note",
    "project_actions",
    "message_status",
    "spawn_requests",
    # Which models a harness's CLI actually has on this machine, asked of the CLI
    # itself. A read in the strictest sense - it runs the harness's own listing
    # command and nothing else - and it is here rather than folded into
    # `request_spawn`'s refusal because an agent choosing a model has to be able
    # to look before it asks. Deliberately advisory: absence from the list never
    # refuses a spawn, because a catalogue lags every vendor release
    # (`model_catalog.py`).
    "list_models",
    # Phase 7.5 cross-session memory reads (CP §6.1-6.3, §6.10). Deterministic
    # queries over Tier 0 facts, the git-provenance ledger, the experience
    # corpus, and the scan timeline. Each is per-project opt-in through the
    # enablement DAG and returns nothing in preference to a weak match.
    "provenance",
    "verified_status",
    "prior_resolutions",
    "dead_ends",
    # Phase 7.10: which docs owe an update for a Project's recent source changes,
    # re-derived from each doc's "Key files" section and gated on the doc-debt
    # detector's own per-Project opt-in.
    "doc_debt",
    # Phase 7.11: the scan timeline as an agent-readable surface. `scan_timeline`
    # is session-scoped and gated on that session's Project opting into
    # `scan_reads`; `scan_search` is the already-shipped semantic query over
    # distilled records, gated on `semantic_history_search`. Reads only - no scan
    # or backfill is reachable through MCP, because a scan spends the human's
    # gated budget.
    "scan_timeline",
    "scan_search",
    # Phase 7.9 code-structure graph reads (deterministic, model-free). Pull-only:
    # the agent consults them on its own initiative, nothing is pushed. Each is
    # gated on the per-Project `code_graph` opt-in and returns empty rather than a
    # low-confidence guess; static reverse-caller sets are labelled a lower bound.
    "blast_radius",
    "find_definition",
    "find_callers",
    "find_references",
    "code_context",
    "test_gap",
    # Session-settle watches. Declared a read, and the reasoning is worth
    # stating because the tool does eventually cause a message: it reads a
    # target's state and nothing else, and the one write it matures into is a
    # fixed daemon-authored template into the *caller's own* prompt queue. It
    # addresses nobody, actuates nothing, spends nothing, and re-arming returns
    # the watch that already exists - so it grants strictly less than
    # `list_sessions` polled in a loop, which is exactly what it replaces.
    # Permission-gating it would put an approval prompt in front of the
    # monitoring call an orchestrator makes most often, buying nothing.
    "watch_session",
    # Which checkout the targetless land tools would use. A pure read over the
    # caller's live cwd and its run-bound selection; it changes neither.
    "worktree_context",
)

WRITE_TOOL_NAMES = (
    "notify",
    # Withdraw one still-undelivered message the caller itself sent. A write,
    # and the narrowest one here: it can only cancel a message already attributed
    # to the caller, and nothing it touches has reached anyone. It is on this
    # list rather than the read list because it changes queue state, not because
    # it grants anything - a sender that can stage a message can obviously
    # un-stage it, and before this existed a stranded duplicate had no
    # MCP-reachable cleanup at all.
    "revoke_message",
    "request_spawn",
    # `run_action` is a write in the sense that matters here: it starts a process.
    # Its authority comes from the exact-bytes approval a human already gave the
    # task file that defines the action, so it can only run commands the user has
    # seen and approved. An agent authoring a new action changes that file's
    # fingerprint, which un-trusts it, so an agent cannot approve its own command.
    "run_action",
    # Phase 7.6 session control (CP §7.6, §16). The first tools that act on a
    # running agent. Kept as two tools with different blast radii, governed by a
    # per-Project grant: `granted` (the default since 2026-08-25) acts directly
    # inside the daemon's bounds, and a Project lowered to `draft` gets the
    # inert approval request a human acts on.
    "interrupt",
    "end_session",
    # Select an exact Git-listed linked worktree for a Codex-style session whose
    # host process remains on trunk. This is deliberately separate from the
    # dangerous land call: the selection is validated, run-bound and auditable,
    # while request_land/request_verify keep accepting no target at all.
    "use_worktree",
    # Phase 14: ask for this worktree's branch to be landed. A request, not the
    # action - it enqueues, and the daemon's fixed git vocabulary is what runs. Like
    # interrupt/end it defaults to a per-Project `draft` grant, so the call writes an
    # inert request a human approves. Deliberately session-scoped with no target
    # argument: a live linked-worktree cwd wins (Claude), otherwise only the
    # separately validated run-bound selection may supply the checkout (Codex).
    "request_land",
    # Phase 2a: the same pipeline stopped before its last step. It moves no trunk, so
    # the `draft` grant enqueues it rather than drafting it - there is nothing for a
    # human to decide in advance about merging the trunk into the requester's own
    # branch and running bytes a human already approved. `off` still refuses it. It is
    # a separate tool rather than a flag on `request_land` so the dangerous call is
    # never the default spelling of the safe one, and it carries the same
    # targetless scoping: no target argument at all.
    "request_verify",
)


#: The configurator agent's own reads (`configurator.py`). Kept apart from
#: `READ_TOOL_NAMES` because they are not part of the fleet surface at all: they
#: describe and diagnose *swe-mux*, not the work any session is doing, and they
#: are listed only to sessions the daemon itself launched as configurators. An
#: ordinary session neither sees them in `tools/list` nor can call them.
CONFIGURATOR_READ_TOOL_NAMES = (
    "configurator_capabilities",
    "configurator_guide",
    "configurator_diagnostics",
    # The other two settings locations. Reads only: `device_settings` serves the
    # per-device UI store (the command rail, sounds, alerts) with the rail
    # resolved into something legible, and `project_settings` serves one
    # Project's own committed config with its automation closure resolved.
    #
    # They exist because the alternative is what actually happened without them:
    # the agent found and parsed `~/.mux/settings.json` itself, spent 195 KB of
    # transcript on a question about twelve strings, and - having no way to
    # resolve a Project id to a name - attributed another Project's rail button
    # to the one it was standing in (2026-08-24).
    "configurator_device_settings",
    "configurator_project_settings",
)

#: The configurator's writes: one per settings location, each through the same
#: validated path the corresponding UI surface uses.
#:
#: `apply_settings` changes install-wide config through `update_config` - the same
#: call `PATCH /api/config` makes. `apply_project_settings` writes a Project's own
#: committed `.swe-mux/config.toml` through `write_project_config`, which is
#: revision-guarded and validates against a closed field set that *refuses* the
#: daemon-authority keys outright. `edit_device_settings` is the odd one and its
#: shape reflects that: five of the seven device-settings domains are stored
#: opaquely because the browser owns their schema, so it takes path-scoped
#: operations rather than a document - everything an operation did not name is
#: untouched by construction, which is the only safety available when nothing in
#: this process can tell a valid rail from a mangled one (`settings_patch.py`).
#:
#: Three tools rather than one with a `location` argument, deliberately. They have
#: different blast radii and different validators, and collapsing them would make
#: the committed, repository-shared write the same call as the local one.
CONFIGURATOR_WRITE_TOOL_NAMES = (
    "configurator_apply_settings",
    "configurator_edit_device_settings",
    "configurator_apply_project_settings",
)


def claude_read_permissions() -> list[str]:
    """Claude permission rules for the read-only mux MCP tools.

    The configurator reads are included even though almost no session can call
    them. A permission rule only decides whether the CLI *prompts*; the daemon
    still refuses a caller that is not a configurator, so pre-allowing them
    grants nothing and spares the one session that can from an approval dialog
    in front of every "what is this setting called" lookup. The configurator
    *write* is deliberately absent: a settings change is exactly the thing a
    human should see before it happens.
    """
    return [
        f"mcp__mux__{name}" for name in (*READ_TOOL_NAMES, *CONFIGURATOR_READ_TOOL_NAMES)
    ]
