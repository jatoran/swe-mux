"""The degraded `mux doctor` report that runs when no daemon answers.

`doctor.build_doctor_report` assembles the full report out of payloads the daemon
produced, so it presupposes exactly the thing a broken install does not have. That
made the one diagnostic command the project ships useless for the single most
likely new-user failure - the daemon not starting - which answered a connection
error and nothing else.

This module is the other half: the checks answerable from **this machine alone**,
run when the daemon is unreachable. It is a fallback, never a substitute. The
daemon report is untouched and byte-compatible when a daemon answers; nothing here
runs on that path.

Three rules the design turns on.

**"Could not check" is its own answer.** The status vocabulary gains ``unchecked``
beside ``ok``/``warn``/``fail``/``unavailable``. Folding a check that never ran
into ``ok`` claims health nobody proved, and folding it into ``unavailable`` claims
a capability was measured absent when it was not measured at all - either one turns
a degraded report into a confident wrong one, which is worse than the connection
error it replaced. Every check the local report does not run is emitted as an
``unchecked`` row naming what is unknown *and why*, and the summary counts them
separately.

**One implementation per check.** Prerequisites and harness detection are pure host
probes with no daemon state in them, so this module calls the daemon's own
detection functions and its own row builders (`doctor._prerequisite_checks`,
`doctor._harness_checks`) rather than growing a second, drifting copy. What is
*not* re-answered here is anything that reads daemon runtime state.

**The checks are the ones that stop a daemon starting**, chosen from what actually
breaks on a fresh install rather than from what is easy to probe. Each carries its
justification at its own definition below; the shared thread is that every one of
them is a way `muxd` fails before it can serve a single request, which is precisely
the window in which this report is the only report there is.
"""

from __future__ import annotations

import importlib
import importlib.util
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .doctor import DOCTOR_REPORT_VERSION, _check, _harness_checks, _prerequisite_checks
from .host_platform import IS_WINDOWS, platform_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .config import Config

# `requires-python` in `pyproject.toml`, restated because a wheel does not carry
# that file and this check has to work in an installed copy. `test_doctor_local`
# reconciles the two so the restatement cannot drift.
MINIMUM_PYTHON = (3, 12)

# Optional extras, their import probes, and how to install them. Probed with
# `find_spec` rather than a real import: `onnxruntime` and `playwright` cost
# seconds to import and the question is only whether they resolve.
#
# `voice-edge` is deliberately absent. It is source-install convenience only - the
# runtime reaches Edge TTS through an externally managed bridge interpreter - so
# whether `edge_tts` resolves *in this environment* says nothing about whether the
# feature works, and a row asserting otherwise would be a confident wrong answer.
_EXTRAS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "desktop",
        "Desktop shell (tray + WebView)",
        ("pystray", "webview"),
        "uv sync --extra desktop",
    ),
    (
        "voice-local",
        "On-device speech (Kokoro TTS + Whisper dictation)",
        ("faster_whisper", "onnxruntime", "misaki"),
        "uv sync --extra voice-local",
    ),
    (
        "preview-capture",
        "Preview screenshot capture",
        ("playwright",),
        "",  # filled from preview_capture.INSTALL_HINT, which owns the two-step form
    ),
)

