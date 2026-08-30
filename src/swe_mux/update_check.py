"""Telling the operator that a newer swe-mux release exists. Nothing more.

This is **detection and presentation only**: nothing here downloads an artifact,
verifies a hash, or stages a swap. The frozen-app updater that does those things
is a separate item and reuses the redeploy machinery (`ROADMAP.md` Phase 11,
"Update propagation").

Three properties are load-bearing, and each one is a constraint rather than a
preference.

**This is the only request swe-mux makes on its own behalf.** The README and
`SECURITY.md` both say the project has no telemetry, so this fetch is the single
documented exception and it has to stay one: no query string, no custom header,
no install id, no cookie jar, and no request body - a `GET` of a static file that
is byte-identical for every install on earth. `update_check_enabled` turns it off
entirely, and off means *nothing leaves the machine*, which
`tests/test_update_check.py` proves by counting fetches rather than by reading
this paragraph.

**A restart loop must not become a request loop.** The interval gate is keyed on
a timestamp persisted in `<data_dir>/update-check.json`, so a daemon that
restarts fifty times in an hour still makes at most one request. A wall-clock
timestamp is what survives the process, and it is deliberately treated as
untrustworthy in one direction: a record dated in the future (a clock that was
wound back) is due immediately rather than suppressing the check until the clock
catches up.

**Every failure is a non-event.** Offline, DNS, 404, HTML where JSON was
expected, a `schema` this build has never heard of - all of them land as a logged
status and an answer of "unknown". Nothing here raises into a request path, and
nothing here runs on the startup path: the loop is supervised by
`background_tasks` and takes its first look a minute after the daemon is up.

The manifest is a **published contract** written by `.github/workflows/release.yml`
and served at `https://swemux.dev/version.json`:

```json
{"schema": 1, "version": "0.1.0", "tag": "v0.1.0",
 "published": "2026-08-27T00:00:00Z",
 "changelog": "https://github.com/jatoran/swe-mux/releases/tag/v0.1.0",
 "artifacts": [{"name": "...", "url": "...", "sha256": "..."}]}
```

`schema` is the version of that contract, and an unrecognized value is answered
with "cannot tell" rather than with a best guess - a build installed today will
still be reading this file in three years, and it cannot be asked to change
first.

`artifacts` is parsed here and used by `update_install.py`, but it is
deliberately **not persisted** in `<data_dir>/update-check.json` and not served
by `GET /api/update`. A hash is only worth anything at the moment it is checked
against bytes: the release workflow uploads with `--clobber`, so a day-old cached
hash is a claim about a file that may since have been replaced. The updater
therefore re-fetches the manifest at install time and reads the artifacts from
*that* response, and this module keeps only the four things a banner needs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import aiohttp

from . import __version__
from .background_tasks import background
from .config import Config
from .tls_trust import trusting_connector

log = logging.getLogger(__name__)

UPDATE_CHECK_LOOP = "update-check"

#: The published endpoint. `release.yml` documents the path as an interface and
#: it never moves; a build years old has this string compiled into it.
MANIFEST_URL = "https://swemux.dev/version.json"

#: The fallback, used only when the manifest did not produce an answer. GitHub's
#: unauthenticated limit is 60 requests an hour per address, which is ample for a
#: check that runs once a day, and `releases/latest` is a stable contract that
#: predates this project by a decade.
#:
#: The repository is the one the release workflow publishes to, so this constant
#: and `release.yml`'s `github.repository` must name the same thing: the manifest
#: this file exists to stand in for is written from that repository's own
#: artifacts, and a fallback pointed elsewhere would answer with a different
#: project's version rather than failing. While the repository is empty this
#: resolves to a 404, which is exactly the "unknown" the module degrades to.
GITHUB_REPOSITORY = "jatoran/swe-mux"
GITHUB_RELEASES_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"

#: The manifest schema this build understands. Anything else is "cannot tell".
SUPPORTED_SCHEMA = 1

#: Daily, as the roadmap specifies. Enforced against a persisted timestamp, not
#: against process uptime.
DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60

#: How long after start the loop takes its first look. The interval gate already
#: makes a restart cheap; this is what keeps a start from being *accompanied* by
#: an outbound request, which matters on a laptop whose network comes up after
#: the daemon does.
INITIAL_DELAY_SECONDS = 60.0

#: How often the loop wakes to ask whether the interval has elapsed. Far shorter
#: than the interval so that a daemon which has been up for days still checks on
#: the day boundary rather than on its own start anniversary.
WAKE_SECONDS = 30 * 60.0

#: Bounded, because this runs behind a supervised loop on a daemon that stays up
#: for weeks and a hung socket would otherwise pin a task forever.
REQUEST_TIMEOUT_SECONDS = 10.0

#: A version manifest is a few hundred bytes; a GitHub release payload is a few
#: kilobytes. Anything past this is not the document we asked for, and reading it
#: into memory is the only thing an unfriendly server could make us do.
MAX_RESPONSE_BYTES = 256 * 1024

#: The state file's own schema, independent of the manifest's.
STATE_SCHEMA = 1
STATE_FILENAME = "update-check.json"

#: How many dismissed versions are remembered. A dismissal is a decision about
#: one release, and the only ones that matter are recent; the bound stops a
#: long-lived install accumulating a list forever.
MAX_DISMISSED = 20


# --- version comparison ------------------------------------------------------
#
# Pure, and separated from everything that touches the network or the disk,
# because this is the part that will be wrong if anything is. A string compare
# puts `0.10.0` below `0.9.0` and calls `0.1.0` newer than itself, and both
# mistakes are invisible until the day they matter.

#: PEP 440's public-version grammar, minus the parts a release tag never uses.
#: Epoch and local segments are accepted so a version carrying one is understood
#: rather than refused; `post` and `dev` are accepted because `uv build` emits
#: them for anything built off a tag.
#: Every optional segment captures its own *label* as well as its number. A
#: number alone cannot answer the question the parser has to answer, because
#: `1.0.post` is `post0` while `1.0` has no post-release at all, and both leave
#: the number group unset. Longer alternatives are listed first so the match does
#: not depend on backtracking to prefer `alpha` over `a`.
_VERSION = re.compile(
    r"""
    ^\s*v?
    (?:(?P<epoch>\d+)!)?
    (?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre_label>alpha|a|beta|b|preview|pre|rc|c)[-_.]?(?P<pre_n>\d+)?)?
    (?:-(?P<post_implicit>\d+)|[-_.]?(?P<post_label>post|rev|r)[-_.]?(?P<post_n>\d+)?)?
    (?:[-_.]?(?P<dev_label>dev)[-_.]?(?P<dev_n>\d+)?)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: `a < b < rc < (final)`. The aliases exist because a human writing a tag by
