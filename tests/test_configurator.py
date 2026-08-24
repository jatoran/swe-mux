"""The configurator agent's generated inventory, its guides, and its gate.

The tests worth having here are the ones that fail when a *derivation* stops
deriving. A settings catalog that lists 197 rows proves nothing; a settings
catalog whose constraint for `log_level` is the sentence `_validate` would
actually answer with proves that nobody has quietly reintroduced a hand-written
table beside the validator.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux import configurator
from swe_mux.config import RESTART_FIELDS, Config, update_config
from swe_mux.configurator import (
    GUIDES,
    MANIFEST_SECTIONS,
    RAIL_DOMAIN,
    RAIL_PROFILE,
    build_manifest,
    compose_seed_prompt,
    guide_index,
    is_secret_field,
    project_settings_catalog,
    rail_projection,
    read_guide,
    settings_catalog,
)
from swe_mux.harness import resolve_default_harness
from swe_mux.mcp_contract import (
    CONFIGURATOR_READ_TOOL_NAMES,
    CONFIGURATOR_WRITE_TOOL_NAMES,
    claude_read_permissions,
)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")


# ------------------------------------------------------------ settings catalog


def test_catalog_covers_every_config_field(config: Config) -> None:
    rows = {row["name"]: row for row in settings_catalog(config)}
    assert rows.keys() == set(Config.__dataclass_fields__)


def test_constraints_are_the_validator_s_own_sentences(config: Config) -> None:
    """The whole point of the probe: no transcription step to go stale.

    If someone replaces the probe with a hand-maintained table, these strings
    become a copy that can drift. They are asserted verbatim because they are
    what `_validate` says today, and a change to either half must be a change to
    both.
    """
    rows = {row["name"]: row for row in settings_catalog(config)}
    assert rows["log_level"]["constraint"] == "must be DEBUG, INFO, WARNING, or ERROR"
    assert rows["port"]["constraint"] == "must be between 1 and 65535"
    assert rows["default_harness"]["constraint"] == "must be empty or a registered agent"
    assert rows["default_backend"]["constraint"] == "must be shell or a registered agent"


def test_a_field_with_no_check_reports_no_constraint(config: Config) -> None:
    # `theme` is validated, but a plain unconstrained string field must answer
    # None rather than inventing a rule, so an agent does not refuse a legal write.
    rows = {row["name"]: row for row in settings_catalog(config)}
    assert "constraint" not in rows["startup_cwd"]


def test_probing_leaves_the_live_config_untouched(config: Config) -> None:
    """A read must not be a write.

    The catalog mutates a *candidate* through one `_validate` pass per field. A
    restore that missed would corrupt the running install's settings, and would
    do it invisibly - the corrupted value is a NUL-prefixed sentinel that nothing
    else would ever produce.
    """
    before = {name: getattr(config, name) for name in Config.__dataclass_fields__}
    settings_catalog(config)
    for name, value in before.items():
        assert getattr(config, name) == value, f"{name} was left mutated by the probe"


def test_read_only_and_restart_flags_come_from_the_code_that_enforces_them(
    config: Config,
) -> None:
    rows = {row["name"]: row for row in settings_catalog(config)}
    for name in configurator.READ_ONLY_FIELDS:
        assert rows[name]["writable"] is False
        # A refused field must not advertise a constraint: there is no value that
        # would be accepted, so a rule would read as "send a better one".
        assert "constraint" not in rows[name]
    for name in RESTART_FIELDS:
        assert rows[name]["restart_required"] is True
    assert rows["theme"]["restart_required"] is False


def test_read_only_set_matches_what_update_config_refuses(config: Config) -> None:
    """The catalog's read-only set is a claim about `update_config`; hold it true."""
    for name in configurator.READ_ONLY_FIELDS:
        with pytest.raises(ValueError) as caught:
            update_config(config, {name: getattr(config, name)})
        assert name in caught.value.args[0]


def test_a_budget_ceiling_is_not_mistaken_for_a_credential() -> None:
    """The redaction pattern must not swallow every `*_tokens` limit.

    A loose substring match on "token" hid nine budget ceilings behind `<set>`,
    which is worse than useless: the operator asks what the limit is and the
    agent tells them it is a secret.
    """
    assert not is_secret_field("automation_max_output_tokens")
    assert not is_secret_field("tts_summary_max_tokens")
    assert not is_secret_field("clipboard_history_redact_secrets")
    assert is_secret_field("token")
    assert is_secret_field("openrouter_api_key")
    assert is_secret_field("provider_secret")


