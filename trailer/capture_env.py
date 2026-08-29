#!/usr/bin/env python3
"""Stand up the PII-free capture environment the site screenshots are shot in.

Everything visible in `site/img/` has to come from somewhere, and the somewhere
must not be this machine. The captures that used to occupy those filenames were
screenshots of a live daemon: a real project sidebar, a real operator name, real
account spend percentages, real absolute paths. `site/` is a public deploy root,
and an image published there is scraped before it can be withdrawn.

So this script builds a *second*, synthetic install - invented projects, invented
sessions, invented git history, invented notes - and `capture_site_shots.py`
photographs that instead.

    uv run --with playwright python trailer/capture_env.py up
    uv run --with playwright python trailer/capture_site_shots.py
    uv run --with playwright python trailer/capture_env.py down

Three rules it follows, each of which is the reason for a choice that would
otherwise look arbitrary.

**It never touches the operator's daemon.** The live daemon owns port 8765 and
`~/.mux`, and both are process-wide singletons; colliding with either would
disrupt real agent sessions. `muxd` has no `--data-dir`, but `resolve_data_dir`
falls back to the config file's parent, so `--config <root>/config.toml` puts the
whole install under `<root>`. `--local-only` clears `tailnet_enabled`, which is
what keeps this daemon from reaching for the operator's Tailscale Serve route on
443. `down` terminates only the PID `up` recorded, and never `muxd --shutdown`,
which would reap every live session on the host.

**The child environment is scrubbed.** This script is itself usually run from
inside a swe-mux session, so its own environment carries `MUX_*` and `CLAUDE_*`
variables pointing at the *live* daemon - a hook URL, a session id, a shim
directory. Inherited, they would make the capture daemon's sessions report
themselves to the operator's install and rename its panes. `child_env()` drops
them.

**No agent CLI is launched.** Every session here is a shell. That is a deliberate
limit rather than an oversight: a real agent authenticates from the operator's
own `~/.claude`, and the account it surfaces (`GET /api/provider-accounts`
returns a live email and organisation on this host) is exactly the class of data
these captures exist to keep out. What the shots need from a session is what
swe-mux itself draws - the row, the status, the tab rail, the drawer - and all of
that is swe-mux's own UI over a real PTY.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

# --------------------------------------------------------------------- layout
# Outside the repository, and named so nothing in a screenshot reads as a path on
# somebody's machine. `CODE_ROOT` is what a shell prompt renders, so it is kept
# short and free of a user directory.
ROOT = Path(os.environ.get("MUX_CAPTURE_ROOT") or "D:/swemux-capture")
CODE_ROOT = Path(os.environ.get("MUX_CAPTURE_CODE_ROOT") or (ROOT / "code"))
# `muxd` has no `--data-dir`. `supervisor.resolve_data_dir` falls back to the
# config file's own parent, so naming the config inside `data/` is what puts the
# entire install - database, supervisor discovery, shims, logs - in one directory
# that `up` can move aside whole.
DATA_DIR = ROOT / "data"
# Outside `CODE_ROOT`: a checkout nested inside a Project's root would show up in
# that Project's own file browser and dirty counts.
WORKTREE_ROOT = ROOT / "worktrees"
CONFIG_PATH = DATA_DIR / "config.toml"
STATE_PATH = ROOT / "capture-state.json"
DB_PATH = DATA_DIR / "mux.db"
PORT = int(os.environ.get("MUX_CAPTURE_PORT") or 8799)
BASE = f"http://127.0.0.1:{PORT}"
LIVE_PORT = 8765

# The operator's real agent config directory, resolved from the environment this
# script itself was started in - before child_env() repoints the home. It is used
# only when `up --claude-config` asked for it, and it is the single deliberate
# exception to "nothing here is derived from this machine": the desktop-insight
# slot needs one real agent run behind it (`SITE_SHOTS.md` has the argument), a
# real agent CLI needs a real credential, and the operator approved spending a
# little quota for it. Everything the CLI then *sees* - project, home, git
# identity, account chips - stays synthetic.
REAL_HOME = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
REAL_CLAUDE_CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or str(REAL_HOME / ".claude")
_claude_config_enabled = False

# ------------------------------------------------------------------- the cast
# A small coherent fleet that reads like a working developer's, invented whole.
# Names, branches, commit subjects, authors, and note prose are all fiction; none
# of it is derived from this machine, and none of it names a real person.
AUTHORS = [
    ("Rina Delacroix", "rina@example.invalid"),
    ("Theo Almquist", "theo@example.invalid"),
    ("Priya Nandakumar", "priya@example.invalid"),
]

PROJECTS = [
    {
        "slug": "atlas-api",
        "name": "atlas-api",
        "blurb": "Ingest and query service.",
        "files": {
            "README.md": "# atlas-api\n\nIngest and query service for the Atlas fleet.\n",
            "src/ingest.py": (
                '"""Ingest endpoint: accept a batch, enqueue it, answer with a receipt."""\n\n'
                "from dataclasses import dataclass\n\n\n"
                "@dataclass(frozen=True)\nclass Receipt:\n"
                "    batch_id: str\n    accepted: int\n    rejected: int\n\n\n"
                "def accept(batch):\n"
                "    accepted = [row for row in batch if row.get('ts')]\n"
                "    return Receipt(batch.id, len(accepted), len(batch) - len(accepted))\n"
            ),
            "src/limits.py": (
                "\"\"\"Token bucket, per tenant. Refills on read rather than on a timer.\"\"\"\n\n"
                "import time\n\n\n"
                "class Bucket:\n"
                "    def __init__(self, capacity: int, per_second: float) -> None:\n"
                "        self.capacity = capacity\n"
                "        self.per_second = per_second\n"
                "        self.tokens = float(capacity)\n"
                "        self.checked = time.monotonic()\n\n"
                "    def take(self, cost: int = 1) -> bool:\n"
                "        now = time.monotonic()\n"
                "        self.tokens = min(\n"
                "            self.capacity, self.tokens + (now - self.checked) * self.per_second\n"
                "        )\n"
                "        self.checked = now\n"
                "        if self.tokens < cost:\n            return False\n"
                "        self.tokens -= cost\n        return True\n"
            ),
            "tests/test_limits.py": (
                "from src.limits import Bucket\n\n\n"
                "def test_bucket_refuses_past_capacity():\n"
                "    bucket = Bucket(capacity=2, per_second=0.0)\n"
                "    assert bucket.take() and bucket.take()\n"
                "    assert not bucket.take()\n"
            ),
        },
        "branches": [
            ("rate-limit-ingest", [("src/limits.py", "    # burst allowance, per tenant\n")]),
            ("receipt-schema", [("src/ingest.py", "\n# receipt schema v2 lands here\n")]),
        ],
    },
    {
        "slug": "harbor-ui",
        "name": "harbor-ui",
        "blurb": "Operator console.",
        "files": {
            "README.md": "# harbor-ui\n\nThe operator console for Atlas.\n",
            "src/Legend.tsx": (
                "export function Legend({series}: {series: string[]}) {\n"
                "  return <ul class=\"legend\">{series.map(name =>\n"
                "    <li key={name}><button type=\"button\">{name}</button></li>)}</ul>\n"
                "}\n"
            ),
            "src/tokens.css": ":root{--surface:#0d1116;--ink:#e6edf3;--accent:#7ee787}\n",
        },
        "branches": [("legend-focus-order", [("src/Legend.tsx", "\n// focus ring\n")])],
    },
    {
        "slug": "tidepool",
        "name": "tidepool",
        "blurb": "Nightly loaders.",
        "files": {
            "README.md": "# tidepool\n\nNightly loaders and the dedupe pass.\n",
            "loaders/nightly.py": (
                "\"\"\"Nightly loader. Idempotent by (source, natural_key).\"\"\"\n\n"
                "def dedupe(rows):\n"
                "    seen = set()\n    out = []\n"
                "    for row in rows:\n"
                "        key = (row['source'], row['natural_key'])\n"
                "        if key in seen:\n            continue\n"
                "        seen.add(key)\n        out.append(row)\n"
                "    return out\n"
            ),
        },
        "branches": [],
    },
    {
        "slug": "quill-docs",
        "name": "quill-docs",
        "blurb": "Release notes and guides.",
        "files": {
            "README.md": "# quill-docs\n\nRelease notes, guides, and the export pipeline.\n",
            "docs/release-notes.md": "# Release notes\n\n## 2.4\n\n- Ingest receipts\n",
        },
        "branches": [],
    },
    {
        "slug": "wayfinder-cli",
        "name": "wayfinder-cli",
        "blurb": "Operator CLI.",
        "files": {
            "README.md": "# wayfinder-cli\n\nThe operator CLI.\n",
            "cmd/root.go": "package cmd\n\nfunc Execute() error {\n\treturn nil\n}\n",
        },
        "branches": [],
    },
]

# Commit subjects, oldest first. Written to read like ordinary work.
HISTORY = {
    "atlas-api": [
        "Bootstrap the ingest service",
        "Reject rows with no timestamp instead of dropping them",
        "Add a per-tenant token bucket",
        "Refill the bucket on read so an idle tenant is not penalised",
        "Cover the capacity refusal",
    ],
    "harbor-ui": [
        "Bootstrap the console",
        "Extract the chart legend into its own component",
        "Move the palette into tokens",
    ],
    "tidepool": [
        "Bootstrap the loaders",
        "Dedupe on the natural key rather than the row hash",
    ],
    "quill-docs": ["Start the release notes", "Record the 2.4 ingest receipts"],
    "wayfinder-cli": ["Bootstrap the CLI"],
}

# Sessions, per project. `backend` is always shell; see the module docstring.
SESSIONS = [
    ("atlas-api", "Rate limit the ingest route"),
    ("atlas-api", "Receipt schema v2"),
    ("atlas-api", "Ingest throughput bench"),
    ("harbor-ui", "Chart legend focus order"),
    ("harbor-ui", "Design token audit"),
    ("tidepool", "Nightly loader dedupe"),
    ("quill-docs", "Release notes export"),
]

NOTE_TITLE = "Rate limiting: decisions and open questions"
NOTE_BODY = """# Rate limiting: decisions and open questions

