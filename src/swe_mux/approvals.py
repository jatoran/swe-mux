"""Control-plane approval policy: what mux may answer on the user's behalf.

The decision is made *here*, from the harness's own structured permission
request (`tool_name` + `tool_input`), and never from the PTY screen. That
distinction is the whole design. `pty_tail_state` can say "a dialog is up"; it
cannot say what the dialog is asking, so a screen-driven auto-approval is a
blind Enter that lands equally in a permission prompt, a trust dialog, a
`/clear` confirmation, or the composer a millisecond after the dialog resolved.
A hook decision knows the tool and its arguments, so "approve reads and VS Code
task writes, escalate everything else" is expressible; on the screen it is not.

Three positions, and only three:

- ``wait`` - today's behaviour. Every request reaches the human.
- ``allowlisted`` - requests matching the Project's allow rules are answered;
  everything else still reaches the human.
- ``allow_all`` - everything except the floor below is answered.

Two rules hold in every position:

- **Never auto-deny.** A denial is a decision the agent acts on: it will try
  something else, usually something worse, and the human never learns a choice
  was made for them. Refusing to decide (``ask``) is always available and always
  safe, so ``deny`` is not in the vocabulary at all.
- **The floor is not a mode.** `FLOOR_PATTERNS` and `SECRET_PATH_PATTERNS` are
  checked before the mode is consulted and cannot be switched off by any of
  them, because this daemon is reachable over Tailscale from a phone and
  writable by agents over MCP - one prompt injection into one session must not
  be a machine compromise.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import Any

from .models import APPROVAL_MODES, ApprovalMode

#: Hook events whose response the harness reads back as a decision. Kept in step
#: with `hook_client._DECISION_EVENTS`, which cannot import this module: the hook
#: shim runs as a fresh interpreter on the agent's critical path for every event,
#: so it imports nothing from the package. `tests/test_approvals.py` asserts the
#: two sets are equal rather than letting them drift silently.
DECISION_HOOK_EVENTS = frozenset({"PermissionRequest"})

#: Cap on how many rules a Project may declare, and how long one may be. Rules
#: are matched per request on the agent's critical path, so the bound is a
#: latency bound as much as a sanity one.
MAX_ALLOW_RULES = 256
MAX_RULE_CHARS = 200

#: Bash operators that separate independently-executed commands. Every segment
#: must match a rule on its own, or `Bash(git status*)` would approve
#: `git status && rm -rf .` - the allowlist would be matching the *prefix* of a
#: script rather than the commands in it.
_COMMAND_SPLIT = re.compile(r"&&|\|\||[;\n|]")

#: Never auto-approved, in any mode, by any Project. Matched case-insensitively
#: against the whole raw command text, before segmentation, so an obfuscation
#: that defeats the segmenter still meets the floor.
FLOOR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bgit\s+push\b", "git push"),
    (r"\bgit\s+reset\s+(--hard|--merge|--keep)\b", "git reset --hard"),
    (r"\bgit\s+clean\b", "git clean"),
    (r"\bgit\b[^\n]*--force\b", "a forced git operation"),
    (r"\bgit\b[^\n]*\s-f\b", "a forced git operation"),
    # Any rm carrying -r or -f, in either order and in a combined cluster.
    (r"\brm\b[^\n]*\s-[a-z]*[rf]", "a recursive or forced delete"),
    (r"\b(rmdir|del)\b[^\n]*\s[/-][sq]\b", "a recursive delete"),
    (r"\bRemove-Item\b[^\n]*-Recurse\b", "a recursive delete"),
    (r"\bdd\s+if=", "a raw disk write"),
    (r"\b(mkfs|diskpart|fdisk|format)\b", "a filesystem operation"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "a host power operation"),
    (r"\bsudo\b", "an elevated command"),
    (r"\bchmod\s+(-[a-z]+\s+)*777\b", "a world-writable permission change"),
    (r"\btaskkill\b", "a process kill"),
    (r"\bmuxd\b[^\n]*--shutdown", "a swe-mux shutdown (this reaps every session)"),
    # Remote code execution: fetch and pipe straight into a shell.
    (
        r"\b(curl|wget|iwr|Invoke-WebRequest)\b[^\n]*\|\s*(ba|z|fi)?sh\b",
        "piping a download into a shell",
    ),
    (
        r"\b(curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*"
        r"(\s-d\b|--data|--upload-file|\s-T\b|\s-F\b|--form|-Method\s+Post)",
        "an outbound upload",
    ),
    # Publishing: irreversible, public, and attributable to the user.
    (r"\bnpm\s+publish\b", "npm publish"),
    (r"\b(twine\s+upload|cargo\s+publish|gem\s+push|dotnet\s+nuget\s+push)\b", "a package publish"),
    (
        r"\bgh\s+(pr|release|repo|api)\b[^\n]*\b(create|merge|delete|edit|--method)\b",
        "a GitHub write",
    ),
    (r"\bdocker\s+push\b", "docker push"),
    (r"\b(terraform|pulumi)\s+(apply|destroy)\b", "an infrastructure change"),
    (r"\b(kubectl|helm)\s+(delete|apply|uninstall)\b", "a cluster change"),
    (r"\baws\s+\w+\s+(delete|put|create|terminate)", "an AWS write"),
)

#: Paths whose contents are credentials. Matched against every path-shaped
#: subject *and* against raw Bash command text, because `cat ~/.ssh/id_rsa` is
#: the same disclosure as `Read(~/.ssh/id_rsa)` and only the second one is a
#: path-shaped subject.
SECRET_PATH_PATTERNS: tuple[str, ...] = (
    r"(^|[\\/])\.env(\.[\w.-]+)?$",
    r"(^|[\\/])\.env(\.[\w.-]+)?[\\/]",
    r"(^|[\\/])\.ssh([\\/]|$)",
    r"\bid_(rsa|dsa|ecdsa|ed25519)\b",
    r"(^|[\\/])\.aws([\\/]|$)",
    r"(^|[\\/])\.npmrc$",
    r"(^|[\\/])\.pypirc$",
    r"(^|[\\/])\.git-credentials$",
    r"(^|[\\/])\.netrc$",
    r"(^|[\\/])\.claude\.json$",
    r"(^|[\\/])credentials(\.json|\.yml|\.yaml)?$",
    r"(^|[\\/])secrets?([\\/.]|$)",
    r"\.(pem|key|p12|pfx|keystore|jks)$",
    r"\bAKIA[0-9A-Z]{16}\b",
)

_FLOOR = tuple((re.compile(pattern, re.IGNORECASE), label) for pattern, label in FLOOR_PATTERNS)
_SECRETS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in SECRET_PATH_PATTERNS)

#: What `allowlisted` means when a Project declares nothing of its own. Reads,
#: search, and the inert local writes that motivated the feature - editor task
#: files and the agent's own scratch config - plus mux's own read-only MCP
#: surface, which is already permission-allowed at spawn for exactly this
#: reason (`mcp_contract.claude_read_permissions`).
DEFAULT_ALLOW_RULES: tuple[str, ...] = (
    "Read",
    "Glob",
    "Grep",
    "LS",
    "NotebookRead",
    "TodoWrite",
    "WebSearch",
    "mcp__mux__*",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git log*)",
    "Bash(git show*)",
    "Bash(git branch)",
    "Bash(git branch --list*)",
    "Bash(ls*)",
    "Bash(pwd*)",
    "Bash(echo*)",
    "Write(**/.vscode/*.json)",
    "Edit(**/.vscode/*.json)",
    "Write(**/.claude/settings.json)",
    "Edit(**/.claude/settings.json)",
)

#: Where each tool's decision-relevant argument lives. A tool absent from this
#: map has no subject, so a bare-tool rule (`Read`) still matches it while a
#: patterned rule (`Read(...)`) never can - which is the safe direction: an
#: unrecognized tool cannot be narrowed, so it can only be allowed wholesale or
#: not at all.
_TOOL_SUBJECTS: dict[str, tuple[str, ...]] = {
    "bash": ("command",),
    "bashoutput": ("bash_id",),
    "read": ("file_path", "notebook_path"),
    "write": ("file_path",),
    "edit": ("file_path",),
    "multiedit": ("file_path",),
    "notebookread": ("notebook_path", "file_path"),
    "notebookedit": ("notebook_path", "file_path"),
    "glob": ("pattern",),
    "grep": ("pattern",),
    "ls": ("path",),
    "webfetch": ("url",),
    "websearch": ("query",),
    "task": ("subagent_type",),
    "agent": ("subagent_type",),
}

#: Tools whose subject is a shell command line rather than a path, and which
#: therefore get segmented before matching and scanned whole for the floor.
_SHELL_TOOLS = frozenset({"bash", "shell", "run_command", "exec", "local_shell"})


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    """One decision, with the evidence that produced it.

    `decision` is only ever ``allow`` or ``ask``; see the module docstring for
    why ``deny`` does not exist here.
    """

    decision: str
    reason: str
    matched_rule: str | None = None
    floor: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


def normalize_rules(rules: Any) -> list[str]:
    """Coerce a declared allowlist into bounded, de-duplicated rule strings."""
    if not isinstance(rules, (list, tuple)):
        return []
    seen: dict[str, None] = {}
    for item in rules:
        if not isinstance(item, str):
            continue
        rule = item.strip()
        if not rule or len(rule) > MAX_RULE_CHARS:
            continue
        seen.setdefault(rule, None)
        if len(seen) >= MAX_ALLOW_RULES:
            break
    return list(seen)


def split_rule(rule: str) -> tuple[str, str | None]:
    """``Bash(npm run *)`` -> ``("Bash", "npm run *")``; ``Read`` -> ``("Read", None)``."""
    rule = rule.strip()
    if rule.endswith(")") and "(" in rule:
        tool, _, pattern = rule[:-1].partition("(")
        return tool.strip(), pattern.strip()
    return rule, None


def tool_subject(tool_name: str, tool_input: Any) -> str:
    """The single argument a rule pattern is matched against, or ``""``."""
    if not isinstance(tool_input, dict):
        return ""
    for key in _TOOL_SUBJECTS.get(tool_name.strip().lower(), ()):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_path(value: str) -> str:
    return value.replace("\\", "/")


def _pattern_matches(pattern: str, subject: str) -> bool:
    if not subject:
        return False
    normalized_pattern = _normalize_path(pattern)
    normalized_subject = _normalize_path(subject)
    if fnmatch.fnmatch(normalized_subject, normalized_pattern):
        return True
    # `**/x` should also match a bare `x`, which fnmatch does not do on its own.
    if normalized_pattern.startswith("**/") and fnmatch.fnmatch(
        normalized_subject, normalized_pattern[3:]
    ):
        return True
    # A path rule written without a leading glob still names a suffix the caller
    # means: `Write(.vscode/tasks.json)` and an absolute path are the same ask.
    return "/" in normalized_pattern and fnmatch.fnmatch(
        normalized_subject, f"*/{normalized_pattern.lstrip('/')}"
    )


def _command_segments(command: str) -> list[str]:
    return [segment.strip() for segment in _COMMAND_SPLIT.split(command) if segment.strip()]


def floor_reason(tool_name: str, tool_input: Any) -> str | None:
    """Why this request may never be auto-approved, or None.

    Scanned over the raw subject *and*, for shell tools, the whole command line
    including its operators - segmentation is for matching allow rules, not for
    the floor, which must see what the segmenter might mis-split.
    """
    name = tool_name.strip().lower()
    subject = tool_subject(tool_name, tool_input)
    haystacks = [subject]
    if isinstance(tool_input, dict):
        # A path can arrive under a key this tool map does not name (a harness
        # spells `path` where another spells `file_path`), and a credential read
        # must not depend on having guessed the key correctly.
        haystacks.extend(
            value
            for key, value in tool_input.items()
            if isinstance(value, str) and isinstance(key, str) and len(value) <= 4096
        )
    for pattern in _SECRETS:
        for haystack in haystacks:
            if haystack and pattern.search(_normalize_path(haystack)):
                return "a credential or secret path"
    if name in _SHELL_TOOLS and subject:
        for pattern, label in _FLOOR:
            if pattern.search(subject):
                return label
    return None


def rule_matches(rule: str, tool_name: str, tool_input: Any) -> bool:
    """Whether one allow rule covers this request."""
    rule_tool, pattern = split_rule(rule)
    if not rule_tool:
        return False
    if not fnmatch.fnmatchcase(tool_name, rule_tool) and tool_name.lower() != rule_tool.lower():
        return False
    if pattern is None:
        return True
    subject = tool_subject(tool_name, tool_input)
    if not subject:
        return False
    if tool_name.strip().lower() in _SHELL_TOOLS:
        # Every independently-executed segment must be covered, or the rule
        # would be approving a script by its first command.
        segments = _command_segments(subject)
        return bool(segments) and all(_pattern_matches(pattern, part) for part in segments)
    return _pattern_matches(pattern, subject)


def allow_rule_for(rules: list[str], tool_name: str, tool_input: Any) -> str | None:
    """The first rule that covers this request, or None.

    A shell command is covered only if *every* segment is covered, possibly by
    different rules, which is why the segment loop lives here rather than inside
    a single rule's match: `git status && ls` is legitimately two rules.
    """
    if not rules:
        return None
    name = tool_name.strip().lower()
    if name in _SHELL_TOOLS:
        subject = tool_subject(tool_name, tool_input)
        segments = _command_segments(subject)
        if not segments:
            return None
        matched: list[str] = []
        for segment in segments:
            hit = next(
                (
                    rule
                    for rule in rules
                    if rule_matches(rule, tool_name, {**dict(tool_input or {}), "command": segment})
                ),
                None,
            )
            if hit is None:
                return None
            matched.append(hit)
        return matched[0]
    return next((rule for rule in rules if rule_matches(rule, tool_name, tool_input)), None)


def decide(
    *,
    mode: ApprovalMode,
    rules: list[str],
    tool_name: str,
    tool_input: Any,
) -> ApprovalOutcome:
    """Answer one permission request, or decline to answer it.

    Order is load-bearing: the floor is consulted before the mode, so no Project
    configuration and no operator switch can reach past it.
    """
    if mode not in APPROVAL_MODES or mode == "wait":
        return ApprovalOutcome("ask", "approval mode is wait")
    if not tool_name.strip():
        return ApprovalOutcome("ask", "the request names no tool")
    floor = floor_reason(tool_name, tool_input)
    if floor is not None:
        return ApprovalOutcome("ask", f"never auto-approved: {floor}", floor=floor)
    if mode == "allow_all":
        return ApprovalOutcome("allow", "approval mode is allow_all")
    matched = allow_rule_for(rules, tool_name, tool_input)
    if matched is not None:
        return ApprovalOutcome("allow", f"matched {matched}", matched_rule=matched)
    return ApprovalOutcome("ask", "no allow rule covers this request")


def describe_request(tool_name: str, tool_input: Any) -> str:
    """A short human label for a request, for the ledger and the UI.

    Bounded hard: this string is persisted on every auto-approval and rendered
    in a one-line strip, and a tool input can carry an entire file.
    """
    subject = tool_subject(tool_name, tool_input)
    if not subject:
        return tool_name.strip() or "approval"
    collapsed = " ".join(subject.split())
    if len(collapsed) > 120:
        collapsed = collapsed[:117] + "..."
    return f"{tool_name.strip()}({collapsed})"
