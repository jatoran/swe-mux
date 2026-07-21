from __future__ import annotations

from dataclasses import dataclass

# The enablement DAG for the control-plane substrate and consumers.
#
# Every automation is per-project opt-in. Substrate captures facts but never acts
# or spends; consumers are assembled from substrate. A consumer is only *effective*
# when the full transitive closure of its dependencies is also opted in — this is
# the gate that keeps nothing running without the foundation it reads from. See
# .docs/development/CONTROL_PLANE_IDEAS.md §8.

SUBSTRATE = "substrate"
CONSUMER = "consumer"


@dataclass(frozen=True, slots=True)
class Automation:
    id: str
    kind: str
    label: str
    requires: tuple[str, ...] = ()


_AUTOMATIONS: tuple[Automation, ...] = (
    # Substrate.
    Automation("raw_store", SUBSTRATE, "Raw transcript store"),
    Automation("tier0", SUBSTRATE, "Deterministic fact capture", ("raw_store",)),
    Automation("project_card", SUBSTRATE, "Project card"),
    Automation("scan_timeline", SUBSTRATE, "Scan timeline", ("tier0", "raw_store")),
    # Consumers.
    Automation("provenance_graph", CONSUMER, "Provenance graph", ("tier0",)),
    Automation("declared_vs_verified", CONSUMER, "Declared vs verified", ("tier0",)),
    Automation("loop_detection", CONSUMER, "Loop / stall detection", ("tier0",)),
    Automation("doc_debt", CONSUMER, "Doc-debt ledger", ("tier0", "project_card")),
    Automation("dead_end_memory", CONSUMER, "Dead-end memory", ("tier0", "scan_timeline")),
    Automation("continuous_title", CONSUMER, "Continuous session title", ("scan_timeline",)),
    Automation(
        "cross_session_interlocks",
        CONSUMER,
        "Cross-session interlocks",
        ("provenance_graph",),
    ),
    Automation("absence_report", CONSUMER, "Absence report / digest", ("scan_timeline",)),
    Automation("attention_ranking", CONSUMER, "Attention ranking", ("tier0",)),
    Automation("observation_inbox", CONSUMER, "Observation inbox"),
    Automation("screenshot_to_agent", CONSUMER, "Screenshot to agent", ("project_card",)),
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
