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
    "attention_observers_enabled": False,
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
        changes.update(
            automation_enabled=True,
            scan_timeline_enabled=True,
            attention_observers_enabled=True,
        )
    changes["experience_tier"] = tier
    return changes