# What the local report does not answer, and why. Two distinct reasons live here
# and the detail says which, because they are not the same fact: some of these
# read daemon runtime state that exists nowhere else, and some are simply not the
# question while nothing is listening. Collapsing them into one "unavailable" is
# the bug this whole module exists to avoid.
#
# Every category `doctor.build_doctor_report` can emit is either covered by a real
# local check or listed here; `test_doctor_local` asserts that over the categories
# the remote builder actually produces, so a new remote category fails the gate
# instead of quietly going unmentioned.
_DAEMON_ONLY: tuple[tuple[str, str, str, str], ...] = (
    (
        "daemon",
        "Daemon health and PTY supervisor",
        "Needs a running daemon: whether it is healthy, which UI build it serves, "
        "whether the PTY supervisor is attached, and whether any supervised session "
        "is unadopted are all facts about a live process.",
        "",
    ),
    (
        "status",
        "Fleet status health",
        "Needs a running daemon: identity collisions, blind screen classifiers, and "
        "stuck sessions are computed over live sessions.",
        "",
    ),
    (
        "background",
        "Background loops",
        "Needs a running daemon: loop liveness and fault counts are per-boot state "
        "held in the daemon process.",
        "",
    ),
    (
        "freshness",
        "Observation freshness",
        "Needs a running daemon: whether an agent session is reporting a dead or "
        "relocated conversation is a property of live sessions.",
        "",
    ),
    (
        "remote",
        "Tailscale and remote access",
        "Not run in the local report: whether a phone can reach this machine is not "
        "the question while nothing is listening on it. Re-run once the daemon "
        "starts.",
        "",
    ),
    (
        "firewall",
        "Host firewall",
        "Not run in the local report: an inbound rule only matters once there is a "
        "socket behind it. Re-run once the daemon starts.",
        "",
    ),
    (
        "wsl",
        "WSL agent bridge",
        "Not run in the local report: inspecting a distribution starts it, which a "
        "diagnostic must not do on a host that has not opted into the bridge.",
        "",
    ),
)


def _unchecked_rows() -> list[dict[str, Any]]:
    return [
        _check(
            id=f"{category}.unchecked",
            category=category,
            title=title,
            status="unchecked",
            severity="info",
            detail=detail,
            remedy=remedy or None,
        )
        for category, title, detail, remedy in _DAEMON_ONLY
    ]


# --------------------------------------------------------------------------- #
# Local checks
# --------------------------------------------------------------------------- #


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _source_checkout_root() -> Path | None:
    """The repository root when this package is imported from a source checkout.

    ``src/swe_mux/doctor_local.py`` -> ``src/swe_mux`` -> ``src`` -> the root. The
    distinction matters for exactly one check: a missing frontend bundle is a
    one-command fix in a checkout (`npm run build`, whose output is gitignored, so
    a fresh clone legitimately has none) and a broken artifact in an installed
    copy, and telling a wheel user to run a build they have no `frontend/` for
    would send them nowhere.
    """
    root = _package_root().parent.parent
    return root if (root / "frontend" / "package.json").is_file() else None


def _python_check() -> dict[str, Any]:
    """Python version and host identity.

    First because it is the failure that produces the least legible error: an
    interpreter below the floor fails at install time with a resolver message, or
    at import time on syntax this codebase uses freely, and neither names the
    version as the cause.
    """
    running = sys.version_info[:3]
    frozen = bool(getattr(sys, "frozen", False))
    where = f"{platform_key()} ({sys.platform})"
    version = ".".join(str(part) for part in running)
    if frozen:
        # A frozen build carries its own interpreter, so the floor cannot be
        # violated and "install a newer Python" would be advice about a Python the
        # user does not control.
        return _check(
            id="install.python",
            category="install",
            title="Python runtime",
            status="ok",
            severity="info",
            detail=f"Bundled Python {version} on {where} (frozen desktop app).",
        )
    if running[:2] < MINIMUM_PYTHON:
        floor = ".".join(str(part) for part in MINIMUM_PYTHON)
        return _check(
            id="install.python",
            category="install",
            title="Python runtime",
            status="fail",
            severity="critical",
            detail=f"Python {version} on {where} is below the {floor} floor swe-mux "
            "requires; the daemon cannot start on it.",
            remedy=f"Install Python {floor} or newer and reinstall swe-mux into it.",
        )
    return _check(
        id="install.python",
        category="install",
        title="Python runtime",
        status="ok",
        severity="info",
        detail=f"Python {version} on {where}.",
    )


