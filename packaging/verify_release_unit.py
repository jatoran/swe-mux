"""Prove a tag, the source it names, and the wheel built from it are one release.

Phase 11 ("validate tag, source, frontend bundle, wheel/sdist metadata,
migrations, documented commands, and capability/version diagnostics as one
release unit"). Structural twin of `verify_release_artifact.py`, and it borrows
that module's `Check`/`Report`/`render` rather than growing a second reporting
shape: two validators that speak differently are harder to read in one CI log
than one more check in a familiar one.

The failure this exists to catch
--------------------------------
`verify_release_artifact.py` proves a wheel is internally well-formed - it has a
frontend, the frontend is from one build, the licence files shipped. Every one of
those questions is answered without ever looking at the tag or at the source
tree, which is exactly why a whole class of release defect passes it:

- A `v0.2.0` tag pushed over a `pyproject.toml` still saying `0.1.0` builds a
  perfectly valid wheel of the wrong version. The release page, the download
  URLs and `version.json` are keyed by the tag while the installed package
  reports its metadata version, so the in-app update check compares the wrong
  pair of numbers *forever* - every install is told a newer version exists, and
  installing it does not make the banner go away.
- `RELEASING.md` records that the version is a string literal in five more
  places than `pyproject.toml`, and that nothing keeps them in sync. Bumping the
  package alone ships a daemon that answers `/api/health` with the previous
  version and never fails while doing it.
- A `CHANGELOG.md` whose entries are still under `## [Unreleased]` publishes a
  GitHub Release with no notes and a changelog page that does not mention the
  version a user just installed.
- A `[project.urls]` entry with the `OWNER` placeholder still in it is a dead
  link on PyPI, on the artifact, and in every installed copy's metadata - and
  unlike a document, it cannot be edited after publication.
- A README that tells a user to run a command `[project.scripts]` no longer
  declares is a quickstart that fails on the first line.

None of those is a defect *in* the artifact. Each is a disagreement *between*
the tag, the source, and the artifact, so each needs the three read together -
which is what "as one release unit" means and what this module does.

What it deliberately does not check
-----------------------------------
The frontend bundle, the licence files, and the licence expression are the
sibling's, and both scripts run in `release.yml`. Duplicating them here would
give two answers that only probably agree.

Migration *contiguity* is not checkable and is not faked. Migrations in this
repository are `PRAGMA table_info` column-add lists (`.docs/technical/backend/
sqlite.md`); no per-version step list exists anywhere in the source, so "the
current schema version equals the highest migration" is not a statement the code
can be asked. What the document *does* state is checkable and is checked here -
see `_check_migration_coherence`. The composition none of it can see is proven at
runtime by `tests/test_migration_compatibility.py`, against a database a real
older revision wrote.

`RELEASING.md` step 1 also asks that no `TODO(release)` placeholder survive in
`pyproject.toml`, `CHANGELOG.md`, or `SECURITY.md`. That is a release-unit
invariant and it is not enforced here, because the tree legitimately carries
those markers today (they are the operator's own reminders, resolved during the
release commit). Adding it is a one-line change to this file the day the last one
is resolved; adding it now would make this validator red on a healthy tree, which
is how a gate gets skipped.

Usage
-----
    uv run python packaging/verify_release_unit.py --tag v0.1.0 dist/swe_mux-*.whl
    uv run python packaging/verify_release_unit.py --json <wheel>

Exit 0 when every check passes, 1 when any fails, 2 when no tag was supplied.
`--tag` defaults to `$GITHUB_REF_NAME` when that names a tag, which is what makes
the `release.yml` step a bare invocation. There is deliberately no third
behaviour when neither is present: the subject of this validator is the agreement
between a tag and everything else, so a run without one would have to report a
pass it did not earn.

`--json` writes the whole report - every check's verdict, its observed detail,
and the evidence behind it - to stdout, so a CI step can publish the reading
rather than only the exit code.
"""

from __future__ import annotations

import argparse
import ast
import email
import json
import os
import re
import sys
import tomllib
import zipfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_release_artifact import (  # noqa: E402 - sibling module, path set above
    Check,
    Report,
    render,
)

ROOT = Path(__file__).resolve().parents[1]

# Tags are `vX.Y.Z` (`RELEASING.md` § 3): a leading `v`, no prefix, no suffix.
# The PEP 440 pre-release suffixes are admitted because a TestPyPI alpha before
# the name is reserved is a planned step (`.docs/development/ROADMAP.md` Phase
# 11) and `release.yml` already keys its `--prerelease` flag off exactly this
# shape. Nothing else is: `0.1.0`, `release-0.1.0`, and `v0.1` are all rejected,
# because each of them silently makes the tag and the metadata version different
# strings that a naive comparison would still call equal after "cleaning" them.
TAG_PATTERN = re.compile(r"^v(?P<version>\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?)$")

# A *fully* anchored match, so a dependency pin (`>=1.2.3`), a date, or a path
# never counts as one of these. Only a literal that is exactly a version does.
VERSION_LITERAL = re.compile(r"^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?$")

# The version-reporting locations `RELEASING.md` tabulates, minus
# `src/swe_mux/__init__.py`, which `version-sources` owns because it is a
# declaration rather than a report. Each of these holds the version as a bare
# string literal, and `RELEASING.md` records the follow-up owed: they should read
# `swe_mux.__version__` instead. This check gets *weaker* as that happens, which
# is correct - a file with no version literal left in it has nothing to drift.
VERSION_REPORTING_SOURCES = (
    "src/swe_mux/routes/system.py",
    "src/swe_mux/routes/diagnostics.py",
    "src/swe_mux/mcp.py",
    "src/swe_mux/provider_accounts.py",
)
FRONTEND_PACKAGE_JSON = "frontend/package.json"

