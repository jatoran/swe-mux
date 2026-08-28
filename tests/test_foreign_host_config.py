"""A `config.toml` outlives its host; its host-shaped values must not.

The failure this file exists to prevent recurring was measured on a live WSL
Ubuntu daemon on 2026-08-28. `harness_exe` held `claude.exe`/`codex.exe` on a
Linux host, so the Run menu could not launch Claude and provider login died with
`No such file or directory: 'codex.exe'`, while typing `claude` in a shell worked
perfectly. `config.default_harness_executables()` had derived the right names
since 2026-08-17, but the loader merged stored over default, so no default could
displace the frozen value and the install could never heal itself. Its
`config.toml.bak` was dated 2026-08-16 - one day before the fix that could not
reach it.

The ratchet is the deliverable, not the fix. `harness_exe` was not the first
field of its kind and will not be the last, so the coverage tests below walk
`Config.__dataclass_fields__` rather than a list of names: a field that can hold
a path or an executable and has neither a reconciliation rule nor a reasoned
exemption fails here the moment it is declared.
"""

from __future__ import annotations

import dataclasses
import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from swe_mux.config import (
    _HOST_SHAPED_RULES,
    HOST_SHAPED_FIELD_EXEMPTIONS,
    HOST_SHAPED_FIELD_MARKERS,
    Config,
    LaunchProfile,
    _serialize,
    default_ccusage_command,
    default_harness_executables,
    default_shell_executable,
    host_shaped_field_candidates,
    is_foreign_host_path,
    load_config,
)
from swe_mux.harness import HARNESSES
from swe_mux.host_platform import IS_WINDOWS
from tests.support.foreign_host_configs import FOREIGN_HOST_CONFIGS, foreign_host_config

#: A value this host could not possibly have written. Both spellings carry every
#: marker the predicate reads, so a rule that fires for one direction of the
#: corpus fires for the other.
FOREIGN_VALUE = "/opt/vendor/bin/tool" if IS_WINDOWS else "C:\\Program Files\\vendor\\tool.exe"

RULED_FIELDS = tuple(name for name, _ in _HOST_SHAPED_RULES)

#: Shapes the dataclass default cannot show, because the default is an empty
#: container. Test-side knowledge about a *shape*, never about a rule - which is
#: why `test_a_synthesis_sample_is_only_declared_where_the_default_cannot_show_the_shape`
#: fails if one of these fields ever gains a non-empty default and the sample
#: becomes a second, drifting opinion about it.
SYNTHESIS_SAMPLES: dict[str, Any] = {
    "usage_commands": {"claude": ["ccusage", "claude", "daily", "--json"]},
    "shell_profiles": [LaunchProfile("default", "Default shell", "bash")],
}


def _foreign_like(value: Any) -> Any:
    """A copy of `value` with every host-shaped position replaced.

    Recurses by runtime shape, and into a nested dataclass by the same naming
    convention the discovery uses, so a `LaunchProfile.executable` is reached
    without this test knowing that field by name.
    """
    if isinstance(value, (str, Path)):
        return FOREIGN_VALUE
    if isinstance(value, list):
        return [_foreign_like(value[0])] if value else [FOREIGN_VALUE]
    if isinstance(value, dict):
        key, sample = next(iter(value.items()), ("claude", ""))
        return {key: _foreign_like(sample)}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        replacements = {
            name: _foreign_like(getattr(value, name))
            for name in type(value).__dataclass_fields__
            if name.endswith(HOST_SHAPED_FIELD_MARKERS)
        }
        return dataclasses.replace(value, **replacements)
    raise TypeError(f"no foreign spelling for {type(value).__name__}")


def _config_carrying(field_name: str, tmp_path: Path) -> Path:
    """Write a config whose only foreign value is in `field_name`, and return it.

    Serialized rather than saved: `save_config` validates, and half the point is
    that some foreign values do not validate here - a Windows `worktree_root` is
    not an absolute path on POSIX at all, so the daemon refuses to load its own
    config rather than starting degraded. A file that could only be produced by
    the validator could never carry that case.
    """
    reference = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")
    sample = SYNTHESIS_SAMPLES.get(field_name, getattr(reference, field_name))
    setattr(reference, field_name, _foreign_like(sample))
    path = tmp_path / "config.toml"
    path.write_text(_serialize(reference), encoding="utf-8", newline="\n")
    return path


