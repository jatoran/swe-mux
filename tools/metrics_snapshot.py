"""Daily adoption snapshot: read what the outside world will tell us, keep it forever.

    uv run python tools/metrics_snapshot.py                 # collect every source
    uv run python tools/metrics_snapshot.py --source pypi   # just one
    uv run python tools/metrics_snapshot.py --report 30     # print the last 30 days
    uv run python tools/metrics_snapshot.py --dry-run       # fetch, print, write nothing

swe-mux has no telemetry, so nothing here asks a user anything. Every number below is
either published by somebody else about their own infrastructure (PyPI, GitHub) or is a
count of requests that already arrive at this project's website. `.docs/marketing/
GTM_ROADMAP.md` § Metrics is where they are interpreted; this file only collects them.

**Why a local database rather than four dashboards.** Each source forgets, and each one
forgets differently. GitHub's traffic API is a **14-day rolling window** - a fortnight
unobserved is a fortnight gone, permanently, with no way to recover it. Cloudflare keeps
Analytics Engine data for three months. Release `download_count` is cumulative and never
tells you what a single day did. So the only way to own a year-long trend is to write the
observations down as they pass, which is all this does: one row per (day, source, metric),
upserted, so re-running is free and a missed week backfills itself from whatever window
the source still has.

**Nothing here is per-person, because nothing here can be.** The Cloudflare side reads a
counter whose entire schema is a constant label and an HTTP status (`worker/index.js`);
PyPI and GitHub publish aggregates. There is no identifier to store and no join to make,
which is a property of the sources rather than a policy applied on top of them.

**Read these as four different questions, and never add them together.**

- `cloudflare` counts requests for `version.json`, one per install per day, from installs
  that have the update check on. It is the closest thing to daily-active-installs and it
  is a *floor*: it cannot see anyone who turned the check off, and it counts a crawler as
  an install.
- `pypi` counts wheel downloads including mirrors and CI. Read `without_mirrors` and read
  the shape, not the number.
- `github_releases` counts desktop artifact downloads, which nothing automated pulls, so
  it is the cleanest of the four and measures a click rather than a run.
- `github_traffic` counts visits to the repository, which measures reach and not use.

Credentials, all read from the environment and never logged:

    CLOUDFLARE_ACCOUNT_ID          the account the Worker is deployed in
    CLOUDFLARE_ANALYTICS_TOKEN     an API token with Account Analytics: Read
    GITHUB_TOKEN                   optional; the `gh` CLI is used when available

Read-only against every source. The only thing it writes is its own database and log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

CHECKOUT_ROOT = Path(__file__).resolve().parents[1]
LOG_NAME = "metrics-snapshot.log"


def primary_checkout() -> Path:
    """The main working tree, even when this script is run from a git worktree.

    A worktree isolates the *files*, not the operator's state, and a metrics database is
    state. Defaulting to `parents[1]` would mean a run from a worktree quietly starts a
    second history in a directory that is deleted when the worktree is - and the whole
    reason this database exists is that the sources it reads cannot be asked for the past
    twice. `--git-common-dir` resolves to the primary checkout's `.git` from anywhere
    inside the repository, so every run lands in one place.

    Falls back to this file's own checkout when git cannot answer, which keeps the script
    usable from an export or a copy where being approximately right is better than
    failing.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(CHECKOUT_ROOT), "rev-parse", "--path-format=absolute",
             "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return CHECKOUT_ROOT
    if result.returncode != 0 or not result.stdout.strip():
        return CHECKOUT_ROOT
    return Path(result.stdout.strip()).resolve().parent


def default_db() -> Path:
    """`.private/` is gitignored operator state, and that is load-bearing.

    The repository is public, so a file of adoption numbers that lands in a commit is a
    published one. Putting the database anywhere tracked is how that happens by accident.
    """
    return primary_checkout() / ".private" / "metrics.sqlite"

GITHUB_REPO = "jatoran/swe-mux"
PYPI_PACKAGE = "swe-mux"
CLOUDFLARE_DATASET = "swemux_site"

# Must match `COUNTED_LABEL` in `worker/index.js`. A mismatch is a query that returns
# nothing forever while everything still serves correctly, so it is asserted by
# `tests/test_site_metrics_counter.py` rather than left to be noticed.
VERSION_CHECK_LABEL = "version-check"

HTTP_TIMEOUT = 30.0
SECONDS_PER_DAY = 86400

# Stripped from the environment of any child process whose stdout is parsed. See
# `github_api` for what they do when they survive into one.
_COLOR_FORCING_VARS = frozenset({"CLICOLOR_FORCE", "FORCE_COLOR", "GH_FORCE_TTY"})

log = logging.getLogger("metrics_snapshot")


class SourceError(RuntimeError):
    """One source could not be collected. The others still are."""


