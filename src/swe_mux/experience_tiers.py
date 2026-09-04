"""Experience tiers: the first-run choice of how much of swe-mux is on.

The tiers name a boundary the architecture already has - almost everything is
off until you ask - rather than inventing a mode system. Three tiers:

- **terminal**: real terminals, zero opinions, nothing watching. No lifecycle
  hooks, no MCP registration, no shims, no fleet plumbing. A genuine product in
  its own right, not a reduced one.
- **deterministic**: transcripts, status detection, managed harnesses, and the
  agent fleet surface. Model-free throughout; this is the install default.
- **automations**: deterministic plus the model-backed layer's master switches
  (automation, the scan timeline, the attention observers), whose budgets and
  per-Project opt-ins still apply.

Two rules make a tier safe to offer:

- **A tier sets defaults; it never locks capability.** Applying one is a single
  absolute assignment of the ordinary config keys below, every one of which
  stays individually editable afterwards, and every surface a tier turns off
  keeps its in-place switch (`design/features/setting-links.md`). Nothing may
  read `experience_tier` to decide what a user can do.
- **The assignment is absolute, not a delta.** Every tier writes the same key
  inventory, so switching tiers is deterministic whatever was set before, and
  choosing a tier twice is idempotent. The cost, stated plainly: re-applying a
  tier overwrites hand edits to exactly these keys, which is why the Settings
  control applies on an explicit press rather than on selection.

The first-run panel's granular choices live here for the same reason the tier
key sets do: the browser must never hold a second copy of the policy. Three
additions to the plain tier:

- **Autonomy** is a second, orthogonal axis over the prompt queue's
  auto-delivery keys plus the spawn budget - how much an agent may do without a
  human pressing send. Same rules as a tier: an absolute assignment of ordinary
  keys, `supervised` byte-identical to a fresh install, nothing locked.
- **Overrides** let the first-run panel flip individual boolean switches inside
  the chosen tier's assignment without recomputing it browser-side: the daemon
  applies `tier_changes(tier)` and then the named deviations, and refuses any
  key outside `OVERRIDABLE_KEYS`.
- **The preview payload** (`GET /api/experience-tiers`) serves every
  assignment so the panel can *show* what a tier or autonomy level sets
  without restating it.
"""

from __future__ import annotations

from typing import Any

from .harness import HARNESSES

TIERS = ("terminal", "deterministic", "automations")

#: The install-default (deterministic) values of every key a tier assigns.
#: Values here must equal the `Config` field defaults - asserted in
#: `tests/test_experience_tiers.py` rather than trusted - so a fresh install
#: that picks "deterministic" is byte-identical to one that never chose.
_DETERMINISTIC: dict[str, Any] = {
    "harness_instrument_enabled": {},
    "harness_mcp_enabled": {},
    # The other two agent-surface maps ride the tier too, so the terminal tier
    # can actually mean "no fleet plumbing": the CLI capability map (absent key
    # = on, like MCP) and the skill map (absent key = OFF - its non-Claude half
    # writes into checkouts, so the empty deterministic map keeps it off).
    "harness_cli_enabled": {},
    "harness_skill_enabled": {},
    "agent_shims_on_shell_path": True,
    "agent_messaging_enabled": True,
    "agent_interject_enabled": True,
    "session_control_enabled": True,
    "request_spawn_enabled": True,
    "session_watch_enabled": True,
    "scheduled_runs_enabled": True,
    "land_queue_enabled": True,
    "automation_enabled": False,
    "scan_timeline_enabled": False,
}


def tier_changes(tier: str) -> dict[str, Any]:
    """The config assignment for ``tier``, including the tier stamp itself.

    The per-harness maps are written as explicit all-off entries for the
    terminal tier (a harness registered by a later release then defaults back
    to instrumented, which is correct: the tier described the install at the
    moment it was chosen, and the new harness's surfaces all carry gates).
    """
    if tier not in TIERS:
        raise ValueError(f"unknown experience tier {tier!r}")
    changes = dict(_DETERMINISTIC)
    if tier == "terminal":
        all_off = {name: False for name in HARNESSES}
        changes.update(
            harness_instrument_enabled=all_off,
            harness_mcp_enabled=dict(all_off),
            harness_cli_enabled=dict(all_off),
            harness_skill_enabled=dict(all_off),
            agent_shims_on_shell_path=False,
            agent_messaging_enabled=False,
            agent_interject_enabled=False,
            session_control_enabled=False,
            request_spawn_enabled=False,
            session_watch_enabled=False,
            scheduled_runs_enabled=False,
            land_queue_enabled=False,
        )
    elif tier == "automations":
        # The two install masters. The model-backed observers (session titler,
        # attention observers) are per-Project automations since schema 36 and
        # are opted in through the "AI timeline" starting set at Project
        # creation rather than switched install-wide here - a tier assigns
        # ordinary keys absolutely, and writing the whole default template from
        # it would erase every entry the operator (or the schema-36 migration)
        # had put there.
        changes.update(
            automation_enabled=True,
            scan_timeline_enabled=True,
        )
    changes["experience_tier"] = tier
    return changes


#: The keys the first-run panel may deviate from a tier's assignment, one at a
#: time. Exactly the tier inventory's booleans: the per-harness maps are edited
#: through their own Settings surface (three checkboxes per harness, or the
#: install-wide fleet-access choice on the first-run agents page), and the tier
#: stamp itself is never an override.
OVERRIDABLE_KEYS = frozenset(
    key for key, value in _DETERMINISTIC.items() if isinstance(value, bool)
)

AUTONOMY_LEVELS = ("supervised", "assisted", "autonomous")

#: The install-default (supervised) values of every key an autonomy level
#: assigns. Same contract as `_DETERMINISTIC`: values must equal the `Config`
#: field defaults, asserted in `tests/test_experience_tiers.py`, so declining
#: the choice changes nothing.
_SUPERVISED: dict[str, Any] = {
    "auto_delivery_enabled": False,
    "auto_delivery_max_consecutive": 3,
    "auto_delivery_session_ttl_minutes": 60,
    "auto_delivery_reply_window_minutes": 30,
    "agent_spawn_hourly_budget": 10,
}


def autonomy_changes(level: str) -> dict[str, Any]:
    """The config assignment for one autonomy level.

    Orthogonal to the tier on purpose: how much swe-mux *watches* (the tier)
    and how much an agent may *act unattended* (this) are different questions,
    and the second one is about spend of the operator's attention rather than
    of tokens. `assisted` turns the auto-delivery master on under the shipped
    bounds; `autonomous` widens those bounds to fit long-running multi-agent
    work (more consecutive sends, a longer idle grant and reply window, twice
    the spawn budget). Every value still sits inside `_validate`'s ranges, and
    every gate auto-delivery answers to - stability window, quiet hours, the
    emergency pause, head-of-line order - still applies.
    """
    if level not in AUTONOMY_LEVELS:
        raise ValueError(f"unknown autonomy level {level!r}")
    changes = dict(_SUPERVISED)
    if level == "assisted":
        changes["auto_delivery_enabled"] = True
    elif level == "autonomous":
        changes.update(
            auto_delivery_enabled=True,
            auto_delivery_max_consecutive=10,
            auto_delivery_session_ttl_minutes=120,
            auto_delivery_reply_window_minutes=60,
            agent_spawn_hourly_budget=20,
        )
    return changes
