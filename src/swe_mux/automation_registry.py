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
    # The project card reads the project's own `.docs`, not the raw store or
    # Tier 0, so it depends on nothing. It is substrate all the same: it is
    # built once and several consumers read it (CP §5.4). Unlike the rest of
    # the substrate it does spend — one cheap model call per documentation
    # fingerprint — which is exactly why it is opt-in rather than ambient.
    Automation("project_card", SUBSTRATE, "Project card"),
    Automation(
        "scan_timeline", SUBSTRATE, "Scan timeline", ("tier0", "raw_store"), implemented=False
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
        implemented=False,
    ),
    Automation(
        "continuous_title",
        CONSUMER,
        "Continuous session title",
        ("scan_timeline",),
        implemented=False,
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
        implemented=False,
    ),
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
        implemented=False,
    ),
    # Keep the persisted id for settings compatibility. The human observation
    # inbox UI is retired; this now names review of agent spawn requests in the
    # Fleet Queue.
    Automation("observation_inbox", CONSUMER, "Spawn request review"),
    Automation("screenshot_to_agent", CONSUMER, "Screenshot to agent"),
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