def test_a_secret_row_reports_only_whether_it_is_set(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `Config` field is a credential today, so this arms the guard on one.

    Testing the redaction against a real secret field would be better, and there
    is none - which is exactly why the mechanism has to be tested against a
    stand-in. The day a credential is added, this is what says the catalog
    already handles it instead of leaking it into a transcript.
    """
    monkeypatch.setattr(configurator, "_SECRET_FIELDS", frozenset({"startup_cwd", "theme"}))
    config.startup_cwd = "D:/private"
    rows = {row["name"]: row for row in settings_catalog(config)}
    assert rows["startup_cwd"]["secret"] is True
    assert rows["startup_cwd"]["current"] == "<set>"
    assert "D:/private" not in json.dumps(rows)
    # A default credential is still a credential, so the row omits it entirely
    # rather than reporting the empty one it happens to ship with.
    assert "default" not in rows["startup_cwd"]
    # An unset one still answers the question a configurator actually asks.
    config.startup_cwd = ""
    unset = {row["name"]: row for row in settings_catalog(config)}
    assert unset["startup_cwd"]["current"] == "<unset>"


# -------------------------------------------------------------------- manifest


def test_manifest_is_json_serializable_and_complete(config: Config) -> None:
    manifest = build_manifest(config, version="9.9.9", sections=MANIFEST_SECTIONS)
    assert set(manifest) == {*MANIFEST_SECTIONS, "sections"}
    # Everything crosses a JSON boundary to reach the agent; a Path or a
    # dataclass that survives into the payload fails there rather than here.
    json.dumps(manifest)
    assert manifest["install"]["version"] == "9.9.9"


def test_the_settings_catalog_is_omitted_by_default(config: Config) -> None:
    """45 KB of the manifest's 56 KB, on every turn of a tool loop.

    Measured 2026-08-24: a question whose answer was twelve strings cost 61k
    input tokens, most of it a catalog nobody had asked for. The default has to
    be the cheap read, and the expensive one has to be reachable by naming it -
    the reverse leaves an agent paying for it before it knows whether it wants it.
    """
    default = build_manifest(config)
    assert "settings" not in default
    assert "settings" in default["sections"]["omitted"]
    full = build_manifest(config, sections=("settings",))
    assert len(json.dumps(default)) < len(json.dumps(full))


def test_a_settings_query_narrows_the_catalog(config: Config) -> None:
    narrowed = build_manifest(config, sections=("settings",), settings_query="harness")
    names = [row["name"] for row in narrowed["settings"]]
    assert names and all("harness" in name for name in names)
    assert "theme" not in names


def test_an_unknown_section_is_refused_naming_the_real_ones(config: Config) -> None:
    with pytest.raises(ValueError, match="harnesses"):
        build_manifest(config, sections=("settingz",))


def test_the_session_block_marks_which_project_the_caller_is_in(config: Config) -> None:
    """The absent fact behind a confident, wrong answer (2026-08-24).

    An install with two dozen Projects hands a configurator per-Project state it
    cannot attribute. Marking the caller's own Project in the list is what turns
    "the only override I can see" into "an override belonging to swe-mux, and I
    am in cmr-capture-manager".
    """
    projects = [
        {"id": "p1", "name": "swe-mux", "root": "D:/a"},
        {"id": "p2", "name": "cmr-capture-manager", "root": "D:/b"},
    ]
    manifest = build_manifest(
        config, projects=projects, session={"project_id": "p2", "project_name": "cmr"}
    )
    marked = {entry["name"]: entry["is_this_session_project"] for entry in manifest["projects"]}
    assert marked == {"swe-mux": False, "cmr-capture-manager": True}
    assert manifest["install"]["session"]["project_id"] == "p2"


def test_an_unset_config_path_is_null_rather_than_the_string_none(tmp_path: Path) -> None:
    manifest = build_manifest(Config(data_dir=tmp_path))
    assert manifest["install"]["config_path"] is None


def test_automation_rows_carry_the_closure_not_only_the_declared_edges(
    config: Config,
) -> None:
    """The closure is the answer to "I turned it on and nothing happened"."""
    rows = {row["id"]: row for row in build_manifest(config)["automations"]}
    scan_reads = rows["scan_reads"]
    assert scan_reads["requires"] == ["scan_timeline"]
    # Transitive: scan_timeline itself needs tier0 and raw_store.
    assert set(scan_reads["closure"]) >= {"scan_timeline", "tier0", "raw_store"}
    assert rows["scan_timeline"]["spends"] is True
    assert rows["tier0"]["spends"] is False


def test_project_settings_report_the_forbidden_set_as_well_as_the_allowed(
    config: Config,
) -> None:
    catalog = project_settings_catalog()
    assert "automations" in catalog["fields"]
    # The boundary, not an oversight: a committed repository file must not be
    # able to set this daemon's bind address or the command a harness runs.
    assert {"token", "host", "port", "command"} <= set(catalog["forbidden"])
    assert not set(catalog["fields"]) & set(catalog["forbidden"])


# ------------------------------------------------------------ rail projection


#: The shape of a real rail blob, reduced to what the projection reads. Modelled
#: on the live one that produced the 2026-08-24 misattribution: one global mobile
#: row, and exactly one project override - belonging to a *different* Project.
RAIL_BLOB = {
    "version": 3,
    "items": [
        {"id": "custom:prompt:7b58", "type": "prompt", "label": "Tree"},
        {"id": "padArrows", "type": "pad", "label": "Arrows"},
    ],
    "layouts": {
        "mobile": {
            "strip": [
                {"id": "mobile-strip", "items": ["kbdToggle", "paste"]},
                {
                    "id": "row-2-43lio",
                    "items": ["ctrlU", "padArrows", "up", "down", "left", "right"],
                },
            ]
        }
    },
    "projects": {
        "p-swe-mux": {
            "mode": "delta",
            "items": [{"id": "custom:prompt:7b58", "label": "Tree"}],
            "splices": {
                "mobile": {
                    "strip": [
                        {
                            "row": "row-2-43lio",
                            "item": "custom:prompt:7b58",
                            "after": "right",
                        }
                    ]
                }
            },
        }
    },
}

NAMES = {"p-swe-mux": "swe-mux", "p-cmr": "cmr-capture-manager"}


def test_a_foreign_project_override_is_named_and_not_claimed() -> None:
    """The exact failure this projection exists to prevent.

    A blob keyed by bare UUIDs, holding one override, read by an agent standing
    in a different Project: on 2026-08-24 that produced "This project (CMR
    Capture Manager) has a per-project rail delta" about a button belonging to
    swe-mux. Resolving the id to a name and marking the caller's own is the whole
    fix, and it has to be in the *answer* rather than derivable from it.
    """
    view = rail_projection(RAIL_BLOB, project_names=NAMES, session_project_id="p-cmr")
    override = view["project_overrides"][0]
    assert override["project_name"] == "swe-mux"
    assert override["is_this_session_project"] is False
    assert view["this_session_has_an_override"] is False


def test_the_caller_s_own_override_is_marked_when_it_really_is_theirs() -> None:
    view = rail_projection(RAIL_BLOB, project_names=NAMES, session_project_id="p-swe-mux")
    assert view["project_overrides"][0]["is_this_session_project"] is True
    assert view["this_session_has_an_override"] is True


def test_an_override_for_a_project_that_no_longer_exists_says_so() -> None:
    # A Project removed from the registry leaves its rail delta behind. Reporting
    # the bare id would invite the reader to treat it as some Project; saying it
    # is not registered is the fact.
    view = rail_projection(RAIL_BLOB, project_names={}, session_project_id="p-cmr")
    assert view["project_overrides"][0]["project_name"] == "<not a registered Project>"


def test_rows_carry_labels_and_the_exact_path_an_edit_would_name() -> None:
    view = rail_projection(RAIL_BLOB, project_names=NAMES)
    rows = {row["row_id"]: row for row in view["layouts"]["mobile"]["strip"]}
    row = rows["row-2-43lio"]
    assert [entry["id"] for entry in row["items"]][:3] == ["ctrlU", "padArrows", "up"]
    assert next(entry for entry in row["items"] if entry["id"] == "padArrows")["label"] == "Arrows"
    # Selector form, not an index: a row named by its own id cannot be reordered
    # out from under a write composed against this reading.
    assert row["items_path"] == "/layouts/mobile/strip/[id=row-2-43lio]/items"


def test_the_projection_states_the_global_first_rule_and_the_storage_trap() -> None:
    view = rail_projection(RAIL_BLOB, project_names=NAMES)
    assert "GLOBAL" in view["scope_rule"]
    assert view["storage"]["profile"] == RAIL_PROFILE == "desktop"
    assert view["storage"]["domain"] == RAIL_DOMAIN == "commandRail"
    # The trap: both device layouts live in one document under `desktop`, so
    # "edit the mobile rail" is not "write the mobile profile".
    assert "read by nothing" in view["storage"]["note"]


def test_an_unreadable_blob_says_so_rather_than_guessing() -> None:
    assert rail_projection("not a blob", project_names={})["readable"] is False
    # A blob whose layouts moved is still partially readable; the projection is a
    # reading of an opaque document and must degrade rather than raise.
    view = rail_projection({"version": 9, "layouts": "gone"}, project_names={})
    assert view["readable"] is True
    assert view["layouts"] == {}


# ---------------------------------------------------------------------- guides


def test_every_listed_guide_ships_and_every_shipped_guide_is_listed() -> None:
    """A guide missing from the bundle must fail here, not in front of a user.

    This is the packaging assertion the whole feature rests on: the guides live
    under `assets/` precisely so the wheel and the PyInstaller bundle carry them,
    and a file that exists in the source tree but is not listed is unreachable.
    """
    listed = {guide.id for guide in GUIDES}
    on_disk = {path.stem for path in configurator.GUIDE_DIR.glob("*.md")}
    assert listed == on_disk
    for guide in GUIDES:
        assert guide.path.is_file(), f"{guide.id} is listed but has no file"
        assert guide.path.read_text(encoding="utf-8").strip(), f"{guide.id} is empty"


def test_the_index_carries_a_summary_for_every_guide() -> None:
    index = guide_index()
    assert len(index) == len(GUIDES)
    for entry in index:
        assert entry["title"].strip() and entry["summary"].strip()


def test_an_unknown_guide_names_the_ones_that_exist() -> None:
    with pytest.raises(KeyError) as caught:
        read_guide("does-not-exist")
    message = str(caught.value)
    assert "orientation" in message


def test_a_guide_reads_back_its_own_text() -> None:
    assert "switched off" in read_guide("orientation")


# ------------------------------------------------------------- default harness


def test_the_first_available_agent_wins_when_nothing_is_preferred() -> None:
    assert resolve_default_harness(preferences=(), available=("codex", "claude")) == "codex"


def test_a_preference_that_is_not_available_falls_through() -> None:
    # An operator who set `default_harness` to a CLI they since uninstalled gets
    # a working launch, not a refusal about a machine fact they cannot see.
    assert resolve_default_harness(preferences=("omp",), available=("claude",)) == "claude"


def test_shell_is_skipped_rather_than_answered() -> None:
    """`default_backend` legitimately holds `shell`, and it is not an answer here.

    A shell cannot receive a seeded prompt, so returning one would turn a
    missing-harness problem into a launch that succeeds and does nothing.
    """
    assert resolve_default_harness(preferences=("shell",), available=("claude",)) == "claude"
    assert resolve_default_harness(preferences=("shell",), available=()) is None


def test_preferences_are_honoured_in_order() -> None:
    assert (
        resolve_default_harness(
            preferences=("", "codex", "claude"), available=("claude", "codex")
        )
        == "codex"
    )


def test_no_agent_available_is_none_not_a_substitute() -> None:
    assert resolve_default_harness(preferences=("claude",), available=("shell",)) is None


# ----------------------------------------------------------------- seed prompt


def test_the_seed_prompt_names_this_machine_rather_than_a_generic_install(
    config: Config,
) -> None:
    prompt = compose_seed_prompt(config, harness="claude", cwd="D:/work", version="1.2.3")
    assert "claude" in prompt
    assert "D:/work" in prompt
    assert "1.2.3" in prompt
    # Every tool it holds must be named, or it will not know it has them.
    for name in (*CONFIGURATOR_READ_TOOL_NAMES, *CONFIGURATOR_WRITE_TOOL_NAMES):
        assert name in prompt


def test_the_seed_prompt_tells_a_frozen_install_it_cannot_edit_code(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure this prevents is an agent hunting for files that are not there.

    A frozen app has no source checkout, and an edit to anything that looks like
    swe-mux source is not what that app runs - the single most expensive
    misunderstanding in this codebase.
    """
    monkeypatch.setattr(configurator, "install_mode", lambda: "frozen")
    monkeypatch.setattr(configurator, "source_checkout", lambda: None)
    prompt = compose_seed_prompt(config, harness="claude", cwd="C:/app")
    assert "frozen desktop app" in prompt
    assert "cannot change swe-mux's own code" in prompt