CHANGELOG = "CHANGELOG.md"
PYPROJECT = "pyproject.toml"
# Normalized per PEP 503, because a wheel's METADATA may spell it either way.
DISTRIBUTION_NAME = "swe-mux"
INIT = "src/swe_mux/__init__.py"
UNRELEASED = "Unreleased"

# Documents that tell a user what to run. Both are read for command invocations
# and every one that is not a third-party tool has to resolve against
# `[project.scripts]`.
DOCUMENTED_COMMAND_SOURCES = ("README.md", "RELEASING.md")

# A closed allowlist, on purpose. The alternative - guessing which bare word is a
# third-party tool - is how a check starts reporting on text it did not
# understand. A document that introduces a new external tool fails this check
# once, and the remedy line says which of the two things to do about it.
KNOWN_EXTERNAL_COMMANDS = frozenset(
    {
        "bash",
        "cd",
        "curl",
        "docker",
        "echo",
        "gh",
        "git",
        "grep",
        "ls",
        "mkdir",
        "mypy",
        "node",
        "npm",
        "npx",
        "pip",
        "pipx",
        "playwright",
        "pytest",
        "python",
        "python3",
        "ruff",
        "sh",
        "ssh",
        "tailscale",
        "uv",
        "uvx",
    }
)

# `uv run <options> <command>` and friends. The command being documented is the
# one *after* the runner, so `uv run mux doctor` is a use of `mux` and not of
# `uv`. Only the two-word form is a runner: `uv build` and `uv tool install` are
# uses of `uv` itself.
_RUNNER_PREFIXES = (("uv", "run"), ("pipx", "run"), ("uvx",), ("npx",))

# Options that consume the token after them, so the scan does not mistake an
# option's *value* for the command (`uv run --extra desktop swe-mux`).
_VALUE_OPTIONS = frozenset(
    {
        "--directory",
        "--extra",
        "--group",
        "--index",
        "--index-url",
        "--project",
        "--python",
        "--with",
        "--with-requirements",
        "-m",
        "-p",
    }
)

# A console script is a lowercase word. Anything else in command position is
# prose, a heading, a header name, or a path, and is not a command this document
# is telling anyone to run.
_COMMAND_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
_DOCUMENT_SUFFIXES = (
    ".cfg",
    ".exe",
    ".html",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"^\s*```")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_CHANGELOG_HEADING = re.compile(r"^##\s+\[(?P<label>[^\]]+)\]")
_LINK_REFERENCE = re.compile(r"^\[(?P<label>[^\]]+)\]:\s*(?P<url>\S+)\s*$")

# Substrings that mean a URL was never resolved. The uppercase ones are matched
# case-sensitively because they are the literal placeholder tokens this
# repository used (`pyproject.toml` names `OWNER`), and a case-insensitive match
# on "owner" would flag a legitimate path segment.
_URL_PLACEHOLDERS_EXACT = ("OWNER", "USERNAME", "TODO", "XXX", "YOUR_", "YOUR-", "<", ">")
_URL_PLACEHOLDERS_LOWER = ("example.com", "example.org", "changeme", "your-org")

# `PRAGMA user_version` is a property of the *file*, and eleven stores share
# `mux.db` (`.docs/technical/backend/sqlite.md`). Each one stamping it means the
# last connect wins and every store reads a neighbour's number - a mechanism that
# looks armed while being unusable.
_FORBIDDEN_PRAGMA = "user_version"
_STAMP_FUNCTION = "write_schema_version"


# --------------------------------------------------------------------------- source reading


@dataclass(frozen=True)
class SourceTree:
    """The repository, read tolerantly.

    Every accessor answers `None` for a file that is not there rather than
    raising, because "the checkout this ran against was incomplete" is a verdict
    a release gate has to be able to *report*, not a traceback it dies on.
    """

    root: Path

    def text(self, relative: str) -> str | None:
        path = self.root / relative
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def pyproject(self) -> dict[str, Any] | None:
        path = self.root / PYPROJECT
        try:
            with path.open("rb") as handle:
                loaded: dict[str, Any] = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return None
        return loaded

    def project_table(self) -> dict[str, Any]:
        data = self.pyproject() or {}
        table = data.get("project")
        return table if isinstance(table, dict) else {}


def declared_version(tree: SourceTree) -> str | None:
    """`[project] version` from pyproject.toml - the authoritative declaration."""
    value = tree.project_table().get("version")
    return value if isinstance(value, str) else None


def dunder_version(tree: SourceTree) -> str | None:
    """`__version__` from `src/swe_mux/__init__.py`, read as syntax, not as text.

    Parsed rather than grepped so a mention of `__version__` in the module
    docstring - or in a comment recording the rule - cannot be read as the
    declaration itself.
    """
    source = tree.text(INIT)
    if source is None:
        return None
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    for node in module.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__version__":
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
    return None


def _docstring_nodes(module: ast.Module) -> set[int]:
    """Identity of every docstring constant, so prose is never read as code."""
    found: set[int] = set()
    for node in ast.walk(module):
        if not isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def string_constants(module: ast.Module) -> Iterator[str]:
    """Every string literal in the module except its docstrings.

    Comments never appear at all - which is the point. Three modules mention
    `PRAGMA user_version` in a comment explaining why they do not use it, and a
    text scan would report all three as violations of the rule they document.
    """
    docstrings = _docstring_nodes(module)
    for node in ast.walk(module):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.value


