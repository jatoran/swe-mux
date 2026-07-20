from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


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
    spawn.add_argument("--backend", choices=("shell", "claude", "codex"), default="shell")
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
    sub.add_parser("history")
    sub.add_parser("projects")
    sub.add_parser("profiles")
    sub.add_parser("doctor")
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
    elif args.command == "history":
        result = request("GET", "/api/history")
    elif args.command == "projects":
        result = request("GET", "/api/projects")
    elif args.command == "profiles":
        result = request("GET", "/api/profiles")
    elif args.command == "doctor":
        result = request("GET", "/api/remote/status")
    else:
        result = request("POST", f"/api/history/{args.id}/resume", {"project_id": args.project})
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
