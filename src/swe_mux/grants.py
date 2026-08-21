"""What a gate is allowed to switch on, and what it must never touch.

A *grant* is one deliberate act by the operator, made from the surface that cannot work
without it, rather than from the overlay that owns the switch. The Land queue's
verification gate has worked this way from the start - it states the block, shows the
exact bytes, and approves them in place - and this module is that pattern's vocabulary
generalised so every other inert surface can offer the same thing.

Three rules make it safe to expose a write from anywhere in the app:

- **A grant only ever turns something on.** There is no revoking here. Turning things
  off stays with the surface that owns the switch (Settings, the Projects registry),
  which is what keeps "one owner per switch" true while many surfaces can grant.
- **Only allowlisted keys.** `GRANTABLE_INSTALL_KEYS` and `GRANTABLE_PROJECT_VALUES`
  are closed sets, checked against `Config` and `project_files` at import. A gate
  cannot reach a setting nobody designed a gate for, and a renamed field fails at
  startup rather than at the click.
- **Validate everything, then apply.** A grant that half-lands is worse than one that
  refuses, so the whole request is checked before the first write.

`server.apply_grants` is the one caller; `.docs/design/features/setting-links.md` is the
contract this implements.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .automation_registry import REGISTRY, enabling_closure, needs_llm, spends_money

# Install-wide switches a gate may turn on.
#
# Every one is a boolean `Config` field that gates a whole feature, and every one has a
# surface somewhere that goes inert while it is off. Deliberately *not* here: anything
# with a value rather than a state (budgets, model ids, ceilings, widths), because a
# gate can offer "turn this on" honestly and cannot offer "pick a number" at all.
GRANTABLE_INSTALL_KEYS: frozenset[str] = frozenset(
    {
        "agent_messaging_enabled",
        "approval_auto_enabled",
        "automation_enabled",
        "auto_delivery_enabled",
        "ccusage_enabled",
        "clipboard_history_enabled",
        "land_queue_enabled",
        "scan_timeline_enabled",
        "scheduled_runs_enabled",
        "stt_enabled",
        "tts_enabled",
    }
)

# Per-Project typed config fields a gate may set, and the only values it may set them
# to. A tuple rather than a bare "any value" because these are authority fields: a gate
# may raise `land_grant` to `granted`, and lowering it back to `draft` belongs to the
# Project's own editor along with every other way of taking permission away.
GRANTABLE_PROJECT_VALUES: Mapping[str, tuple[Any, ...]] = {
    "scan_timeline_auto_enable": (True,),
    "land_grant": ("granted",),
    "session_control_grant": ("granted",),
    "spawn_grant": ("granted",),
    "interject_grant": ("granted",),
}

# Install switches whose whole point is a feature that calls a model. Turning one on
# does not spend by itself - a Project still has to permit the work - but a gate that
# offered them without saying so would be hiding the one fact worth reading twice.
SPENDING_INSTALL_KEYS: frozenset[str] = frozenset(
    {"automation_enabled", "scan_timeline_enabled"}
)

# Install switches whose feature is a model call and nothing else. Identical to
# `SPENDING_INSTALL_KEYS` today and kept separate for the same reason
# `Automation.needs_llm` is kept apart from `Automation.spends`: one is a cost
# disclosure, the other is a dependency on a provider that may be free and local.
LLM_INSTALL_KEYS: frozenset[str] = frozenset(
    {"automation_enabled", "scan_timeline_enabled"}
)


class GrantRefusal(Exception):
    """A grant that will not be attempted, with the machine code the browser branches on."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class GrantPlan:
    """A validated grant, ready to apply. Nothing here has been written yet."""

    install: dict[str, bool]
    """Install switches to raise. Already filtered to those not already on."""

    automations: frozenset[str]
    """Project opt-ins to add, dependency closure included."""

    values: dict[str, Any]
    """Project typed config fields to set."""

    spends: bool
    """Whether applying this can cost money, closure included."""

    needs_llm: bool
    """Whether anything here is inert without a verified model provider.

    Disclosed on the gate beside `spends`, and asked of the closure the same way.
    A grant is still applied when this is true and the provider is not ready -
    the opt-in is a real permission and withholding it would mean the operator
    had to grant twice - but a gate that stayed quiet would hand back a switch
    that reads on and does nothing, which is the failure this phase removes.
    """

    @property
    def empty(self) -> bool:
        return not (self.install or self.automations or self.values)

    def audit_keys(self) -> list[str]:
        """Everything this grant touches, scope-qualified, for the audit event."""
        return sorted(
            [f"install:{key}" for key in self.install]
            + [f"automation:{key}" for key in self.automations]
            + [f"project:{key}" for key in self.values]
        )


