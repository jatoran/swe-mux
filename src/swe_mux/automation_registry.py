from __future__ import annotations

from dataclasses import dataclass

# The enablement DAG for the control-plane substrate and consumers.
#
# Every automation is per-project opt-in. Substrate captures facts but never acts
# or spends; consumers are assembled from substrate. A consumer is only *effective*
# when the full transitive closure of its dependencies is also opted in — this is
# the gate that keeps nothing running without the foundation it reads from. See
# .docs/development/CONTROL_PLANE_ROADMAP.md §8.

SUBSTRATE = "substrate"
CONSUMER = "consumer"


@dataclass(frozen=True, slots=True)
class Automation:
    id: str
    kind: str
    label: str
    requires: tuple[str, ...] = ()
    # False while the automation is a reserved id with no implementation behind
    # it. The toggle surface renders dependencies straight from this registry, so
    # a placeholder edge presented as a complete dependency set would let a user
    # switch on something that then does nothing — and would contradict the
    # published design while looking authoritative.
    implemented: bool = True


_AUTOMATIONS: tuple[Automation, ...] = (
    # Substrate.
    Automation("raw_store", SUBSTRATE, "Raw transcript store"),
    Automation("tier0", SUBSTRATE, "Deterministic fact capture", ("raw_store",)),
    Automation("scan_timeline", SUBSTRATE, "Scan timeline", ("tier0", "raw_store")),
    # Consumers. The deterministic four (control-plane step 3) are model-free
    # queries over Tier 0 and ship together; everything below them needs a layer
    # that does not exist yet and is marked unimplemented rather than toggleable.
    Automation("provenance_graph", CONSUMER, "Provenance graph", ("tier0",)),
    Automation("declared_vs_verified", CONSUMER, "Declared vs verified", ("tier0",)),
    Automation("loop_detection", CONSUMER, "Loop / stall detection", ("tier0",)),
    # Doc debt derives its file → owning-doc map from the repository's own docs,
    # so it needs Tier 0 and nothing else. It previously claimed a project_card
    # dependency that its implementation does not use.
    Automation("doc_debt", CONSUMER, "Doc-debt ledger", ("tier0",)),
    Automation(
        "dead_end_memory",
        CONSUMER,
        "Dead-end memory",
        ("tier0", "scan_timeline"),
    ),
    # Phase 7.9: the deterministic code-structure graph. Model-free — it parses
    # the Tier 0 file_write stream with tree-sitter and stores nodes/edges, so it
    # reads Tier 0 and nothing else. Gates the blast-radius/navigation/context/
    # test-gap MCP reads, the human blast-radius annotations, and the per-session
    # change map. Off by default; costs no tokens.
    Automation("code_graph", CONSUMER, "Code-structure graph", ("tier0",)),
    # Phase 7.5: the per-project opt-in that gates the `mux.prior_resolutions`
    # MCP read. It reads the experience corpus (model-scored verified fixes,
    # keyed by normalized error signature), which no detector produces, so it is
    # its own consumer id rather than a read over another automation's output. It
    # needs Tier 0 as the base fact record the experience corpus is derived from.
    Automation("prior_resolutions", CONSUMER, "Prior resolutions", ("tier0",)),
    # Phase 7.7: adaptive session title. It broadens an auto-named run's title
    # only on a genuine scope pivot detected over that run's scan records, so it
    # reads the scan timeline. Off by default and independently toggleable; with
    # it off, titling stays the one-shot behaviour.
    Automation(
        "continuous_title",
        CONSUMER,
        "Adaptive session title",
        ("scan_timeline",),
    ),
    Automation(
        "cross_session_interlocks",
        CONSUMER,
        "Cross-session interlocks",
        ("provenance_graph",),
        implemented=False,
    ),
    Automation(
        "absence_report",
        CONSUMER,
        "Absence report / digest",
        ("scan_timeline",),
    ),
    # Phase 7.7 near-term scan-timeline consumers. Each is a cheap derivation over
    # the per-record behavioral spine, independently toggleable, and reads the
    # scan timeline it is assembled from.
    #
    # Phase-transition signals emit an event on a genuine work_phase pivot or a
    # prolonged flat-novelty stall, feeding the attention channels. It shares the
    # adaptive titler's one pivot definition, so the two never disagree.
    Automation("phase_transitions", CONSUMER, "Phase-transition signals", ("scan_timeline",)),
    # Timeline-based handoff regenerates the handoff export from a run's scan
    # spine rather than from raw annotations, so it is phase-structured.
    Automation("timeline_handoff", CONSUMER, "Timeline-based handoff", ("scan_timeline",)),
    # Catch-me-up is an on-demand per-session / per-Project rollup of the scan
    # spine: phases gone through, claims, and what is blocking.
    Automation("catch_me_up", CONSUMER, "Catch-me-up digest", ("scan_timeline",)),
    # Live blockers aggregates the `blockers` field across active sessions into a
    # fleet glance without opening any of them.
    Automation("live_blockers", CONSUMER, "Live blockers view", ("scan_timeline",)),
    # Semantic history search resolves a query against distilled scan
    # summary/intent/target records rather than a raw transcript grep.
    Automation(
        "semantic_history_search",
        CONSUMER,
        "Semantic history search",
        ("scan_timeline",),
    ),
    # Phase 7.11: whether *agents* may read this Project's scan timeline through
    # the `scan_timeline` MCP tool. Its own consumer id rather than the
    # `scan_timeline` substrate id, because a distilled intent summary is in some
    # ways more revealing than the transcript excerpt it was derived from, and
    # gating agent reads on the substrate would leave no way to keep the timeline
    # while withholding it from siblings. Off by default; costs no tokens.
    Automation("scan_reads", CONSUMER, "Agent scan-timeline reads", ("scan_timeline",)),
    # "← everything" in the design: ranking has nothing to rank without the
    # detectors and the timeline that feed it. The old `("tier0",)` would have let
    # the toggle surface present a one-dependency tree as complete.
    Automation(
        "attention_ranking",
        CONSUMER,
        "Attention ranking",
        (
            "tier0",
            "scan_timeline",
            "loop_detection",
            "declared_vs_verified",
            "doc_debt",
        ),
    ),
    # The model tier, and the only automation here that spends tokens. It is a
    # "why" over items ranking has already produced, so it depends on ranking
    # rather than on the detectors: with ranking off there is nothing to narrate.
    Automation("model_narration", CONSUMER, "Model narration", ("attention_ranking",)),
    # Keep the persisted id for settings compatibility. The human observation
    # inbox UI is retired; this now names review of agent spawn requests in the
    # Fleet Queue.
    Automation("observation_inbox", CONSUMER, "Spawn request review"),
    Automation("screenshot_to_agent", CONSUMER, "Screenshot to agent"),
    # Phase 7.6: the per-Project opt-in that makes the `interrupt` and
    # `end_session` MCP tools reachable at all. It gates a capability rather than
    # a read over another consumer's output, so it depends on no substrate; the
    # delivery-readiness predicate an interrupt gates on is intrinsic, not an
    # opt-in. Off by default (not in any defaults template), and even when on the
    # authority defaults to `draft` - a human approves every action - until the
    # Project's `session_control_grant` is raised to `granted`.
    Automation("session_control", CONSUMER, "Agent session control", ()),
    # Scheduled runs: cron/interval/one-off spawns of an agent session in this
    # Project, authored by a human ahead of time. Like `session_control` it gates
    # a capability rather than a read over another automation's output, so it
    # depends on no substrate. Off by default, and permission alone starts
    # nothing: the schedules themselves are machine-local rows in the daemon's
    # database, so a clone that inherits this opt-in has none of them
    # (`schedule_store.py`).
    Automation("scheduled_runs", CONSUMER, "Scheduled runs", ()),
)

