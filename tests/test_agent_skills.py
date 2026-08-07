"""Skill discovery: the roots each CLI reads, and the metadata that changes meaning.

Every root asserted here was verified against a live install (Claude Code 2.1.220,
Codex 0.145 with skills 0.146) before it was encoded, including the two negatives:
Codex reads `.agents/skills` and does *not* read `.claude/skills`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swe_mux.agent_skills import (
    CACHE_TTL_SECONDS,
    MAX_SKILLS,
    clear_cache,
    discover_skills,
    parse_frontmatter,
    parse_yaml_head,
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    clear_cache()
    yield
    clear_cache()


def write_skill(root: Path, name: str, description: str = "does a thing") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "SKILL.md"
    manifest.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nbody\n",
        encoding="utf-8",
    )
    return manifest


def names(payload: dict, scope: str | None = None) -> list[str]:
    return [
        skill["name"]
        for skill in payload["skills"]
        if scope is None or skill["scope"] == scope
    ]


def test_frontmatter_keeps_colons_and_folds_continuations() -> None:
    parsed = parse_frontmatter(
        "---\n"
        "name: probe\n"
        "description: Use when X: do Y,\n"
        "  and also when Z\n"
        "metadata:\n"
        "  short-description: 'quoted here'\n"
        "---\n"
        "body: not frontmatter\n"
    )
    assert parsed["name"] == "probe"
    # The first colon splits; the rest belongs to the value, which is what keeps a
    # real description ("Use when the user asks: ...") from being cut in half.
    assert parsed["description"] == "Use when X: do Y, and also when Z"
    assert parsed["metadata.short-description"] == "quoted here"
    assert "body" not in parsed


def test_frontmatter_absent_is_empty_not_an_error() -> None:
    assert parse_frontmatter("# Just a heading\n\ntext\n") == {}


def test_yaml_head_reads_codex_sidecar_shape() -> None:
    parsed = parse_yaml_head(
        'interface:\n'
        '  display_name: "Evaluate Update"\n'
        '  short_description: "Discuss before coding"\n'
        "\n"
        "policy:\n"
        "  allow_implicit_invocation: false\n"
    )
    assert parsed["interface.display_name"] == "Evaluate Update"
    assert parsed["policy.allow_implicit_invocation"] == "false"


def test_claude_scans_user_project_and_command_roots(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    write_skill(home / "skills", "handoff")
    write_skill(cwd / ".claude" / "skills", "verify")
    (home / "commands").mkdir(parents=True)
    (home / "commands" / "evaluate-update.md").write_text(
        "Evaluate and discuss this. Do not code yet.\n", encoding="utf-8"
    )
    nested = cwd / ".claude" / "commands" / "git"
    nested.mkdir(parents=True)
    (nested / "sync.md").write_text(
        "---\ndescription: sync the trunk\n---\n\nbody\n", encoding="utf-8"
    )

    payload = discover_skills("claude", cwd, claude_home=home)

    assert names(payload, "project") == ["git:sync", "verify"]
    assert names(payload, "user") == ["evaluate-update", "handoff"]
    # Claude namespaces a nested command file with ':' and invokes it that way.
    invocations = {skill["name"]: skill["invocation"] for skill in payload["skills"]}
    assert invocations["git:sync"] == "/git:sync"
    assert invocations["verify"] == "/verify"
    # A command with no frontmatter still gets a description, from its first line.
    described = {skill["name"]: skill["description"] for skill in payload["skills"]}
    assert described["evaluate-update"] == "Evaluate and discuss this. Do not code yet."
    assert payload["builtin_skills_hidden"] is True


def test_claude_scans_enabled_plugins_and_names_the_skipped(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    live = tmp_path / "cache" / "live"
    dormant = tmp_path / "cache" / "dormant"
    write_skill(live / "skills", "dev-browser")
    write_skill(dormant / "skills", "never-loaded")
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "live@market": [{"scope": "user", "installPath": str(live)}],
                    "dormant@market": [{"scope": "user", "installPath": str(dormant)}],
                },
            }
        ),
        encoding="utf-8",
    )
    (home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"live@market": True, "dormant@market": False}}),
        encoding="utf-8",
    )

    payload = discover_skills("claude", cwd, claude_home=home)

    assert names(payload, "plugin") == ["dev-browser"]
    # Named rather than dropped: a missing plugin skill has one honest explanation.
    assert payload["skipped_plugins"] == ["dormant@market"]
    assert "never-loaded" not in names(payload)


def test_project_settings_can_enable_a_plugin_the_user_left_off(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    install = tmp_path / "cache" / "here"
    write_skill(install / "skills", "repo-only")
    (home / "plugins").mkdir(parents=True)
    (home / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"p@market": [{"installPath": str(install)}]}}),
        encoding="utf-8",
    )
    (home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"p@market": False}}), encoding="utf-8"
    )
    (cwd / ".claude").mkdir(parents=True)
    (cwd / ".claude" / "settings.local.json").write_text(
        json.dumps({"enabledPlugins": {"p@market": True}}), encoding="utf-8"
    )

    payload = discover_skills("claude", cwd, claude_home=home)

    assert names(payload, "plugin") == ["repo-only"]


def test_codex_scans_both_repo_roots_and_ignores_the_claude_one(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    write_skill(home / "skills", "learn")
    write_skill(home / "skills" / ".system", "skill-creator")
    write_skill(cwd / ".codex" / "skills", "probe-codex")
    write_skill(cwd / ".agents" / "skills", "probe-agents")
    # Verified against Codex 0.145: a repo's .claude/skills is Claude's alone.
    write_skill(cwd / ".claude" / "skills", "probe-claude")

    payload = discover_skills("codex", cwd, codex_home=home)

    assert names(payload, "project") == ["probe-agents", "probe-codex"]
    assert names(payload, "user") == ["learn"]
    assert names(payload, "system") == ["skill-creator"]
    assert "probe-claude" not in names(payload)
    assert all(skill["invocation"].startswith("$") for skill in payload["skills"])


def test_omp_scans_native_and_imported_skill_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "home"
    omp_home = user_home / ".omp" / "agent"
    cwd = user_home / "repo" / "nested"
    (user_home / "repo" / ".git").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user_home))
    write_skill(omp_home / "skills", "native-user")
    write_skill(omp_home / "managed-skills", "managed")
    write_skill(user_home / ".claude" / "skills", "claude-user")
    write_skill(user_home / ".agents" / "skills", "agents-user")
    write_skill(user_home / "repo" / ".omp" / "skills", "native-project")
    write_skill(cwd / ".claude" / "skills", "claude-project")
    write_skill(cwd / ".agents" / "skills", "agents-project")
    write_skill(cwd / ".codex" / "skills", "codex-project")
    write_skill(cwd / ".github" / "skills", "github-project")

    payload = discover_skills("omp", cwd, omp_home=omp_home)

    assert set(names(payload)) == {
        "agents-project",
        "agents-user",
        "claude-project",
        "claude-user",
        "codex-project",
        "github-project",
        "managed",
        "native-project",
        "native-user",
    }
    assert all(skill["invocation"].startswith("/skill:") for skill in payload["skills"])


def test_codex_scans_only_plugins_config_says_are_enabled(tmp_path: Path) -> None:
    """The plugin cache is a download area, not an install list.

    Measured on a real machine: seven marketplace plugins sat in the cache
    carrying 25 skills, and Codex loaded exactly one of them. Scanning the cache
    unfiltered advertises commands the agent does not have.
    """
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    cache = home / "plugins" / "cache"
    write_skill(cache / "openai-bundled" / "browser" / "26.5" / "skills", "control-in-app-browser")
    write_skill(cache / "openai-curated" / "spreadsheets" / "0.1.2" / "skills", "spreadsheet")
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        '[plugins."browser@openai-bundled"]\nenabled = true\n', encoding="utf-8"
    )

    payload = discover_skills("codex", cwd, codex_home=home)

    assert names(payload, "plugin") == ["control-in-app-browser"]
    assert payload["skipped_plugins"] == ["spreadsheets@openai-curated"]
    origin = next(skill["origin"] for skill in payload["skills"] if skill["scope"] == "plugin")
    assert origin == "plugin: browser"


def test_claude_names_by_directory_and_codex_by_frontmatter(tmp_path: Path) -> None:
    """The one divergence that decides what the button types.

    Probed against both CLIs with a skill whose directory and frontmatter names
    differ: Claude's own list showed the directory name, Codex's showed the
    frontmatter name.
    """
    claude_home = tmp_path / ".claude"
    codex_home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    for root in (claude_home / "skills", codex_home / "skills"):
        directory = root / "CreateSkill"
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            "---\nname: skill-creator\ndescription: makes skills\n---\n", encoding="utf-8"
        )

    claude = discover_skills("claude", cwd, claude_home=claude_home)
    codex = discover_skills("codex", cwd, codex_home=codex_home)

    assert [(s["name"], s["invocation"]) for s in claude["skills"]] == [
        ("CreateSkill", "/CreateSkill")
    ]
    assert [(s["name"], s["invocation"]) for s in codex["skills"]] == [
        ("skill-creator", "$skill-creator")
    ]


def test_a_skill_with_no_frontmatter_is_described_by_its_heading(tmp_path: Path) -> None:
    """Claude lists such a skill and describes it from the body; so do we."""
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = home / "skills" / "handoff"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "# Handoff — Session Context Transfer\n\nGenerate a handoff prompt.\n", encoding="utf-8"
    )

    payload = discover_skills("claude", cwd, claude_home=home)

    assert payload["skills"][0]["description"] == "Handoff — Session Context Transfer"
    assert payload["errors"] == []


def test_codex_sidecar_marks_explicit_only_and_display_name(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    write_skill(home / "skills", "evaluate-update")
    write_skill(home / "skills", "learn")
    sidecar = home / "skills" / "evaluate-update" / "agents"
    sidecar.mkdir(parents=True)
    (sidecar / "openai.yaml").write_text(
        'interface:\n  display_name: "Evaluate Update"\n\npolicy:\n'
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )

    payload = discover_skills("codex", cwd, codex_home=home)
    by_name = {skill["name"]: skill for skill in payload["skills"]}

    # Installed and invocable as `$evaluate-update`, just withheld from the model's
    # own list — which is why a scan reports it and `codex debug prompt-input` does not.
    assert by_name["evaluate-update"]["implicit"] is False
    assert by_name["evaluate-update"]["display_name"] == "Evaluate Update"
    assert by_name["learn"]["implicit"] is True
    assert by_name["learn"]["display_name"] is None


def test_project_skill_shadows_a_user_skill_of_the_same_name(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    write_skill(home / "skills", "verify", "the global one")
    write_skill(cwd / ".claude" / "skills", "verify", "the project one")

    payload = discover_skills("claude", cwd, claude_home=home)
    entries = [skill for skill in payload["skills"] if skill["name"] == "verify"]

    assert [skill["scope"] for skill in entries] == ["project", "user"]
    assert entries[0]["shadowed_by"] is None
    assert entries[1]["shadowed_by"] == "project skills"


def test_broken_entries_are_reported_not_silently_dropped(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    (home / "skills" / "no-manifest").mkdir(parents=True)
    directory = home / "skills" / "no-description"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: no-description\n---\n", encoding="utf-8")

    payload = discover_skills("claude", cwd, claude_home=home)
    messages = sorted(error["message"] for error in payload["errors"])

    assert messages == ["directory has no SKILL.md", "no description and no body text"]
    # The one with a manifest still lists: the CLI would load it too.
    assert names(payload) == ["no-description"]


def test_missing_roots_are_reported_as_scanned_and_empty(tmp_path: Path) -> None:
    home = tmp_path / ".claude"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    home.mkdir()

    payload = discover_skills("claude", cwd, claude_home=home)

    assert payload["skills"] == []
    # A root that quietly stopped being scanned must not look like an empty one.
    assert [root["exists"] for root in payload["roots"]] == [False, False, False, False]
    assert {root["scope"] for root in payload["roots"]} == {"project", "user"}


def test_dir_name_wins_when_frontmatter_omits_the_name(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    directory = home / "skills" / "unnamed"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\ndescription: still usable\n---\n", encoding="utf-8")

    payload = discover_skills("codex", cwd, codex_home=home)

    assert names(payload) == ["unnamed"]


def test_inventory_is_capped(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    for index in range(MAX_SKILLS + 5):
        write_skill(home / "skills", f"skill-{index:04d}")

    payload = discover_skills("codex", cwd, codex_home=home)

    assert len(payload["skills"]) == MAX_SKILLS
    assert payload["truncated"] is True


def test_cache_holds_briefly_and_refresh_bypasses_it(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    write_skill(home / "skills", "first")

    first = discover_skills("codex", cwd, codex_home=home, now=1000.0)
    write_skill(home / "skills", "second")

    assert names(discover_skills("codex", cwd, codex_home=home, now=1000.5)) == ["first"]
    assert names(discover_skills("codex", cwd, codex_home=home, now=1000.5, refresh=True)) == [
        "first",
        "second",
    ]
    later = discover_skills("codex", cwd, codex_home=home, now=1000.5 + CACHE_TTL_SECONDS)
    assert names(later) == ["first", "second"]
    assert first["generated_at"] == 1000.0


def test_cache_is_keyed_by_cwd(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    left = tmp_path / "left"
    right = tmp_path / "right"
    write_skill(left / ".codex" / "skills", "left-only")
    write_skill(right / ".codex" / "skills", "right-only")

    assert names(discover_skills("codex", left, codex_home=home, now=1.0)) == ["left-only"]
    assert names(discover_skills("codex", right, codex_home=home, now=1.0)) == ["right-only"]


def test_shell_sessions_have_no_skills_to_discover(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        discover_skills("shell", tmp_path)