#: hand writes `alpha` about as often as `a`.
_PRE_RANK = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}

#: Sorts below every real prerelease label, for a `.devN` with no prerelease at
#: all: PEP 440 puts `1.0.dev1` below `1.0a1`.
_PRE_DEV_ONLY = (-1, 0)
#: Sorts above every prerelease label, for a final release.
_PRE_FINAL = (len(_PRE_RANK) + 1, 0)


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed public version, and the tuple that orders it.

    `text` is kept so a caller can render exactly what it was handed rather than
    a normalized form the operator has never seen.
    """

    text: str
    epoch: int
    release: tuple[int, ...]
    pre: tuple[int, int] | None
    post: int | None
    dev: int | None

    @property
    def sort_key(self) -> tuple[Any, ...]:
        """A total order over parsed versions, following PEP 440.

        Trailing zeros are stripped from the release so `1.0` and `1.0.0` are one
        version rather than two that differ by a digit nobody typed. The three
        trailing components each encode "absent" as the value that puts it on the
        correct side: a prerelease sorts *below* its release, a post-release
        *above* it, and a dev release below whatever it is a dev of.
        """
        release = _trim_release(self.release)
        if self.pre is not None:
            pre = self.pre
        elif self.dev is not None and self.post is None:
            pre = _PRE_DEV_ONLY
        else:
            pre = _PRE_FINAL
        post = (1, self.post) if self.post is not None else (0, 0)
        dev = (0, self.dev) if self.dev is not None else (1, 0)
        return (self.epoch, release, pre, post, dev)


def _trim_release(release: tuple[int, ...]) -> tuple[int, ...]:
    trimmed = list(release)
    while len(trimmed) > 1 and trimmed[-1] == 0:
        trimmed.pop()
    return tuple(trimmed)


def parse_version(text: object) -> Version | None:
    """Parse a public version string, or `None` when it is not one.

    `None` is a real answer and means "cannot tell", never "older": every caller
    has to decide what to do about an unreadable version, and returning a
    sentinel that compares would decide it for them - wrongly, and silently.
    """
    if not isinstance(text, str):
        return None
    match = _VERSION.match(text)
    if match is None:
        return None
    release = tuple(int(part) for part in match.group("release").split("."))
    pre_label = match.group("pre_label")
    pre: tuple[int, int] | None = None
    if pre_label is not None:
        # `1.0rc` with no number is `1.0rc0`, per PEP 440's implicit zero.
        pre = (_PRE_RANK[pre_label.lower()], int(match.group("pre_n") or 0))
    post_implicit = match.group("post_implicit")
    post: int | None = None
    if post_implicit is not None:
        post = int(post_implicit)
    elif match.group("post_label") is not None:
        post = int(match.group("post_n") or 0)
    dev = int(match.group("dev_n") or 0) if match.group("dev_label") is not None else None
    return Version(
        text=text.strip(),
        epoch=int(match.group("epoch") or 0),
        release=release,
        pre=pre,
        post=post,
        dev=dev,
    )


def compare_versions(left: str, right: str) -> int | None:
    """`-1`, `0`, `1`, or `None` when either side is not a version."""
    parsed_left = parse_version(left)
    parsed_right = parse_version(right)
    if parsed_left is None or parsed_right is None:
        return None
    if parsed_left.sort_key < parsed_right.sort_key:
        return -1
    return 1 if parsed_left.sort_key > parsed_right.sort_key else 0


def is_newer(candidate: str, current: str) -> bool | None:
    """Whether `candidate` is a later release than `current`.

    `None` means the question could not be answered, and the caller must render
    that as "unknown" rather than folding it into `False` - a build whose own
    version does not parse is a build that must stop claiming it is current.
    """
    order = compare_versions(candidate, current)
    return None if order is None else order > 0


# --- the manifest ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Artifact:
    """One downloadable file named by the manifest.

    `sha256` may be empty, and that is a real state rather than an oversight: it
    is what an artifact list assembled from anywhere other than the manifest
    looks like. An empty hash is never treated as "skip the check" - it is what
    makes an artifact un-installable (`update_install.py`).
    """

    name: str
    url: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "url": self.url, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class Release:
    """What a check found, reduced to the four things a banner needs.

    `artifacts` rides along for the updater and is excluded from `as_dict` on
    purpose: that projection is what gets persisted and served, and a stored hash
    is a claim about bytes nobody is holding.
    """

    version: str
    tag: str
    published: str
    changelog: str
    #: `manifest` or `github`, so the surface can say which source answered.
    source: str
    artifacts: tuple[Artifact, ...] = ()

    def as_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "tag": self.tag,
            "published": self.published,
            "changelog": self.changelog,
            "source": self.source,
        }


#: Every non-success outcome, as the exact word the state file and the API
#: report. Kept as a closed set so a surface can branch on it and a new failure
#: mode has to be named rather than silently rendering as one of these.
UNREACHABLE = "unreachable"
MALFORMED = "malformed"
UNSUPPORTED_SCHEMA = "unsupported_schema"
INCOMPARABLE = "incomparable"
DISABLED = "disabled"
NEVER_CHECKED = "never_checked"
OK = "ok"


def parse_manifest(payload: object) -> tuple[Release | None, str]:
    """`(release, reason)` for a decoded `version.json` body.

    The schema is checked **before** any field is read. A future manifest may
    repurpose `version` or drop `tag`, so a build that reads them anyway is not
    being lenient - it is guessing, and it would show the operator a confident
    wrong answer rather than the honest "cannot tell" this returns.
    """
    if not isinstance(payload, dict):
        return None, MALFORMED
    # An absent `schema` lands here too, and deliberately: the contract has always
    # carried one, so its absence means this is not the document this parser was
    # written against - which is the same fact as a schema from the future.
    if payload.get("schema") != SUPPORTED_SCHEMA:
        return None, UNSUPPORTED_SCHEMA
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return None, MALFORMED
    tag = payload.get("tag")
    changelog = payload.get("changelog")
    published = payload.get("published")
    return (
        Release(
            version=version.strip(),
            tag=tag.strip() if isinstance(tag, str) else f"v{version.strip()}",
            published=published.strip() if isinstance(published, str) else "",
            changelog=changelog.strip() if isinstance(changelog, str) else "",
            source="manifest",
            artifacts=parse_artifacts(payload.get("artifacts")),
        ),
        OK,
    )


def parse_artifacts(payload: object) -> tuple[Artifact, ...]:
    """The manifest's `artifacts` list, keeping only fully-described entries.

    Called only from inside the schema gate above, so it never has to decide
    whether it is reading a document it understands.

    An entry missing any of its three fields is dropped rather than kept with a
    blank hash. The distinction matters at the other end: "this release names no
    installable artifact for your platform" is a fact an operator can act on,
    while a half-described entry that reaches the downloader would have to be
    refused there anyway, one layer further from where the manifest was read.
    """
    if not isinstance(payload, list):
        return ()
    artifacts: list[Artifact] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        url = entry.get("url")
        digest = entry.get("sha256")
        if not (isinstance(name, str) and isinstance(url, str) and isinstance(digest, str)):
            continue
        if not (name.strip() and url.strip() and digest.strip()):
            continue
        artifacts.append(
            Artifact(name=name.strip(), url=url.strip(), sha256=digest.strip().lower())
        )
    return tuple(artifacts)


def parse_github_release(payload: object) -> tuple[Release | None, str]:
    """The same reduction over GitHub's `releases/latest` body.

    `releases/latest` already excludes drafts and prereleases, so the two flags
    are checked rather than trusted: a payload that carries `draft: true` is not
    the endpoint we think we called, and answering "unknown" is better than
    announcing a release that is not published.

    It yields **no artifacts**, and that is the point rather than a gap. GitHub's
    release payload lists assets with download URLs and no digests, so a release
    found this way can be announced but can never be installed: the updater's
    first rule is that nothing is staged without a hash from the manifest, and
    inventing an artifact list here would put an unverifiable download one
    refusal away from the swap.
    """
    if not isinstance(payload, dict):
        return None, MALFORMED
    if payload.get("draft") is True:
        return None, MALFORMED
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return None, MALFORMED
    version = tag.strip().lstrip("vV")
    if not version:
        return None, MALFORMED
    html_url = payload.get("html_url")
    published = payload.get("published_at")
    return (
        Release(
            version=version,
            tag=tag.strip(),
            published=published.strip() if isinstance(published, str) else "",
            changelog=html_url.strip() if isinstance(html_url, str) else "",
            source="github",
        ),
        OK,
    )


# --- the fetcher -------------------------------------------------------------


class Fetcher(Protocol):
    """One bounded GET. Injected so a test can prove nothing was fetched.

    Returns `(status, body)`. Raising is a legitimate outcome and means
    "unreachable"; the caller catches everything.
    """

    async def __call__(
        self, url: str, *, headers: Mapping[str, str] | None = None
    ) -> tuple[int, bytes]: ...


async def http_fetch(url: str, *, headers: Mapping[str, str] | None = None) -> tuple[int, bytes]:
    """The real fetcher: a plain bounded GET carrying nothing about this install.

    A `DummyCookieJar` rather than the default, which is not a detail: aiohttp's
    normal jar would accept a `Set-Cookie` from the site and send it back on the
    next day's check, which is an install identifier by any other name. This one
    stores nothing and sends nothing, so the property holds by construction
    instead of by the site's good manners.
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(
        timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(), connector=trusting_connector()
    ) as session:
        async with session.get(url, headers=dict(headers or {}), allow_redirects=True) as response:
            body = await response.content.read(MAX_RESPONSE_BYTES + 1)
            return response.status, body[:MAX_RESPONSE_BYTES]