def version_literals(tree: SourceTree, relative: str) -> list[str] | None:
    """Version-shaped string literals in one Python source file, or None if unreadable."""
    source = tree.text(relative)
    if source is None:
        return None
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    return [value for value in string_constants(module) if VERSION_LITERAL.match(value)]


# --------------------------------------------------------------------------- changelog


def _strip_html_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", text)


def changelog_sections(text: str) -> dict[str, str]:
    """Body of each `## [label]` section, keyed by label, in document order."""
    sections: dict[str, str] = {}
    label: str | None = None
    body: list[str] = []
    for line in _strip_html_comments(text).splitlines():
        heading = _CHANGELOG_HEADING.match(line)
        if heading:
            if label is not None:
                sections[label] = "\n".join(body)
            label = heading.group("label")
            body = []
        elif label is not None:
            body.append(line)
    if label is not None:
        sections[label] = "\n".join(body)
    return sections


def _has_content(body: str) -> bool:
    """True when a section says anything.

    Link-reference lines do not count. They sit at the foot of the file, which
    is inside the *last* section by any structural reading, so counting them
    would make an empty final entry look populated - and an empty entry for the
    version being released is precisely one of the states this checks for.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or _LINK_REFERENCE.match(stripped):
            continue
        return True
    return False


def changelog_links(text: str) -> dict[str, str]:
    references: dict[str, str] = {}
    for line in text.splitlines():
        match = _LINK_REFERENCE.match(line.strip())
        if match:
            references[match.group("label")] = match.group("url")
    return references


def _section_for(sections: dict[str, str], version: str) -> str | None:
    """The section whose label opens with the version, so a dated heading matches."""
    for label, body in sections.items():
        if label == version or label.startswith(f"{version} ") or label.startswith(f"{version}]"):
            return body
    return None


# --------------------------------------------------------------------------- documented commands


@dataclass(frozen=True)
class DocumentedCommand:
    """One invocation a document tells a reader to run."""

    command: str
    source: str
    snippet: str


def _code_snippets(text: str) -> Iterator[tuple[str, bool]]:
    """Every code snippet in a markdown document, with whether it was fenced.

    HTML comments are removed first. A command inside one is a note the author
    left for themselves - README's `TODO(release)` block holds the `uv tool
    install` line the project cannot honour yet - and reading it as an
    instruction would gate the release on a command that is deliberately not
    being given.
    """
    fenced = False
    for line in _strip_html_comments(text).splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            yield line, True
        else:
            for span in _INLINE_CODE.findall(line):
                yield span, False


def _command_word(tokens: list[str]) -> str | None:
    index = 0
    for prefix in _RUNNER_PREFIXES:
        if tuple(tokens[: len(prefix)]) == prefix:
            index = len(prefix)
            break
    if index:
        while index < len(tokens) and tokens[index].startswith("-"):
            option = tokens[index]
            index += 2 if option in _VALUE_OPTIONS and "=" not in option else 1
    if index >= len(tokens):
        return None
    word = tokens[index]
    if not _COMMAND_NAME.match(word) or word.endswith(_DOCUMENT_SUFFIXES):
        return None
    return word


def documented_commands(text: str, source: str) -> list[DocumentedCommand]:
    """Command invocations a markdown document gives its reader.

    An *unfenced* snippet has to carry at least one argument to count. A bare
    inline word is overwhelmingly a noun in this repository's documents -
    `verify`, `desktop`, `master`, `num2words` - and treating those as commands
    would bury the three real entry points in fifty false ones. Inside a fenced
    block the surrounding prose is gone, so a bare word there is an instruction.
    """
    found: list[DocumentedCommand] = []
    for snippet, fenced in _code_snippets(text):
        for part in re.split(r"&&|\|\||[;|]", snippet):
            tokens = part.split()
            if not tokens or (not fenced and len(tokens) < 2):
                continue
            word = _command_word(tokens)
            if word is not None:
                found.append(DocumentedCommand(word, source, " ".join(tokens)))
    return found


# --------------------------------------------------------------------------- migrations


@dataclass(frozen=True)
class SchemaStamp:
    """One `write_schema_version(db, "<store>", <CONSTANT>)` call site."""

    module: str
    store: str | None
    constant: str | None
    version: int | None


def _module_level_ints(module: ast.Module) -> dict[str, int]:
    found: dict[str, int] = {}
    for node in module.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = node.value if isinstance(node, ast.Assign | ast.AnnAssign) else None
        if not isinstance(value, ast.Constant) or not isinstance(value.value, int):
            continue
        if isinstance(value.value, bool):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _stamps_in(module: ast.Module, name: str) -> list[SchemaStamp]:
    constants = _module_level_ints(module)
    stamps: list[SchemaStamp] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        called = (
            function.id
            if isinstance(function, ast.Name)
            else function.attr
            if isinstance(function, ast.Attribute)
            else ""
        )
        if called != _STAMP_FUNCTION or len(node.args) < 3:
            continue
        store_argument = node.args[1]
        version_argument = node.args[2]
        store = (
            store_argument.value
            if isinstance(store_argument, ast.Constant) and isinstance(store_argument.value, str)
            else None
        )
        constant = version_argument.id if isinstance(version_argument, ast.Name) else None
        stamps.append(
            SchemaStamp(
                module=name,
                store=store,
                constant=constant,
                version=constants.get(constant) if constant else None,
            )
        )
    return stamps


def scan_schema_stamps(tree: SourceTree) -> tuple[list[SchemaStamp], list[str]]:
    """Every schema-version stamp in the package, plus modules using the banned pragma.

    Only files whose text mentions one of the two tokens are parsed. A file that
    mentions neither can contain neither, so the pre-filter is exact rather than
    a heuristic - and it keeps a release gate from parsing every module in a
    5000-line package to find eleven call sites.
    """
    package = tree.root / "src" / "swe_mux"
    stamps: list[SchemaStamp] = []
    pragma_users: list[str] = []
    if not package.is_dir():
        return stamps, pragma_users
    for path in sorted(package.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if _STAMP_FUNCTION not in source and _FORBIDDEN_PRAGMA not in source:
            continue
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue
        name = path.relative_to(tree.root).as_posix()
        stamps.extend(_stamps_in(module, name))
        if any(_FORBIDDEN_PRAGMA in value for value in string_constants(module)):
            pragma_users.append(name)
    return stamps, pragma_users


# --------------------------------------------------------------------------- wheel reading


@dataclass(frozen=True)
class WheelFacts:
    """Everything the checks need from the artifact, read in one pass."""

    names: list[str]
    metadata: str | None
    entry_points: str | None
    dist_info: str | None


def _dist_info_dir(names: list[str]) -> str | None:
    for name in names:
        head = name.split("/", 1)[0]
        if head.endswith(".dist-info"):
            return head
    return None


def read_wheel(wheel: Path) -> WheelFacts:
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(info.filename for info in archive.infolist() if not info.is_dir())
        dist_info = _dist_info_dir(names)

        def read(member: str) -> str | None:
            if dist_info is None:
                return None
            full = f"{dist_info}/{member}"
            if full not in names:
                return None
            return archive.read(full).decode("utf-8", errors="replace")

        return WheelFacts(
            names=names,
            metadata=read("METADATA"),
            entry_points=read("entry_points.txt"),
            dist_info=dist_info,
        )


def metadata_headers(metadata: str | None) -> dict[str, list[str]]:
    if metadata is None:
        return {}
    message = email.message_from_string(metadata)
    headers: dict[str, list[str]] = {}
    for key, value in message.items():
        headers.setdefault(key, []).append(value.strip())
    return headers


def metadata_project_urls(metadata: str | None) -> dict[str, str]:
    """`Project-URL: Label, https://…` lines, as a label→url map."""
    urls: dict[str, str] = {}
    for value in metadata_headers(metadata).get("Project-URL", []):
        label, _, url = value.partition(",")
        if url:
            urls[label.strip()] = url.strip()
    return urls