The ingest route needs a ceiling per tenant, not per connection. A connection
ceiling is the obvious one and it is wrong here: a tenant with forty workers
would get forty times the budget of a tenant with one.

## Decided

- **Token bucket, refilled on read.** A timer thread refilling every bucket is a
  cost proportional to tenants rather than to traffic, and an idle tenant does
  not need waking to be told it has room.
- **Capacity is a burst allowance, not a rate.** The two are separate knobs
  because a batch importer is bursty and legitimate.
- **Refusal answers 429 with the retry window**, so a caller can back off by
  arithmetic instead of by guessing.

## Open

1. What happens to a batch that is *partly* over the ceiling?
   1. Accept the prefix and report the remainder in the receipt.
   2. Refuse the whole batch and make the caller split it.
2. Where does the bucket live when there are two ingest processes?

## Before this lands

- [x] Bucket refuses past capacity
- [x] Refill is proportional to elapsed time
- [ ] Two-process bucket has an owner
- [ ] The 429 body carries the retry window
"""

# Attention items. The inbox has to show ranked work, an interrupt budget, and at
# least one *suppressed* item with its reason - an empty inbox is a picture of the
# feature switched off, and a suppression is the argument for the feature.
ATTENTION = [
    {
        "title": "atlas-api is waiting on a decision it cannot make",
        "summary": (
            "The run has asked twice whether a partly-over-ceiling batch should be "
            "truncated or refused, and has stopped rather than guessing."
        ),
        "action": "Answer the truncate-or-refuse question in the ingest run.",
        "incident_class": "awaiting_decision",
        "kinds": ["awaiting_user"],
        "channel": "interrupt_now",
        "cost_to_resolve": "seconds",
        "score": 0.91,
        "confidence": 0.88,
        "suppressed_reason": None,
        "project": "atlas-api",
        "session": "Rate limit the ingest route",
        "age": 240,
        "evidence": [
            {"kind": "transcript", "detail": "asked the same question in two consecutive turns"},
            {"kind": "status", "detail": "awaiting for 4m with no output"},
        ],
    },
    {
        "title": "The same failing test has been re-run six times",
        "summary": (
            "tidepool's loader run has re-run test_dedupe_keeps_first six times with no "
            "edit to the loader between attempts."
        ),
        "action": "Look at the dedupe key before the seventh run.",
        "incident_class": "loop",
        "kinds": ["loop", "no_progress"],
        "channel": "next_breakpoint",
        "cost_to_resolve": "minutes",
        "score": 0.74,
        "confidence": 0.81,
        "suppressed_reason": None,
        "project": "tidepool",
        "session": "Nightly loader dedupe",
        "age": 900,
        "evidence": [
            {"kind": "tool", "detail": "pytest tests/test_dedupe.py x6, no intervening edit"},
        ],
    },
    {
        "title": "harbor-ui claims the focus order is fixed and ran no test",
        "summary": (
            "The run reported the legend focus order as corrected. No test was run and "
            "the only edit was to a comment."
        ),
        "action": "Ask for the failing case before believing the claim.",
        "incident_class": "declared_not_verified",
        "kinds": ["declared_not_verified"],
        "channel": "next_breakpoint",
        "cost_to_resolve": "minutes",
        "score": 0.66,
        "confidence": 0.79,
        "suppressed_reason": None,
        "project": "harbor-ui",
        "session": "Chart legend focus order",
        "age": 1500,
        "evidence": [{"kind": "diff", "detail": "1 file changed, 1 insertion(+), comment only"}],
    },
    {
        "title": "quill-docs export run has been quiet for 20 minutes",
        "summary": "No output since the export started. The process is alive.",
        "action": "Check whether the export is waiting on the network.",
        "incident_class": "stall",
        "kinds": ["stall"],
        "channel": "digest",
        "cost_to_resolve": "minutes",
        "score": 0.41,
        "confidence": 0.55,
        "suppressed_reason": "below the interrupt budget for today",
        "project": "quill-docs",
        "session": "Release notes export",
        "age": 2400,
        "evidence": [{"kind": "status", "detail": "no PTY output for 20m, process alive"}],
    },
    {
        "title": "atlas-api bench session repeated a benchmark with no change",
        "summary": "Two identical benchmark runs, four minutes apart, same arguments.",
        "action": "None yet; a repeat is not yet a loop.",
        "incident_class": "loop",
        "kinds": ["loop"],
        "channel": "digest",
        "cost_to_resolve": "seconds",
        "score": 0.28,
        "confidence": 0.44,
        "suppressed_reason": "the same rule fired on this run 12 minutes ago",
        "project": "atlas-api",
        "session": "Ingest throughput bench",
        "age": 3300,
        "evidence": [{"kind": "tool", "detail": "identical argv, 2 runs"}],
    },
]

# Behavioural timeline records for the Insight tab. Shaped exactly like the
# observer's own output (`scan_timeline.SCAN_SCHEMA`) so the surface renders them
# through its ordinary path rather than through a capture-only branch.
SCANS = [
    {
        "trigger": "turn_started",
        "behavior": ["grounding", "retrieving"],
        "work_phase": "investigation",
        "intent": "Find where the ingest route decides a batch is acceptable.",
        "claim": "",
        "user_ask": "Put a per-tenant ceiling on the ingest route.",
        "blocked_on": "none",
        "summary": (
            "Read src/ingest.py and src/limits.py, then searched for existing callers of "
            "accept() to find who would see a refusal."
        ),
        "approach_status": "active",
        "dead_end": "",
        "confidence": 0.86,
        "age": 2100,
        "tokens": (7412, 388),
        "cost": 0.0021,
    },
    {
        "trigger": "tool_result",
        "behavior": ["planning"],
        "work_phase": "investigation",
        "intent": "Choose between a per-connection and a per-tenant ceiling.",
        "claim": "A per-connection ceiling scales with worker count, not with the tenant.",
        "user_ask": "Put a per-tenant ceiling on the ingest route.",
        "blocked_on": "none",
        "summary": (
            "Rejected the per-connection ceiling on the grounds that a tenant with forty "
            "workers would get forty times the budget of a tenant with one."
        ),
        "approach_status": "active",
        "dead_end": "",
        "confidence": 0.9,
        "age": 1740,
        "tokens": (8104, 452),
        "cost": 0.0024,
    },
    {
        "trigger": "turn_ended",
        "behavior": ["executing"],
        "work_phase": "implementation",
        "intent": "Refill the bucket on a timer.",
        "claim": "A refill thread keeps every bucket current.",
        "user_ask": "Put a per-tenant ceiling on the ingest route.",
        "blocked_on": "none",
        "summary": (
            "Wrote a refill thread, then dropped it: the cost is proportional to tenants "
            "rather than to traffic, and an idle tenant does not need waking."
        ),
        "approach_status": "abandoned",
        "dead_end": "Timer-driven refill: cost scales with tenants, not with traffic.",
        "confidence": 0.83,
        "age": 1320,
        "tokens": (9330, 611),
        "cost": 0.0031,
    },
    {
        "trigger": "turn_ended",
        "behavior": ["executing", "evaluating"],
        "work_phase": "implementation",
        "intent": "Refill on read instead.",
        "claim": "Refill-on-read is exact and costs nothing while a tenant is idle.",
        "user_ask": "Put a per-tenant ceiling on the ingest route.",
        "blocked_on": "none",
        "summary": (
            "Replaced the thread with a refill computed from elapsed time at take(). "
            "Added the capacity-refusal test and ran it."
        ),
        "approach_status": "active",
        "dead_end": "",
        "confidence": 0.92,
        "age": 780,
        "tokens": (10218, 704),
        "cost": 0.0036,
    },
    {
        "trigger": "heartbeat",
        "behavior": ["reflecting"],
        "work_phase": "test",
        "intent": "Decide what a partly-over-ceiling batch should do.",
        "claim": "",
        "user_ask": "Put a per-tenant ceiling on the ingest route.",
        "blocked_on": "user_input",
        "summary": (
            "Stopped at the truncate-or-refuse question rather than choosing: the two "
            "answers give callers different contracts and only one is recoverable."
        ),
        "approach_status": "active",
        "dead_end": "",
        "confidence": 0.88,
        "age": 240,
        "tokens": (6890, 402),
        "cost": 0.0020,
    },
]


# ---------------------------------------------------------------------- shell
def child_env() -> dict[str, str]:
    """This process's environment with every live-install variable removed.

    Two strips, and the second one is the one that was found by looking at a
    capture rather than by reasoning about it.

    A capture daemon that inherited `MUX_HOOK_URL` would report its sessions to
    the operator's daemon; one that inherited `CLAUDE_JOB_DIR` would rename its
    own panes after the session that started it. Neither failure says anything
    when it happens, so that strip is unconditional rather than conditional on
    having noticed one.

    `USERPROFILE`/`HOME` are repointed because `ProviderAccountService` reads
    `Path.home()` for the live provider credentials, and the *first* seeded
    capture rendered the operator's Claude organisation and Codex login as two
    labelled chips in the bottom-left corner of the workspace - which is the
    same class of leak (`ADAM`) that pulled the original screenshots. With a
    synthetic home there is no credential to read, so there is no label to draw.
    It also keeps the operator's global `.gitconfig` out of the synthetic
    repositories' commits.
    """
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(("MUX_", "CLAUDE_", "SWE_MUX_")) or key == "CLAUDECODE":
            del env[key]
    home = ROOT / "home"
    home.mkdir(parents=True, exist_ok=True)
    env["USERPROFILE"] = str(home)
    env["HOME"] = str(home)
    env["HOMEDRIVE"] = str(home.drive)
    env["HOMEPATH"] = str(home)[len(home.drive) :] or "\\"
    if _claude_config_enabled:
        # Added back *after* the strip, so it is the one CLAUDE_* variable the
        # daemon carries, and it exists for the daemon's *discovery* half only:
        # `harness._claude_data_home` reads the daemon's own environment to find
        # transcripts under `<dir>/projects`. The agent CLI itself must NOT see
        # this value - with CLAUDE_CONFIG_DIR set, the CLI keeps its account
        # state in `<dir>/.claude.json`, which is not where the operator's real
        # state lives (`~/.claude.json`), so it would open a sign-in screen over
        # a valid credential (measured 2026-08-28). `command_agent_run` masks it
        # per-session with an empty string, which the CLI treats as unset.
        # `ProviderAccountService` reads `Path.home()` instead, so the account
        # chips stay the seeded fixture either way.
        env["CLAUDE_CONFIG_DIR"] = REAL_CLAUDE_CONFIG_DIR
    return env


def run(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(
        args, cwd=str(cwd), env=env or child_env(), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SystemExit(
            f"{' '.join(args)} failed in {cwd} ({result.returncode})\n"
            f"{result.stdout}\n{result.stderr}"
        )


def api(method: str, path: str, body: object | None = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> {exc.code}: {exc.read().decode()[:400]}") from exc
    return json.loads(raw) if raw else None


def port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def live_daemon_ok() -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{LIVE_PORT}/api/health", timeout=5
        ) as response:
            return bool(json.loads(response.read().decode()).get("ok"))
    except OSError:
        return False


# ----------------------------------------------------------------- the repos
def build_repos() -> None:
    """One git repository per project, with invented history and invented authors.

    Authors are set per-invocation rather than by `git config`, so the operator's
    own identity cannot end up in a commit that a screenshot of the Git map then
    publishes.
    """
    if WORKTREE_ROOT.exists():
        graveyard = ROOT / ".trash"
        graveyard.mkdir(parents=True, exist_ok=True)
        WORKTREE_ROOT.rename(graveyard / f"worktrees-{time.strftime('%Y%m%d-%H%M%S')}")
    if CODE_ROOT.exists():
        # Moved rather than removed. `shutil.rmtree` cannot take a git object
        # store on Windows anyway (the loose objects are read-only, so `unlink`
        # answers WinError 5), and a previous run's checkout is the evidence for
        # why a previous run's screenshot looks the way it does.
        graveyard = ROOT / ".trash"
        graveyard.mkdir(parents=True, exist_ok=True)
        CODE_ROOT.rename(graveyard / f"code-{time.strftime('%Y%m%d-%H%M%S')}")
    CODE_ROOT.mkdir(parents=True, exist_ok=True)
    for index, project in enumerate(PROJECTS):
        root = CODE_ROOT / project["slug"]
        root.mkdir(parents=True)
        run(["git", "init", "-b", "main"], root)
        run(["git", "config", "commit.gpgsign", "false"], root)
        # Every file here is written with LF. Without this, `git diff` in a
        # capture pane prints a CRLF conversion warning over the output the shot
        # is of.
        run(["git", "config", "core.autocrlf", "false"], root)
        subjects = HISTORY[project["slug"]]
        items = list(project["files"].items())
        # Spread the files across the commits so the history has shape rather than
        # one commit holding everything.
        per = max(1, len(items) // max(1, len(subjects)))
        cursor = 0
        base = time.time() - 86_400 * 9
        for step, subject in enumerate(subjects):
            chunk = items[cursor : cursor + per] if step < len(subjects) - 1 else items[cursor:]
            cursor += len(chunk)
            for name, text in chunk:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
            if not chunk:
                marker = root / "README.md"
                marker.write_text(
                    marker.read_text(encoding="utf-8") + f"\n<!-- {subject.lower()} -->\n",
                    encoding="utf-8",
                    newline="\n",
                )
            author, email = AUTHORS[(index + step) % len(AUTHORS)]
            when = time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(base + step * 7_200 + index * 1_100)
            )
            env = {
                **child_env(),
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": email,
                "GIT_AUTHOR_DATE": when,
                "GIT_COMMITTER_DATE": when,
            }
            run(["git", "add", "-A"], root, env)
            run(["git", "commit", "-m", subject], root, env)
        for branch, edits in project["branches"]:
            run(["git", "checkout", "-b", branch], root)
            for name, addition in edits:
                path = root / name
                path.write_text(
                    path.read_text(encoding="utf-8") + addition, encoding="utf-8", newline="\n"
                )
            author, email = AUTHORS[(index + 1) % len(AUTHORS)]
            env = {
                **child_env(),
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": email,
            }
            run(["git", "add", "-A"], root, env)
            run(["git", "commit", "-m", f"{branch.replace('-', ' ').capitalize()}"], root, env)
            run(["git", "checkout", "main"], root)
        if project["branches"]:
            # One more commit on the trunk *after* the branches were cut, so the Git
            # map has something to count in both directions. A row that is only ever
            # ahead is a picture of half the column.
            author, email = AUTHORS[index % len(AUTHORS)]
            env = {
                **child_env(),
                "GIT_AUTHOR_NAME": author,
                "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": author,
                "GIT_COMMITTER_EMAIL": email,
            }
            readme = root / "README.md"
            readme.write_text(
                readme.read_text(encoding="utf-8") + "\nRun the tests with `pytest`.\n",
                encoding="utf-8",
                newline="\n",
            )
            run(["git", "add", "-A"], root, env)
            run(["git", "commit", "-m", "Say how to run the tests"], root, env)
            for branch, _ in project["branches"]:
                # Checked out worktrees, because the Git map lists checkouts rather
                # than branches: a branch with no worktree has no row to carry a
                # count.
                target = WORKTREE_ROOT / f"{project['slug']}-{branch}"
                WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
                run(["git", "worktree", "add", str(target), branch], root)
        # One project is left with an uncommitted edit, because a Git map whose
        # every row is clean is a picture of the feature with nothing to say.
        if project["slug"] == "atlas-api":
            (root / "src" / "limits.py").write_text(
                (root / "src" / "limits.py").read_text(encoding="utf-8")
                + "\n\ndef retry_after(bucket: Bucket, cost: int = 1) -> float:\n"
                "    \"\"\"Seconds until `cost` tokens exist. Answers the 429 body.\"\"\"\n"
                "    missing = max(0.0, cost - bucket.tokens)\n"
                "    return missing / bucket.per_second if bucket.per_second else float('inf')\n",
                encoding="utf-8",
                newline="\n",
            )


# ---------------------------------------------------------------- the daemon
def reset_data_dir() -> None:
    """Move any previous install aside so a capture run starts from a known state.

    Moved rather than deleted: a shot that came out wrong is usually explained by
    what the previous run had in its database, and that is gone forever if the
    directory is removed.
    """
    if DATA_DIR.exists():
        graveyard = ROOT / ".trash"
        graveyard.mkdir(parents=True, exist_ok=True)
        DATA_DIR.rename(graveyard / f"data-{time.strftime('%Y%m%d-%H%M%S')}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def write_config() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        "\n".join(
            [
                f"port = {PORT}",
                'host = "127.0.0.1"',
                "# The capture install is deliberately quiet: nothing here should reach a",
                "# network, spend a budget, or ask the operator for anything mid-shoot.",
                "tailnet_enabled = false",
                "# The capture install runs its own PTY supervisor, discovered through",
                "# THIS data dir's supervisor.json and therefore fully separate from the",
                "# operator's. It exists so the survive-a-daemon-restart loop can be",
                "# recorded honestly: /api/daemon/restart refuses without one, because a",
                "# restart would otherwise reap the sessions it claims to preserve.",
                "pty_supervisor_enabled = true",
                "# No release banner over a shot or a loop: the capture install is not",
                "# an install anyone updates, and the banner is the first thing a crop",
                "# would have to crop around.",
                "update_check_enabled = false",
                "attention_daily_interrupt_budget = 4",
                "# A quota poll would dial a provider with the synthetic credential",
                "# `seed_accounts` writes and turn the chips into an error state",
                "# mid-shoot. 1440 is the schema's ceiling.",
                "provider_quota_poll_minutes = 1440",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def start_daemon() -> int:
    if not port_is_free(PORT):
        raise SystemExit(
            f"port {PORT} is already in use. Pick another with MUX_CAPTURE_PORT and "
            "check it with `netstat -ano | grep <port>` first."
        )
    creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "swe_mux",
            "--config",
            str(CONFIG_PATH),
            "--port",
            str(PORT),
            "--local-only",
        ],
        env=child_env(),
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation,
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"capture daemon exited during startup ({process.returncode})")
        try:
            with urllib.request.urlopen(f"{BASE}/api/health", timeout=4) as response:
                if json.loads(response.read().decode()).get("status") == "ready":
                    return process.pid
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("capture daemon did not become ready within 180s")


def stop_daemon() -> None:
    """Terminate only the PID `up` recorded.

    Never `muxd --shutdown` and never a name-matched taskkill: both would reach
    the operator's daemon and reap every live session on the host.
    """
    if not STATE_PATH.exists():
        print("no recorded capture daemon")
        return
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    pid = int(state.get("pid") or 0)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            print(f"could not signal {pid}: {exc}")
        deadline = time.time() + 30
        while time.time() < deadline and not port_is_free(PORT):
            time.sleep(1)
        if not port_is_free(PORT):
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    # The capture install's own supervisor outlives its daemon by design, so it
    # is stopped here too - by the PID in THIS data dir's discovery file, which
    # is what scopes the kill to the capture install. The operator's supervisor
    # discovers through ~/.mux and is untouchable from here.
    discovery = DATA_DIR / "supervisor.json"
    if discovery.exists():
        try:
            supervisor_pid = int(json.loads(discovery.read_text(encoding="utf-8")).get("pid") or 0)
        except (OSError, ValueError):
            supervisor_pid = 0
        if supervisor_pid:
            subprocess.run(
                ["taskkill", "/PID", str(supervisor_pid), "/T", "/F"], capture_output=True
            )
            print(f"capture supervisor {supervisor_pid} stopped (with its sessions)")
    STATE_PATH.unlink(missing_ok=True)
    print(f"capture daemon {pid} stopped; port {PORT} free: {port_is_free(PORT)}")
    print(f"operator daemon on {LIVE_PORT} healthy: {live_daemon_ok()}")


# --------------------------------------------------------------- the accounts
# Two invented provider accounts, so the quota chips in the sidebar footer show
# the feature rather than "signed_out". Both halves are needed and neither is a
# real credential:
#
#   - a *file* at the synthetic home's `.claude/.credentials.json`, because
#     `_reconcile_current` clears the selection whenever the live auth file is
#     unreadable, and a cleared selection means no quota to draw. Its contents are
#     a placeholder string; nothing here authenticates against anything.
#   - a manifest whose account carries `auth_digest` equal to that file's sha256,
#     which is the "digest" match `_matching_account` treats as strong enough to
#     bind the credential to the saved slot.
#
# The invented quota numbers are the point of the exercise: the shots this
# environment exists to replace leaked the operator's real ones.
ACCOUNTS = [
    {
        "provider": "claude",
        "dir": ".claude",
        "file": ".credentials.json",
        "label": "atlas-team",
        "email": "dev@example.invalid",
        "organization": "Atlas Team",
        "session_percent": 34.0,
        "weekly_percent": 61.0,
        "session_reset_hours": 2.5,
        "weekly_reset_hours": 74.0,
    },
    {
        "provider": "codex",
        "dir": ".codex",
        "file": "auth.json",
        "label": "atlas-team",
        "email": "dev@example.invalid",
        "organization": None,
        "session_percent": 12.0,
        "weekly_percent": 28.0,
        "session_reset_hours": 4.0,
        "weekly_reset_hours": 120.0,
    },
]


def seed_accounts() -> None:
    import hashlib

    home = ROOT / "home"
    now = time.time()
    manifest: dict[str, object] = {
        "version": 2,
        "selected": {},
        "accounts": [],
        "quota": {},
        "identities": {},
    }
    selected: dict[str, str] = {}
    accounts: list[dict[str, object]] = []
    quota: dict[str, object] = {}
    for spec in ACCOUNTS:
        auth_path = home / str(spec["dir"]) / str(spec["file"])
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_path.write_text(
            json.dumps(
                {
                    "note": "synthetic capture credential; authenticates against nothing",
                    "provider": spec["provider"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        digest = hashlib.sha256(auth_path.read_bytes()).hexdigest()
        account_id = f"capture-{spec['provider']}"
        selected[str(spec["provider"])] = account_id
        accounts.append(
            {
                "id": account_id,
                "provider": spec["provider"],
                "label": spec["label"],
                "email": spec["email"],
                "organization": spec["organization"],
                "provider_account_id": None,
                "identity_source": "file",
                "auth_digest": digest,
                "created_at": now - 86_400 * 30,
                "updated_at": now,
            }
        )
        quota[account_id] = {
            "session": {
                "used_percent": spec["session_percent"],
                "window_minutes": 300,
                "resets_at": now + 3600 * float(spec["session_reset_hours"]),
            },
            "weekly": {
                "used_percent": spec["weekly_percent"],
                "window_minutes": 10080,
                "resets_at": now + 3600 * float(spec["weekly_reset_hours"]),
            },
            "status": "ready",
            "error": None,
            "refreshed_at": now - 240,
        }
    manifest["selected"] = selected
    manifest["accounts"] = accounts
    manifest["quota"] = quota
    (DATA_DIR / "provider-accounts.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


# ------------------------------------------------------------------ the fleet
def seed_fleet() -> dict[str, object]:
    projects: dict[str, str] = {}
    for project in PROJECTS:
        created = api(
            "POST",
            "/api/projects",
            {"name": project["name"], "root": str(CODE_ROOT / project["slug"])},
        )
        assert isinstance(created, dict)
        projects[project["slug"]] = str(created["id"])
    for slug, project_id in projects.items():
        # First run in a repository, the Git tab leads with a question about how
        # git should treat `.swe-mux/`. Answering it here is what keeps a
        # first-run prompt out of a screenshot of the map.
        del slug
        api(
            "POST",
            "/api/git/swe-mux-setup",
            {"project_id": project_id, "decision": "keep_visible"},
        )
    sessions: list[dict[str, str]] = []
    for slug, name in SESSIONS:
        created = api(
            "POST",
            "/api/sessions",
            {"project_id": projects[slug], "backend": "shell", "name": name},
        )
        assert isinstance(created, dict)
        api("PATCH", f"/api/sessions/{created['id']}", {"name": name})
        sessions.append({"id": str(created["id"]), "name": name, "project": slug})
    # A two-pane workspace, set as the Project's stored layout rather than driven
    # through the UI. Splitting from the UI is a drag gesture, and the command
    # behind it is a no-op for a session that is already a tab in the layout -
    # which every freshly spawned session is. `normalize_layout` still accepts the
    # v1 form, whose whole content is an ordered list of session ids, and turns it
    # into a balanced pane tree; that is the smallest thing this rig has to know
    # about a structure the app otherwise owns.
    hero = [item["id"] for item in sessions if item["project"] == "atlas-api"][:2]
    api(
        "PATCH",
        f"/api/projects/{projects['atlas-api']}",
        {"layout": {"version": 1, "panes": hero}},
    )
    note = api(
        "POST",
        f"/api/projects/{projects['atlas-api']}/notes",
        {"title": NOTE_TITLE},
    )
    assert isinstance(note, dict)
    api(
        "PUT",
        f"/api/projects/{projects['atlas-api']}/notes/{note['id']}",
        # The field is `markdown`; `body` is silently ignored and leaves an empty
        # note behind, which photographs as the editor with nothing in it.
        {"markdown": NOTE_BODY, "revision": note.get("revision")},
    )
    return {"projects": projects, "sessions": sessions, "note_id": str(note["id"])}


def seed_store(fleet: dict[str, object]) -> None:
    """Write the attention items and behavioural records straight into `mux.db`.

    Both are produced in a live install by a metered model call. Nothing here may
    spend the operator's key to make a screenshot, and a capture-only code path in
    the product would be worse than a seeded row - so the rows are written in the
    exact shape the observers write, and every surface reads them through its
    ordinary query.
    """
    import sqlite3

    projects = fleet["projects"]
    sessions = fleet["sessions"]
    assert isinstance(projects, dict) and isinstance(sessions, list)
    by_name = {str(item["name"]): item for item in sessions}
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    db = sqlite3.connect(str(DB_PATH))
    try:
        for item in ATTENTION:
            session = by_name.get(str(item["session"]))
            created = now - float(item["age"])
            db.execute(
                "INSERT OR REPLACE INTO attention_items"
                "(id,incident_key,project_id,session_id,agent_run_id,incident_class,"
                "kinds_json,title,summary,action,channel,cost_to_resolve,score,confidence,"
                "evidence_json,contributions,narration,narration_status,suppressed_reason,"
                "state,budget_day,delivered_at,resolved_at,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,NULL,'none',?,'open',?,?,NULL,?,?)",
                (
                    uuid.uuid4().hex,
                    f"capture:{item['incident_class']}:{item['session']}",
                    projects[str(item["project"])],
                    session["id"] if session else None,
                    f"capture-run-{item['project']}",
                    item["incident_class"],
                    json.dumps(sorted(item["kinds"]), separators=(",", ":")),
                    item["title"],
                    item["summary"],
                    item["action"],
                    item["channel"],
                    item["cost_to_resolve"],
                    item["score"],
                    item["confidence"],
                    json.dumps(item["evidence"], separators=(",", ":")),
                    item["suppressed_reason"],
                    day,
                    created if item["channel"] == "interrupt_now" else None,
                    created,
                    created,
                ),
            )
        insight = by_name["Rate limit the ingest route"]
        run_id = "capture-run-atlas-api"
        db.execute(
            "INSERT OR REPLACE INTO scan_timeline_runs"
            "(agent_run_id,session_id,project_id,enabled,enabled_at,disabled_at,"
            "last_scan_at,last_source_ts,updated_at) VALUES(?,?,?,1,?,NULL,?,?,?)",
            (
                run_id,
                insight["id"],
                projects["atlas-api"],
                now - 2400,
                now - 240,
                now - 240,
                now,
            ),
        )
        for index, record in enumerate(SCANS):
            t1 = now - float(record["age"])
            body = {
                key: record[key]
                for key in (
                    "behavior",
                    "work_phase",
                    "intent",
                    "claim",
                    "user_ask",
                    "blocked_on",
                    "summary",
                    "approach_status",
                    "dead_end",
                    "confidence",
                )
            }
            db.execute(
                "INSERT OR REPLACE INTO scan_timeline_records"
                "(id,session_id,agent_run_id,project_id,t0,t1,trigger,record_json,input_hash,"
                "requested_model,resolved_model,generation_id,input_tokens,output_tokens,"
                "cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"capture-scan-{index}",
                    insight["id"],
                    run_id,
                    projects["atlas-api"],
                    t1 - 180,
                    t1,
                    record["trigger"],
                    json.dumps(body, separators=(",", ":")),
                    f"capture{index:04d}",
                    "capture/observer",
                    "capture/observer",
                    f"capture-gen-{index}",
                    record["tokens"][0],
                    record["tokens"][1],
                    record["cost"],
                    t1,
                ),
            )
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------- the agent run
# One bounded, read-only run in the synthetic atlas-api checkout. Its only job is
# to leave a real harness transcript behind, which is what the Activity tab's
# Timeline segment is gated on (`hasHarnessTranscript`); the timeline *content*
# the shot shows is the seeded SCANS, re-keyed onto this run by `agent-run`.
AGENT_SESSION_NAME = "Ingest receipt contract"
AGENT_PROMPT = (
    "Read README.md, src/ingest.py, and src/limits.py, then answer in two or "
    "three sentences: what does this service do, and what is still undecided "
    "about its rate limiting? Do not modify or create any file."
)


def wait_for_session(sid: str, states: set[str], timeout: float) -> dict:
    deadline = time.time() + timeout
    record: dict = {}
    while time.time() < deadline:
        fetched = api("GET", f"/api/sessions/{sid}")
        assert isinstance(fetched, dict)
        record = fetched
        if record.get("state") in states:
            return record
        time.sleep(2)
    raise SystemExit(
        f"session {sid} did not reach {sorted(states)} within {timeout:.0f}s "
        f"(state={record.get('state')!r})"
    )


def command_agent_run() -> None:
    """Produce the one real agent run the desktop-insight shot needs.

    Requires a daemon started with `up --claude-config`. Spawns a claude session
    into atlas-api, sends one anodyne read-only prompt through the app's own
    input path, waits the turn out, and then re-keys the seeded scan-timeline
    rows onto the run so the Timeline segment has records to draw. Refuses to
    run twice: a second agent run doubles the spend for a shot that needs one.
    """
    import sqlite3

    if not STATE_PATH.exists():
        raise SystemExit("no capture daemon is recorded; run `up --claude-config` first")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if not state.get("claude_config"):
        raise SystemExit(
            "the capture daemon was started without --claude-config, so the claude CLI "
            "cannot authenticate. Run `down`, then `up --claude-config`."
        )
    if state.get("agent_session"):
        print(f"agent session already recorded: {state['agent_session']}")
        return
    projects = state["fleet"]["projects"]
    # The Timeline segment renders records only when the whole enablement chain
    # is green (`ScanTimelineTab` gates on `global_enabled && project_enabled`):
    # the install switch, a *configured* model provider (presence is what
    # `llm_readiness` checks for OpenRouter - the placeholder authenticates
    # against nothing and any scan it lets through fails with 401 at zero cost),
    # and the Project's own opt-in closure. The project config must carry
    # `version = 1` and the dependency closure explicitly, because
    # `parse_project_config` rejects a file without the version and the
    # resolver treats an absent dependency as off rather than implying it.
    api("PATCH", "/api/config", {"scan_timeline_enabled": True})
    api(
        "POST",
        "/api/automation/provider/key",
        {"operation": "set", "key": "capture-placeholder-not-a-key", "test": False},
    )
    (CODE_ROOT / "atlas-api" / ".swe-mux").mkdir(parents=True, exist_ok=True)
    (CODE_ROOT / "atlas-api" / ".swe-mux" / "config.toml").write_text(
        "version = 1\n\n[automations]\nraw_store = true\ntier0 = true\nscan_timeline = true\n",
        encoding="utf-8",
        newline="\n",
    )
    # No per-session environment. The CLI inherits the daemon's
    # CLAUDE_CONFIG_DIR (the real `~/.claude`) and authenticates from the
    # account state and credential inside it, while USERPROFILE stays the
    # synthetic home - so git identity, prompts, and every path in frame remain
    # invented. This shape requires the operator to have mirrored the account
    # fields of `~/.claude.json` into `~/.claude/.claude.json` once (the CLI
    # keeps its state *inside* the config dir when the variable is set); with
    # that file absent the CLI opens a sign-in screen over a valid credential,
    # and masking the variable with an empty string instead breaks the CLI's
    # own resolution and lands on "Not logged in" (both measured 2026-08-28).
    created = api(
        "POST",
        "/api/sessions",
        {
            "project_id": projects["atlas-api"],
            "backend": "claude",
            "name": AGENT_SESSION_NAME,
        },
    )
    assert isinstance(created, dict)
    sid = str(created["id"])
    api("PATCH", f"/api/sessions/{sid}", {"name": AGENT_SESSION_NAME})
    print(f"claude session {sid} spawned; waiting for the CLI to come up")
    wait_for_session(sid, {"idle", "awaiting", "running"}, 120)
    time.sleep(6)
    # A first run in this directory opens the CLI's trust dialog, and its
    # *default* answer is "No, exit" - a bare Enter here confirmed the exit and
    # crashed the session (measured 2026-08-28). Arrow-down selects "Yes, I
    # trust this folder"; at an already-trusted prompt the same keys are a no-op
    # in an empty composer.
    api("POST", f"/api/sessions/{sid}/input", {"data": "\x1b[B"})
    time.sleep(1)
    api("POST", f"/api/sessions/{sid}/input", {"data": "\r"})
    time.sleep(4)
    api("POST", f"/api/sessions/{sid}/input", {"data": AGENT_PROMPT})
    time.sleep(2)
    api("POST", f"/api/sessions/{sid}/input", {"data": "\r"})
    print("prompt sent; waiting for the turn to end")
    deadline = time.time() + 420
    record: dict = {}
    while time.time() < deadline:
        fetched = api("GET", f"/api/sessions/{sid}")
        assert isinstance(fetched, dict)
        record = fetched
        if record.get("last_turn_ms") is not None and record.get("state") in {
            "idle",
            "awaiting",
        }:
            break
        time.sleep(3)
    else:
        raise SystemExit(
            f"the agent turn did not complete within 420s (state={record.get('state')!r}); "
            "look at the pane before retrying - the run may be waiting on a dialog"
        )
    run_id = str(record.get("agent_run_id") or "")
    if not run_id:
        raise SystemExit("the turn ended but no agent_run_id was bound; nothing was re-keyed")
    print(f"turn ended in {record.get('last_turn_ms')}ms; re-keying seeded scans onto {run_id}")
    db = sqlite3.connect(str(DB_PATH))
    try:
        db.execute(
            "UPDATE scan_timeline_runs SET agent_run_id=?, session_id=? "
            "WHERE agent_run_id='capture-run-atlas-api'",
            (run_id, sid),
        )
        db.execute(
            "UPDATE scan_timeline_records SET agent_run_id=?, session_id=? "
            "WHERE agent_run_id='capture-run-atlas-api'",
            (run_id, sid),
        )
        db.commit()
    finally:
        db.close()
    # Any scan attempt made before the Project's opt-in landed left a terminal
    # skip reason in service memory, and the panel renders it in red over the
    # records. A scan attempt clears it on entry (`_scan` calls `_clear_skip`
    # first) and then fails harmlessly on the placeholder key, so one manual
    # scan is the reset. The 500 it answers with is expected.
    try:
        api("POST", f"/api/sessions/{sid}/scan-timeline/scan")
    except SystemExit:
        pass
    state["agent_session"] = {"id": sid, "run_id": run_id, "name": AGENT_SESSION_NAME}
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8", newline="\n")
    print("agent run recorded; shoot with: capture_site_shots.py desktop-insight.webp")


# ------------------------------------------------------------------ commands
def write_home_gitconfig() -> None:
    """Give the synthetic home a git identity, so daemon-driven git can commit.

    The seeded history sets authors per-invocation, but the land queue's
    reconcile makes a real merge commit through the daemon's own environment,
    and a home with no `.gitconfig` fails it with "Committer identity unknown"
    (measured 2026-08-28). The identity is one of the invented authors, which is
    also what keeps the operator's own name out of any commit a capture-side
    surface makes.
    """
    home = ROOT / "home"
    home.mkdir(parents=True, exist_ok=True)
    author, email = AUTHORS[0]
    (home / ".gitconfig").write_text(
        f"[user]\n\tname = {author}\n\temail = {email}\n"
        "[commit]\n\tgpgsign = false\n"
        "[core]\n\tautocrlf = false\n",
        encoding="utf-8",
        newline="\n",
    )


def command_up() -> None:
    if STATE_PATH.exists():
        raise SystemExit(f"{STATE_PATH} exists; run `down` first (or delete it if stale)")
    print(f"operator daemon on {LIVE_PORT} healthy before start: {live_daemon_ok()}")
    write_home_gitconfig()
    build_repos()
    print(f"built {len(PROJECTS)} synthetic repositories under {CODE_ROOT}")
    reset_data_dir()
    write_config()
    seed_accounts()
    pid = start_daemon()
    print(f"capture daemon {pid} ready on {BASE}")
    fleet = seed_fleet()
    seed_store(fleet)
    STATE_PATH.write_text(
        json.dumps(
            {
                "pid": pid,
                "port": PORT,
                "fleet": fleet,
                "claude_config": _claude_config_enabled,
            },
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"seeded {len(fleet['projects'])} projects, {len(SESSIONS)} sessions")
    print(f"state: {STATE_PATH}")
    print(f"operator daemon on {LIVE_PORT} healthy after start: {live_daemon_ok()}")


def command_status() -> None:
    print(f"capture root      {ROOT}")
    print(f"code root         {CODE_ROOT}")
    print(f"capture daemon    {BASE}  reachable={not port_is_free(PORT)}")
    print(f"operator daemon   http://127.0.0.1:{LIVE_PORT}  healthy={live_daemon_ok()}")
    if STATE_PATH.exists():
        print(f"state             {STATE_PATH}")


def main() -> None:
    global _claude_config_enabled
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "down", "status", "agent-run"))
    parser.add_argument(
        "--claude-config",
        action="store_true",
        help=(
            "Let the capture daemon read the operator's real CLAUDE_CONFIG_DIR so one "
            "agent run can authenticate (needed by `agent-run`). Spends real quota; "
            "everything the CLI sees stays synthetic."
        ),
    )
    args = parser.parse_args()
    _claude_config_enabled = bool(args.claude_config)
    {
        "up": command_up,
        "down": stop_daemon,
        "status": command_status,
        "agent-run": command_agent_run,
    }[args.command]()


if __name__ == "__main__":
    main()