def plan_grant(
    *,
    install: Mapping[str, Any] | None,
    automations: Iterable[str] | None,
    values: Mapping[str, Any] | None,
    current_install: Mapping[str, Any],
    current_automations: Mapping[str, bool],
    current_values: Mapping[str, Any],
) -> GrantPlan:
    """Validate a grant request against the allowlists and the current state.

    Raises `GrantRefusal` for anything outside the allowlists, and returns a plan
    holding only what actually needs writing - a grant for something already on is a
    no-op rather than a redundant write, so a double click on a gate cannot bump a
    revision and race an open editor.
    """
    planned_install: dict[str, bool] = {}
    for key, value in (install or {}).items():
        if key not in GRANTABLE_INSTALL_KEYS:
            raise GrantRefusal(
                "not_grantable",
                f"{key} is not a switch a gate may turn on",
            )
        if value is not True:
            # The one direction. A gate that could send `false` would be a way to
            # disable a feature from a surface that never says it can.
            raise GrantRefusal(
                "grant_is_additive",
                f"{key} can only be granted, not withdrawn; change it where it is owned",
            )
        if current_install.get(key) is not True:
            planned_install[key] = True

    wanted = list(automations or [])
    unknown = sorted({item for item in wanted if item not in REGISTRY})
    if unknown:
        raise GrantRefusal("unknown_automation", f"unknown automations: {', '.join(unknown)}")
    closure = enabling_closure(wanted) if wanted else frozenset()
    unimplemented = sorted(item for item in closure if not REGISTRY[item].implemented)
    if unimplemented:
        # Same refusal the Projects registry makes, for the same reason: a switch that
        # reads as on and does nothing is worse than one that would not turn on.
        raise GrantRefusal(
            "automation_not_implemented",
            f"not implemented yet: {', '.join(unimplemented)}",
        )
    planned_automations = frozenset(
        item for item in closure if current_automations.get(item) is not True
    )

    planned_values: dict[str, Any] = {}
    for key, value in (values or {}).items():
        allowed = GRANTABLE_PROJECT_VALUES.get(key)
        if allowed is None:
            raise GrantRefusal(
                "not_grantable",
                f"{key} is not a Project field a gate may set",
            )
        if value not in allowed:
            rendered = ", ".join(repr(item) for item in allowed)
            raise GrantRefusal(
                "grant_is_additive",
                f"{key} may only be granted ({rendered}); change it where it is owned",
            )
        if current_values.get(key) != value:
            planned_values[key] = value

    # Asked of the whole closure rather than of what still needs writing: a Project that
    # already permits the timeline is not made free by the fact that this click only
    # adds a consumer over it, and the operator is entitled to read that either way.
    spends = bool(wanted) and spends_money(closure)
    spends = spends or bool(SPENDING_INSTALL_KEYS & set(planned_install))
    wants_model = bool(wanted) and needs_llm(closure)
    wants_model = wants_model or bool(LLM_INSTALL_KEYS & set(planned_install))
    return GrantPlan(
        install=planned_install,
        automations=planned_automations,
        values=planned_values,
        spends=spends,
        needs_llm=wants_model,
    )


def project_values_after(
    current: Mapping[str, Any], plan: GrantPlan, current_automations: Mapping[str, bool]
) -> dict[str, Any]:
    """The Project's whole typed-config table after this grant.

    One merged dict rather than two sequential writes: `automations` and the authority
    fields live in the same `.swe-mux/config.toml`, so writing them separately would
    make the second write race the revision the first one just bumped, and would leave
    a half-applied grant behind whenever it lost.
    """
    values = dict(current)
    if plan.automations:
        table = {key: True for key, value in current_automations.items() if value}
        for automation_id in plan.automations:
            table[automation_id] = True
        values["automations"] = table
    values.update(plan.values)
    return values


def _validate_allowlists() -> None:
    """Fail at import if an allowlist names a field that no longer exists.

    The point of a closed set is that it is checked. Without this, renaming a `Config`
    field would leave a gate offering a switch the daemon rejects at the click - the
    exact stranded-link failure this whole feature exists to remove, moved one layer
    down.
    """
    from dataclasses import fields as dataclass_fields

    from .config import Config
    from .project_files import PROJECT_CONFIG_FIELDS

    config_fields = {field.name: field for field in dataclass_fields(Config)}
    for key in sorted(GRANTABLE_INSTALL_KEYS):
        field = config_fields.get(key)
        if field is None:
            raise ValueError(f"grantable install key {key} is not a Config field")
        if field.type not in {"bool", bool}:
            raise ValueError(f"grantable install key {key} must be a boolean setting")
    unknown_project = sorted(set(GRANTABLE_PROJECT_VALUES) - set(PROJECT_CONFIG_FIELDS))
    if unknown_project:
        raise ValueError(
            f"grantable Project fields are not in the project config: {', '.join(unknown_project)}"
        )


_validate_allowlists()