def wheel_console_scripts(entry_points: str | None) -> dict[str, str]:
    """The `[console_scripts]` section of a wheel's `entry_points.txt`."""
    scripts: dict[str, str] = {}
    if entry_points is None:
        return scripts
    section = ""
    for line in entry_points.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section != "console_scripts" or "=" not in stripped:
            continue
        name, _, target = stripped.partition("=")
        scripts[name.strip()] = target.strip()
    return scripts


# --------------------------------------------------------------------------- the checks


def _check_tag_format(tag: str) -> Check:
    name = "tag-format"
    if TAG_PATTERN.match(tag):
        return Check(name, True, f"Tag {tag} has the released shape vX.Y.Z.")
    return Check(
        name,
        False,
        f"Tag {tag!r} is not of the form vX.Y.Z (a leading `v`, three numbers, and at "
        "most a PEP 440 pre-release suffix such as `rc1`).",
        "`RELEASING.md` § 3 fixes the tag shape because the release page, the download "
        "URLs, and `site/version.json` are all keyed by it. Re-tag with the right name "
        "rather than teaching this check a second spelling; a tag that has published to "
        "PyPI cannot be reused, but one that has not can be deleted and rewritten.",
    )


def _check_version_sources(tree: SourceTree, version: str, tag: str) -> Check:
    name = "version-sources"
    declared = declared_version(tree)
    dunder = dunder_version(tree)
    if declared is None:
        return Check(
            name,
            False,
            f"{PYPROJECT} has no readable `[project] version` under {tree.root}.",
            "Run this from a complete checkout of the tagged revision; the version in "
            "`pyproject.toml` is what the wheel, PyPI, and every installer read.",
        )
    if dunder is None:
        return Check(
            name,
            False,
            f"{INIT} declares no `__version__` string.",
            "`RELEASING.md` records `__version__` as what the configurator's generated "
            "inventory reports to an agent. Restore the module-level "
            f'`__version__ = "{version}"`.',
        )
    disagreements = []
    if declared != version:
        disagreements.append(f"{PYPROJECT} says {declared}")
    if dunder != version:
        disagreements.append(f"{INIT} says {dunder}")
    if disagreements:
        return Check(
            name,
            False,
            f"Tag {tag} claims version {version}, but {' and '.join(disagreements)}.",
            f'Set `version = "{version}"` in {PYPROJECT} and '
            f'`__version__ = "{version}"` in {INIT}, in one commit, and re-cut the tag '
            "over it. A wheel built from this tree is a valid artifact of the wrong "
            "version: the update manifest is keyed by the tag while the installed "
            "package reports its metadata version, so the two never agree again.",
        )
    return Check(
        name,
        True,
        f"Tag {tag}, {PYPROJECT}, and {INIT} all say {version}.",
    )


