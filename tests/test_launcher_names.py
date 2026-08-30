"""No document may tell a reader to run a launcher this project does not ship.

`mux` and `muxd` were `[project.scripts]` aliases for one day (2026-08-29 to
2026-08-30) and were removed; `pyproject.toml` carries the reasoning. The single
failure that removal can cause is a document, a help string, or an error message
that still says `mux doctor` - which is worse than the collision it was removed
to avoid, because it sends someone to a command that does not exist rather than
to somebody else's.

So the sweep is asserted rather than remembered. `packaging/verify_release_unit.
py` checks the same property, but only over `README.md` and `RELEASING.md`
(`DOCUMENTED_COMMAND_SOURCES`), and 215 of the 236 invocations measured before
the rename lived elsewhere - in `.docs/`, in the embedded agent skill, and in
Python strings the user actually reads. This is the wide net.

**It scans code contexts only**, and that is the whole design. The word `mux` is
load-bearing in several namespaces that are not launchers and must never be
renamed: the data directory `~/.mux`, the database `mux.db`, the MCP server named
`mux` and its `mcp__mux__*` tools, and `swe-mux` itself. A check over prose would
either fire on all of those or need an allowlist that grows until it stops
meaning anything.

**And it borrows `verify_release_unit.documented_commands` rather than matching a
regex of its own.** That function already decides what counts as an invocation -
it strips runner prefixes (`uv run mux ls` is a use of `mux`), splits on `&&` and
`;`, skips option values, and requires an unfenced span to carry an argument so a
bare noun is not read as a command. A second implementation here would be a
second thing to keep in agreement, and the first divergence would be silent: a
looser one fires on prose like `persisted mux event` inside a fence, and a
tighter one passes while a document still names a dead command.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):  # type: ignore[no-untyped-def]
    """Import a `packaging/` script by path; it is not an installed package."""
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "packaging" / f"{name}.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify_release_unit = _load("verify_release_unit")

#: Launchers this project no longer ships, and must no longer document.
REMOVED_LAUNCHERS = ("mux", "muxd")

#: Directories with no bearing on what a reader is told to type.
#:
#: `site/` is deliberately **not** here, and the near-miss is worth recording:
#: excluding it would also exclude `site/tools/docs_content.py`, which is the
#: source every published documentation page is generated from - so the one
#: document with the largest audience would have been the only one unchecked.
#: The generated pages under `site/` need no exclusion of their own, because they
#: are `.html` and `.js` and `SCANNED_SUFFIXES` does not reach them; the check
#: therefore fires on the source and never on a stale artifact.
SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        ".trash",
        ".claude",
        ".codex",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        "htmlcov",
    }
)

#: Where a reader is told to type something. Markdown code spans and fences, plus
#: the source files that build help text, error messages and the published site.
SCANNED_SUFFIXES = frozenset({".md", ".py", ".ts", ".tsx", ".iss", ".toml"})

#: This file names the removed launchers on purpose, as does the pyproject
#: comment recording why they went. A rule that cannot survive being written down
#: is not a rule anyone can follow.
EXEMPT_FILES = frozenset(
    {
        "tests/test_launcher_names.py",
        "pyproject.toml",
        "CHANGELOG.md",
        ".docs/development/ROADMAP.md",
    }
)

#: Python sources whose string literals are themselves published code blocks,
#: rather than prose that happens to be quoted.
#:
#: `site/tools/docs_content.py` builds every page on swemux.dev out of plain
#: string literals - `"swemux start   # the daemon, in the background"` - with no
#: backticks anywhere, so a backtick-only scan misses the document with the
#: largest audience in the repository. That is how this check nearly shipped with
#: its most important file exempt.
#:
#: Scoped rather than applied to all of `src/`, and the reason is measured: run
#: over every Python source, the same scan produced dozens of false positives
#: from **wrapped prose**. A docstring line beginning "mux session." is the tail
#: of "...inside a swe-\nmux session", and `_command_word` reads the first token
#: of a line, so it cannot tell that from an invocation. Elsewhere this
#: repository names commands in backticks without exception, and the backtick
#: rule has no such ambiguity.
LITERAL_CODE_SOURCES = ("site/tools/docs_content.py",)


def _python_string_document(text: str) -> str:
    """Every string literal in a Python source, as one fenced markdown document.

    Fenced, so a one-token command counts. `_command_word` reads the *first*
    token of a line, so `persisted mux event` and `the mux MCP server` are not
    invocations and do not become ones here.

    A file that will not parse yields nothing rather than raising - this is a
    documentation check, and a syntax error is somebody else's failure.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ""
    blocks = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    return "\n".join(f"```\n{block}\n```" for block in blocks)


def scan() -> list[str]:
    """Every code span in the tree that invokes a launcher this project removed."""
    removed = set(REMOVED_LAUNCHERS)
    found: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if path.suffix not in SCANNED_SUFFIXES or not path.is_file():
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if any(part in SKIP_DIRECTORIES for part in path.relative_to(REPO_ROOT).parts):
            continue
        if relative in EXEMPT_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        sources = [text]
        if relative in LITERAL_CODE_SOURCES:
            sources.append(_python_string_document(text))
        for source in sources:
            for item in verify_release_unit.documented_commands(source, relative):
                if item.command in removed:
                    found.append(f"{relative}: {item.snippet.strip()[:90]}")
    return sorted(set(found))


def test_no_document_tells_a_reader_to_run_a_removed_launcher() -> None:
    offenders = scan()
    assert not offenders, (
        f"{len(offenders)} code span(s) still name a launcher this project no "
        "longer ships. Use `swemux` and `swemuxd`:\n  " + "\n  ".join(offenders[:40])
    )


def test_the_check_would_actually_catch_one() -> None:
    """Self-check, because a scanner that matches nothing passes silently.

    The failure this guards is the one that makes a green gate meaningless: a
    suffix list that stops covering the files commands live in, or a parser
    change upstream that narrows what counts as an invocation. Asserted against
    the borrowed parser directly, on the shapes this repository actually writes.
    """

    def commands(text: str) -> set[str]:
        return {item.command for item in verify_release_unit.documented_commands(text, "t")}

    assert "mux" in commands("Run `mux doctor` for the report.")
    assert "mux" in commands("```\nuv run mux ls\n```")
    assert "muxd" in commands("Stop it with `muxd --shutdown`.")
    assert "mux" in commands("```\nswemux ls && mux doctor\n```")
    # The namespaces that share the word and are not launchers. None of these may
    # ever be renamed, and none of them may fire this check.
    for safe in (
        "The data directory is `~/.mux` on every host.",
        "Rows land in `mux.db` under the data directory.",
        "Call `mcp__mux__notify` to reach a sibling session.",
        "```\nswe-mux --hidden\n```",
        "```\nswemux doctor\n```",
        "```text\npersisted mux event\n  -> allowlisted envelope\n```",
        "The `mux` MCP server exposes these tools.",
    ):
        assert not commands(safe) & set(REMOVED_LAUNCHERS), safe


def test_the_shipped_launchers_are_the_ones_the_project_declares() -> None:
    """`SHIPPED_COMMANDS` and `[project.scripts]` describe the same install.

    They are read by different audiences - one builds the wheel, the other tells
    a user where their commands went - and a drift between them is a `--where`
    report that looks for a launcher no installer ever wrote.
    """
    import tomllib

    from swe_mux.install_location import SHIPPED_COMMANDS

    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(manifest["project"]["scripts"]) | set(
        manifest["project"]["gui-scripts"]
    )
    assert {name for name, _ in SHIPPED_COMMANDS} == declared
    assert not declared & set(REMOVED_LAUNCHERS)
