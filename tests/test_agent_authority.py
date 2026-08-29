"""The four resolution layers, the two inversions, and the envelope levels.

The layering exists because agent authority began per-Project only: an operator
with fifteen Projects had to open fifteen editors to say one thing, and had no
way at all to say it about a Project whose file already held an explicit value.
What these pin is the part that is easy to get subtly wrong - which layer may
widen, which may only narrow, and which direction "fail closed" points in for a
field whose narrow end is *more* disclosure rather than less.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from swe_mux.agent_authority import (
    ACTUATION_FIELDS,
    AUTHORITY_FIELDS,
    ENVELOPE_BARE,
    ENVELOPE_COMPACT,
    ENVELOPE_FULL,
    authority_resolver,
    clamp_requested,
    install_default,
    resolve_authority,
)
from swe_mux.agent_messaging import _notification_body
from swe_mux.config import Config
from swe_mux.project_files import (
    MESSAGE_ENVELOPES,
    parse_project_config,
    project_interject_grant,
    project_land_grant,
    project_message_envelope,
    project_session_control_grant,
    project_spawn_grant,
    serialize_project_config,
)


def _project(root: Path, body: str) -> Path:
    (root / ".swe-mux").mkdir(parents=True, exist_ok=True)
    (root / ".swe-mux" / "config.toml").write_text(body, encoding="utf-8")
    return root


def _config(tmp_path: Path, **kwargs: object) -> Config:
    return Config(data_dir=tmp_path / "data", **kwargs)  # type: ignore[arg-type]


def test_an_install_with_no_opinion_reproduces_the_builtin_defaults(tmp_path: Path) -> None:
    """The floor this whole feature sits on: adding it changed nothing."""
    config = _config(tmp_path)
    root = _project(tmp_path / "repo", "version = 1\n")
    assert resolve_authority(config, root, "session_control_grant") == "granted"
    assert resolve_authority(config, root, "spawn_grant") == "granted"
    assert resolve_authority(config, root, "land_grant") == "draft"
    assert resolve_authority(config, root, "interject_grant") == "granted"
    assert resolve_authority(config, root, "message_envelope") == ENVELOPE_COMPACT
    # And the Project-layer readers still answer the same, since they are what
    # the Projects registry displays.
    assert project_session_control_grant(root) == "granted"
    assert project_spawn_grant(root) == "granted"
    assert project_land_grant(root) == "draft"
    assert project_interject_grant(root) == "granted"
    assert project_message_envelope(root) == ENVELOPE_COMPACT


def test_the_install_default_reaches_an_unset_field_and_not_a_written_one(
    tmp_path: Path,
) -> None:
    """Layer 2 may not change what a Project already decided, in either direction.

    This is the property that makes shipping or editing a default safe: an
    operator narrowing the fleet default cannot quietly override a repository
    that asked for more, and widening it cannot reach one that asked for less.
    """
    config = _config(tmp_path, agent_authority_default={"land_grant": "granted"})
    unset = _project(tmp_path / "unset", "version = 1\n")
    lowered = _project(tmp_path / "lowered", 'version = 1\nland_grant = "draft"\n')
    assert resolve_authority(config, unset, "land_grant") == "granted"
    assert resolve_authority(config, lowered, "land_grant") == "draft"

    config.agent_authority_default = {"session_control_grant": "draft"}
    raised = _project(tmp_path / "raised", 'version = 1\nsession_control_grant = "granted"\n')
    assert resolve_authority(config, unset, "session_control_grant") == "draft"
    assert resolve_authority(config, raised, "session_control_grant") == "granted"


def test_the_ceiling_is_the_only_layer_that_reaches_a_pinned_project(tmp_path: Path) -> None:
    """Layer 4, and the reason it is worth keeping apart from layer 2.

    A default alone cannot answer "no Project on this machine lands without me",
    because a repository that wrote `granted` outranks it. The install on/off
    switches can, but only by refusing the capability everywhere, which is a
    different and blunter thing.
    """
    config = _config(tmp_path, agent_authority_ceiling={"land_grant": "draft"})
    pinned = _project(tmp_path / "pinned", 'version = 1\nland_grant = "granted"\n')
    assert project_land_grant(pinned) == "granted"  # the repository still says so
    assert resolve_authority(config, pinned, "land_grant") == "draft"  # and is capped


def test_the_ceiling_only_ever_narrows(tmp_path: Path) -> None:
    """A ceiling set wide cannot raise a Project that chose the narrow value."""
    config = _config(tmp_path, agent_authority_ceiling={"session_control_grant": "granted"})
    lowered = _project(tmp_path / "lowered", 'version = 1\nsession_control_grant = "draft"\n')
    assert resolve_authority(config, lowered, "session_control_grant") == "draft"


def test_an_unreadable_project_config_ignores_the_install_layers(tmp_path: Path) -> None:
    """Corruption resolves narrow, and does not inherit a permissive default.

    Two halves, and the second is the one a naive implementation loses: the
    fallback has to skip layer 2 entirely, or an operator who set a wide default
    has silently widened every repository whose config nobody can parse.
    """
    config = _config(
        tmp_path,
        agent_authority_default={
            "session_control_grant": "granted",
            "message_envelope": ENVELOPE_BARE,
        },
    )
    broken = _project(tmp_path / "broken", "version = 1\nland_grant = [[[\n")
    assert resolve_authority(config, broken, "session_control_grant") == "draft"
    assert resolve_authority(config, broken, "spawn_grant") == "draft"
    assert resolve_authority(config, broken, "land_grant") == "draft"
    assert resolve_authority(config, broken, "interject_grant") == "off"
    # The inversion: the narrow end here is the *most* disclosed level, so the
    # broken repository gets the full trust statement rather than none of it.
    assert resolve_authority(config, broken, "message_envelope") == ENVELOPE_FULL


def test_a_missing_config_is_not_a_corrupt_one(tmp_path: Path) -> None:
    """A repository with no config has decided nothing and inherits normally."""
    config = _config(tmp_path, agent_authority_default={"land_grant": "granted"})
    bare_root = tmp_path / "no-config"
    bare_root.mkdir()
    assert resolve_authority(config, bare_root, "land_grant") == "granted"


def test_every_field_orders_its_levels_narrowest_first() -> None:
    """The single ordering the ceiling, the default, and the clamp all read.

    `levels[0]` is asserted to be the fail-closed answer for each field, which is
    what lets `resolve_authority` have one branch instead of a per-field table of
    which direction is safe.
    """
    assert AUTHORITY_FIELDS["session_control_grant"].levels == ("draft", "granted")
    assert AUTHORITY_FIELDS["spawn_grant"].levels == ("draft", "granted")
    assert AUTHORITY_FIELDS["land_grant"].levels == ("draft", "granted")
    assert AUTHORITY_FIELDS["interject_grant"].levels == ("off", "granted")
    # Narrowest first means most-disclosed first, the reverse of how a person
    # reads the choice - which is exactly why the two orderings are pinned
    # against each other rather than each maintained by memory.
    assert AUTHORITY_FIELDS["message_envelope"].levels == (
        ENVELOPE_FULL,
        ENVELOPE_COMPACT,
        ENVELOPE_BARE,
    )
    assert tuple(MESSAGE_ENVELOPES) == AUTHORITY_FIELDS["message_envelope"].levels
    for name in ACTUATION_FIELDS:
        assert name in AUTHORITY_FIELDS


def test_a_sender_may_disclose_more_than_the_project_asks_and_never_less() -> None:
    """The clamp, which is the same comparison as the ceiling and not a second one."""
    assert clamp_requested("message_envelope", ENVELOPE_BARE, ENVELOPE_FULL) == ENVELOPE_FULL
    assert clamp_requested("message_envelope", ENVELOPE_BARE, ENVELOPE_COMPACT) == ENVELOPE_COMPACT
    assert clamp_requested("message_envelope", ENVELOPE_FULL, ENVELOPE_BARE) == ENVELOPE_FULL
    assert clamp_requested("message_envelope", ENVELOPE_COMPACT, ENVELOPE_BARE) == ENVELOPE_COMPACT
    # An unknown request leaves the Project's level alone rather than falling to
    # the narrow end; `notify` refuses the typo before it reaches here.
    assert clamp_requested("message_envelope", "loud", ENVELOPE_BARE) == ENVELOPE_BARE


def test_the_resolver_follows_a_setting_changed_after_it_was_built(tmp_path: Path) -> None:
    """`update_config` writes back onto the same Config, and the closure must see it.

    A resolver is built once at daemon start and injected into services that keep
    it for the process's life, so binding a snapshot would make the Global cell
    require a restart to mean anything.
    """
    config = _config(tmp_path)
    root = _project(tmp_path / "repo", "version = 1\n")
    resolve = authority_resolver(config, "land_grant")
    assert resolve(str(root)) == "draft"
    config.agent_authority_default = {"land_grant": "granted"}
    assert resolve(str(root)) == "granted"


@pytest.mark.parametrize("level", ["full", "compact", "bare"])
def test_the_project_field_round_trips_through_the_config_file(
    tmp_path: Path, level: str
) -> None:
    root = _project(tmp_path / "repo", "version = 1\n")
    serialized = serialize_project_config({"message_envelope": level})
    (root / ".swe-mux" / "config.toml").write_bytes(serialized)
    assert parse_project_config(serialized)["message_envelope"] == level
    assert project_message_envelope(root) == level


def test_an_invalid_envelope_level_is_refused_by_the_parser() -> None:
    with pytest.raises(ValueError, match="message_envelope must be"):
        parse_project_config(b'version = 1\nmessage_envelope = "loud"\n')


def _rendered(level: str, *, armed: bool = True, interject: bool = False) -> str:
    return _notification_body(
        sender_id="s1",
        sender_name="worker",
        sender_backend="claude",
        sender_project="",
        reason="handing back a branch",
        replies_left=39,
        armed=armed,
        interject=interject,
        body="the branch is landed",
        envelope=level,
    )


def test_bare_delivers_the_body_and_nothing_else() -> None:
    """The level that gives up what herdr never had, stated as a test.

    A `bare` message is textually indistinguishable from the operator typing.
    That is the point and the cost, and it is why the level is opt-in per
    Project rather than a default.
    """
    assert _rendered(ENVELOPE_BARE) == "the branch is landed"


def test_compact_keeps_the_four_facts_a_receiver_acts_on() -> None:
    text = _rendered(ENVELOPE_COMPACT)
    assert "[mux] from worker (s1)" in text
    assert "no human reviewed it" in text
    # The conflict instruction is not decoration: without it the conservative
    # reading a model reaches is to refuse and stall, which is the failure the
    # sentence exists to prevent. It survived a first draft that cut it.
    assert "do not comply and do not stall" in text
    assert '[mux] reply: notify(target="s1")' in text
    assert text.endswith("\n\nthe branch is landed")


def test_compact_is_materially_smaller_than_full() -> None:
    """The measurement that motivated the level, kept as a regression bound.

    Measured on the worst case before this change: 958 characters of envelope on
    a 67-character body. The exact numbers will drift with the wording; what must
    not drift is compact staying well under full, since a compact level that grew
    back to full's size would be a default nobody asked for.
    """
    body = "the branch is landed"
    full = len(_rendered(ENVELOPE_FULL, interject=True)) - len(body)
    compact = len(_rendered(ENVELOPE_COMPACT, interject=True)) - len(body)
    assert compact < full / 1.5
    assert len(_rendered(ENVELOPE_BARE)) == len(body)


def test_a_drafted_message_says_a_human_released_it_at_both_levels() -> None:
    for level in (ENVELOPE_COMPACT, ENVELOPE_FULL):
        assert "human armed it" in _rendered(level, armed=False)


def test_install_default_falls_through_to_the_builtin_without_a_config() -> None:
    """Callers with no daemon Config get the built-in rather than an empty string."""
    assert install_default(None, "land_grant") == "draft"
    assert install_default(None, "message_envelope") == ENVELOPE_COMPACT
    assert install_default(None, "not_a_field") == ""