def _check_version_literals(tree: SourceTree, version: str) -> Check:
    """The five reporting locations `RELEASING.md` tabulates, plus the frontend's.

    Finding *no* literal in one of these files is a pass, and deliberately so:
    that is what the file looks like once it reads `swe_mux.__version__`, which
    `RELEASING.md` names as the real fix. The check exists to catch a literal
    that is *stale*, not to require that one exists.
    """
    name = "version-literals"
    stale: list[str] = []
    unreadable: list[str] = []
    checked = 0
    for relative in VERSION_REPORTING_SOURCES:
        literals = version_literals(tree, relative)
        if literals is None:
            unreadable.append(relative)
            continue
        checked += len(literals)
        stale.extend(f"{relative} reports {value}" for value in literals if value != version)

    package_json = tree.text(FRONTEND_PACKAGE_JSON)
    if package_json is None:
        unreadable.append(FRONTEND_PACKAGE_JSON)
    else:
        try:
            frontend = json.loads(package_json).get("version")
        except (json.JSONDecodeError, AttributeError):
            frontend = None
        if isinstance(frontend, str):
            checked += 1
            if frontend != version:
                stale.append(f"{FRONTEND_PACKAGE_JSON} says {frontend}")

    if unreadable:
        return Check(
            name,
            False,
            f"Could not read {', '.join(unreadable)}, so the version those files report "
            "was not compared.",
            "Run this from a complete checkout of the tagged revision. These files are "
            "tracked, so their absence means the tree is incomplete rather than that the "
            "version moved.",
        )
    if stale:
        return Check(
            name,
            False,
            f"{len(stale)} location(s) report a version other than {version}: "
            f"{'; '.join(stale)}.",
            f"Bump every location in `RELEASING.md` § Versioning to {version} in one "
            "commit. A daemon that answers `/api/health` with the previous version never "
            "fails while doing it, and it is what `mux doctor` and the in-app update "
            "check both read. If one of these literals is not the swe-mux version at all, "
            "the fix is the one `RELEASING.md` calls owed: make these read "
            "`swe_mux.__version__` instead of carrying a copy.",
        )
    return Check(
        name,
        True,
        f"{checked} version literal(s) across "
        f"{len(VERSION_REPORTING_SOURCES) + 1} reporting location(s) all say {version}.",
    )


def _check_changelog_entry(tree: SourceTree, version: str) -> Check:
    name = "changelog-entry"
    text = tree.text(CHANGELOG)
    if text is None:
        return Check(
            name,
            False,
            f"{CHANGELOG} is not readable under {tree.root}.",
            "Run this from a complete checkout of the tagged revision; the GitHub "
            "Release body and the site's changelog page are both drawn from this file.",
        )
    sections = changelog_sections(text)
    body = _section_for(sections, version)
    if body is None:
        unreleased = sections.get(UNRELEASED, "")
        consequence = (
            f" The `## [{UNRELEASED}]` section is not empty, so the entries for this "
            "release are most likely still sitting in it."
            if _has_content(unreleased)
            else ""
        )
        return Check(
            name,
            False,
            f"{CHANGELOG} has no `## [{version}]` section "
            f"(it has: {', '.join(sections) or 'no sections at all'}).{consequence}",
            f"Move everything under `## [{UNRELEASED}]` into a new "
            f"`## [{version}] - YYYY-MM-DD` section, as `RELEASING.md` § 1 describes. "
            "The GitHub Release body is this section; without it the release publishes "
            "with no notes.",
        )
    if not _has_content(body):
        return Check(
            name,
            False,
            f"{CHANGELOG} has a `## [{version}]` heading with nothing under it.",
            f"Write the entry. It is deliberately not generated from commit subjects "
            f"(`RELEASING.md`, 'What is not automated'), so an empty `## [{version}]` "
            "section publishes a release whose notes are a heading.",
        )
    if _has_content(sections.get(UNRELEASED, "")):
        return Check(
            name,
            False,
            f"`## [{version}]` is written, but `## [{UNRELEASED}]` still has content "
            "above it, so part of this release is recorded as unreleased.",
            f"Move the remaining `## [{UNRELEASED}]` entries into `## [{version}]` and "
            f"leave `## [{UNRELEASED}]` empty, per `RELEASING.md` § 1. Anything left "
            "there is a change that shipped in this version and says it did not.",
        )
    return Check(
        name,
        True,
        f"`## [{version}]` carries the release notes and `## [{UNRELEASED}]` is empty.",
    )


def _check_changelog_links(tree: SourceTree, version: str) -> Check:
    """Keyed off the version's canonical tag rather than the string that was passed.

    `RELEASING.md` § 3 makes `v<version>` the only spelling, and `tag-format`
    already owns whether the supplied tag is that. Deriving it here means a
    malformed tag produces one failure that says so, instead of a second one
    here that reads as a changelog defect.
    """
    name = "changelog-links"
    tag = f"v{version}"
    text = tree.text(CHANGELOG)
    if text is None:
        return Check(
            name,
            False,
            f"Not evaluated: {CHANGELOG} is not readable.",
            "Fix `changelog-entry` first.",
        )
    references = changelog_links(text)
    release = references.get(version)
    unreleased = references.get(UNRELEASED)
    problems = []
    if release is None:
        problems.append(f"there is no `[{version}]:` link reference")
    elif f"/{tag}" not in release:
        problems.append(f"`[{version}]:` points at {release}, which does not name {tag}")
    if unreleased is None:
        problems.append(f"there is no `[{UNRELEASED}]:` link reference")
    elif f"{tag}...HEAD" not in unreleased:
        problems.append(
            f"`[{UNRELEASED}]:` points at {unreleased}, which does not compare from {tag}"
        )
    if problems:
        return Check(
            name,
            False,
            f"{CHANGELOG}'s link references disagree with {tag}: {'; '.join(problems)}.",
            f"At the foot of {CHANGELOG}, point `[{UNRELEASED}]` at "
            f"`compare/{tag}...HEAD` and add `[{version}]` pointing at "
            f"`releases/tag/{tag}`, per `RELEASING.md` § 1. These are the links the "
            "published changelog page renders, so a wrong one is a dead link on the "
            "site rather than in the repository.",
        )
    return Check(
        name,
        True,
        f"`[{version}]` and `[{UNRELEASED}]` both reference {tag}.",
    )