@dataclass(frozen=True)
class Observation:
    """One number, for one day, from one source.

    `day` is the day the number is *about*, not the day it was read. For a cumulative
    figure that has no per-day meaning (a release's total downloads, a star count) that
    is the day it was observed, and re-reading it later in the same day overwrites it.
    """

    day: str
    source: str
    metric: str
    value: float


# --------------------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    day         TEXT NOT NULL,
    source      TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (day, source, metric)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS observations_by_source ON observations (source, day);

CREATE TABLE IF NOT EXISTS runs (
    started_at   TEXT NOT NULL,
    finished_at  TEXT NOT NULL,
    source       TEXT NOT NULL,
    status       TEXT NOT NULL,
    rows_written INTEGER NOT NULL,
    detail       TEXT
);

CREATE INDEX IF NOT EXISTS runs_by_time ON runs (started_at);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection


def write_observations(
    connection: sqlite3.Connection, observations: list[Observation]
) -> int:
    """Upsert, so a re-run is free and a partial day is corrected rather than duplicated.

    Today's row from every source is provisional by construction: PyPI's day is still
    accruing, the Cloudflare bucket is still filling, and a cumulative count is only ever
    "as of now". Overwriting on conflict is what makes running this hourly, daily, or
    twice by accident all produce the same table.
    """
    captured_at = datetime.now(UTC).isoformat(timespec="seconds")
    with connection:
        connection.executemany(
            """
            INSERT INTO observations (day, source, metric, value, captured_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (day, source, metric) DO UPDATE SET
                value = excluded.value,
                captured_at = excluded.captured_at
            """,
            [(o.day, o.source, o.metric, o.value, captured_at) for o in observations],
        )
    return len(observations)


def record_run(
    connection: sqlite3.Connection,
    *,
    started_at: datetime,
    source: str,
    status: str,
    rows_written: int,
    detail: str | None,
) -> None:
    with connection:
        connection.execute(
            "INSERT INTO runs (started_at, finished_at, source, status, rows_written, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                started_at.isoformat(timespec="seconds"),
                datetime.now(UTC).isoformat(timespec="seconds"),
                source,
                status,
                rows_written,
                detail,
            ),
        )


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