def _imports_check() -> dict[str, Any]:
    """Whether the daemon's own module graph imports.

    The broadest single answer to "why will it not start". `swe_mux.server` pulls
    in aiohttp, the route tree, and most of the package, so a partial install, a
    missing runtime dependency, or a native extension that will not load surfaces
    here with the real exception attached - instead of as a traceback the user has
    to run `muxd` to see, which is the thing they came here because they could not
    do.
    """
    try:
        importlib.import_module("swe_mux.server")
    except BaseException as exc:  # noqa: BLE001 - any import-time failure is the answer
        return _check(
            id="install.imports",
            category="install",
            title="Package imports",
            status="fail",
            severity="critical",
            detail=f"Importing swe_mux.server failed: {type(exc).__name__}: {exc}. "
            "The daemon cannot start until this resolves.",
            remedy="Reinstall the package and its dependencies "
            "(`uv sync` in a checkout, or `pip install --force-reinstall swe-mux`).",
        )
    return _check(
        id="install.imports",
        category="install",
        title="Package imports",
        status="ok",
        severity="critical",
        detail="swe_mux.server and its dependency graph import cleanly.",
    )


def _frontend_check() -> dict[str, Any]:
    """Whether the installed package carries a frontend bundle.

    `swe_mux/static/` is build output and gitignored, so a source checkout has
    none until `npm run build` runs once - normal, and fixable in one command. An
    *installed* copy with no `index.html` is a broken artifact: the API answers and
    the browser gets nothing, which reads to a new user as "swe-mux does not work"
    with no error anywhere. `packaging/verify_release_artifact.py` now refuses to
    publish such a wheel; this is the same fact self-reported by the copy that is
    already on the machine.
    """
    from .ui_build import read_ui_build_id

    static = _package_root() / "static"
    index = static / "index.html"
    if index.is_file():
        build_id = read_ui_build_id(static)
        return _check(
            id="install.frontend",
            category="install",
            title="Frontend bundle",
            status="ok" if build_id else "warn",
            severity="critical",
            detail=(
                f"Bundle present at {static} (build {build_id[:12]})."
                if build_id
                else f"An index.html is present at {static} but carries no build "
                "identity; it may be a stale or hand-made file rather than a "
                "production build."
            ),
            remedy=None if build_id else "Rebuild the frontend.",
        )
    checkout = _source_checkout_root()
    if checkout is not None:
        return _check(
            id="install.frontend",
            category="install",
            title="Frontend bundle",
            status="warn",
            severity="critical",
            detail=f"No frontend bundle at {static}. This is a source checkout, where "
            "the bundle is gitignored build output, so a fresh clone serves no UI "
            "until it is built once.",
            remedy="npm --prefix frontend ci && npm --prefix frontend run build",
        )
    return _check(
        id="install.frontend",
        category="install",
        title="Frontend bundle",
        status="fail",
        severity="critical",
        detail=f"No frontend bundle at {static}. This installed copy of swe-mux "
        "shipped without its browser UI; the daemon will answer the API and serve "
        "no interface.",
        remedy="Reinstall swe-mux from a complete artifact; a wheel with no "
        "swe_mux/static/index.html is a packaging fault worth reporting.",
    )


def _writable(directory: Path) -> str | None:
    """None when a file can be created in ``directory``, else the reason it cannot.

    A probe write, not ``os.access``: on Windows ``os.access(dir, W_OK)`` reports
    the read-only attribute and effectively nothing else, so it answers "yes" for
    directories an ACL denies. The temporary file is removed by the context
    manager, so the diagnostic leaves nothing behind in the directory it is
    diagnosing.
    """
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".mux-doctor-"):
            return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


