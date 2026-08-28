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
  failing check. Scripts branch on these, never on prose.
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
from typing import Any

from .harness import agent_harnesses

# Actionable exit codes. 2 is reserved by argparse for usage errors, so it is not
# reused here. Scripts branch on these; they are part of the CLI contract.
EXIT_OK = 0
EXIT_DOCTOR_FAIL = 1
EXIT_CONNECTION = 3
EXIT_HTTP = 4
EXIT_AMBIGUOUS = 5
EXIT_NOT_FOUND = 6

DEFAULT_URL = "http://127.0.0.1:8765"


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
    method: str, path: str, body: dict[str, object] | None = None, *, base: str
) -> Any:
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
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


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", action="store_true", help="print the raw daemon JSON instead of a table"
    )
    common.add_argument(
        "--url", help="daemon base URL (overrides MUX_URL and config)", default=None
    )

    parser = argparse.ArgumentParser(prog="mux", description="Control the swe-mux daemon.")
    sub = parser.add_subparsers(dest="command", required=True)

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

    resume = sub.add_parser("resume", parents=[common], help="resume a history entry")
    resume.add_argument("id", help="history entry id")
    resume.add_argument("--project", required=True)
    return parser


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
    if args.command == "resume":
        body = {"project_id": args.project}
        return request("POST", f"/api/history/{args.id}/resume", body, base=base), None
    raise CliError(f"unknown command {args.command!r}", EXIT_NOT_FOUND)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base = resolve_base_url(getattr(args, "url", None))
    try:
        result, human = dispatch(args, base)
    except CliError as exc:
        print(f"mux: {exc}", file=sys.stderr)
        return exc.code
    _print(result, getattr(args, "json", False), human)
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
