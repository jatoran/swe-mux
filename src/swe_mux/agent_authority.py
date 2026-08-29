"""Install-wide defaults and ceilings over the per-Project agent authority fields.

Every field here answers "does an agent still need a human for this in *that*
repository". They began per-Project only, which is right for the decision and
wrong for the ergonomics: an operator with fifteen Projects had to open fifteen
editors to say one thing, and had no way at all to say it about a Project whose
file already held an explicit value.

Four layers, in this order:

1. **The Project's own explicit value** in ``.swe-mux/config.toml``. A written
   value is a decision somebody made about that repository and outranks
   anything on this machine, up to the ceiling below.
2. **The install default** (``Config.agent_authority_default``), which applies
   only where the Project left the field unset. This is "what should a Project
   that has not decided do".
3. **The built-in default**, unchanged from before this existed, so an install
   that sets neither of the above behaves exactly as it did.
4. **The install ceiling** (``Config.agent_authority_ceiling``), which caps all
   three. This is the only layer that can reach a Project that wrote an
   explicit value, and it can only ever narrow. It is a different question to
   the default - "what may no Project on this machine do, whatever its file
   says" - and keeping them apart is what lets the UI offer one control with a
   lock rather than two controls with a precedence rule to remember.

**Widening never happens implicitly.** Layer 2 reaches only unset fields, so
shipping a new default cannot change what an existing Project does, and layer 4
only subtracts. That is the same rule ``automation_global_allow`` follows and
the same reason the first-use starting sets are written into each Project's own
file rather than inherited from a constant (`automation-enablement.md`).

**One signed ordering, not two comparison paths.** ``levels`` runs narrowest
first, where "narrow" means *less latitude for the agent*. For the four
actuation fields that is the obvious direction: drafting is narrower than
acting. For ``message_envelope`` it inverts on the surface - the narrow value is
``full``, the one that discloses the most - because an agent that must announce
itself has less latitude than one that may arrive looking like the operator. So
a ceiling caps the *rank* in every case, and the field that reads like a floor
("this repository requires at least a compact envelope") is the same comparison
as the fields that read like caps.

**Fail-closed lands on the narrow end for free.** A Project config that cannot
be read or parsed resolves to ``levels[0]``, skipping layers 2 and 3 entirely:
corruption must never inherit a permissive install default, and it must never
widen what an explicit value narrowed. Because of the ordering above, that rule
gives ``draft``/``off`` for the actuation fields and ``full`` for the envelope
without a second branch - which is the point, since a naive implementation that
reused the actuation direction here would strip the trust context from exactly
the repository whose configuration nobody can read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, types only
    from .config import Config


@dataclass(frozen=True)
class AuthorityField:
    """One agent authority level, and everything a surface needs to render it."""

    #: The key in `.swe-mux/config.toml`, in `agent_authority_default`, and in
    #: `agent_authority_ceiling`. One name in all three, so a UI row and a
    #: validation error name the same thing.
    field: str
    #: Every level the field may hold, **narrowest first** (see the module
    #: docstring). `levels[0]` is both the fail-closed answer and the strongest
    #: thing a ceiling can say.
    levels: tuple[str, ...]
    #: What an unset field means when the install expresses no default. Chosen
    #: to reproduce the pre-2026-08-29 behaviour exactly.
    builtin: str
    #: Human label, shared by the policy matrix and the Projects registry so
    #: the two surfaces cannot drift into describing the same switch
    #: differently.
    label: str
    #: The automation opt-in that has to be on for this field to mean anything,
    #: or None where the capability is gated by something outside the registry
    #: (delivery readiness for `interject_grant`; nothing at all for
    #: `message_envelope`, which shapes a message rather than permitting one).
    #: A surface greys the row when this is off, because a level on a
    #: capability nobody may use is a control that does nothing.
    gated_by: str | None

    @property
    def narrowest(self) -> str:
        return self.levels[0]

    @property
    def widest(self) -> str:
        return self.levels[-1]

    def rank(self, level: str) -> int:
        """Position in the ordering, or -1 for a level this field cannot hold."""
        try:
            return self.levels.index(level)
        except ValueError:
            return -1

    def narrower(self, left: str, right: str) -> str:
        """Whichever of the two grants less latitude, resolving unknowns narrow."""
        left_rank = self.rank(left)
        right_rank = self.rank(right)
        if left_rank < 0:
            return right if right_rank >= 0 else self.narrowest
        if right_rank < 0:
            return left
        return left if left_rank <= right_rank else right


#: Message envelope levels, narrowest (most disclosed) first. Named here as
#: well as in the registry below because `agent_messaging` renders them and
#: should not have to index into a tuple to know which is which.
ENVELOPE_FULL = "full"
ENVELOPE_COMPACT = "compact"
ENVELOPE_BARE = "bare"

AUTHORITY_FIELDS: dict[str, AuthorityField] = {
    field.field: field
    for field in (
        AuthorityField(
            field="session_control_grant",
            levels=("draft", "granted"),
            builtin="granted",
            label="Interrupt and end sessions",
            gated_by="session_control",
        ),
        AuthorityField(
            field="spawn_grant",
            levels=("draft", "granted"),
            builtin="granted",
            label="Start new sessions here",
            gated_by="session_control",
        ),
        AuthorityField(
            field="land_grant",
            levels=("draft", "granted"),
            # The one field that starts narrow. Landing moves a repository's
            # trunk, so it stays at the inert draft until somebody raises it.
            builtin="draft",
            label="Land a branch onto the trunk",
            gated_by="land_queue",
        ),
        AuthorityField(
            field="interject_grant",
            levels=("off", "granted"),
            builtin="granted",
            label="Write into a running turn",
            # Gated by the delivery-readiness predicate rather than by an
            # automation opt-in, so there is no registry id to grey it against.
            gated_by=None,
        ),
        AuthorityField(
            field="message_envelope",
            # Narrowest first, and here that is the *most* disclosed: an agent
            # whose messages must announce what they are has less latitude than
            # one whose messages may arrive looking like the operator's.
            levels=(ENVELOPE_FULL, ENVELOPE_COMPACT, ENVELOPE_BARE),
            builtin=ENVELOPE_COMPACT,
            label="Metadata on delivered agent messages",
            gated_by=None,
        ),
    )
}

#: The four fields that decide whether an agent still needs a human. Kept apart
#: from the envelope field because a surface that groups them wants to say
#: "these permit, that one discloses", and because `spends`-style validation of
#: the actuation set should not have to special-case a level that costs nothing
#: and permits nothing.
ACTUATION_FIELDS: tuple[str, ...] = (
    "session_control_grant",
    "spawn_grant",
    "land_grant",
    "interject_grant",
)


def authority_levels(name: str) -> tuple[str, ...]:
    """Every level a field may hold, narrowest first. Empty for an unknown name."""
    field = AUTHORITY_FIELDS.get(name)
    return field.levels if field else ()


def install_default(config: Config | None, name: str) -> str:
    """Layer 2 over layer 3: what an *unset* Project field means on this install.

    `config` is optional so a caller that has no daemon `Config` to hand -
    a unit test, or a read that only wants what the repository itself says -
    gets the built-in default rather than having to fabricate an install.

    The ceiling is deliberately not applied here. A surface renders this as the
    Global cell's own value and the lock separately, so folding them together
    would make ticking the lock silently rewrite the dropdown beside it.
    """
    field = AUTHORITY_FIELDS.get(name)
    if field is None:
        return ""
    configured = (config.agent_authority_default or {}).get(name) if config else None
    if isinstance(configured, str) and field.rank(configured) >= 0:
        return configured
    return field.builtin


def install_ceiling(config: Config | None, name: str) -> str | None:
    """Layer 4, or None where the install expresses no ceiling for this field."""
    field = AUTHORITY_FIELDS.get(name)
    if field is None:
        return None
    configured = (config.agent_authority_ceiling or {}).get(name) if config else None
    if isinstance(configured, str) and field.rank(configured) >= 0:
        return configured
    return None


def apply_ceiling(config: Config | None, name: str, level: str) -> str:
    """Cap one already-chosen level at the install ceiling."""
    field = AUTHORITY_FIELDS.get(name)
    if field is None:
        return level
    ceiling = install_ceiling(config, name)
    return level if ceiling is None else field.narrower(level, ceiling)


def resolve_authority(
    config: Config | None,
    root: str | Path,
    name: str,
    *,
    read_project: Callable[[str | Path, str], tuple[str | None, bool]] | None = None,
) -> str:
    """The effective level for `name` in the Project rooted at `root`.

    `read_project` exists for tests and for callers that already hold the
    Project's parsed values; it defaults to reading the file, which is what the
    daemon wants. It returns `(explicit value or None, readable)`, and an
    unreadable config short-circuits to the narrow end without consulting the
    install layers at all.
    """
    field = AUTHORITY_FIELDS.get(name)
    if field is None:
        return ""
    if read_project is None:
        from .project_files import read_project_authority

        read_project = read_project_authority
    explicit, readable = read_project(root, name)
    if not readable:
        # Layers 2 and 3 are skipped on purpose: a config nobody can parse must
        # not inherit a permissive install default.
        return field.narrowest
    chosen = explicit if explicit is not None and field.rank(explicit) >= 0 else None
    if chosen is None:
        chosen = install_default(config, name)
    return apply_ceiling(config, name, chosen)


def authority_resolver(config: Config, name: str) -> Callable[[str], str]:
    """A `(project_root) -> level` closure, the shape every service injects.

    Bound to the live `Config` instance rather than to a snapshot of its values,
    because `update_config` writes the new values back onto the same object: a
    resolver built at startup therefore reflects a setting changed at runtime
    without a daemon restart, which is the whole point of the Global cell.
    """

    def resolve(root: str) -> str:
        return resolve_authority(config, root, name)

    return resolve


def clamp_requested(name: str, requested: str, resolved: str) -> str:
    """What a *sender* asking for `requested` actually gets in a `resolved` Project.

    A caller may always ask for a narrower level than the Project permits and
    never a wider one, which for `message_envelope` is the rule that a sender
    may disclose more about itself but never less. Silently clamping rather than
    refusing is deliberate: the sender's message is still worth delivering, and
    the effective level is reported back so it learns what happened.
    """
    field = AUTHORITY_FIELDS.get(name)
    if field is None or field.rank(requested) < 0:
        return resolved
    return field.narrower(requested, resolved)