def _check_wheel_version(facts: WheelFacts, version: str, tag: str) -> Check:
    name = "wheel-version"
    if facts.metadata is None:
        return Check(
            name,
            False,
            "The wheel has no dist-info METADATA, so it declares no version at all.",
            "Rebuild it with `uv build --wheel`.",
        )
    headers = metadata_headers(facts.metadata)
    built = (headers.get("Version") or [""])[0]
    distribution = (headers.get("Name") or [""])[0]
    if built != version:
        return Check(
            name,
            False,
            f"The wheel's METADATA declares Version: {built or '(absent)'}, but tag {tag} "
            f"claims {version}.",
            "The wheel was built from a different revision than the one that was tagged. "
            "Build the artifact from the tagged commit and nowhere else - `release.yml` "
            "does this by checking the tag out and building in the same job.",
        )
    if distribution.replace("_", "-").lower() != DISTRIBUTION_NAME:
        return Check(
            name,
            False,
            f"The wheel's METADATA declares Name: {distribution or '(absent)'}, not "
            f"{DISTRIBUTION_NAME}.",
            "This is not this project's wheel. Check the path given to this script; a "
            "`dist/` directory left over from another build is the usual cause.",
        )
    return Check(
        name,
        True,
        f"The wheel declares {distribution} {built}, matching tag {tag}.",
    )


def _url_problem(url: str) -> str | None:
    if not url or url != url.strip() or any(character.isspace() for character in url):
        return "is empty or carries whitespace"
    for token in _URL_PLACEHOLDERS_EXACT:
        if token in url:
            return f"still contains the placeholder {token!r}"
    lowered = url.lower()
    for token in _URL_PLACEHOLDERS_LOWER:
        if token in lowered:
            return f"still contains the placeholder {token!r}"
    parts = urlsplit(url)
    if parts.scheme != "https":
        return f"is {parts.scheme or 'scheme-less'} rather than https"
    if not parts.netloc:
        return "has no host"
    return None


def _check_project_urls(tree: SourceTree, facts: WheelFacts) -> Check:
    """`[project.urls]` well-formed in the source, and carried into the artifact.

    Both halves, because they fail separately. A placeholder in the source is a
    dead link on PyPI; a URL that never reached the wheel's metadata is an
    installed copy that cannot say where it came from, and neither is visible
    from the other side.
    """
    name = "project-urls"
    declared = tree.project_table().get("urls")
    if not isinstance(declared, dict) or not declared:
        return Check(
            name,
            False,
            f"{PYPROJECT} declares no `[project.urls]`, so the artifact and its index "
            "page point nowhere.",
            "Declare Homepage, Repository, Documentation, Changelog, and Issues under "
            "`[project.urls]`. They are the one place a wrong URL becomes a dead link on "
            "a published artifact rather than in a document that can be edited "
            "afterwards.",
        )
    malformed = []
    for label, url in sorted(declared.items()):
        problem = _url_problem(str(url))
        if problem:
            malformed.append(f"{label} ({url}) {problem}")
    if malformed:
        return Check(
            name,
            False,
            f"{len(malformed)} of {len(declared)} `[project.urls]` entries are not "
            f"publishable: {'; '.join(malformed)}.",
            "Resolve every placeholder and use an absolute https URL. `RELEASING.md` § 1 "
            "names this explicitly: the `OWNER` placeholder is resolved once, when the "
            "repository is published, and must not reach a published artifact.",
        )
    shipped = metadata_project_urls(facts.metadata)
    missing = [
        f"{label} -> {url}" for label, url in sorted(declared.items()) if shipped.get(label) != url
    ]
    if missing:
        return Check(
            name,
            False,
            f"{len(missing)} declared URL(s) did not reach the wheel's METADATA as a "
            f"`Project-URL` line: {'; '.join(missing)}. The wheel carries "
            f"{len(shipped)} such line(s).",
            "The wheel was built from a different `pyproject.toml` than the one in this "
            "checkout. Rebuild from the tagged revision with `uv build --wheel`.",
        )
    return Check(
        name,
        True,
        f"All {len(declared)} `[project.urls]` entries are absolute https URLs with no "
        f"placeholder, and each reached the wheel's METADATA ({', '.join(sorted(declared))}).",
    )


def scan_documented_commands(tree: SourceTree) -> tuple[list[DocumentedCommand], list[str]]:
    """Every documented invocation, plus the documents that could not be read."""
    found: list[DocumentedCommand] = []
    unreadable: list[str] = []
    for source in DOCUMENTED_COMMAND_SOURCES:
        text = tree.text(source)
        if text is None:
            unreadable.append(source)
            continue
        found.extend(documented_commands(text, source))
    return found, unreadable


