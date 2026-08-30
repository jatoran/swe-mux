"""Install the swe-mux agent skill into the directories harness CLIs read.

The skill (`assets/skills/swe-mux/SKILL.md`) is how an agent learns what swe-mux
offers it when the mux MCP tools are not attached. It ships inside the package -
wheel, app bundle, and CLI bundle all carry `assets/` - so `swemux --skill`
always prints the copy matching this exact release and the guidance cannot drift
from the code, the way a registry-fetched copy would.

Where it is written is the entire design question, and the answer is measured
rather than assumed (verified against the CLIs `agent_skills.py` names):

- Every non-Claude agent harness registered today - codex, pi, omp, opencode -
  reads the shared project root `.agents/skills/`, and pi/omp/opencode read the
  per-user `~/.agents/skills/` as well. One directory serves four CLIs.
- Claude reads `.claude/skills/` (project and user), which omp also scans.
- Codex alone among the four ignores `~/.agents/skills` at user scope; its user
  root is `<CODEX_HOME>/skills/`.

None of those four accepts a per-session skills directory by flag, env var, or
config key - `codex plugin add` installs only from marketplace snapshots, and
redirecting `CODEX_HOME`/`PI_CODING_AGENT_DIR` wholesale would move auth with
it - so writing into these trees is genuinely the only route, and every write
here is an explicit act: the `swemux install-skill` command, never a spawn-time
side effect. (Claude does accept `--plugin-dir`, which is why the *automatic*
per-session delivery for Claude lives on the spawn argv instead of here.)

Two rules keep the writes honest:

- **Global scope is disclosed before it happens.** `~/.claude/skills/` reaches
  every agent that user ever runs, including outside swe-mux, so the CLI prints
  the exact paths and proceeds only under an explicit confirmation flag.
- **Removal only takes what it can recognize.** A file is removed only when its
  frontmatter carries the `managed-by: swe-mux` marker this installer writes; a
  user-authored skill that happens to share the directory name is reported and
  left in place. Refusing is the command working, not failing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .harness import descriptor

#: Claude keys a skill by its directory name and Codex by the frontmatter name,
#: so the directory and the frontmatter `name:` must be the same string for the
#: invocation (`/swe-mux`, `$swe-mux`) to be one name everywhere.
SKILL_DIR_NAME = "swe-mux"
#: Recognition marker for removal. Present in the shipped frontmatter; checked
#: as a literal because removal must not need a parser to decline safely.
MANAGED_MARKER = "managed-by: swe-mux"
#: How much of a file to read when deciding whether it is ours. Frontmatter in
#: practice is under 1 KiB; reading more buys nothing.
_MARKER_WINDOW = 16 * 1024

_ASSET = Path(__file__).with_name("assets") / "skills" / SKILL_DIR_NAME / "SKILL.md"


def skill_text() -> str:
    """The embedded skill, exactly as this release ships it."""
    return _ASSET.read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class SkillTarget:
    """One skills root, with the harnesses whose CLIs read it.

    ``readers`` is a fact about the CLIs (verified in `agent_skills.py`), kept on
    the target so every report can say *why* a path was written - "wrote X" is
    not actionable, "wrote X, read by codex, pi" is.
    """

    root: Path
    readers: tuple[str, ...]

    def skill_path(self) -> Path:
        return self.root / SKILL_DIR_NAME / "SKILL.md"


def project_targets(project: Path) -> list[SkillTarget]:
    """The roots inside one checkout. Two writes cover all five harnesses."""
    return [
        SkillTarget(project / ".claude" / "skills", ("claude", "omp")),
        SkillTarget(project / ".agents" / "skills", ("codex", "pi", "omp", "opencode")),
    ]


def global_targets(
    *,
    claude_home: Path | None = None,
    codex_home: Path | None = None,
    user_home: Path | None = None,
) -> list[SkillTarget]:
    """The per-user roots. Reaches every session those CLIs run anywhere."""
    home = user_home or Path.home()
    claude_root = (claude_home or descriptor("claude").data_home()) / "skills"
    codex_root = (codex_home or descriptor("codex").data_home()) / "skills"
    return [
        SkillTarget(claude_root, ("claude", "omp")),
        SkillTarget(codex_root, ("codex",)),
        SkillTarget(home / ".agents" / "skills", ("pi", "omp", "opencode")),
    ]


def filter_targets(targets: list[SkillTarget], harnesses: list[str]) -> list[SkillTarget]:
    """Only the roots at least one of ``harnesses`` reads; all when unfiltered."""
    if not harnesses:
        return targets
    wanted = set(harnesses)
    return [target for target in targets if wanted.intersection(target.readers)]


@dataclass(frozen=True, slots=True)
class SkillWrite:
    """One path's outcome. ``error`` is True only for an attempted act that the
    filesystem refused - a policy refusal (foreign file) is not an error."""

    path: str
    action: str  # planned | wrote | unchanged | removed | absent | refused
    readers: tuple[str, ...]
    reason: str = ""
    error: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "action": self.action,
            "readers": list(self.readers),
            "reason": self.reason,
            "error": self.error,
        }


def plan(targets: list[SkillTarget]) -> list[SkillWrite]:
    """What an install would touch, stated without touching it."""
    return [
        SkillWrite(str(target.skill_path()), "planned", target.readers) for target in targets
    ]


def install(targets: list[SkillTarget], text: str) -> list[SkillWrite]:
    """Write ``text`` under each target; unchanged files are left alone.

    Write-if-changed for the same reason `adapters/claude.py` does it: replacing
    identical bytes moves mtime, and mtime is what `agent_skills.py` uses to say
    a skill appeared after a session started.
    """
    report: list[SkillWrite] = []
    for target in targets:
        path = target.skill_path()
        try:
            if path.is_file() and path.read_text(encoding="utf-8") == text:
                report.append(SkillWrite(str(path), "unchanged", target.readers))
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            staged = path.with_suffix(".md.tmp")
            staged.write_text(text, encoding="utf-8", newline="\n")
            staged.replace(path)
            report.append(SkillWrite(str(path), "wrote", target.readers))
        except OSError as exc:
            report.append(
                SkillWrite(str(path), "refused", target.readers, reason=str(exc), error=True)
            )
    return report


def written_by_us(path: Path) -> bool:
    """Whether ``path`` carries the marker this installer writes."""
    try:
        with path.open("rb") as handle:
            head = handle.read(_MARKER_WINDOW).decode("utf-8", "replace")
    except OSError:
        return False
    return MANAGED_MARKER in head


def remove(targets: list[SkillTarget]) -> list[SkillWrite]:
    """Remove recognized files; report and leave everything else in place."""
    report: list[SkillWrite] = []
    for target in targets:
        path = target.skill_path()
        if not path.is_file():
            report.append(SkillWrite(str(path), "absent", target.readers))
            continue
        if not written_by_us(path):
            report.append(
                SkillWrite(
                    str(path),
                    "refused",
                    target.readers,
                    reason="not written by swe-mux (no managed-by marker); left in place",
                )
            )
            continue
        try:
            path.unlink()
            # The directory only if the skill was its sole content: another
            # file beside it means someone extended the skill, and taking the
            # directory would take their work with it.
            try:
                path.parent.rmdir()
            except OSError:
                pass
            report.append(SkillWrite(str(path), "removed", target.readers))
        except OSError as exc:
            report.append(
                SkillWrite(str(path), "refused", target.readers, reason=str(exc), error=True)
            )
    return report