def _strings_in(value: Any) -> list[str]:
    if isinstance(value, Path):
        # `str()` on a Path prints this host's separators, so a POSIX path loaded
        # on Windows renders as `\opt\tools` and stops looking foreign - which
        # would make this walk quietly stop checking `data_dir` on Windows.
        return [value.as_posix()]
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings_in(item)]
    if isinstance(value, (list, tuple)):
        return [text for item in value for text in _strings_in(item)]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [
            text
            for name in type(value).__dataclass_fields__
            for text in _strings_in(getattr(value, name))
        ]
    return []


def _surviving_foreign_values(config: Config) -> dict[str, list[str]]:
    """Every host-shaped string still reachable through a reconciled field."""
    survivors: dict[str, list[str]] = {}
    for field_name in host_shaped_field_candidates():
        if "." in field_name or field_name in HOST_SHAPED_FIELD_EXEMPTIONS:
            continue
        foreign = [
            text
            for text in _strings_in(getattr(config, field_name))
            if is_foreign_host_path(text)
        ]
        if foreign:
            survivors[field_name] = foreign
    return survivors


# --------------------------------------------------------------------------- #
# The ratchet: coverage of the dataclass, not of a list of names
# --------------------------------------------------------------------------- #


def test_every_field_that_can_hold_a_host_shaped_value_is_reconciled_or_exempted() -> None:
    """A new `*_exe` or `*_root` must be handled before it can ship.

    Discovery is by naming convention over the dataclass, so nothing has to be
    remembered when a field is added: it appears here on its own, and the only
    two ways to make this pass are to give it a rule or to write down why it does
    not need one.
    """
    unhandled = sorted(
        set(host_shaped_field_candidates()) - set(RULED_FIELDS) - set(HOST_SHAPED_FIELD_EXEMPTIONS)
    )

    assert unhandled == [], (
        "these configuration fields can hold a value shaped for another host and "
        "have neither a rule in config._HOST_SHAPED_RULES nor an entry in "
        f"config.HOST_SHAPED_FIELD_EXEMPTIONS: {unhandled}"
    )


