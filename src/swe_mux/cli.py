"""The `mux` command-line control surface.

`mux` is not a text twin of the browser: the browser and mobile clients are the
interactive surface, and MCP serves structured reads to agents. This CLI is the
part with no substitute - the operations you run when the UI is not the right tool
and the things a script needs: a scriptable spawn, a filtered session list, kill,
history resume, a daemon reload, and a consolidated `doctor` report.

Design rules this file keeps (ROADMAP Phase 7, "Practical CLI control"):

- **Stable ids and name conflicts.** A session is addressed by its stable id, its
  exact name, or a unique id prefix; an ambiguous name exits with a distinct code
  and lists the candidates rather than picking one.
- **Actionable exit codes.** 0 success, 2 usage (argparse), 3 daemon unreachable,
  4 daemon HTTP error, 5 ambiguous name, 6 not found, 1 a `doctor` report with a
  failing check or a local command that could not do what was asked. Scripts
  branch on these, never on prose.
  `doctor` is the one command that answers when the daemon does not: an
  unreachable daemon produces the **local** report (`doctor_local`) rather than a
  bare connection error, and its exit code composes the two existing meanings
  rather than adding a scheme. A local check failed is still `1`, because a named
  broken check is the more actionable fact; a local report with nothing failing is
  `3`, which is exactly what `3` already meant - the daemon is unreachable. A
  degraded report therefore never exits `0`, and a script gating on `mux doctor`
  keeps working unchanged.
- **Human tables by default, `--json` for machines.** Default output is a table a
  person reads; `--json` prints the raw daemon payload verbatim. Scripts never
  parse the human prose.
- **Config-based URL resolution with `MUX_URL` precedence.** `--url` beats
  `MUX_URL`, which beats the daemon host/port from config, which beats the
  loopback default.
- **Registry-driven harness lists.** Every harness choice and label comes from the
  harness registry (`agent_harnesses`, `/api/harnesses`), never a hardcoded
  `claude`/`codex`, so a new harness needs no CLI change.
- **No secrets.** Nothing here accepts or prints a provider secret; the daemon
  payloads it renders are the same sanitized surfaces the browser reads.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from .harness import agent_harnesses

# Actionable exit codes. 2 is reserved by argparse for usage errors, so it is not
# reused here. Scripts branch on these; they are part of the CLI contract.
EXIT_OK = 0
EXIT_DOCTOR_FAIL = 1
#: The same code, under the name that says what it means for a command that
#: does its work on this machine instead of asking the daemon for it. Kept as an
#: alias rather than a seventh number: "swe-mux could not do what you asked" is
#: one meaning, and splitting it would make scripts branch on two.
EXIT_LOCAL_FAIL = 1
EXIT_CONNECTION = 3
EXIT_HTTP = 4
EXIT_AMBIGUOUS = 5
EXIT_NOT_FOUND = 6

DEFAULT_URL = "http://127.0.0.1:8765"

#: The launcher names `[project.scripts]` declares for this client, and the only
#: values `invoked_as` will echo back into help and error text. A closed set
#: rather than "whatever `argv[0]` says": the value is printed to the user, and a
#: renamed or symlinked copy naming itself something else should read as the
#: command this project documents rather than as whatever it was called.
LAUNCHER_NAMES = frozenset({"swemux", "mux"})
#: What an unrecognizable `argv[0]` prints. The primary spelling, so the fallback
#: teaches the name the documents use.
DEFAULT_PROG = "swemux"


class CliError(Exception):
    """A CLI-level failure carrying the process exit code to report it with.

    ``reason`` is the bare transport failure ("[WinError 10061] ..."), kept beside
    the human message so a consumer that re-renders the failure - the local doctor
    report's preamble - can state the cause without also restating the advice the
    message already gives.
    """

    def __init__(self, message: str, code: int, *, reason: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason


def resolve_base_url(explicit: str | None) -> str:
    """Resolve the daemon URL: --url, then MUX_URL, then config, then default.

    An explicit `--url` is the most specific and wins. `MUX_URL` keeps its
    precedence over config so an operator can override per shell without editing a
    file. Config supplies the host/port a non-default daemon actually listens on.
    Config loading is best-effort: a missing or unreadable config falls back to
    the loopback default rather than failing a command that never needed it.
    """
    if explicit:
        return explicit.rstrip("/")
    env = os.environ.get("MUX_URL")
    if env:
        return env.rstrip("/")
    try:
        from .config import LOOPBACK_HOSTS, load_config

        config = load_config()
        host = config.host if config.host in LOOPBACK_HOSTS else "127.0.0.1"
        return f"http://{host}:{config.port}"
    except Exception:
        return DEFAULT_URL


def request(
    method: str,
    path: str,
    body: dict[str, object] | None = None,
    *,
    base: str,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> Any:
    # `headers` exists for the explicit-gesture routes (the update check and the
    # updater), which refuse a request that does not carry one. Typing a command
    # is exactly the deliberate act that header stands for, so the CLI is
    # entitled to send it - and it is spelled at the call site rather than
    # defaulted here, so nothing acquires the gesture by accident.
    #
    # `timeout` is 10s because every command here is a state read or a signal that
    # returns immediately. The exceptions raise it explicitly: a handler that does
    # real synchronous work (hashing and extracting a frontend tree) can outlast
    # the default, and a client that gave up while the server succeeded would
    # report a failure that did not happen.
    headers = {"Content-Type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()
        raise CliError(f"daemon returned HTTP {exc.code}: {detail}", EXIT_HTTP) from exc
    except urllib.error.URLError as exc:
        raise CliError(
            f"cannot reach the mux daemon at {base}: {exc.reason}. "
            "Is it running? Set MUX_URL or pass --url to point elsewhere.",
            EXIT_CONNECTION,
            reason=str(exc.reason),
        ) from exc


# --------------------------------------------------------------------------- #
# Session id / name resolution
# --------------------------------------------------------------------------- #


def resolve_session(token: str, *, base: str) -> str:
    """Resolve a token to one stable session id, or fail with a distinct code.

    Resolution order is exact id, then exact name, then unique id prefix. An
    ambiguous name or prefix exits `EXIT_AMBIGUOUS` and prints the candidates; no
    match exits `EXIT_NOT_FOUND`. This is client-side presentation over stable
    ids: the mutation it precedes still routes through the daemon's typed op.
    """
    sessions = request("GET", "/api/sessions", base=base)
    if not isinstance(sessions, list):
        raise CliError("unexpected session list from the daemon", EXIT_HTTP)
    by_id = {str(s.get("id")): s for s in sessions}
    if token in by_id:
        return token
    by_name = [s for s in sessions if s.get("name") == token]
    if len(by_name) == 1:
        return str(by_name[0]["id"])
    if len(by_name) > 1:
        _raise_ambiguous(token, by_name)
    prefix = [s for s in sessions if str(s.get("id")).startswith(token)]
    if len(prefix) == 1:
        return str(prefix[0]["id"])
    if len(prefix) > 1:
        _raise_ambiguous(token, prefix)
    raise CliError(f"no session matches {token!r}", EXIT_NOT_FOUND)


def _raise_ambiguous(token: str, candidates: list[dict[str, Any]]) -> None:
    lines = [f"{token!r} is ambiguous; matches {len(candidates)} sessions:"]
    for candidate in candidates:
        lines.append(
            f"  {str(candidate.get('id'))[:12]}  {candidate.get('name')}  "
            f"[{candidate.get('backend')}]"
        )
    lines.append("Re-run with a full id.")
    raise CliError("\n".join(lines), EXIT_AMBIGUOUS)


# --------------------------------------------------------------------------- #
# Human table rendering
# --------------------------------------------------------------------------- #


def render_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    """Render list-of-dicts as an aligned table. columns is [(header, key), ...]."""
    if not rows:
        return "(none)"
    headers = [header for header, _ in columns]
    cells = [[_cell(row.get(key)) for _, key in columns] for row in rows]
    widths = [len(h) for h in headers]
    for line in cells:
        for i, value in enumerate(line):
            widths[i] = max(widths[i], len(value))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for line in cells:
        out.append("  ".join(line[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(out)


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _print(result: Any, use_json: bool, human: Any) -> None:
    """Print `result` as JSON, or via the `human` renderer when not --json."""
    if use_json or human is None:
        json.dump(result, sys.stdout, indent=2)
        print()
    else:
        text = human(result)
        if text:
            print(text)


# --------------------------------------------------------------------------- #
# Human renderers per command
# --------------------------------------------------------------------------- #


def _render_sessions(result: Any) -> str:
    rows = result if isinstance(result, list) else []
    for row in rows:
        row["short_id"] = str(row.get("id"))[:12]
    return render_table(
        rows,
        [
            ("ID", "short_id"),
            ("NAME", "name"),
            ("PROJECT", "project_id"),
            ("BACKEND", "backend"),
            ("STATE", "state"),
        ],
    )


def _render_id_name(result: Any) -> str:
    rows = result if isinstance(result, list) else []
    # A best-effort name column: whatever id/name-shaped keys the payload carries.
    name_key = next(
        (k for k in ("name", "label", "display_name", "title") if rows and k in rows[0]),
        "name",
    )
    return render_table(rows, [("ID", "id"), ("NAME", name_key)])


def _render_harnesses(result: Any) -> str:
    entries = result.get("harnesses") if isinstance(result, dict) else None
    rows = entries or []
    for row in rows:
        row["_installed"] = (
            "-" if row.get("installed") is None else ("yes" if row.get("installed") else "no")
        )
        row["_untested"] = "yes" if row.get("version_untested") else ""
    return render_table(
        rows,
        [
            ("NAME", "name"),
            ("LABEL", "display_name"),
            ("LEVEL", "level"),
            ("INSTALLED", "_installed"),
            ("VERSION", "cli_version"),
            ("UNTESTED", "_untested"),
        ],
    )


def _render_doctor(result: Any) -> str:
    """Render either doctor report.

    One renderer for both, because two would drift and the local report is read in
    exactly the situation where a formatting difference reads as a different
    product. The local-only parts - the preamble, the `????` mark, the `unchecked`
    tally - are all conditioned on facts the daemon report does not carry, so the
    bytes it produces are unchanged by their existence.
    """
    if not isinstance(result, dict):
        return json.dumps(result, indent=2)
    checks = result.get("checks") or []
    summary = result.get("summary") or {}
    # `????` rather than a reuse of `n/a `: "not measured" and "measured absent"
    # are different facts and must not render the same.
    mark = {
        "ok": "OK  ",
        "warn": "WARN",
        "fail": "FAIL",
        "unavailable": "n/a ",
        "unchecked": "????",
    }
    local = result.get("mode") == "local"
    lines: list[str] = []
    if local:
        daemon = result.get("daemon") or {}
        lines += [
            f"mux could not reach the swe-mux daemon at {daemon.get('url')}"
            + (f": {daemon.get('detail')}" if daemon.get("detail") else "")
            + ".",
            "This is the LOCAL report: only the checks answerable from this machine ran.",
            "[????] marks a check that did NOT run - nothing is known about it either way.",
            "",
        ]
    for check in checks:
        status = str(check.get("status"))
        lines.append(f"[{mark.get(status, status)}] {check.get('title')}: {check.get('detail')}")
        remedy = check.get("remedy")
        if remedy and status in {"warn", "fail"}:
            lines.append(f"         -> {remedy}")
    label = "swe-mux doctor (local, no daemon)" if local else "swe-mux doctor"
    header = (
        f"{label}: {summary.get('ok', 0)} ok, {summary.get('warn', 0)} warn, "
        f"{summary.get('fail', 0)} fail, {summary.get('unavailable', 0)} unavailable"
    )
    unchecked = int(summary.get("unchecked", 0) or 0)
    if unchecked:
        header += f", {unchecked} unchecked"
    if local:
        verdict = (
            "PROBLEMS FOUND"
            if not result.get("ok")
            else "no local problem found; the daemon is still not running"
        )
    else:
        verdict = "healthy" if result.get("ok") else "PROBLEMS FOUND"
    return "\n".join([*lines, "", f"{header} - {verdict}"])


def _render_update(result: Any) -> str:
    """The two halves an operator needs: what exists, and what this install can do.

    They are separate questions, and the answer to the first does not imply the
    second: a source install is told about a release it must upgrade to with a
    different command entirely, and reporting "an update is available" without
    that would send someone hunting for a button that is correctly not there.
    """
    check = result.get("check", {}) if isinstance(result, dict) else {}
    install = result.get("install", {}) if isinstance(result, dict) else {}
    latest = check.get("latest") or {}
    lines = [
        f"running   {check.get('current_version', '?')}",
        f"latest    {latest.get('version', '-')} ({check.get('status', '?')})",
        f"install   {install.get('install_kind', '?')}"
        + ("" if install.get("swappable") else " (no bundle swap)"),
    ]
    if not install.get("swappable") and install.get("upgrade_command"):
        lines.append(f"upgrade   {install['upgrade_command']}")
    elif check.get("update_available") and latest.get("version"):
        lines.append(f"upgrade   mux update --install {latest['version']}")
    if install.get("phase") and install.get("phase") != "idle":
        lines.append(f"last      {install.get('phase')} {install.get('reason', '')}".rstrip())
        if install.get("message"):
            lines.append(f"          {install['message']}")
    return "\n".join(lines)


def _render_update_install(result: Any) -> str:
    payload = result if isinstance(result, dict) else {}
    head = f"{payload.get('phase', '?')} {payload.get('version', '')}".strip()
    message = payload.get("message") or payload.get("error") or ""
    return f"{head}\n{message}".strip()


def _render_ui_overlay(result: Any) -> str:
    """What is installed, what is being served, and why they might differ.

    The last of those is the line worth having. A frontend overlay that is
    installed and refused looks, from the browser, exactly like one that was never
    installed - the same "verified-correct fix that still does nothing" this
    feature exists to end - so the refusal reason is printed rather than folded
    into a boolean.
    """
    payload = result if isinstance(result, dict) else {}
    if not payload.get("supported", True):
        return "this daemon has no frontend overlay support"
    state = payload.get("state") or {}
    serving = payload.get("serving") or {}
    lines = [
        f"backend    {payload.get('backend_version', '?')}",
        f"serving    {serving.get('serving', '?')} ({serving.get('directory', '?')})",
    ]
    if payload.get("installed"):
        lines.append(
            f"installed  {(state.get('digest') or '')[:16]} "
            f"pinned to swe-mux {state.get('requires_backend') or '?'}"
            + ("" if state.get("active") else "  [reverted]")
        )
        if state.get("installed_from"):
            lines.append(f"from       {state['installed_from']}")
        if not payload.get("tree_exists"):
            lines.append("           its files are no longer on disk")
    else:
        lines.append("installed  none")
    if serving.get("faulted"):
        lines.append(f"refused    {serving.get('reason', '?')}: {serving.get('message', '')}")
    elif serving.get("reason") and serving.get("reason") != "ok":
        lines.append(f"reason     {serving['reason']}")
    for key in ("message", "error"):
        if payload.get(key):
            lines.append(str(payload[key]))
            break
    return "\n".join(lines)


def _ui_overlay_command(args: Any, base: str) -> tuple[Any, Callable[[Any], str] | None]:
    """Dispatch one `mux ui-overlay` action.

    `install` sends a much longer timeout than the CLI's default: the daemon
    hashes and extracts the whole tree, and a client that gave up at ten seconds
    while the server went on to succeed would report a failure that did not
    happen.
    """
    if args.action == "revert":
        return (
            request(
                "POST",
                "/api/frontend/overlay/revert",
                {},
                base=base,
                headers={"X-Mux-User-Gesture": "frontend-overlay-revert"},
            ),
            _render_ui_overlay,
        )
    if args.action == "restore":
        return (
            request(
                "POST",
                "/api/frontend/overlay/restore",
                {},
                base=base,
                headers={"X-Mux-User-Gesture": "frontend-overlay-restore"},
            ),
            _render_ui_overlay,
        )
    if args.action == "install":
        if not args.source:
            raise CliError(
                "install needs a source: an overlay .zip, a built static directory, or "
                "an https URL with --sha256",
                EXIT_NOT_FOUND,
            )
        body = _ui_overlay_source(args.source, args.sha256)
        return (
            request(
                "POST",
                "/api/frontend/overlay/install",
                body,
                base=base,
                headers={"X-Mux-User-Gesture": "frontend-overlay-install"},
                timeout=300,
            ),
            _render_ui_overlay,
        )
    return request("GET", "/api/frontend/overlay", base=base), _render_ui_overlay


def _ui_overlay_source(source: str, sha256: str | None) -> dict[str, object]:
    """Classify what the operator typed into the one field the endpoint wants.

    A URL is recognized by its scheme and a path by what it is on disk, and a path
    that is neither a file nor a directory fails here rather than at the daemon:
    the daemon would have to answer "source_missing" about a path it cannot see
    the way the shell that typed it can.
    """
    lowered = source.lower()
    if lowered.startswith(("http://", "https://")):
        if not sha256:
            raise CliError(
                "installing from a URL requires --sha256. Nothing can vouch for bytes "
                "that arrived over a network, so an unverifiable download is refused "
                "rather than installed.",
                EXIT_NOT_FOUND,
            )
        return {"url": source, "sha256": sha256}
    path = Path(source).expanduser()
    resolved = str(path.resolve())
    if path.is_dir():
        return {"directory": resolved}
    if path.is_file():
        return {"archive": resolved, "sha256": sha256 or ""}
    raise CliError(f"{source} is neither a file, a directory, nor a URL", EXIT_NOT_FOUND)


def local_doctor_report(*, base: str, detail: str) -> dict[str, Any]:
    """The degraded report, built when no daemon answered at ``base``.

    A thin seam over `doctor_local` so the fallback is one call in `dispatch` and
    the checks themselves are testable without going through argparse. Imported
    lazily because the local report imports most of the package to *check* that it
    imports, which every other `mux` subcommand has no reason to pay for.
    """
    from . import doctor_local

    config, config_error = doctor_local.load_config_for_doctor()
    return doctor_local.build_local_doctor_report(
        config=config,
        config_error=config_error,
        unreachable_url=base,
        unreachable_detail=detail,
        now=time.time(),
    )


# --------------------------------------------------------------------------- #
# Argument parsing and dispatch
# --------------------------------------------------------------------------- #


def invoked_as(
    argv0: str | None = None,
    *,
    names: frozenset[str] = LAUNCHER_NAMES,
    default: str = DEFAULT_PROG,
) -> str:
    """The name this process was launched under, for `usage:` and error text.

    `[project.scripts]` declares four launchers over two programs - `swemux` and
    `mux` for this client, `swemuxd` and `muxd` for the daemon - so a hardcoded
    `prog` would print one name at a user who typed the other. Nothing here
    dispatches on the answer; it is display text only, which is why an
    unrecognizable `argv[0]` falls back rather than failing. `names` and `default`
    are arguments because the daemon has its own pair (`swemuxd`, `muxd`) and the
    rule is identical; only the vocabulary differs.

    The `.exe` strip is Windows-specific and load-bearing there: a console script
    is a real executable, so `sys.argv[0]` ends in `.exe` and argparse's own
    default would print `usage: swemux.exe`. `python -m swe_mux.cli` gives an
    `argv[0]` that is a path to a module file, and an empty or directory-like
    `argv[0]` (an embedder, a test) gives nothing usable - both take the default.

    The split is `PureWindowsPath` rather than `Path` **on every platform**, and
    that is deliberate rather than a Windows leftover. `Path` takes the running
    host's flavour, so a POSIX interpreter reading a Windows `argv[0]` treats the
    backslashes as ordinary characters, finds no separator, and falls back - which
    is what reddened ubuntu and macOS while Windows passed. `PureWindowsPath`
    accepts forward and backward slashes both, so one call is correct for either
    shape, whoever is running it. The cost is a POSIX filename that genuinely contains a backslash,
    which resolves to no known launcher and takes the same safe fallback as any
    other unrecognized name; this is display text with a default, so that is a
    non-event rather than a trade.
    """
    raw = sys.argv[0] if argv0 is None else argv0
    stem = PureWindowsPath(raw).stem if raw else ""
    return stem if stem and stem in names else default


def build_parser(prog: str | None = None) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", help="print the raw daemon JSON instead of a table"
    )
    common.add_argument(
        "--url", help="daemon base URL (overrides MUX_URL and config)", default=None
    )

    parser = argparse.ArgumentParser(
        prog=prog or invoked_as(),
        description=(
            "Control the swe-mux daemon. `swemux` and `mux` are the same command; "
            "`swemuxd` and `muxd` start the daemon itself, and `swe-mux` opens the "
            "desktop app rather than printing anything."
        ),
    )
    # herdr's `--skill` shape: the binary carries its own agent instructions, so
    # what an agent reads always matches this exact release. A flag rather than a
    # subcommand because it is the one thing here addressed to a program that has
    # not learned the subcommands yet.
    parser.add_argument(
        "--skill",
        action="store_true",
        help="print the swe-mux agent skill (SKILL.md) embedded in this release and exit",
    )
    # Not `required=True`: `--skill` is a complete invocation with no subcommand.
    # `main` restores the old behaviour for a bare `swemux` by raising the same
    # argparse usage error (exit 2) a required subparser would have.
    sub = parser.add_subparsers(dest="command", required=False)

    ls = sub.add_parser("ls", parents=[common], help="list sessions (filterable)")
    ls.add_argument("--project", help="only sessions in this project id")
    ls.add_argument("--state", help="only sessions in this state")
    ls.add_argument("--backend", help="only sessions on this backend")

    spawn = sub.add_parser("spawn", parents=[common], help="spawn a session")
    spawn.add_argument("--backend", choices=("shell", *agent_harnesses()), default="shell")
    spawn.add_argument("--name")
    spawn.add_argument("--project", required=True)
    spawn.add_argument("--profile", help="launch profile id")
    spawn.add_argument("--exe", help="executable override")
    spawn.add_argument("--arg", action="append", default=[], help="extra argv (repeatable)")

    send = sub.add_parser("send", parents=[common], help="send input to a session")
    send.add_argument("session", nargs="?", help="session id, name, or unique id prefix")
    send.add_argument("text")
    send.add_argument("--all-broadcast", action="store_true")

    kill = sub.add_parser("kill", parents=[common], help="terminate a session")
    kill.add_argument("session", help="session id, name, or unique id prefix")

    reload_daemon = sub.add_parser(
        "reload-daemon",
        parents=[common],
        help="restart the daemon in place; sessions survive with the PTY supervisor",
    )
    reload_daemon.add_argument(
        "--force",
        action="store_true",
        help="restart even without the PTY supervisor (kills every session)",
    )

    sub.add_parser("history", parents=[common], help="list conversation history")
    sub.add_parser("projects", parents=[common], help="list projects")
    sub.add_parser("profiles", parents=[common], help="list launch profiles")
    sub.add_parser("harnesses", parents=[common], help="list harnesses and detection state")

    doctor = sub.add_parser(
        "doctor", parents=[common], help="consolidated read-only diagnostics report"
    )
    doctor.add_argument(
        "--export",
        action="store_true",
        help="print the full diagnostics bundle (config, remote, firewall, logs) as JSON",
    )

    accounts = sub.add_parser(
        "accounts",
        parents=[common],
        help="inspect provider accounts, re-verify identities, or read the credential audit",
    )
    accounts.add_argument("action", choices=("list", "verify", "audit"), nargs="?", default="list")
    accounts.add_argument("--limit", type=int, default=50, help="audit entries to show")

    duplicates = sub.add_parser(
        "history-duplicates",
        parents=[common],
        help="report, or merge, history entries that share one conversation",
    )
    duplicates.add_argument(
        "action",
        choices=("report", "repair"),
        nargs="?",
        default="report",
        help="report lists what would change; repair merges each conversation's rows",
    )

    update = sub.add_parser(
        "update",
        parents=[common],
        help="report the release check and this install's update path",
    )
    update.add_argument(
        "--install",
        metavar="VERSION",
        help=(
            "download exactly this version, verify its SHA-256 against the published "
            "manifest, and hand it to the staged swap (frozen desktop app only)"
        ),
    )

    overlay = sub.add_parser(
        "ui-overlay",
        parents=[common],
        help="inspect, install, or revert the daemon's frontend overlay",
        description=(
            "A frontend overlay is a hash-verified static tree in the data dir that "
            "the daemon serves in place of its own bundled one, so a UI fix reaches a "
            "frozen desktop app without a bundle swap. `revert` is the reason this "
            "command exists rather than only an endpoint: an overlay's own failure "
            "mode is a UI that will not load, and a control reachable only through "
            "that UI would be no control at all. Every subcommand takes effect at the "
            "next daemon start; `mux reload-daemon` applies one while preserving "
            "sessions."
        ),
    )
    overlay.add_argument(
        "action",
        choices=("status", "install", "revert", "restore"),
        nargs="?",
        default="status",
    )
    overlay.add_argument(
        "source",
        nargs="?",
        help="for install: an overlay .zip, a built static directory, or an https URL",
    )
    overlay.add_argument(
        "--sha256",
        help="the digest the payload must match; required for a URL, optional for a path",
    )

    shortcut = sub.add_parser(
        "install-shortcut",
        parents=[common],
        help="create Start Menu and Desktop shortcuts for the desktop app (Windows)",
        description=(
            "Create the shortcuts a wheel install structurally cannot: pip and uv "
            "write launchers into a scripts directory and have no hook that runs "
            "afterwards, so nothing reaches the Start Menu. Idempotent - a second "
            "run reports `unchanged` and rewrites nothing."
        ),
    )
    shortcut.add_argument(
        "--startup",
        action="store_true",
        help="also add a run-at-login shortcut in shell:startup (starts in the tray)",
    )
    shortcut.add_argument(
        "--no-desktop", action="store_true", help="skip the Desktop shortcut"
    )
    shortcut.add_argument(
        "--no-start-menu", action="store_true", help="skip the Start Menu shortcut"
    )
    shortcut.add_argument(
        "--remove",
        action="store_true",
        help=(
            "remove every shortcut this command creates, including a run-at-login "
            "entry added by an earlier run"
        ),
    )

    install_skill = sub.add_parser(
        "install-skill",
        parents=[common],
        help="write the swe-mux agent skill into the directories agent CLIs read",
        description=(
            "Write the embedded swe-mux skill (`swemux --skill` prints it) into "
            "the skill roots agent CLIs actually read, so agents in a checkout "
            "learn what swe-mux offers them without any third-party tool or "
            "registry. The default scope is one project checkout: two writes "
            "(`.claude/skills/` and the shared `.agents/skills/`) cover every "
            "registered harness. `--global` targets the per-user roots instead, "
            "which reach every session those CLIs run anywhere - including "
            "outside swe-mux - so it prints the exact paths first and writes "
            "only under `--yes`. `--remove` takes back only files it can "
            "recognize as its own."
        ),
    )
    install_skill.add_argument(
        "--project",
        metavar="DIR",
        help="the checkout to install into (default: the current directory)",
    )
    install_skill.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="target the per-user skill roots every session reads",
    )
    install_skill.add_argument(
        "--harness",
        action="append",
        default=[],
        choices=agent_harnesses(),
        help="only the roots this harness reads (repeatable; default: all)",
    )
    install_skill.add_argument(
        "--remove",
        action="store_true",
        help="remove previously installed copies (recognized files only)",
    )
    install_skill.add_argument(
        "--yes",
        action="store_true",
        help="proceed with a --global install or removal instead of only printing the plan",
    )

    resume = sub.add_parser("resume", parents=[common], help="resume a history entry")
    resume.add_argument("id", help="history entry id")
    resume.add_argument("--project", required=True)
    return parser


def install_shortcut_command(args: argparse.Namespace) -> tuple[Any, Any]:
    """Run `install-shortcut` here, without asking a daemon anything.

    The only subcommand that touches no HTTP at all, and deliberately so: the
    person who needs it is the one whose install produced no way to start a
    daemon in the first place, so requiring one would make the command useless
    exactly where it is the answer.

    A refusal is reported, not raised, when it is a fact about the host - POSIX
    has no shell links, and saying so cleanly is the specified behaviour - while
    a shortcut that was attempted and failed becomes a non-zero exit through
    `main`, so a script can tell "not applicable here" from "it went wrong".
    """
    from . import shortcuts
    from .config import load_config

    slots = []
    if not args.no_start_menu:
        slots.append(shortcuts.SLOT_START_MENU)
    if not args.no_desktop:
        slots.append(shortcuts.SLOT_DESKTOP)
    if args.startup:
        slots.append(shortcuts.SLOT_STARTUP)
    if not slots and not args.remove:
        raise CliError(
            "nothing to do: --no-start-menu and --no-desktop leave no shortcut to "
            "create. Add --startup, or drop one of them.",
            EXIT_LOCAL_FAIL,
        )
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001 - a broken config is the expected caller
        raise CliError(
            f"the swe-mux config did not load ({type(exc).__name__}: {exc}), so there "
            "is no data directory to anchor a shortcut in. Run `mux doctor` for the "
            "local report.",
            EXIT_LOCAL_FAIL,
        ) from exc
    report = shortcuts.apply_shortcuts(config=config, slots=slots, remove=args.remove)
    return report.as_dict(), lambda _: shortcuts.render_report(report)


def install_skill_command(args: argparse.Namespace) -> tuple[Any, Any]:
    """Run `install-skill` here, without asking a daemon anything.

    Local like `install-shortcut`, and for the same reason: the person pointing
    an agent CLI at a checkout may have no daemon running at all, and the write
    is a plain file into directories this machine already owns.

    Global scope is the one act that gets a disclosure step. `~/.claude/skills/`
    reaches every agent this user ever runs, including outside swe-mux, so
    without `--yes` the command prints exactly what it would touch and stops -
    a successful preview, exit 0, not a failure.
    """
    from . import skill_install

    if args.project and args.global_scope:
        raise CliError(
            "--project and --global name two different scopes; pass one of them",
            EXIT_LOCAL_FAIL,
        )
    if args.global_scope:
        targets = skill_install.global_targets()
        scope = "global"
    else:
        project = Path(args.project or ".").expanduser().resolve()
        if not project.is_dir():
            raise CliError(f"{project} is not a directory", EXIT_NOT_FOUND)
        targets = skill_install.project_targets(project)
        scope = "project"
    targets = skill_install.filter_targets(targets, args.harness)
    verb = "remove" if args.remove else "install"
    if args.global_scope and not args.yes:
        writes = skill_install.plan(targets)
        confirm = f"Pass --yes to {verb} at these per-user paths."
    elif args.remove:
        writes = skill_install.remove(targets)
        confirm = ""
    else:
        writes = skill_install.install(targets, skill_install.skill_text())
        confirm = ""
    result = {
        "scope": scope,
        "action": verb,
        "confirmed": not confirm,
        "writes": [write.to_dict() for write in writes],
        "ok": not any(write.error for write in writes),
    }

    def render(_: Any) -> None:
        for write in writes:
            readers = ", ".join(write.readers)
            line = f"{write.action:9s} {write.path}  (read by {readers})"
            if write.reason:
                line += f" - {write.reason}"
            print(line)
        if confirm:
            print(confirm)

    return result, render


def dispatch(args: argparse.Namespace, base: str) -> tuple[Any, Any]:
    """Run the command; return (result, human_renderer). Renderer None = JSON only."""
    if args.command == "ls":
        query = "&".join(
            f"{key}={value}"
            for key, value in (
                ("project", args.project),
                ("state", args.state),
                ("backend", args.backend),
            )
            if value
        )
        path = "/api/sessions" + (f"?{query}" if query else "")
        return request("GET", path, base=base), _render_sessions
    if args.command == "spawn":
        return (
            request(
                "POST",
                "/api/sessions",
                {
                    "backend": args.backend,
                    "name": args.name,
                    "project_id": args.project,
                    "profile_id": args.profile,
                    "executable": args.exe,
                    "argv": args.arg,
                },
                base=base,
            ),
            None,
        )
    if args.command == "send":
        if args.all_broadcast:
            return request("POST", "/api/broadcast/input", {"data": args.text}, base=base), None
        if not args.session:
            raise CliError("send requires a session unless --all-broadcast is used", EXIT_NOT_FOUND)
        sid = resolve_session(args.session, base=base)
        return request("POST", f"/api/sessions/{sid}/input", {"data": args.text}, base=base), None
    if args.command == "kill":
        sid = resolve_session(args.session, base=base)
        return request("DELETE", f"/api/sessions/{sid}", base=base), None
    if args.command == "update":
        if args.install:
            return (
                request(
                    "POST",
                    "/api/update/install",
                    {"version": args.install},
                    base=base,
                    headers={"X-Mux-User-Gesture": "update-install"},
                ),
                _render_update_install,
            )
        return (
            {
                "check": request("GET", "/api/update", base=base),
                "install": request("GET", "/api/update/install", base=base),
            },
            _render_update,
        )
    if args.command == "reload-daemon":
        return request("POST", "/api/daemon/restart", {"force": args.force}, base=base), None
    if args.command == "history":
        return request("GET", "/api/history", base=base), None
    if args.command == "projects":
        return request("GET", "/api/projects", base=base), _render_id_name
    if args.command == "profiles":
        return request("GET", "/api/profiles", base=base), _render_id_name
    if args.command == "harnesses":
        return request("GET", "/api/harnesses", base=base), _render_harnesses
    if args.command == "doctor":
        if args.export:
            # The export bundle is an artifact to copy, not a table; always JSON.
            # It has no local equivalent - every one of its sections is daemon
            # state - so an unreachable daemon still fails here, with a pointer at
            # the command that does answer.
            try:
                return request("GET", "/api/diagnostics/export", base=base), None
            except CliError as exc:
                if exc.code != EXIT_CONNECTION:
                    raise
                raise CliError(
                    f"{exc} The export bundle is daemon state and has no local form; "
                    "run `mux doctor` (without --export) for the local report.",
                    EXIT_CONNECTION,
                ) from exc
        try:
            return request("GET", "/api/diagnostics/doctor", base=base), _render_doctor
        except CliError as exc:
            if exc.code != EXIT_CONNECTION:
                # An HTTP error means a daemon answered; it is a daemon fault, not
                # an install fault, and the local report would answer the wrong
                # question about it.
                raise
            return local_doctor_report(base=base, detail=exc.reason), _render_doctor
    if args.command == "history-duplicates":
        dry = args.action != "repair"
        # The endpoint's dry run reports the merge itself (keeper + learned values),
        # which is more useful than a bare listing.
        return request("POST", "/api/history/duplicates/repair", {"dry_run": dry}, base=base), None
    if args.command == "accounts":
        if args.action == "verify":
            return request("POST", "/api/provider-accounts/verify", {}, base=base), None
        if args.action == "audit":
            return (
                request("GET", f"/api/provider-accounts/audit?limit={args.limit}", base=base),
                None,
            )
        return request("GET", "/api/provider-accounts", base=base), None
    if args.command == "ui-overlay":
        return _ui_overlay_command(args, base)
    if args.command == "install-shortcut":
        return install_shortcut_command(args)
    if args.command == "install-skill":
        return install_skill_command(args)
    if args.command == "resume":
        body = {"project_id": args.project}
        return request("POST", f"/api/history/{args.id}/resume", body, base=base), None
    raise CliError(f"unknown command {args.command!r}", EXIT_NOT_FOUND)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.skill:
        from . import skill_install

        print(skill_install.skill_text(), end="")
        return EXIT_OK
    if args.command is None:
        # The subparser is optional only so `--skill` can stand alone; a bare
        # invocation keeps the usage error (exit 2) it has always had.
        parser.error("a command is required (or --skill)")
    base = resolve_base_url(getattr(args, "url", None))
    try:
        result, human = dispatch(args, base)
    except CliError as exc:
        print(f"mux: {exc}", file=sys.stderr)
        return exc.code
    _print(result, getattr(args, "json", False), human)
    if args.command == "install-skill" and isinstance(result, dict):
        # A policy refusal (a foreign file left in place) is the command doing
        # its job; only an attempted write or removal the filesystem rejected
        # exits non-zero.
        if not result.get("ok", True):
            return EXIT_LOCAL_FAIL
    if args.command == "install-shortcut" and isinstance(result, dict):
        # An unsupported host is not a failure - nothing was asked of it that it
        # could have done - so only a shortcut that was attempted and did not
        # land exits non-zero. Gating on `supported` too would make the command
        # red on every POSIX machine that merely asked.
        if result.get("supported") and not result.get("ok", True):
            return EXIT_LOCAL_FAIL
    # doctor is the one command whose exit code reflects the daemon's health, not
    # just whether the request succeeded, so a script can gate on `mux doctor`.
    if args.command == "doctor" and not getattr(args, "export", False):
        if isinstance(result, dict):
            if not result.get("ok", True):
                return EXIT_DOCTOR_FAIL
            # A local report with nothing failing still ran a fraction of the
            # checks against a daemon that is not there, so it must not exit as
            # though everything passed. 3 is not a new code: it is what "daemon
            # unreachable" has always meant, which is exactly what happened.
            if result.get("mode") == "local":
                return EXIT_CONNECTION
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