REGISTRY: dict[str, Automation] = {automation.id: automation for automation in _AUTOMATIONS}


def _validate_registry() -> None:
    for automation in REGISTRY.values():
        if automation.kind not in {SUBSTRATE, CONSUMER}:
            raise ValueError(f"{automation.id} has unknown kind {automation.kind}")
        for dependency in automation.requires:
            if dependency not in REGISTRY:
                raise ValueError(f"{automation.id} requires unknown automation {dependency}")
            if automation.kind == SUBSTRATE and REGISTRY[dependency].kind == CONSUMER:
                raise ValueError(
                    f"substrate {automation.id} cannot depend on consumer {dependency}"
                )
    # Reject cycles so the closure walk always terminates.
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> None:
        if node in done:
            return
        if node in visiting:
            raise ValueError(f"dependency cycle involving {node}")
        visiting.add(node)
        for dependency in REGISTRY[node].requires:
            visit(dependency)
        visiting.discard(node)
        done.add(node)

    for automation_id in REGISTRY:
        visit(automation_id)


_validate_registry()


def automation_ids() -> frozenset[str]:
    return frozenset(REGISTRY)


def dependency_closure(automation_id: str) -> frozenset[str]:
    """Return every transitive dependency of one automation (excluding itself)."""
    seen: set[str] = set()
    stack = list(REGISTRY[automation_id].requires)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(REGISTRY[current].requires)
    return frozenset(seen)


@dataclass(frozen=True, slots=True)
class Resolution:
    """The effective enablement state for one project.

    `enabled` are automations whose full dependency closure is opted in.
    `blocked` maps a requested automation to the dependencies it still needs,
    so the UI can prompt to enable them when a consumer is toggled on.
    """

    enabled: frozenset[str]
    blocked: dict[str, tuple[str, ...]]

    def is_enabled(self, automation_id: str) -> bool:
        return automation_id in self.enabled


def requested_from_config(
    project_map: dict[str, bool] | None,
    defaults: dict[str, bool] | None = None,
) -> set[str]:
    """Merge an inherited default template with a project's explicit opt-ins.

    Global config is only an inherited default; the project map overrides it.
    Unknown ids are dropped so a stale config never enables a phantom automation.
    """
    merged: dict[str, bool] = {**(defaults or {}), **(project_map or {})}
    return {key for key, value in merged.items() if value and key in REGISTRY}


def resolve(requested: set[str]) -> Resolution:
    known = {automation_id for automation_id in requested if automation_id in REGISTRY}
    enabled: set[str] = set()
    blocked: dict[str, tuple[str, ...]] = {}
    for automation_id in known:
        missing = tuple(
            sorted(
                dependency
                for dependency in dependency_closure(automation_id)
                if dependency not in known
            )
        )
        if missing:
            blocked[automation_id] = missing
        else:
            enabled.add(automation_id)
    return Resolution(frozenset(enabled), blocked)


def resolve_config(
    project_map: dict[str, bool] | None,
    defaults: dict[str, bool] | None = None,
) -> Resolution:
    return resolve(requested_from_config(project_map, defaults))
