from __future__ import annotations

from collections.abc import Iterable
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
    # True when switching this on can cost money. Whether an opt-in spends was
    # documented only in the comments here, which meant a one-click grant gate
    # could not tell the operator the one thing they most need to know before
    # pressing it. It travels in the registry payload so the disclosure is read
    # from the same source as the dependency edges rather than restated in the
    # browser, where it would drift.
    spends: bool = False
    # True when this automation cannot do its job without a language model.
    #
    # Kept apart from `spends` even though the two coincide exactly today,
    # because they answer different questions and a bring-your-own endpoint is
    # precisely where they come apart: a model running on the operator's own
    # machine needs the provider and costs nothing. `spends` is a *disclosure*
    # ("this can bill you"); this is a *predicate* ("there is a dependency
    # outside the DAG"), and it is what `resolve` consults to decide the switch
    # is inert. `_validate_registry` holds spends ⊆ needs_llm, since every way of
    # spending money in swe-mux is a model call.
    needs_llm: bool = False


_AUTOMATIONS: tuple[Automation, ...] = (
    # Substrate.
    Automation("raw_store", SUBSTRATE, "Raw transcript store"),
    Automation("tier0", SUBSTRATE, "Deterministic fact capture", ("raw_store",)),
    Automation(
        "scan_timeline",
        SUBSTRATE,
        "Scan timeline",
        ("tier0", "raw_store"),
        spends=True,
        needs_llm=True,
    ),
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
        spends=True,
        needs_llm=True,
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
    Automation(
        "model_narration",
        CONSUMER,
        "Model narration",
        ("attention_ranking",),
        spends=True,
        needs_llm=True,
    ),
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
    # Phase 14: serialized branch landing. Like `session_control` and
    # `scheduled_runs` it gates a *capability* rather than a read over another
    # automation's output, so it depends on no substrate. Its own id rather than a
    # second meaning for `session_control`: that one acts on a session, this one
    # acts on a repository, and they deserve separate switches and separate
    # budgets. Off by default, and permission alone lands nothing - the Project's
    # `land_grant` stays at the inert `draft` until a human raises it.
    Automation("land_queue", CONSUMER, "Land queue", ()),
)

REGISTRY: dict[str, Automation] = {automation.id: automation for automation in _AUTOMATIONS}