def _check_documented_commands(
    tree: SourceTree, found: list[DocumentedCommand], unreadable: list[str]
) -> Check:
    name = "documented-commands"
    scripts = tree.project_table().get("scripts")
    declared = set(scripts) if isinstance(scripts, dict) else set()
    if unreadable:
        return Check(
            name,
            False,
            f"Could not read {', '.join(unreadable)}, so the commands they document were "
            "not resolved.",
            "Run this from a complete checkout of the tagged revision.",
        )
    unresolved = [
        item
        for item in found
        if item.command not in declared and item.command not in KNOWN_EXTERNAL_COMMANDS
    ]
    if unresolved:
        listed = "; ".join(
            f"`{item.snippet}` ({item.source})"
            for item in sorted(unresolved, key=lambda item: (item.command, item.source))
        )
        return Check(
            name,
            False,
            f"{len(unresolved)} documented command(s) resolve against neither "
            f"`[project.scripts]` ({', '.join(sorted(declared)) or 'nothing'}) nor the "
            f"known third-party tools: {listed}.",
            "Either the entry point was renamed or removed - in which case the document "
            "is telling a new user to run something that does not exist, and the fix is "
            "in one of them - or the command is a third-party tool, in which case add it "
            "to `KNOWN_EXTERNAL_COMMANDS` in this file. The list is closed on purpose: "
            "guessing which bare word is a tool is how a check starts reporting on text "
            "it did not understand.",
        )
    used = sorted({item.command for item in found if item.command in declared})
    return Check(
        name,
        True,
        f"{len(found)} documented command(s) across "
        f"{len(DOCUMENTED_COMMAND_SOURCES)} document(s) all resolve; "
        f"{len(used)} of them are this project's own ({', '.join(used) or 'none'}).",
    )


def _check_console_scripts(tree: SourceTree, facts: WheelFacts) -> Check:
    """The other half of the same join: a declared script has to be in the wheel."""
    name = "console-scripts"
    scripts = tree.project_table().get("scripts")
    declared = {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}
    if not declared:
        return Check(
            name,
            False,
            f"{PYPROJECT} declares no `[project.scripts]`, so an install provides no "
            "commands at all.",
            "Declare the entry points under `[project.scripts]`; `README.md` tells a new "
            "user to run them immediately after installing.",
        )
    shipped = wheel_console_scripts(facts.entry_points)
    if not shipped:
        return Check(
            name,
            False,
            "The wheel carries no `[console_scripts]` in its dist-info "
            "`entry_points.txt`, so installing it puts nothing on PATH.",
            "Rebuild with `uv build --wheel` from the tagged revision. A wheel with no "
            "entry points installs cleanly and leaves the user with no `mux` and no "
            "`muxd`, which is only visible after the install has already succeeded.",
        )
    wrong = [
        f"{command} (declared {target}, wheel has {shipped.get(command, '(absent)')})"
        for command, target in sorted(declared.items())
        if shipped.get(command) != target
    ]
    if wrong:
        return Check(
            name,
            False,
            f"{len(wrong)} of {len(declared)} declared script(s) are missing from the "
            f"wheel or point elsewhere: {'; '.join(wrong)}.",
            "The wheel was built from a different `pyproject.toml` than this checkout's. "
            "Rebuild from the tagged revision with `uv build --wheel`.",
        )
    return Check(
        name,
        True,
        f"All {len(declared)} declared script(s) are in the wheel's entry_points.txt "
        f"with the same target ({', '.join(sorted(declared))}).",
    )


def _check_migration_coherence(
    tree: SourceTree, stamps: list[SchemaStamp], pragma_users: list[str]
) -> Check:
    """The invariants `.docs/technical/backend/sqlite.md` actually states.

    Three of them, and no more. Contiguity is not among them because it is not
    expressible: a migration here is a `PRAGMA table_info` column-add list with
    no version number attached to any step, so nothing in the source maps a
    version onto the steps that produce it. A check for it would either always
    pass or assert an invention.

    What the document does state, and what a release can therefore be held to:
    versions live in the shared per-store `schema_versions` table and never in
    `PRAGMA user_version`; each store owns exactly one row, so two stores under
    one name means each reads the other's number; and a stamped version is the
    module's declared constant rather than a literal beside it, which is what
    keeps the number a reader sees in the schema the number the file records.
    """
    name = "migration-coherence"
    if not stamps and not (tree.root / "src" / "swe_mux").is_dir():
        return Check(
            name,
            False,
            f"No `src/swe_mux` package under {tree.root}, so no store could be read.",
            "Run this from a complete checkout of the tagged revision.",
        )
    if pragma_users:
        return Check(
            name,
            False,
            f"{', '.join(pragma_users)} execute(s) SQL naming `{_FORBIDDEN_PRAGMA}`. "
            "That pragma is a property of the database *file*, and every store shares "
            "`mux.db`, so each one stamping it means the last connect wins and every "
            "store then reads a neighbour's version.",
            "Use `sqlite_store.read_schema_version` / `write_schema_version`, which keep "
            "a row per store (`.docs/technical/backend/sqlite.md`). A file-wide pragma "
            "here looks armed while being unusable, so nothing fails at the time - the "
            "next migration simply does not run.",
        )
    problems = []
    for stamp in stamps:
        if stamp.store is None:
            problems.append(f"{stamp.module} names its store with something other than a literal")
        if stamp.constant is None:
            problems.append(
                f"{stamp.module} stamps a literal version rather than a module constant"
            )
        elif stamp.version is None:
            problems.append(
                f"{stamp.module} stamps `{stamp.constant}`, which is not a module-level int"
            )
        elif stamp.version < 1:
            problems.append(
                f"{stamp.module} declares {stamp.constant} = {stamp.version}, and 0 is what "
                "an unstamped database already reads as"
            )
    seen: dict[str, str] = {}
    for stamp in stamps:
        if stamp.store is None:
            continue
        if stamp.store in seen and seen[stamp.store] != stamp.module:
            problems.append(
                f"{seen[stamp.store]} and {stamp.module} both stamp the store key "
                f"{stamp.store!r}, so each one's migration reads the other's version"
            )
        seen.setdefault(stamp.store, stamp.module)
    if problems:
        return Check(
            name,
            False,
            f"{len(problems)} schema-version problem(s): {'; '.join(problems)}.",
            "Every store stamps its own key with its own module-level "
            "`*_SCHEMA_VERSION` constant, starting at 1 "
            "(`.docs/technical/backend/sqlite.md`). A version that disagrees with the "
            "schema beside it makes an upgrade skip a migration silently, which surfaces "
            "on a user's database and never on a fresh install.",
        )
    return Check(
        name,
        True,
        f"{len(stamps)} store(s) stamp {len(seen)} distinct key(s) "
        f"({', '.join(sorted(seen))}), each from its own module constant, and no module "
        f"executes `{_FORBIDDEN_PRAGMA}`.",
    )


