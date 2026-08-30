"""The embedded agent skill and its installer.

Two contracts are pinned here beyond the file operations themselves:

- The skill's *shape* is what makes it cheap and unable to go stale: the
  directory name and the frontmatter name agree (Claude keys by directory,
  Codex by frontmatter), and the body teaches no CLI commands - it points at
  `--help` and lets the binary be the authority.
- The installer writes exactly where the discovery scanner reads. The roots in
  `agent_skills.py` are verified against the real CLIs, so `discover_skills`
  finding the installed file is the closest offline proof that the CLIs will.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from swe_mux import skill_install
from swe_mux.agent_skills import discover_skills, parse_frontmatter
from swe_mux.cli import main
from swe_mux.skill_install import (
    MANAGED_MARKER,
    SKILL_DIR_NAME,
    filter_targets,
    global_targets,
    install,
    project_targets,
    remove,
    skill_text,
)


def test_the_shipped_skill_names_itself_after_its_directory() -> None:
    meta = parse_frontmatter(skill_text())
    assert meta["name"] == SKILL_DIR_NAME
    # The removal recognizer and the over-firing guard both live in the
    # frontmatter; losing either changes behaviour elsewhere.
    assert MANAGED_MARKER in skill_text()
    assert "MUX_SESSION_ID" in meta["description"]


def test_the_shipped_skill_teaches_no_commands() -> None:
    """Every fenced block is the environment check; none invokes `swemux`.

    The discipline is herdr's: the binary's `--help` is the authority for
    command syntax, so the skill cannot go stale between releases. A fenced
    `swemux <verb>` appearing here is the drift this test exists to stop.
    """
    text = skill_text()
    fenced = re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.DOTALL)
    assert fenced, "the in-session environment check should be a runnable block"
    for block in fenced:
        assert "swemux" not in block and "mux " not in block
    assert "MUX_SESSION_ID" in fenced[0]
    assert "swemux --help" in text


def test_install_writes_the_two_roots_every_harness_reads(tmp_path: Path) -> None:
    report = install(project_targets(tmp_path), skill_text())
    assert [entry.action for entry in report] == ["wrote", "wrote"]
    claude_copy = tmp_path / ".claude" / "skills" / SKILL_DIR_NAME / "SKILL.md"
    shared_copy = tmp_path / ".agents" / "skills" / SKILL_DIR_NAME / "SKILL.md"
    assert claude_copy.read_text(encoding="utf-8") == skill_text()
    assert shared_copy.read_text(encoding="utf-8") == skill_text()
    readers = {reader for entry in report for reader in entry.readers}
    assert readers == {"claude", "codex", "pi", "omp", "opencode"}


def test_reinstall_reports_unchanged_and_moves_no_mtime(tmp_path: Path) -> None:
    targets = project_targets(tmp_path)
    install(targets, skill_text())
    path = targets[0].skill_path()
    before = path.stat().st_mtime_ns
    report = install(targets, skill_text())
    assert {entry.action for entry in report} == {"unchanged"}
    # mtime is what `agent_skills` uses for `added_after_start`; an idempotent
    # reinstall must not make an old skill look new.
    assert path.stat().st_mtime_ns == before


def test_remove_takes_only_what_carries_the_marker(tmp_path: Path) -> None:
    targets = project_targets(tmp_path)
    install(targets, skill_text())
    foreign = tmp_path / ".claude" / "skills" / SKILL_DIR_NAME / "SKILL.md"
    foreign.write_text("---\nname: swe-mux\n---\nsomebody else's skill\n", encoding="utf-8")
    report = {entry.path: entry for entry in remove(targets)}
    ours = targets[1].skill_path()
    assert report[str(ours)].action == "removed"
    assert not ours.exists()
    assert not ours.parent.exists(), "an emptied skill directory is taken with the file"
    refused = report[str(foreign)]
    assert refused.action == "refused"
    assert refused.error is False, "declining a foreign file is policy, not failure"
    assert foreign.read_text(encoding="utf-8").startswith("---")


def test_remove_leaves_a_directory_holding_anything_else(tmp_path: Path) -> None:
    targets = filter_targets(project_targets(tmp_path), ["codex"])
    install(targets, skill_text())
    extra = targets[0].skill_path().parent / "notes.md"
    extra.write_text("local additions\n", encoding="utf-8")
    report = remove(targets)
    assert report[0].action == "removed"
    assert extra.exists(), "removal takes the file it wrote, never a sibling"


def test_global_targets_name_the_documented_per_user_roots(tmp_path: Path) -> None:
    targets = global_targets(
        data_homes={
            "claude": tmp_path / "claude-home",
            "codex": tmp_path / "codex-home",
        },
        user_home=tmp_path / "home",
    )
    roots = {str(target.root) for target in targets}
    assert roots == {
        str(tmp_path / "claude-home" / "skills"),
        str(tmp_path / "codex-home" / "skills"),
        str(tmp_path / "home" / ".agents" / "skills"),
    }
    by_root = {target.root.parent.name: target.readers for target in targets}
    assert by_root["codex-home"] == ("codex",)
    assert "claude" in by_root["claude-home"]


def test_filter_targets_selects_by_reader(tmp_path: Path) -> None:
    targets = project_targets(tmp_path)
    assert [t.root.parts[-2] for t in filter_targets(targets, ["claude"])] == [".claude"]
    assert [t.root.parts[-2] for t in filter_targets(targets, ["pi"])] == [".agents"]
    assert filter_targets(targets, []) == targets


def test_the_scanner_finds_what_the_installer_wrote(tmp_path: Path) -> None:
    """Closes the loop: `agent_skills` reads the roots the CLIs read (verified
    against Claude 2.1.220 / Codex 0.145), and the installer targets the same
    directories, so discovery is the offline stand-in for the CLIs themselves."""
    project = tmp_path / "checkout"
    project.mkdir()
    install(project_targets(project), skill_text())
    claude = discover_skills(
        "claude", project, claude_home=tmp_path / "claude-home", refresh=True
    )
    codex = discover_skills("codex", project, codex_home=tmp_path / "codex-home", refresh=True)
    claude_hit = next(s for s in claude["skills"] if s["name"] == SKILL_DIR_NAME)
    codex_hit = next(s for s in codex["skills"] if s["name"] == SKILL_DIR_NAME)
    assert claude_hit["invocation"] == "/swe-mux"
    assert codex_hit["invocation"] == "$swe-mux"
    assert claude_hit["description"] == codex_hit["description"] != ""


def test_cli_skill_flag_prints_the_embedded_copy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--skill"]) == 0
    assert capsys.readouterr().out == skill_text()


def test_cli_bare_invocation_keeps_its_usage_error() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


def test_cli_install_skill_defaults_to_a_project_scope(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-skill", "--project", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "project"
    assert payload["ok"] is True
    assert {entry["action"] for entry in payload["writes"]} == {"wrote"}
    assert (tmp_path / ".agents" / "skills" / SKILL_DIR_NAME / "SKILL.md").is_file()


def test_cli_install_skill_remove_round_trips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["install-skill", "--project", str(tmp_path)]) == 0
    capsys.readouterr()
    assert main(["install-skill", "--project", str(tmp_path), "--remove", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {entry["action"] for entry in payload["writes"]} == {"removed"}
    assert not (tmp_path / ".agents" / "skills" / SKILL_DIR_NAME / "SKILL.md").exists()


def test_cli_global_without_yes_only_plans(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The disclosure step: exact paths printed, nothing written, exit 0.

    The homes are pointed into tmp_path so even a regression that *did* write
    would land in the sandbox rather than in the operator's real skill roots.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert main(["install-skill", "--global", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is False
    assert {entry["action"] for entry in payload["writes"]} == {"planned"}
    # Config loading may create a data dir under the patched home; the claim
    # is narrower - the plan wrote no skill anywhere.
    assert not list(tmp_path.rglob("SKILL.md"))


def test_cli_global_with_yes_writes_the_per_user_roots(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    assert main(["install-skill", "--global", "--yes", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmed"] is True
    assert {entry["action"] for entry in payload["writes"]} == {"wrote"}
    for base in ("claude-home/skills", "codex-home/skills", "home/.agents/skills"):
        assert (tmp_path / base / SKILL_DIR_NAME / "SKILL.md").is_file(), base


def test_cli_rejects_both_scopes_at_once(tmp_path: Path) -> None:
    assert main(["install-skill", "--project", str(tmp_path), "--global"]) == 1


def test_every_agent_harness_declares_a_project_and_a_user_root() -> None:
    """The installer derives its targets from `skill_install_roots`, so a
    harness that declares neither scope silently drops out of both commands -
    this is the guard that makes the omission loud instead."""
    from swe_mux.harness import agent_harnesses, descriptor

    for name in agent_harnesses():
        kinds = descriptor(name).skill_install_roots
        assert any(kind.startswith("project-") for kind in kinds), name
        assert any(kind.startswith("user-") for kind in kinds), name


def test_the_wheel_and_bundles_carry_the_asset() -> None:
    """The asset travels as package data; a rename that detaches it from the
    `assets/**` artifact globs would strand every frozen install."""
    asset = Path(skill_install.__file__).with_name("assets") / "skills" / SKILL_DIR_NAME
    assert (asset / "SKILL.md").is_file()


# --------------------------------------------------------------------------- #
# Automatic delivery (harness_skill_enabled)
# --------------------------------------------------------------------------- #


def test_claude_delivery_is_a_data_dir_plugin_on_the_argv(tmp_path: Path) -> None:
    """Claude's automatic route writes nothing into any checkout: a data-dir
    plugin named per session via `--plugin-dir`, the `--mcp-config` shape."""
    from swe_mux.adapters import ClaudeAdapter, SpawnOptions

    adapter = ClaudeAdapter(data_dir=tmp_path, skill=True)
    plugin = tmp_path / "agent-skill" / "claude-plugin"
    manifest = json.loads(
        (plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == SKILL_DIR_NAME
    assert (plugin / "skills" / SKILL_DIR_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    ) == skill_text()
    project = tmp_path / "checkout"
    project.mkdir()
    argv = list(adapter.spawn_spec("new-id", SpawnOptions(project)).argv)
    assert argv[argv.index("--plugin-dir") + 1] == str(plugin)
    assert not (project / ".agents").exists(), "claude delivery must not touch the checkout"
    assert not (project / ".claude").exists()


def test_the_claude_plugin_carries_the_skill_and_nothing_else(tmp_path: Path) -> None:
    """swe-mux already delivers hooks by its own route (`--settings`); a plugin
    that grew hooks, commands, or agents would be a second path to the same
    thing that can disagree. Deliberate, so pinned."""
    from swe_mux.skill_install import materialize_claude_plugin

    base = materialize_claude_plugin(tmp_path / "plug")
    assert base is not None
    entries = sorted(item.name for item in base.iterdir())
    assert entries == [".claude-plugin", "skills"]


def test_claude_without_the_toggle_passes_no_plugin_dir(tmp_path: Path) -> None:
    from swe_mux.adapters import ClaudeAdapter, SpawnOptions

    adapter = ClaudeAdapter(data_dir=tmp_path)
    argv = adapter.spawn_spec("new-id", SpawnOptions(tmp_path)).argv
    assert "--plugin-dir" not in argv
    assert not (tmp_path / "agent-skill").exists()


def test_non_claude_delivery_writes_the_shared_root_at_spawn(tmp_path: Path) -> None:
    from swe_mux.adapters import CodexAdapter, SpawnOptions

    project = tmp_path / "checkout"
    project.mkdir()
    off = CodexAdapter()
    off.spawn_spec("sid", SpawnOptions(project))
    assert not (project / ".agents").exists(), "default off writes nothing"
    on = CodexAdapter(skill=True)
    on.spawn_spec("sid", SpawnOptions(project))
    copy = project / ".agents" / "skills" / SKILL_DIR_NAME / "SKILL.md"
    assert copy.read_text(encoding="utf-8") == skill_text()
    # Resume refreshes the same file rather than duplicating anything.
    before = copy.stat().st_mtime_ns
    on.resume_spec("native", SpawnOptions(project))
    assert copy.stat().st_mtime_ns == before


def test_every_non_claude_harness_declares_the_shared_project_root() -> None:
    """`deliver_project_skill` writes only `.agents/skills/`, so a non-Claude
    harness that stopped declaring `project-agents` would get writes its CLI
    never reads. Claude is exempt because its delivery is the plugin dir."""
    from swe_mux.harness import HARNESSES

    for name, harness in HARNESSES.items():
        if harness.adapter_family == "claude":
            continue
        assert "project-agents" in harness.skill_install_roots, name