def _data_dir_check(config: Config | None) -> dict[str, Any]:
    """Whether the data directory exists and can be written.

    Everything durable lives under it - the config, `mux.db`, voice clips, logs -
    so a data dir the daemon cannot write is a start failure. It is genuinely
    reachable on a fresh install: `MUX_DATA_DIR` pointing somewhere that does not
    exist, a redirected or roaming home, or a directory restored from a backup with
    another account's ACL.
    """
    from .config import default_data_dir

    data_dir = config.data_dir if config is not None else default_data_dir()
    if data_dir.is_dir():
        reason = _writable(data_dir)
        if reason is None:
            return _check(
                id="install.data_dir",
                category="install",
                title="Data directory",
                status="ok",
                severity="critical",
                detail=f"{data_dir} exists and is writable.",
            )
        return _check(
            id="install.data_dir",
            category="install",
            title="Data directory",
            status="fail",
            severity="critical",
            detail=f"{data_dir} exists but cannot be written ({reason}); the daemon "
            "cannot persist config, history, or logs.",
            remedy="Grant this account write access to the data directory, or point "
            "MUX_DATA_DIR at one it owns.",
        )
    # Not yet created is the normal state of a first run, so the question becomes
    # whether the daemon will be able to create it.
    parent = next((path for path in data_dir.parents if path.is_dir()), None)
    if parent is None or _writable(parent) is not None:
        return _check(
            id="install.data_dir",
            category="install",
            title="Data directory",
            status="fail",
            severity="critical",
            detail=f"{data_dir} does not exist and cannot be created; no existing "
            "parent directory is writable.",
            remedy="Point MUX_DATA_DIR at a directory this account can write.",
        )
    return _check(
        id="install.data_dir",
        category="install",
        title="Data directory",
        status="ok",
        severity="critical",
        detail=f"{data_dir} does not exist yet; {parent} is writable, so the first "
        "run will create it.",
    )


def _database_check(config: Config | None) -> dict[str, Any]:
    """Whether the SQLite store opens.

    A `mux.db` that will not open stops startup outright, and the two ways to get
    one need no misuse: a machine that lost power mid-write, or a data directory
    copied between hosts while the daemon was running, which leaves a WAL the new
    host has to recover.

    Opened ``mode=rw`` - existing file, never created, so the diagnostic does not
    manufacture the thing it is checking for - and probed with a schema read rather
    than ``PRAGMA integrity_check``: reading the schema page proves the header,
    page size, and catalog parse, while a full integrity check walks every page and
    would cost minutes on a multi-gigabyte store for an answer this report is not
    making a decision on.
    """
    from .config import default_data_dir

    path = config.database_path if config is not None else default_data_dir() / "mux.db"
    if not path.exists():
        return _check(
            id="install.database",
            category="install",
            title="SQLite store",
            status="ok",
            severity="critical",
            detail=f"{path} does not exist yet; the first run will create it.",
        )
    try:
        connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=2.0)
        try:
            tables = connection.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return _check(
            id="install.database",
            category="install",
            title="SQLite store",
            status="fail",
            severity="critical",
            detail=f"{path} exists but did not open: {type(exc).__name__}: {exc}. "
            "The daemon cannot start without its store.",
            remedy="Move the file aside to start from an empty store (history and "
            "durable diagnostics are lost), or recover it with the sqlite3 CLI.",
        )
    return _check(
        id="install.database",
        category="install",
        title="SQLite store",
        status="ok",
        severity="critical",
        detail=f"{path} opens; {tables} object(s) in the schema.",
    )