# --------------------------------------------------------------------------- driver


def verify(wheel: Path, tag: str, root: Path | None = None) -> Report:
    """Run every check over `wheel` against the tag and the tree. Never raises."""
    tree = SourceTree(ROOT if root is None else root)
    try:
        facts = read_wheel(wheel)
    except FileNotFoundError:
        return _unreadable(
            wheel,
            tag,
            f"{wheel} does not exist.",
            "Pass the path to a built wheel, e.g. `uv build --wheel` then "
            "`uv run python packaging/verify_release_unit.py --tag <tag> dist/*.whl`.",
        )
    except (zipfile.BadZipFile, OSError) as error:
        return _unreadable(
            wheel,
            tag,
            f"{wheel} could not be read as a wheel (zip): {error}.",
            "A wheel is a zip archive. Check the path points at the `.whl` and that the "
            "build or the download completed, then rebuild with `uv build --wheel`.",
        )

    match = TAG_PATTERN.match(tag)
    # A malformed tag still has to be compared against something, or every later
    # check reports "not evaluated" and the reader learns one fact where there
    # were eight. The declared version is the honest fallback: `tag-format` has
    # already failed and says why, and the rest go on answering their own
    # questions about the tree and the artifact.
    version = match.group("version") if match else (declared_version(tree) or tag.lstrip("v"))

    stamps, pragma_users = scan_schema_stamps(tree)
    commands, unreadable_documents = scan_documented_commands(tree)

    checks = [
        Check("artifact-readable", True, f"{len(facts.names)} entries in the wheel."),
        _check_tag_format(tag),
        _check_version_sources(tree, version, tag),
        _check_version_literals(tree, version),
        _check_changelog_entry(tree, version),
        _check_changelog_links(tree, version),
        _check_wheel_version(facts, version, tag),
        _check_project_urls(tree, facts),
        _check_documented_commands(tree, commands, unreadable_documents),
        _check_console_scripts(tree, facts),
        _check_migration_coherence(tree, stamps, pragma_users),
    ]
    evidence = _empty_evidence()
    evidence.update(
        {
            "tag": tag,
            "version": version,
            "pyproject_version": declared_version(tree),
            "init_version": dunder_version(tree),
            "wheel_version": (metadata_headers(facts.metadata).get("Version") or [None])[0],
            "declared_urls": tree.project_table().get("urls") or {},
            "metadata_urls": metadata_project_urls(facts.metadata),
            "project_scripts": tree.project_table().get("scripts") or {},
            "wheel_console_scripts": wheel_console_scripts(facts.entry_points),
            "documented_commands": sorted({item.command for item in commands}),
            "schema_stamps": [asdict(stamp) for stamp in stamps],
            "pragma_user_version_modules": pragma_users,
        }
    )
    return Report(
        wheel=str(wheel),
        ok=all(check.ok for check in checks),
        checks=checks,
        evidence=evidence,
    )


def _empty_evidence() -> dict[str, Any]:
    """The evidence keys, always all of them.

    A consumer parsing `--json` must not have to branch on whether the wheel
    could be opened: an unreadable artifact reports empty evidence, never absent
    evidence, so `evidence["project_scripts"]` is a valid read on every report
    this module produces.
    """
    return {
        "tag": "",
        "version": "",
        "pyproject_version": None,
        "init_version": None,
        "wheel_version": None,
        "declared_urls": {},
        "metadata_urls": {},
        "project_scripts": {},
        "wheel_console_scripts": {},
        "documented_commands": [],
        "schema_stamps": [],
        "pragma_user_version_modules": [],
    }


def _unreadable(wheel: Path, tag: str, detail: str, remedy: str) -> Report:
    evidence = _empty_evidence()
    evidence["tag"] = tag
    check = Check("artifact-readable", False, detail, remedy)
    return Report(wheel=str(wheel), ok=False, checks=[check], evidence=evidence)


def resolve_tag(explicit: str | None, environment: str | None) -> str | None:
    """`--tag`, else `$GITHUB_REF_NAME` when that names a tag rather than a branch."""
    if explicit:
        return explicit
    if environment and environment.startswith("v"):
        return environment
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path, help="Path to the built .whl to validate.")
    parser.add_argument(
        "--tag",
        default=None,
        help="The release tag being validated (vX.Y.Z). Defaults to $GITHUB_REF_NAME "
        "when that names a tag.",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Write the full report as JSON to stdout instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    tag = resolve_tag(args.tag, os.environ.get("GITHUB_REF_NAME"))
    if tag is None:
        parser.error(
            "no tag to validate against: pass --tag vX.Y.Z, or run where GITHUB_REF_NAME "
            "names a tag. The subject of this check is the agreement between a tag and "
            "everything else, so a run without one would report a pass it did not earn. "
            "Before tagging, pass the tag you are about to cut - that is the point at "
            "which a mismatch is still fixable."
        )

    report = verify(args.wheel, tag)
    if args.as_json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        print(render(report, subject="Release unit"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