def test_the_seed_prompt_offers_code_changes_only_from_source(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(configurator, "install_mode", lambda: "source")
    monkeypatch.setattr(configurator, "source_checkout", lambda: Path("D:/repo"))
    prompt = compose_seed_prompt(config, harness="claude", cwd="D:/repo")
    assert "modifying-swe-mux" in prompt
    assert "stops every live session" in prompt


def test_the_seed_prompt_stays_small_enough_to_be_an_opening_turn(
    config: Config,
) -> None:
    """It introduces the material; it does not contain it.

    A prompt that inlined the inventory would spend the first turn on text the
    agent can fetch, and would freeze a copy of it into a transcript that
    outlives the settings it describes.
    """
    prompt = compose_seed_prompt(config, harness="claude", cwd="D:/work")
    assert len(prompt) < 8000


# -------------------------------------------------------------- permission set


def test_configurator_reads_are_pre_allowed_and_the_write_is_not() -> None:
    """A settings change is exactly the thing a human should see before it happens."""
    allowed = set(claude_read_permissions())
    for name in CONFIGURATOR_READ_TOOL_NAMES:
        assert f"mcp__mux__{name}" in allowed
    for name in CONFIGURATOR_WRITE_TOOL_NAMES:
        assert f"mcp__mux__{name}" not in allowed