def _port_check(config: Config | None, *, target_url: str) -> dict[str, Any]:
    """Whether something else already owns the port the daemon wants.

    The most common reason a daemon that used to work stops starting, and the one
    with the least helpful symptom: `muxd` exits on a bind error the user sees only
    if they ran it in a terminal, and the tray app shows nothing at all.

    A TCP connect, never a bind. Binding to test a port is the intuitive move and
    is wrong on Windows, where ``SO_REUSEADDR`` lets a second socket take a port
    out from under the owner - a diagnostic must not be able to break the thing it
    is inspecting.

    The row is scoped to *this machine's configured* host and port, which is not
    necessarily what the CLI just failed to reach: `--url`/`MUX_URL` can point at
    another host entirely, and reporting a local port as the cause of a remote
    failure would be a confident wrong answer.
    """
    from .config import LOOPBACK_HOSTS

    host = "127.0.0.1"
    port = 8765
    if config is not None:
        host = config.host if config.host in LOOPBACK_HOSTS else "127.0.0.1"
        port = config.port
    local_url = f"http://{host}:{port}"
    listening = _port_is_listening(host, port)
    if not listening:
        return _check(
            id="install.port",
            category="install",
            title="Configured port",
            status="ok",
            severity="critical",
            detail=f"Nothing is listening on {host}:{port}; the port is free for the "
            "daemon to bind.",
        )
    if local_url != target_url:
        # A healthy local daemon is the likeliest explanation, and this report only
        # ran because a *different* target did not answer.
        return _check(
            id="install.port",
            category="install",
            title="Configured port",
            status="ok",
            severity="info",
            detail=f"A process is listening on this machine's configured {host}:{port}. "
            f"The report targeted {target_url} instead, so this row is not the cause "
            "of that failure.",
        )
    return _check(
        id="install.port",
        category="install",
        title="Configured port",
        status="fail",
        severity="critical",
        detail=f"Something is listening on {host}:{port} but did not answer the swe-mux "
        "API. Either another program holds the port, or a daemon is running and "
        "unhealthy; a fresh daemon cannot bind either way.",
        remedy="Identify the owner (`netstat -ano | findstr :"
        f"{port}` on Windows, `ss -ltnp` on Linux) and stop it, or set a different "
        "port in the swe-mux config.",
    )


def _port_is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _pty_check() -> dict[str, Any]:
    """Whether this host's pseudoterminal backend imports.

    Every session in the product is a PTY, so an unimportable backend is a daemon
    that starts, serves the UI, and fails every spawn - the failure that looks like
    a swe-mux bug and is an install fault. It is the concrete Windows risk: pywinpty
    is the one compiled dependency in the runtime closure, and a wheel/ABI mismatch
    or a missing VC++ runtime shows up as an ImportError nothing else surfaces.
    """
    from .pty_backend import pty_backend_name

    module = "swe_mux.pty_backend_windows" if IS_WINDOWS else "swe_mux.pty_backend_posix"
    backend = pty_backend_name()
    try:
        importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001 - any import-time failure is the answer
        return _check(
            id="install.pty",
            category="install",
            title="Pseudoterminal backend",
            status="fail",
            severity="critical",
            detail=f"The {backend} backend ({module}) did not import: "
            f"{type(exc).__name__}: {exc}. No session of any kind can be spawned.",
            remedy=(
                "Reinstall pywinpty (`uv sync`, or `pip install --force-reinstall "
                "pywinpty`); a compiled-wheel or VC++ runtime mismatch is the usual "
                "cause."
                if IS_WINDOWS
                else "Reinstall the package into a Python with a working `pty` module."
            ),
        )
    return _check(
        id="install.pty",
        category="install",
        title="Pseudoterminal backend",
        status="ok",
        severity="critical",
        detail=f"The {backend} backend imports; sessions can be allocated.",
    )


def _supervisor_bundle_check() -> dict[str, Any]:
    """Whether the frozen app's dedicated supervisor bundle is present.

    Only asks about **presence**, and only where the bundle applies at all. Source
    daemons launch the supervisor from source by design, so "absent" is the correct
    state there and reporting it as a fault would be noise on every developer
    machine.

    It deliberately does not ask whether the bundle is *current*.
    ``packaging.build_desktop.supervisor_bundle_current`` needs PyInstaller to
    compute the source hash it compares against and reports "stale" when the import
    fails, and acting on that answer reaps every live session - so a check that can
    report a false stale is worse than no check.
    """
    from .supervisor_client import dedicated_supervisor_exe

    if not getattr(sys, "frozen", False):
        return _check(
            id="install.supervisor_bundle",
            category="install",
            title="PTY supervisor bundle",
            status="ok",
            severity="info",
            detail="Source install: the supervisor is launched from source, so no "
            "dedicated bundle applies.",
        )
    exe = dedicated_supervisor_exe()
    if exe is None:
        return _check(
            id="install.supervisor_bundle",
            category="install",
            title="PTY supervisor bundle",
            status="warn",
            severity="critical",
            detail="No dedicated supervisor bundle beside this frozen app. Sessions "
            "still run, hosted by the app image itself, so rebuilding the app "
            "requires reaping every session first.",
            remedy="Rebuild it with `python packaging/build_desktop.py "
            "--supervisor-only` (this reaps live sessions; see CLAUDE.md).",
        )
    return _check(
        id="install.supervisor_bundle",
        category="install",
        title="PTY supervisor bundle",
        status="ok",
        severity="critical",
        detail=f"Dedicated supervisor bundle present at {exe}.",
    )