def test_the_ratchet_fails_for_a_field_that_has_just_been_added(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-check: a guard nobody has watched fail is a guard nobody has tested.

    Adds a field that matches the convention and nothing else, and asserts the
    coverage check above would have caught it.
    """
    monkeypatch.setitem(
        Config.__dataclass_fields__, "plugin_exe", Config.__dataclass_fields__["shell_exe"]
    )

    assert "plugin_exe" in host_shaped_field_candidates()
    assert "plugin_exe" not in set(RULED_FIELDS) | set(HOST_SHAPED_FIELD_EXEMPTIONS)


def test_no_rule_or_exemption_names_a_field_that_no_longer_exists() -> None:
    """The other direction, so the table cannot rot into a list of ghosts."""
    candidates = set(host_shaped_field_candidates())

    assert sorted(set(RULED_FIELDS) - candidates) == []
    assert sorted(set(HOST_SHAPED_FIELD_EXEMPTIONS) - candidates) == []


def test_every_exemption_records_a_reason() -> None:
    """An exemption is a decision. An empty one is a suppression."""
    thin = sorted(
        name for name, reason in HOST_SHAPED_FIELD_EXEMPTIONS.items() if len(reason.strip()) < 40
    )

    assert thin == []


def test_a_synthesis_sample_is_only_declared_where_the_default_cannot_show_the_shape(
    tmp_path: Path,
) -> None:
    reference = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")

    for field_name in SYNTHESIS_SAMPLES:
        assert getattr(reference, field_name) == type(getattr(reference, field_name))(), (
            f"{field_name} now has a non-empty default, which is a better source of "
            "its shape than the sample in this file"
        )


@pytest.mark.parametrize("field_name", RULED_FIELDS)
def test_a_foreign_value_in_a_reconciled_field_does_not_survive_the_load(
    field_name: str, tmp_path: Path
) -> None:
    """Every rule in the table is exercised, one field at a time.

    Parametrized off the table itself rather than off a list here, so a rule
    added without a working repair fails immediately.
    """
    path = _config_carrying(field_name, tmp_path)

    config = load_config(path)

    assert _surviving_foreign_values(config) == {}


@pytest.mark.parametrize("field_name", RULED_FIELDS)
def test_a_healed_value_is_written_back_to_the_file(field_name: str, tmp_path: Path) -> None:
    """An install that re-derives the same value on every start has not recovered.

    Read off the *file* rather than off a second load, because a second load
    would repair it again and report the same clean answer either way. The
    written document is the only thing that distinguishes a healed install from
    one that limps along correctly and stays broken on disk.
    """
    path = _config_carrying(field_name, tmp_path)

    load_config(path)

    written = tomllib.loads(path.read_text(encoding="utf-8"))
    assert [text for text in _strings_in(written) if is_foreign_host_path(text)] == []


# --------------------------------------------------------------------------- #
# The corpus
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fixture", FOREIGN_HOST_CONFIGS)
def test_no_foreign_value_survives_any_corpus_config(fixture: str, tmp_path: Path) -> None:
    """Whole-file behaviour, over configurations a real install produced.

    Each fixture is foreign in exactly one direction, so on any given host one of
    them is doing the work and the others are asserting that a *native* config is
    left alone - which is the half that would catch an over-eager rule.
    """
    path = foreign_host_config(fixture, tmp_path)

    config = load_config(path)

    assert _surviving_foreign_values(config) == {}


def test_the_measured_wsl_failure_no_longer_reaches_the_launcher(tmp_path: Path) -> None:
    """The exact reported install: Windows harness names on a POSIX daemon."""
    path = foreign_host_config("windows_authored", tmp_path)

    config = load_config(path)

    assert config.harness_exe == default_harness_executables()
    if not IS_WINDOWS:
        assert config.harness_exe["claude"] == "claude"
        assert config.harness_exe["codex"] == "codex"


def test_a_legacy_per_harness_key_is_reconciled_after_it_is_migrated(tmp_path: Path) -> None:
    """The ancient-schema path lands its Windows names through `<name>_exe`.

    A reconciliation reading the stored `harness_exe` map as written would miss
    these entirely: there is no such map in the file. It has to run after the
    legacy migration has folded them in, which is why the call sits at the end of
    `load_config` rather than beside the merge.
    """
    path = foreign_host_config("ancient_schema", tmp_path)

    config = load_config(path)

    assert config.harness_exe == default_harness_executables()
    assert not is_foreign_host_path(config.shell_exe)
    assert config.worktree_root == "" if not IS_WINDOWS else config.worktree_root != ""


def test_a_posix_config_on_windows_does_not_brick_validation(tmp_path: Path) -> None:
    """The Windows direction fails harder than the POSIX one, and differently.

    `/home/atora/worktrees` is not an absolute path as far as Windows `pathlib`
    is concerned, so `_validate` rejects it and the daemon refuses to load its own
    configuration at all. Reconciling before validation is what turns a daemon
    that will not start into one that starts with an app-managed worktree root.
    """
    path = foreign_host_config("posix_authored", tmp_path)

    config = load_config(path)

    if IS_WINDOWS:
        assert config.worktree_root == ""
        assert config.new_project_parent == ""
        assert config.startup_cwd == ""
        assert config.tts_edge_python == ""
        assert config.data_dir == tmp_path
    else:
        assert config.worktree_root == "/home/atora/worktrees"
        assert config.harness_exe["claude"] == "/home/atora/.local/bin/claude"


# --------------------------------------------------------------------------- #
# What must NOT be rewritten
# --------------------------------------------------------------------------- #


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _toml_string(value: str) -> str:
    """A TOML basic string. Windows paths are full of backslashes to escape."""
    return json.dumps(value)


def test_a_deliberate_override_shaped_for_this_host_survives(tmp_path: Path) -> None:
    """The guard is about shape, and these are all this host's own shapes."""
    override = "claude.cmd" if IS_WINDOWS else "/usr/local/bin/claude"
    path = _write(
        tmp_path / "config.toml",
        f'schema_version = 34\nharness_exe = {{ "claude" = "{override}" }}\n',
    )

    config = load_config(path)

    assert config.harness_exe["claude"] == override


def test_a_bare_command_name_is_never_rewritten(tmp_path: Path) -> None:
    """`claude` names no host and resolves on both. Nothing here may touch it."""
    path = _write(
        tmp_path / "config.toml",
        'schema_version = 34\nharness_exe = { "claude" = "claude" }\n',
    )

    config = load_config(path)

    assert config.harness_exe["claude"] == "claude"


def test_an_uninstalled_cli_keeps_the_executable_the_operator_named(tmp_path: Path) -> None:
    """Shape, not resolution - the distinction the whole rule turns on.

    A CLI that is not installed today and one written on another host look
    identical to `shutil.which`, so a repair keyed off resolution would silently
    discard a deliberate override the moment its target was uninstalled. This
    name is host-shaped for *this* host and resolves nowhere, and must survive.
    """
    missing = "no-such-agent.cmd" if IS_WINDOWS else "/opt/no-such-agent/bin/claude"
    path = _write(
        tmp_path / "config.toml",
        f'schema_version = 34\nharness_exe = {{ "claude" = "{missing}" }}\n',
    )

    config = load_config(path)

    assert config.harness_exe["claude"] == missing


def test_only_the_profile_that_cannot_start_here_is_dropped(tmp_path: Path) -> None:
    """A working profile keeps its id, its arguments, and its place as the default.

    The repair is per profile rather than per list precisely so that a config with
    one dead row does not cost the operator the rows beside it.
    """
    path = _write(
        tmp_path / "config.toml",
        "schema_version = 34\n"
        'default_shell_profile = "native"\n'
        "\n[[shell_profiles]]\n"
        'id = "native"\n'
        'label = "Native"\n'
        f"executable = {_toml_string(default_shell_executable())}\n"
        "platforms = []\n"
        "\n[[shell_profiles]]\n"
        'id = "foreign"\n'
        'label = "Foreign"\n'
        f"executable = {_toml_string(FOREIGN_VALUE)}\n"
        "platforms = []\n",
    )

    config = load_config(path)

    assert [profile.id for profile in config.shell_profiles] == ["native"]
    assert config.default_shell_profile == "native"


def test_an_agent_profile_inheriting_harness_exe_is_never_dropped(tmp_path: Path) -> None:
    """An empty `executable` inherits `harness_exe`, so it is not foreign.

    The foreign shell profile beside it goes and takes the stored default with it;
    a rebuilt shell profile has to arrive in its place, because `_validate`
    requires one and requires the default to name it. Dropping the dead row
    without replacing it would turn a degraded daemon into one that will not load
    its own configuration.
    """
    backend = next(iter(HARNESSES))
    path = _write(
        tmp_path / "config.toml",
        "schema_version = 34\n"
        'default_shell_profile = "foreign"\n'
        "\n[[shell_profiles]]\n"
        'id = "foreign"\n'
        'label = "Foreign"\n'
        f"executable = {_toml_string(FOREIGN_VALUE)}\n"
        "platforms = []\n"
        "\n[[shell_profiles]]\n"
        'id = "agent"\n'
        'label = "Agent"\n'
        'executable = ""\n'
        f'backend = "{backend}"\n'
        "platforms = []\n",
    )

    config = load_config(path)

    identifiers = [profile.id for profile in config.shell_profiles]
    assert "agent" in identifiers
    assert "foreign" not in identifiers
    assert config.default_shell_profile in identifiers
    rebuilt = next(profile for profile in config.shell_profiles if profile.backend == "shell")
    assert not is_foreign_host_path(rebuilt.executable)


# --------------------------------------------------------------------------- #
# The predicate itself
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        "C:\\Program Files\\vendor\\claude.exe",
        "claude.exe",
        "codex.exe",
        "tool.cmd",
        "tool.bat",
        "D:\\PROJECTS\\swe-mux",
    ],
)
def test_windows_shapes_are_foreign_on_posix_and_native_on_windows(value: str) -> None:
    assert is_foreign_host_path(value) is not IS_WINDOWS


@pytest.mark.parametrize("value", ["/bin/bash", "/home/atora/.mux", "/usr/local/bin/claude"])
def test_posix_absolute_paths_are_foreign_on_windows_and_native_on_posix(value: str) -> None:
    assert is_foreign_host_path(value) is IS_WINDOWS


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "claude",
        "ccusage",
        "--json",
        # A UNC path is a Windows path that starts with a slash, and refusing it
        # on Windows would be the predicate misreading its own host's spelling.
        "//fileserver/share/tools/claude.exe" if IS_WINDOWS else "claude",
    ],
)
def test_a_host_neutral_value_is_never_foreign(value: str) -> None:
    assert is_foreign_host_path(value) is False


def test_the_default_executables_are_native_on_whatever_host_runs_this() -> None:
    """The floor under every repair: what it re-derives must itself be native."""
    assert not is_foreign_host_path(default_shell_executable())
    assert not [
        executable
        for executable in default_harness_executables().values()
        if is_foreign_host_path(executable)
    ]
    assert not [item for item in default_ccusage_command() if is_foreign_host_path(item)]