# --- the service -------------------------------------------------------------


@dataclass(slots=True)
class _State:
    """What survives a restart. Deliberately small and human-readable."""

    last_checked: float | None = None
    status: str = NEVER_CHECKED
    release: Release | None = None
    dismissed: tuple[str, ...] = ()


class UpdateChecker:
    """Owns the interval, the state file, the fetch, and the answer.

    Constructed with everything it depends on so that the whole of it is
    testable without a network or a clock: `fetch` counts requests, `clock`
    drives the interval, and `interval_seconds` shortens a day to a millisecond.
    """

    def __init__(
        self,
        config: Config,
        *,
        current_version: str = __version__,
        manifest_url: str = MANIFEST_URL,
        github_url: str = GITHUB_RELEASES_URL,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        initial_delay_seconds: float = INITIAL_DELAY_SECONDS,
        wake_seconds: float = WAKE_SECONDS,
        fetch: Fetcher | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._current_version = current_version
        self._manifest_url = manifest_url
        self._github_url = github_url
        self._interval = float(interval_seconds)
        self._initial_delay = float(initial_delay_seconds)
        self._wake = float(wake_seconds)
        self._fetch: Fetcher = fetch if fetch is not None else http_fetch
        self._clock = clock
        self._state = _State()
        self._loaded = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        # Said once at start rather than at each tick: a daemon that runs for
        # weeks must not repeat "the update check is off" 300 times, and the one
        # moment this is worth reading is when someone is looking at why no
        # banner ever appears - or at what this process talks to.
        log.info(
            "update check loop started",
            extra={
                "update_enabled": self.enabled,
                "update_manifest_url": self._manifest_url if self.enabled else "",
                "update_interval_seconds": self._interval,
            },
        )
        self._task = background.start(UPDATE_CHECK_LOOP, self._run)

    async def stop(self) -> None:
        await background.stop(UPDATE_CHECK_LOOP)
        self._task = None

    async def _run(self) -> None:
        """Wake periodically and check when the persisted interval has elapsed.

        The sleeps sit outside `background.iteration` for the reason
        `background_tasks` documents: timing them would report this loop's idle
        day as its own cost and rank it above everything that actually works.
        """
        await asyncio.sleep(self._initial_delay)
        # unsupervised-loop-ok: supervised by `background.start(UPDATE_CHECK_LOOP, ...)`.
        while True:
            with background.iteration(UPDATE_CHECK_LOOP):
                await self.check()
            await asyncio.sleep(self._wake)

    # -- state ----------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return Path(self._config.data_dir) / STATE_FILENAME

    @property
    def enabled(self) -> bool:
        """Read live off the shared `Config`, so a toggle takes effect at once.

        `runtime_config` mutates the one `Config` object in place rather than
        replacing it, which is what makes this a property and not a constructor
        argument: a value captured at construction would keep checking for a day
        after the operator turned it off.
        """
        return bool(getattr(self._config, "update_check_enabled", True))

    async def ensure_loaded(self) -> None:
        """Read the state file once, off the event loop. Never raises."""
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            self._state = await asyncio.to_thread(self._read_state)
            self._loaded = True

    def _read_state(self) -> _State:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return _State()
        except (OSError, ValueError) as exc:
            # A corrupt file loses the dismissals it held, which is worth saying
            # out loud: the operator will see a banner they had already declined,
            # and "the app forgot" is a worse mystery than one log line.
            log.warning(
                "update check state unreadable; starting from empty",
                extra={"update_state_path": str(self._path), "error_type": type(exc).__name__},
            )
            return _State()
        if not isinstance(payload, dict) or payload.get("schema") != STATE_SCHEMA:
            log.info(
                "update check state has an unrecognized schema; starting from empty",
                extra={"update_state_path": str(self._path)},
            )
            return _State()
        release_payload = payload.get("latest")
        release: Release | None = None
        if isinstance(release_payload, dict) and isinstance(release_payload.get("version"), str):
            release = Release(
                version=str(release_payload.get("version", "")),
                tag=str(release_payload.get("tag", "")),
                published=str(release_payload.get("published", "")),
                changelog=str(release_payload.get("changelog", "")),
                source=str(release_payload.get("source", "manifest")),
            )
        checked = payload.get("last_checked")
        dismissed = payload.get("dismissed")
        return _State(
            last_checked=float(checked) if isinstance(checked, (int, float)) else None,
            status=str(payload.get("status", NEVER_CHECKED)),
            release=release,
            dismissed=tuple(
                str(item) for item in dismissed[:MAX_DISMISSED] if isinstance(item, str)
            )
            if isinstance(dismissed, list)
            else (),
        )

    def _write_state(self) -> None:
        """Atomic, because a half-written file reads as a lost dismissal.

        Failure is swallowed on purpose: the worst consequence of an unwritable
        state file is an extra check tomorrow, and this runs on a path that must
        never turn a housekeeping problem into an error the operator sees.
        """
        payload = {
            "schema": STATE_SCHEMA,
            "last_checked": self._state.last_checked,
            "status": self._state.status,
            "latest": self._state.release.as_dict() if self._state.release else None,
            "dismissed": list(self._state.dismissed),
        }
        path = self._path
        temp = path.with_name(f"{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, path)
        except OSError as exc:
            log.warning(
                "could not persist update check state",
                extra={"update_state_path": str(path), "error_type": type(exc).__name__},
            )

    # -- the check ------------------------------------------------------------

    def _due(self) -> bool:
        """Whether the interval has elapsed since the last recorded check.

        A record dated in the future is treated as due rather than as a very
        recent check. That is the safe direction under a clock that moved: the
        cost of being wrong here is one extra request, while the other reading
        would silently stop checking until wall-clock time caught up with a
        timestamp that may be years out.
        """
        last = self._state.last_checked
        if last is None:
            return True
        now = float(self._clock())
        return now < last or now - last >= self._interval

    async def check(self, *, force: bool = False) -> dict[str, Any]:
        """Run a check if one is due, and return the resulting snapshot.

        Never raises and never blocks a request path for longer than the bounded
        fetch. `force` skips only the interval, never the enablement switch: a
        disabled check makes no request under any caller.
        """
        await self.ensure_loaded()
        if not self.enabled:
            return self.snapshot()
        if not force and not self._due():
            return self.snapshot()
        async with self._lock:
            # Re-checked under the lock so two callers arriving together (the
            # loop and a manual press) make one request rather than two.
            if not self.enabled:
                return self.snapshot()
            if not force and not self._due():
                return self.snapshot()
            release, status = await self._resolve()
            self._state.last_checked = float(self._clock())
            self._state.status = status
            if release is not None:
                self._state.release = release
            await asyncio.to_thread(self._write_state)
        log.info(
            "update check complete",
            extra={
                "update_status": status,
                "update_latest": release.version if release else "",
                "update_source": release.source if release else "",
                "update_current": self._current_version,
            },
        )
        return self.snapshot()

    async def _resolve(self) -> tuple[Release | None, str]:
        """The manifest, then GitHub, then "unknown". Never worse than unknown.

        The fallback runs on *any* non-`ok` manifest outcome, not only on an
        unreachable one: a manifest this build cannot read and a manifest that is
        not there are the same fact from here, and GitHub's release list answers
        the question either way. A fallback that also fails leaves the manifest's
        own reason in place, because that is the one the operator can act on.
        """
        release, reason = await self._fetch_json(self._manifest_url, parse_manifest)
        if release is not None:
            return release, OK
        fallback, fallback_reason = await self._fetch_json(
            self._github_url,
            parse_github_release,
            # Content negotiation, not identification: this pins the response
            # shape `parse_github_release` was written against. It carries
            # nothing about this machine, this install, or this operator.
            headers={"Accept": "application/vnd.github+json"},
        )
        if fallback is not None:
            return fallback, OK
        log.info(
            "update check found no answer",
            extra={"update_manifest_reason": reason, "update_fallback_reason": fallback_reason},
        )
        return None, reason

    async def _fetch_json(
        self,
        url: str,
        parse: Any,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[Release | None, str]:
        """One bounded GET plus one parse, with every failure named, none raised."""
        try:
            status, body = await self._fetch(url, headers=headers)
        except Exception as exc:  # noqa: BLE001 - offline is normal, not exceptional
            log.debug(
                "update check request failed",
                extra={"update_url": url, "error_type": type(exc).__name__},
            )
            return None, UNREACHABLE
        if status != 200:
            log.debug(
                "update check request refused",
                extra={"update_url": url, "http_status": status},
            )
            return None, UNREACHABLE
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            # An HTML error page from a captive portal or a misconfigured host
            # arrives here, which is why this is `malformed` and not a crash.
            log.debug("update check response was not JSON", extra={"update_url": url})
            return None, MALFORMED
        release, reason = parse(payload)
        if release is None:
            log.debug(
                "update check response was not usable",
                extra={"update_url": url, "update_reason": reason},
            )
        return release, reason

    # -- dismissal ------------------------------------------------------------

    async def dismiss(self, version: str) -> dict[str, Any]:
        """Record that this exact version was declined, and keep it declined.

        Per version rather than per banner: dismissing `0.2.0` must not hide
        `0.3.0` a month later, which is the whole difference between a dismissal
        and turning the feature off. Stored beside the daemon's state rather than
        in a browser, because the install is what gets updated - declining on the
        desktop and being nagged again from the phone would be the same decision
        asked twice.
        """
        await self.ensure_loaded()
        candidate = str(version).strip()
        if not candidate:
            return self.snapshot()
        async with self._lock:
            if candidate not in self._state.dismissed:
                self._state.dismissed = (candidate, *self._state.dismissed)[:MAX_DISMISSED]
                await asyncio.to_thread(self._write_state)
        log.info("update dismissed", extra={"update_version": candidate})
        return self.snapshot()

    # -- the answer -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The whole surface, computed from state alone. No I/O, never raises.

        `banner` is derived here rather than in the browser so that the rule -
        "a newer version exists, and this one has not been declined" - has one
        implementation across desktop, phone, and any future client.
        """
        state = self._state
        latest = state.release
        newer = (
            is_newer(latest.version, self._current_version) if latest is not None else None
        )
        if not self.enabled:
            status = DISABLED
        elif state.last_checked is None:
            status = NEVER_CHECKED
        elif state.status == OK and latest is not None and newer is None:
            # The check itself succeeded; the comparison is what failed.
            status = INCOMPARABLE
        else:
            status = state.status
        available = bool(newer)
        return {
            "enabled": self.enabled,
            "current_version": self._current_version,
            "status": status,
            "checked_at": state.last_checked,
            "next_check_at": (
                state.last_checked + self._interval if state.last_checked is not None else None
            ),
            "interval_seconds": self._interval,
            "update_available": available,
            "latest": latest.as_dict() if latest is not None else None,
            "dismissed": list(state.dismissed),
            "banner": available and latest is not None and latest.version not in state.dismissed,
            "manifest_url": self._manifest_url,
        }