def http_json(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """A JSON request with a timeout and an error that names the URL but never a token."""
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise SourceError(f"{url} returned HTTP {exc.code}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SourceError(f"{url} failed: {exc}") from exc


def github_api(path: str) -> Any:
    """Prefer the `gh` CLI, fall back to `GITHUB_TOKEN`, and say which was used.

    `gh` is what this machine already has authenticated, and using it means the script
    needs no credential of its own for three of the four sources. The token path exists
    so the same script runs somewhere `gh` is not installed.
    """
    if shutil.which("gh"):
        # `gh` colourizes its JSON when it is told to, and several shells on this
        # machine export `CLICOLOR_FORCE=1` / `FORCE_COLOR=3` for the benefit of
        # interactive tools. Inherited into a captured subprocess those turn a clean
        # exit into an escape sequence at byte zero and a `JSONDecodeError` that names
        # neither `gh` nor the variable responsible. Scrubbing them is the fix; parsing
        # around it would be treating the symptom.
        environment = {k: v for k, v in os.environ.items() if k not in _COLOR_FORCING_VARS}
        environment["NO_COLOR"] = "1"
        result = subprocess.run(
            ["gh", "api", path],
            capture_output=True,
            text=True,
            timeout=HTTP_TIMEOUT,
            env=environment,
        )
        if result.returncode != 0:
            raise SourceError(
                f"gh api {path} exited {result.returncode}: {result.stderr.strip()[:400]}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise SourceError(f"gh api {path} returned unparseable output: {exc}") from exc

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SourceError(
            f"cannot reach {path}: the `gh` CLI is not on PATH and GITHUB_TOKEN is unset"
        )
    return http_json(
        f"https://api.github.com/{path.lstrip('/')}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "swe-mux-metrics-snapshot",
        },
    )


# --------------------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------------------


def collect_cloudflare(days: int) -> list[Observation]:
    """Daily `version.json` request counts out of Workers Analytics Engine.

    Two details that are easy to get wrong and produce a plausible wrong number:

    **Count with `SUM(_sample_interval)`, never `count()`.** Analytics Engine samples
    once a dataset gets busy and records the sampling rate per row; summing the interval
    is what turns sampled rows back into the true total. `count()` would silently
    under-report exactly when the number started being interesting.

    **Bucket by epoch arithmetic rather than a date function.** `intDiv(toUInt32(...))`
    is the form Cloudflare's own documentation uses and is known to be supported; the
    result is a UTC day boundary, converted to a date here.
    """
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_ANALYTICS_TOKEN")
    if not account or not token:
        raise SourceError(
            "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_ANALYTICS_TOKEN must both be set "
            "(the token needs Account Analytics: Read)"
        )

    query = (
        "SELECT"
        f" intDiv(toUInt32(timestamp), {SECONDS_PER_DAY}) * {SECONDS_PER_DAY} AS day_epoch,"
        " double1 AS status,"
        " SUM(_sample_interval) AS hits"
        f" FROM {CLOUDFLARE_DATASET}"
        f" WHERE blob1 = '{VERSION_CHECK_LABEL}'"
        f" AND timestamp > NOW() - INTERVAL '{int(days)}' DAY"
        " GROUP BY day_epoch, status"
        " ORDER BY day_epoch"
    )
    payload = http_json(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/analytics_engine/sql",
        data=query.encode("utf-8"),
        headers={"Authorization": f"Bearer {token}"},
    )

    rows = payload.get("data") if isinstance(payload, dict) else None
    if rows is None:
        raise SourceError(f"unexpected Analytics Engine response: {str(payload)[:300]}")

    # Two metrics per day: the total, which is the adoption signal, and a per-status
    # breakdown, which is how a broken `version.json` restore becomes visible instead of
    # looking like healthy traffic.
    totals: dict[str, float] = {}
    observations: list[Observation] = []
    for row in rows:
        day = datetime.fromtimestamp(int(row["day_epoch"]), UTC).date().isoformat()
        status = int(float(row["status"]))
        hits = float(row["hits"])
        totals[day] = totals.get(day, 0.0) + hits
        observations.append(
            Observation(day, "cloudflare", f"version_check.status_{status}", hits)
        )
    observations.extend(
        Observation(day, "cloudflare", "version_check.hits", total)
        for day, total in totals.items()
    )
    return observations


def collect_pypi() -> list[Observation]:
    """Per-day download counts from pypistats, split by mirror.

    The split is the point. `with_mirrors` is the number that gets quoted and is close to
    meaningless; `without_mirrors` still contains every CI run that ever installed this
    package. Both are stored so the gap itself stays visible.
    """
    payload = http_json(
        f"https://pypistats.org/api/packages/{PYPI_PACKAGE}/overall",
        headers={"User-Agent": "swe-mux-metrics-snapshot"},
    )
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not rows:
        raise SourceError(f"pypistats returned no data: {str(payload)[:300]}")

    observations: list[Observation] = []
    for row in rows:
        category = str(row.get("category", "unknown"))
        day = str(row.get("date", ""))
        if not day:
            continue
        observations.append(
            Observation(day, "pypi", f"downloads.{category}", float(row.get("downloads", 0)))
        )
    return observations


def collect_github_releases(today: str) -> list[Observation]:
    """Cumulative asset download counts, stamped with the day they were read.

    GitHub only ever reports a running total, so the daily figure is the difference
    between two of these rows. That is why the observation is stored per day rather than
    overwritten in place: a single cumulative number answers no question at all, and a
    year of them answers most of them.
    """
    releases = github_api(f"repos/{GITHUB_REPO}/releases?per_page=100")
    if not isinstance(releases, list):
        raise SourceError(f"unexpected releases response: {str(releases)[:300]}")

    observations: list[Observation] = []
    grand_total = 0.0
    for release in releases:
        tag = str(release.get("tag_name", "untagged"))
        release_total = 0.0
        for asset in release.get("assets") or []:
            name = str(asset.get("name", "unnamed"))
            count = float(asset.get("download_count", 0))
            release_total += count
            observations.append(
                Observation(today, "github_releases", f"asset.{tag}.{name}", count)
            )
        observations.append(Observation(today, "github_releases", f"release.{tag}", release_total))
        grand_total += release_total

    observations.append(Observation(today, "github_releases", "all_releases.total", grand_total))
    return observations


def collect_github_traffic() -> list[Observation]:
    """Views and clones over GitHub's 14-day rolling window.

    This is the source the whole database exists for. GitHub keeps a fortnight and then
    the data is gone with no way to ask for it again, so every run backfills whatever
    part of the window is not already stored - which means missing a week costs nothing
    and missing three weeks costs exactly the days beyond the window.
    """
    observations: list[Observation] = []
    for kind, key in (("views", "views"), ("clones", "clones")):
        payload = github_api(f"repos/{GITHUB_REPO}/traffic/{kind}")
        if not isinstance(payload, dict):
            raise SourceError(f"unexpected traffic response for {kind}: {str(payload)[:300]}")
        for row in payload.get(key) or []:
            day = str(row.get("timestamp", ""))[:10]
            if not day:
                continue
            observations.append(
                Observation(day, "github_traffic", f"{kind}.count", float(row.get("count", 0)))
            )
            observations.append(
                Observation(day, "github_traffic", f"{kind}.uniques", float(row.get("uniques", 0)))
            )
    return observations


def collect_github_repo(today: str) -> list[Observation]:
    """Stars, forks, watchers, open issues.

    `GTM_ROADMAP.md` names these as vanity metrics and watches them anyway because they
    are free. They are stored here for the same reason and should be read the same way:
    a star count is not a user count, and this project's neighbours have 33k of them.
    """
    repo = github_api(f"repos/{GITHUB_REPO}")
    if not isinstance(repo, dict):
        raise SourceError(f"unexpected repo response: {str(repo)[:300]}")
    fields = {
        "stars": "stargazers_count",
        "forks": "forks_count",
        "watchers": "subscribers_count",
        "open_issues": "open_issues_count",
    }
    return [
        Observation(today, "github_repo", metric, float(repo.get(key, 0)))
        for metric, key in fields.items()
    ]


SOURCES = ("cloudflare", "pypi", "github_releases", "github_traffic", "github_repo")


def collect(source: str, *, days: int, today: str) -> list[Observation]:
    if source == "cloudflare":
        return collect_cloudflare(days)
    if source == "pypi":
        return collect_pypi()
    if source == "github_releases":
        return collect_github_releases(today)
    if source == "github_traffic":
        return collect_github_traffic()
    if source == "github_repo":
        return collect_github_repo(today)
    raise SourceError(f"unknown source: {source}")


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


def report(connection: sqlite3.Connection, days: int) -> Iterator[str]:
    """The headline series, one line per day, most recent last."""
    since = (date.today() - timedelta(days=days)).isoformat()
    headline = {
        "version checks": ("cloudflare", "version_check.hits"),
        "pypi (no mirrors)": ("pypi", "downloads.without_mirrors"),
        "repo views": ("github_traffic", "views.uniques"),
        "release downloads": ("github_releases", "all_releases.total"),
        "stars": ("github_repo", "stars"),
    }
    for label, (source, metric) in headline.items():
        rows = connection.execute(
            "SELECT day, value FROM observations"
            " WHERE source = ? AND metric = ? AND day >= ? ORDER BY day",
            (source, metric, since),
        ).fetchall()
        if not rows:
            yield f"{label:>20}: no observations since {since}"
            continue
        series = " ".join(f"{row['day'][5:]}={row['value']:g}" for row in rows[-days:])
        yield f"{label:>20}: {series}"


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def configure_logging(db_path: Path, verbose: bool) -> None:
    """Durable, rotated, and on stderr as well, because this runs both ways.

    Unattended from a scheduler the file is the only record of what happened; run by
    hand, stderr is. Five 1 MiB files is years of daily runs at this volume.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S%z"
    )
    file_handler = RotatingFileHandler(
        db_path.parent / LOG_NAME, maxBytes=1_048_576, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.handlers[:] = [file_handler, stream_handler]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=SOURCES,
        help="collect only this source; repeatable. Default: all of them.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="how far back to ask Cloudflare for daily buckets (default: 7).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="default: `.private/metrics.sqlite` in the primary checkout, from anywhere.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="fetch and print, but write nothing."
    )
    parser.add_argument(
        "--report",
        type=int,
        metavar="DAYS",
        help="print the headline series for the last DAYS days and exit without collecting.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    db_path = args.db if args.db is not None else default_db()
    configure_logging(db_path, args.verbose)
    connection = open_db(db_path)

    if args.report is not None:
        for line in report(connection, args.report):
            print(line)
        return 0

    sources = args.source or list(SOURCES)
    today = date.today().isoformat()
    failures: list[str] = []

    log.info(
        "op=snapshot.start sources=%s db=%s dry_run=%s",
        ",".join(sources),
        db_path,
        args.dry_run,
    )

    for source in sources:
        started_at = datetime.now(UTC)
        try:
            observations = collect(source, days=args.days, today=today)
        except SourceError as exc:
            # One source failing must not cost the others. A missing Cloudflare token is
            # the common case and should still leave a full GitHub and PyPI snapshot.
            failures.append(source)
            log.error("op=source.failed source=%s detail=%s", source, exc)
            if not args.dry_run:
                record_run(
                    connection,
                    started_at=started_at,
                    source=source,
                    status="failed",
                    rows_written=0,
                    detail=str(exc)[:1000],
                )
            continue

        if args.dry_run:
            for observation in observations:
                print(
                    f"{observation.day} {observation.source} "
                    f"{observation.metric} {observation.value:g}"
                )
            log.info("op=source.dry_run source=%s observations=%d", source, len(observations))
            continue

        written = write_observations(connection, observations)
        record_run(
            connection,
            started_at=started_at,
            source=source,
            status="ok",
            rows_written=written,
            detail=None,
        )
        log.info("op=source.ok source=%s observations=%d", source, written)

    log.info(
        "op=snapshot.finish sources=%d failed=%d", len(sources), len(failures)
    )
    if failures:
        log.error("op=snapshot.incomplete failed_sources=%s", ",".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