def _extras_checks() -> list[dict[str, Any]]:
    """Presence or absence of each optional extra, with the command to install it.

    Reported because an optional capability that is simply not installed is
    indistinguishable, from the user's side, from one that is broken: the desktop
    tray does not appear, dictation returns nothing, a preview screenshot never
    arrives. Absent is ``unavailable`` rather than ``warn`` - nothing is wrong -
    and each row carries the exact install command so the next step is not a
    documentation hunt.
    """
    from .preview_capture import INSTALL_HINT as PREVIEW_INSTALL_HINT

    frozen = bool(getattr(sys, "frozen", False))
    checks: list[dict[str, Any]] = []
    for name, label, modules, command in _EXTRAS:
        install = command or PREVIEW_INSTALL_HINT
        if name == "desktop" and not IS_WINDOWS:
            # The extra's own markers are `sys_platform == 'win32'`, so it resolves
            # to nothing elsewhere; "not installed" would read as a fixable gap.
            checks.append(
                _check(
                    id=f"extra.{name}",
                    category="extras",
                    title=f"{label} [{name}]",
                    status="unavailable",
                    severity="optional",
                    detail=f"The {name} extra is Windows-only and does not apply to "
                    "this host.",
                )
            )
            continue
        missing = [module for module in modules if not _module_resolves(module)]
        if not missing:
            checks.append(
                _check(
                    id=f"extra.{name}",
                    category="extras",
                    title=f"{label} [{name}]",
                    status="ok",
                    severity="info",
                    detail=f"The {name} extra is installed.",
                )
            )
            continue
        checks.append(
            _check(
                id=f"extra.{name}",
                category="extras",
                title=f"{label} [{name}]",
                status="unavailable",
                severity="optional",
                detail=f"The {name} extra is not installed (missing "
                f"{', '.join(missing)}); this capability is unavailable.",
                # Extras are fixed at build time in the frozen app, so `uv sync`
                # there would be advice about a different installation.
                remedy=(
                    "Rebuild the desktop app with this extra installed in the build "
                    "environment."
                    if frozen
                    else install
                ),
            )
        )
    return checks