def _validate_registry() -> None:
    for automation in REGISTRY.values():
        if automation.kind not in {SUBSTRATE, CONSUMER}:
            raise ValueError(f"{automation.id} has unknown kind {automation.kind}")
        if automation.spends and not automation.needs_llm:
            # Every way of spending money here is a model call, so an automation
            # that claims to spend and denies needing the provider would sit
            # outside the verified-provider gate while still billing - the exact
            # silent downstream failure that gate exists to remove.
            raise ValueError(f"{automation.id} spends money but does not need a model")
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
    `unverified` holds the ones held back by something *outside* the DAG - a
    language-model provider that is not proven - which is deliberately a
    separate field rather than a `blocked` entry: `blocked` values are automation
    ids a grant can switch on, and there is no automation id whose enabling
    fixes an unverified endpoint. Merging them would produce a gate offering to
    turn on nothing.
    """

    enabled: frozenset[str]
    blocked: dict[str, tuple[str, ...]]
    unverified: frozenset[str] = frozenset()

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


def resolve(requested: set[str], *, llm_ready: bool = True) -> Resolution:
    """Resolve one Project's opt-in set into what actually runs.

    `llm_ready` is the install-wide answer to "is there a proven model provider",
    and it subtracts from `enabled` rather than from `requested`. That ordering
    is the whole design: an automation that needs a model goes inert and *says
    so*, while the free consumers layered over it keep resolving normally and go
    on reading the records that already exist. Failing the whole subtree would
    switch off `catch_me_up` and `live_blockers` - which never call anything -
    the moment somebody rotated a key.

    It defaults to `True` so that resolution stays a pure function of the DAG for
    every caller that has no provider to consult (the registry payload, the
    fleet matrix, the tests). The daemon's one gate passes the real answer.
    """
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
    unverified: frozenset[str] = frozenset()
    if not llm_ready:
        unverified = frozenset(item for item in enabled if REGISTRY[item].needs_llm)
        enabled -= unverified
    return Resolution(frozenset(enabled), blocked, unverified)


def resolve_config(
    project_map: dict[str, bool] | None,
    defaults: dict[str, bool] | None = None,
    *,
    llm_ready: bool = True,
) -> Resolution:
    return resolve(requested_from_config(project_map, defaults), llm_ready=llm_ready)


def llm_dependent_ids() -> frozenset[str]:
    """Every automation that cannot run without a language-model provider."""
    return frozenset(item.id for item in REGISTRY.values() if item.needs_llm)


def needs_llm(automation_ids_wanted: Iterable[str]) -> bool:
    """Whether enabling these (with their closure) requires a model provider.

    Asked of the closure for the same reason `spends_money` is: `catch_me_up`
    calls nothing and cannot be switched on without `scan_timeline`, which does.
    """
    return any(REGISTRY[item].needs_llm for item in enabling_closure(automation_ids_wanted))


def enabling_closure(automation_ids_wanted: Iterable[str]) -> frozenset[str]:
    """Everything that must be opted in for `automation_ids_wanted` to be effective.

    The requested ids plus their whole transitive dependency closure. This is what a
    grant actually writes, and naming it here rather than in each caller is what keeps
    the browser's disclosure ("...plus Deterministic fact capture and Raw transcript
    store") and the daemon's write from drifting apart.

    Unknown ids raise: a grant that silently dropped one would report success for a
    switch it never touched.
    """
    wanted = list(automation_ids_wanted)
    unknown = sorted({item for item in wanted if item not in REGISTRY})
    if unknown:
        raise ValueError(f"unknown automations: {', '.join(unknown)}")
    result: set[str] = set()
    for automation_id in wanted:
        result.add(automation_id)
        result |= dependency_closure(automation_id)
    return frozenset(result)


def spends_money(automation_ids_wanted: Iterable[str]) -> bool:
    """Whether enabling these (with their closure) can cost money.

    Asked of the closure, not the named ids: `phase_transitions` costs nothing by
    itself and cannot be switched on without `scan_timeline`, which does.
    """
    return any(REGISTRY[item].spends for item in enabling_closure(automation_ids_wanted))


# The model-free starting set offered to a newly registered Project.
#
# Every id here parses transcripts the daemon already has and never calls a model, so
# the whole set is free to run - which is what makes it safe to offer as one choice at
# creation instead of leaving a new user to pick from twenty checkboxes whose costs are
# not written down anywhere they can see. `scan_timeline` is deliberately absent: it is
# the one substrate that spends, and it is offered separately with its budget attached.
#
# This is *not* an inherited default template. It is written into the new Project's own
# `.swe-mux/config.toml`, so "nothing runs on a Project that did not opt in" stays
# literally true and no existing Project changes behaviour because this constant did.
RECOMMENDED_PROJECT_AUTOMATIONS: tuple[str, ...] = (
    "provenance_graph",
    "declared_vs_verified",
    "loop_detection",
    "doc_debt",
    "code_graph",
)


def _validate_recommended() -> None:
    for automation_id in RECOMMENDED_PROJECT_AUTOMATIONS:
        if automation_id not in REGISTRY:
            raise ValueError(f"recommended set names unknown automation {automation_id}")
        if not REGISTRY[automation_id].implemented:
            raise ValueError(f"recommended set names unimplemented {automation_id}")
    if spends_money(RECOMMENDED_PROJECT_AUTOMATIONS):
        raise ValueError("the recommended starting set must be free to run")


_validate_recommended()


# The model tier, offered as a second - never defaulted-on - choice at Project creation.
#
# Exactly the automations whose work is a model call: the scan-timeline substrate and the
# spending consumers layered over it. Applied through the same grant path as the
# recommended set, so the dependency closure (`attention_ranking` and the detectors under
# `model_narration`, `tier0` and the raw store under everything) is written with it
# rather than discovered afterwards as a `blocked` entry. The values half of the choice
# (`scan_timeline_auto_enable`) lives in `grants.LLM_PROJECT_VALUES`: what a gate may set
# a field to is the grant allowlist's contract, not the DAG's.
LLM_PROJECT_AUTOMATIONS: tuple[str, ...] = (
    "scan_timeline",
    "continuous_title",
    "model_narration",
)


def _validate_llm_set() -> None:
    for automation_id in LLM_PROJECT_AUTOMATIONS:
        if automation_id not in REGISTRY:
            raise ValueError(f"LLM set names unknown automation {automation_id}")
        if not REGISTRY[automation_id].needs_llm:
            # The checkbox says "the model-backed automations"; a model-free id here
            # would make that sentence quietly wrong.
            raise ValueError(f"LLM set names model-free {automation_id}")
    for automation_id in enabling_closure(LLM_PROJECT_AUTOMATIONS):
        if not REGISTRY[automation_id].implemented:
            raise ValueError(f"LLM set drags in unimplemented {automation_id}")


_validate_llm_set()


# The agent-autonomy starting set. Capability opt-ins only; the authority half that
# raises spawn and land past "a human approves every action" is
# `grants.AUTONOMY_PROJECT_VALUES`, applied in the same grant. `observation_inbox` is
# deliberately included: under this posture whatever still arrives as a draft - a spawn
# over its hourly budget, a control request the grant does not cover - gets its review
# surface instead of silence.
AUTONOMY_PROJECT_AUTOMATIONS: tuple[str, ...] = (
    "session_control",
    "land_queue",
    "observation_inbox",
)


def _validate_autonomy_set() -> None:
    for automation_id in AUTONOMY_PROJECT_AUTOMATIONS:
        if automation_id not in REGISTRY:
            raise ValueError(f"autonomy set names unknown automation {automation_id}")
        if not REGISTRY[automation_id].implemented:
            raise ValueError(f"autonomy set names unimplemented {automation_id}")
    if spends_money(AUTONOMY_PROJECT_AUTOMATIONS):
        raise ValueError("the autonomy starting set must be free to run")


_validate_autonomy_set()
