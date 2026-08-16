from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .harness import agent_harnesses


def request(method: str, path: str, body: dict[str, object] | None = None) -> object:
    base = os.environ.get("MUX_URL", "http://127.0.0.1:8765").rstrip("/")
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"mux: HTTP {exc.code}: {detail}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(prog="mux")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ls")
    spawn = sub.add_parser("spawn")
    spawn.add_argument("--backend", choices=("shell", *agent_harnesses()), default="shell")
    spawn.add_argument("--name")
    spawn.add_argument("--project", required=True)
    spawn.add_argument("--profile")
    spawn.add_argument("--exe")
    spawn.add_argument("--arg", action="append", default=[])
    send = sub.add_parser("send")
    send.add_argument("session", nargs="?")
    send.add_argument("text")
    send.add_argument("--all-broadcast", action="store_true")
    kill = sub.add_parser("kill")
    kill.add_argument("session")
    reload_daemon = sub.add_parser(
        "reload-daemon",
        help="restart the daemon in place; sessions survive when the PTY supervisor is attached",
    )
    reload_daemon.add_argument(
        "--force",
        action="store_true",
        help="restart even without the PTY supervisor (kills every session)",
    )
    sub.add_parser("history")
    sub.add_parser("projects")
    sub.add_parser("profiles")
    doctor = sub.add_parser("doctor")
    doctor.add_argument(
        "--export",
        action="store_true",
        help="print the full diagnostics bundle (config, remote, firewall, logs)",
    )
    accounts = sub.add_parser(
        "accounts",
        help="inspect provider accounts, re-verify their identities, or read the credential audit",
    )
    accounts.add_argument(
        "action", choices=("list", "verify", "audit"), nargs="?", default="list"
    )
    accounts.add_argument("--limit", type=int, default=50, help="audit entries to show")
    duplicates = sub.add_parser(
        "history-duplicates",
        help="report, or merge, history entries that share one conversation",
    )
    duplicates.add_argument(
        "action",
        choices=("report", "repair"),
        nargs="?",
        default="report",
        help="report lists what would change; repair merges each conversation's rows",
    )
    resume = sub.add_parser("resume")
    resume.add_argument("id")
    resume.add_argument("--project", required=True)
    args = parser.parse_args()
    if args.command == "ls":
        result = request("GET", "/api/sessions")
    elif args.command == "spawn":
        result = request(
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
        )
    elif args.command == "send":
        if args.all_broadcast:
            result = request("POST", "/api/broadcast/input", {"data": args.text})
        else:
            if not args.session:
                parser.error("send requires SESSION unless --all-broadcast is used")
            result = request("POST", f"/api/sessions/{args.session}/input", {"data": args.text})
    elif args.command == "kill":
        result = request("DELETE", f"/api/sessions/{args.session}")
    elif args.command == "reload-daemon":
        result = request("POST", "/api/daemon/restart", {"force": args.force})
    elif args.command == "history":
        result = request("GET", "/api/history")
    elif args.command == "projects":
        result = request("GET", "/api/projects")
    elif args.command == "profiles":
        result = request("GET", "/api/profiles")
    elif args.command == "doctor":
        result = request("GET", "/api/diagnostics/export" if args.export else "/api/remote/status")
    elif args.command == "history-duplicates":
        if args.action == "repair":
            result = request("POST", "/api/history/duplicates/repair", {"dry_run": False})
        else:
            # The endpoint's dry run reports the merge itself, which is more useful
            # than a bare listing: it names the keeper and every value it would learn.
            result = request("POST", "/api/history/duplicates/repair", {"dry_run": True})
    elif args.command == "accounts":
        if args.action == "verify":
            # Re-derives every saved account's owner from its own credentials,
            # which is what exposes two slots holding one provider account.
            result = request("POST", "/api/provider-accounts/verify", {})
        elif args.action == "audit":
            result = request("GET", f"/api/provider-accounts/audit?limit={args.limit}")
        else:
            result = request("GET", "/api/provider-accounts")
    else:
        result = request("POST", f"/api/history/{args.id}/resume", {"project_id": args.project})
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