def _module_resolves(module: str) -> bool:
    """Whether ``module`` is importable, without importing it.

    ``find_spec`` raises rather than returning None when a *parent* package fails
    to import, which is exactly the half-installed state this report is looking at,
    so an unhandled raise here would abort the whole run over an optional extra.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #


def load_config_for_doctor() -> tuple[Config | None, str | None]:
    """Load the config for the local report, returning the failure instead of raising.

    A `config.toml` that does not parse or does not validate is itself a start
    failure, and one the CLI otherwise hides: `resolve_base_url` swallows the
    exception and silently falls back to the loopback default, so `mux` quietly
    talks to the wrong place. Here the exception is the answer.
    """
    try:
        from .config import load_config

        return load_config(), None
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        return None, f"{type(exc).__name__}: {exc}"


def _config_check(config: Config | None, config_error: str | None) -> dict[str, Any]:
    if config_error is not None:
        return _check(
            id="install.config",
            category="install",
            title="Configuration file",
            status="fail",
            severity="critical",
            detail=f"The swe-mux config did not load: {config_error}. The daemon "
            "refuses to start on a config it cannot validate, and the CLI falls back "
            "to the loopback default, so every command may be pointed at the wrong "
            "daemon.",
            remedy="Fix or move aside config.toml in the data directory; a removed "
            "config is rewritten with defaults on the next start.",
        )
    assert config is not None
    path = config.config_path or (config.data_dir / "config.toml")
    return _check(
        id="install.config",
        category="install",
        title="Configuration file",
        status="ok",
        severity="critical",
        detail=f"{path} loads and validates (schema {config.schema_version}).",
    )


def collect_local_checks(
    *,
    config: Config | None,
    config_error: str | None,
    target_url: str,
) -> list[dict[str, Any]]:
    """Run every machine-only check, in the order a reader should meet them.

    Ordered by what has to be true first: the interpreter, then the package, then
    the frontend it serves, then the state it writes, then the port it binds, then
    the backend it spawns on. A reader stops at the first `FAIL` and that is the
    one to fix.
    """
    from .harness import detect_installations_with_versions, public_harness_registry
    from .prerequisites import detect_prerequisites

    checks: list[dict[str, Any]] = [
        _python_check(),
        _imports_check(),
        _config_check(config, config_error),
        _frontend_check(),
        _data_dir_check(config),
        _database_check(config),
        _port_check(config, target_url=target_url),
        _pty_check(),
        _supervisor_bundle_check(),
    ]
    # Reused rather than reimplemented: these two are pure host probes with no
    # daemon state in them, so the daemon's own builders produce the same rows here
    # that the full report produces there, and there is one implementation to keep
    # right.
    checks += _prerequisite_checks(detect_prerequisites())
    harness_exe = dict(config.harness_exe) if config is not None else {}
    installations = detect_installations_with_versions(harness_exe)
    checks += _harness_checks(public_harness_registry(installations))
    checks += _extras_checks()
    checks += _unchecked_rows()
    return checks


def build_local_doctor_report(
    *,
    config: Config | None,
    config_error: str | None,
    unreachable_url: str,
    unreachable_detail: str,
    now: float,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the degraded report.

    Shares the daemon report's shape - a flat ``checks`` list and a ``summary``
    count - so one renderer draws both, and adds the three fields that keep the two
    from being confused: ``mode`` names this as the local report, ``complete`` is
    false because checks were skipped, and ``daemon`` records what was unreachable.

    ``ok`` keeps its meaning from the daemon report - no check *failed* - and is
    deliberately not overloaded to mean "everything is fine": that is what
    ``complete`` and the ``unchecked`` count in the summary say. The exit code
    carries it for scripts (see `cli.main`), so a degraded report never exits 0.
    """
    rows = (
        checks
        if checks is not None
        else collect_local_checks(
            config=config, config_error=config_error, target_url=unreachable_url
        )
    )
    summary = {"ok": 0, "warn": 0, "fail": 0, "unavailable": 0, "unchecked": 0}
    for check in rows:
        status = str(check["status"])
        summary[status] = summary.get(status, 0) + 1
    return {
        "version": DOCTOR_REPORT_VERSION,
        "mode": "local",
        "complete": False,
        "generated_at": now,
        "ok": summary["fail"] == 0,
        "summary": summary,
        "daemon": {
            "reachable": False,
            "url": unreachable_url,
            "detail": unreachable_detail,
        },
        "capabilities": {
            "swe_mux_version": _installed_version(),
            "platform": {
                "system": sys.platform,
                "key": platform_key(),
                "python": sys.version.split()[0],
                "frozen": bool(getattr(sys, "frozen", False)),
            },
            "source_checkout": _source_checkout_root() is not None,
        },
        "checks": rows,
    }


def _installed_version() -> str | None:
    """The installed distribution version, or None when it cannot be read.

    Read from installed metadata rather than hardcoded: this report exists to
    describe *the copy on the machine*, and a constant compiled into the source
    would describe the copy the source came from.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("swe-mux")
    except PackageNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - metadata is best-effort in a frozen build
        return None
