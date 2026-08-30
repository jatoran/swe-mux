"""Optional Edge TTS integration through a user-managed external Python."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bounded_subprocess import run_bounded
from .config import Config
from .tts_profiles import TtsProfile

log = logging.getLogger(__name__)

EDGE_RISK_ACK_VERSION = 1
EDGE_BRIDGE_TIMEOUT_SECONDS = 45.0
EDGE_CATALOG_LIMIT = 2 * 1024 * 1024
EDGE_STATUS_LIMIT = 16 * 1024
EDGE_CATALOG_STALE_SECONDS = 7 * 24 * 60 * 60
EDGE_TESTED_VERSION_PREFIX = "7.2."
EDGE_TTS_VERSION = "7.2.8"
EDGE_TTS_REQUIREMENT = f"edge-tts=={EDGE_TTS_VERSION}"
EDGE_INSTALL_TIMEOUT_SECONDS = 300.0
# How long the post-install verification may take. It was 20s, and on a clean
# Windows 11 laptop the install failed there and rolled back everything it had
# just built (2026-08-30).
#
# The tempting diagnosis was a network or TLS failure, because the sibling Kokoro
# download on the same machine really did fail on certificate verification. It is
# not one: the `status` operation imports `edge_tts` and reads its version out of
# the installed metadata (`assets/integrations/edge_tts_bridge.py`) and makes no
# request at all. What it was measuring is a first-ever import of `edge_tts` and
# `aiohttp` from a venv written seconds earlier, with a virus scanner reading
# every one of those files for the first time. The same machine's own log gives
# the scale: `uv venv` took 44.8s and the package install 36s, against roughly 2s
# and 5s on a warm host, so a 20s import budget was never going to hold there.
#
# 120s is generous against that and still bounded, and it is the *verify* budget
# only - `EDGE_BRIDGE_TIMEOUT_SECONDS` still governs steady-state calls, where a
# slow answer is a real fault rather than a cold cache.
EDGE_VERIFY_TIMEOUT_SECONDS = 120.0
EDGE_INSTALL_OUTPUT_LIMIT = 512 * 1024
PYPI_SIMPLE_INDEX = "https://pypi.org/simple"


def managed_interpreter(root: Path) -> Path:
    """Where a virtual environment rooted at `root` puts its interpreter on this host.

    The single owner of that layout. A second copy of it - in a test fixture, say -
    reads correctly on the host it was written on and names a path that does not exist
    on the other, which makes a working environment look absent.
    """

    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _safe_error_message(value: Any) -> str:
    message = str(value or "Edge TTS failed")[:2_000]
    message = re.sub(
        r"(?i)(TrustedClientToken|Sec-MS-GEC|MUID)=([^&\s]+)",
        r"\1=<redacted>",
        message,
    )
    message = re.sub(
        r"(?i)((?:wss|https)://speech\.platform\.bing\.com/)[^\s,)]+",
        r"\1<redacted>",
        message,
    )
    return message[:500]


class EdgeTtsError(RuntimeError):
    """A classified external-integration failure safe to show in the UI."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProvisionPlan:
    """How this host can build the isolated environment edge-tts installs into.

    Two implementations, and the order between them is not a preference: uv is
    strictly more capable and is tried first.

    **uv** provisions the *interpreter* as well as the environment
    (`uv venv --python 3.12` downloads CPython when the machine has none), which
    is the only thing that works on the audience this whole integration is aimed
    at - a clean machine, or the frozen desktop app, where `sys.executable` is the
    PyInstaller bundle and `python -m venv` does not exist at all.

    **venv** covers the case uv cannot be assumed for: a source install on a
    machine that has a real Python but no uv. It builds the environment with the
    daemon's own interpreter, which is safe here for a reason specific to this
    package - `edge-tts` is `py3-none-any`, so it loads on whatever version that
    interpreter happens to be and there is no wheel-tag question to get wrong.
    """

    kind: str
    executable: str

    def create_argv(self, staging: Path) -> list[str]:
        if self.kind == "uv":
            return [self.executable, "venv", "--python", "3.12", str(staging)]
        return [self.executable, "-m", "venv", str(staging)]

    def install_argv(self, staging_python: Path) -> list[str]:
        if self.kind == "uv":
            return [
                self.executable,
                "pip",
                "install",
                "--python",
                str(staging_python),
                "--default-index",
                PYPI_SIMPLE_INDEX,
                EDGE_TTS_REQUIREMENT,
            ]
        # `--index-url` is pip's spelling of the same simple index uv is given, so
        # both paths install the same pinned artifact from the same place. The
        # created environment's *own* python runs it, never the daemon's, so
        # nothing can land outside the staging directory.
        return [
            str(staging_python),
            "-m",
            "pip",
            "install",
            "--index-url",
            PYPI_SIMPLE_INDEX,
            "--disable-pip-version-check",
            "--no-input",
            EDGE_TTS_REQUIREMENT,
        ]


def _classify_environment_failure(plan: ProvisionPlan, failure: EdgeTtsError) -> EdgeTtsError:
    """Name the one environment-creation failure that has a specific remedy.

    Debian and Ubuntu ship `venv` without `ensurepip` unless `python3-venv` is
    installed, so `python -m venv` fails there on a machine that looks perfectly
    healthy - and the raw message is long enough that the apt command in it can be
    lost. It is the single most likely way the non-uv path fails, so it gets a
    sentence rather than a passed-through stderr tail. Every other failure keeps
    its original classification: guessing at causes is what this file is trying
    to stop doing.
    """
    if plan.kind != "venv":
        return failure
    text = str(failure).casefold()
    if "ensurepip" not in text and "python3-venv" not in text:
        return failure
    return EdgeTtsError(
        "install_venv_incomplete",
        "this Python cannot create a virtual environment with pip in it, which on "
        "Debian and Ubuntu means the `python3-venv` package is not installed "
        "(`sudo apt install python3-venv`). Installing uv avoids the whole "
        f"question. Original error: {failure}",
    )


def select_provision_plan(
    *,
    which: Callable[[str], str | None] | None = None,
    interpreter: str | None = None,
    frozen: bool | None = None,
) -> ProvisionPlan | None:
    """The best available way to build the environment, or None when there is none.

    Every input is injectable because the answer is about the *host*, and an
    assertion about behaviour must not be derived from the machine checking it.
    The uv lookup is taken as a **resolver** rather than as a path for exactly
    that reason: a `uv_path: str | None = None` parameter cannot express "there is
    no uv here", because `None` also means "you did not tell me", so the first
    version of this silently consulted the developer's own uv and every test that
    meant to exercise the fallback exercised the uv path instead.

    The frozen guard is the other load-bearing half. In the desktop app
    `sys.executable` is the PyInstaller bundle: it has no `venv` module, and
    running it with `-m` re-enters swe-mux rather than Python. Offering it as a
    fallback would produce a confusing failure at best and a second daemon at
    worst.

    `which` defaults to `None` rather than to `shutil.which` itself: a callable
    default is bound once, when this function is *defined*, so a caller or a test
    that replaces `shutil.which` afterwards would never be consulted.
    """
    resolved_uv = (which or shutil.which)("uv")
    if resolved_uv:
        return ProvisionPlan("uv", resolved_uv)
    if frozen if frozen is not None else bool(getattr(sys, "frozen", False)):
        return None
    executable = interpreter if interpreter is not None else sys.executable
    if not executable:
        return None
    return ProvisionPlan("venv", executable)


class EdgeVoiceCatalog:
    """Last-good service catalog. Reads never reach the network."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"status": "not_loaded", "voices": [], "fetched_at": None, "error": None}
        if not isinstance(raw, dict) or not isinstance(raw.get("voices"), list):
            return {"status": "not_loaded", "voices": [], "fetched_at": None, "error": None}
        return raw

    def snapshot(self, *, selected: str) -> dict[str, Any]:
        state = dict(self._state)
        voices = list(state.get("voices") or [])
        fetched_at = state.get("fetched_at")
        stale = not isinstance(fetched_at, (int, float)) or time.time() - fetched_at > (
            EDGE_CATALOG_STALE_SECONDS
        )
        state.update(
            {
                "voices": voices,
                "stale": stale,
                "selected": selected,
                "selected_present": any(item.get("id") == selected for item in voices),
            }
        )
        return state

    def replace(self, voices: list[dict[str, Any]], *, package_version: str) -> None:
        state = {
            "schema_version": 1,
            "status": "ready",
            "voices": voices,
            "fetched_at": time.time(),
            "package_version": package_version,
            "error": None,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)
        self._state = state

    def record_error(self, message: str) -> None:
        # The last-good voices stay intact. The error is runtime evidence, not a
        # replacement catalog that would strand the selected voice while offline.
        self._state = {**self._state, "error": message[:500]}


def normalize_edge_voices(raw: Any) -> list[dict[str, Any]]:
    """Bound and normalize the untrusted service response."""

    if not isinstance(raw, list):
        raise EdgeTtsError("catalog_invalid", "Microsoft returned an invalid voice catalog")
    result: dict[str, dict[str, Any]] = {}
    for item in raw[:2_000]:
        if not isinstance(item, dict):
            continue
        voice_id = str(item.get("ShortName") or "")[:160]
        locale = str(item.get("Locale") or "")[:40]
        if not voice_id or not locale or not voice_id.endswith("Neural"):
            continue
        raw_tags = item.get("VoiceTag")
        tags: dict[str, Any] = raw_tags if isinstance(raw_tags, dict) else {}
        result[voice_id] = {
            "id": voice_id,
            "locale": locale,
            "gender": str(item.get("Gender") or "")[:20],
            "name": str(item.get("FriendlyName") or voice_id)[:240],
            "status": str(item.get("Status") or "")[:20],
            "codec": str(item.get("SuggestedCodec") or "")[:80],
            "categories": [str(value)[:80] for value in (tags.get("ContentCategories") or [])[:20]],
            "personalities": [
                str(value)[:80] for value in (tags.get("VoicePersonalities") or [])[:20]
            ],
        }
    if not result:
        raise EdgeTtsError("catalog_empty", "Microsoft returned no usable Edge voices")
    return sorted(result.values(), key=lambda item: (item["locale"], item["name"], item["id"]))


class EdgeTtsProvider:
    """Runs the LGPL client outside the frozen application boundary."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.bridge = Path(__file__).with_name("assets") / "integrations" / "edge_tts_bridge.py"
        self.integration_directory = config.data_dir / "integrations" / "edge-tts"
        self.managed_directory = self.integration_directory / "current"
        self.install_state_path = self.integration_directory / "install.json"
        self.catalog = EdgeVoiceCatalog(
            config.data_dir / "voice" / "providers" / "edge" / "voices.json"
        )
        self.temporary_directory = self.catalog.path.parent / "tmp"
        self._sweep_stale_inputs()
        self.last_error: str | None = None
        self.last_error_code: str | None = None
        self.last_probe_at: float | None = None
        self.package_version: str | None = None
        self.failure_count = 0
        self.retry_after = 0.0
        self._operation_lock = asyncio.Lock()
        self._install_task: asyncio.Task[None] | None = None
        self._install_state = self._load_install_state()
        self._recover_interrupted_install()
        managed = self.managed_python()
        if (
            not self.config.tts_edge_python.strip()
            and self._install_state.get("status") == "ready"
            and managed.is_file()
        ):
            installed = str(self._install_state.get("version") or "")
            self.package_version = installed or None

    def _sweep_stale_inputs(self) -> None:
        """No synthesis survives a daemon, so no prior input file is live."""

        try:
            candidates = list(self.temporary_directory.glob("*.txt"))
        except OSError:
            return
        removed = 0
        for candidate in candidates:
            try:
                candidate.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        if removed:
            log.warning("removed stale Edge TTS input files count=%d", removed)

    def managed_python(self, root: Path | None = None) -> Path:
        return managed_interpreter(root or self.managed_directory)

    def _load_install_state(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.install_state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"status": "not_installed", "phase": None, "error": None}
        if not isinstance(raw, dict):
            return {"status": "not_installed", "phase": None, "error": None}
        return raw

    def _write_install_state(self, state: dict[str, Any]) -> None:
        self.integration_directory.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, **state}
        temporary = self.install_state_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.install_state_path)
        self._install_state = payload

    def _recover_interrupted_install(self) -> None:
        interrupted = self._install_state.get("status") == "installing"
        removed = 0
        try:
            staging = list(self.integration_directory.glob(".staging-*"))
        except OSError:
            staging = []
        for path in staging:
            try:
                shutil.rmtree(path)
                removed += 1
            except OSError:
                continue
        if interrupted:
            try:
                message = "the managed Edge TTS installation was interrupted by a restart"
                if self._install_state.get("previous_ready") and self.managed_python().is_file():
                    self._write_install_state(
                        {
                            "status": "ready",
                            "phase": None,
                            "version": self._install_state.get("previous_version"),
                            "installed_at": self._install_state.get("previous_installed_at"),
                            "last_install_error": message,
                            "updated_at": time.time(),
                        }
                    )
                else:
                    self._write_install_state(
                        {
                            "status": "error",
                            "phase": None,
                            "error": message,
                            "updated_at": time.time(),
                        }
                    )
            except OSError:
                log.warning("could not persist interrupted Edge TTS install state", exc_info=True)
        if interrupted or removed:
            log.warning(
                "edge tts managed install recovered interrupted=%s staging_removed=%d",
                interrupted,
                removed,
            )

    def managed_status(self) -> dict[str, Any]:
        installing = self._install_task is not None and not self._install_task.done()
        state = dict(self._install_state)
        status = "installing" if installing else str(state.get("status") or "not_installed")
        if status == "installing" and not installing:
            status = "error"
            state["error"] = "the managed Edge TTS installation was interrupted by a restart"
        python = self.managed_python()
        if status == "ready" and not python.is_file():
            status = "error"
            state["error"] = "the managed Edge TTS Python is missing; repair the integration"
        return {
            "status": status,
            "phase": state.get("phase") if installing else None,
            "error": None if status in {"ready", "installing"} else state.get("error"),
            "version": state.get("version") if status == "ready" else None,
            "python": str(python),
            "requirement": EDGE_TTS_REQUIREMENT,
            "uv_available": shutil.which("uv") is not None,
            # `uv_available` is kept because it is still the *preferred* method and
            # the UI names it, but it is no longer what decides whether the button
            # works: a source install with a real Python and no uv can build the
            # environment with `venv`. Deciding on `uv_available` alone would keep
            # offering the manual-only path to someone the fallback covers.
            "install_method": plan.kind if (plan := select_provision_plan()) else None,
            "installed_at": state.get("installed_at"),
            "updated_at": state.get("updated_at"),
            "last_install_error": state.get("last_install_error"),
        }

    def python(self) -> str | None:
        configured = self.config.tts_edge_python.strip()
        if configured:
            return str(Path(configured).expanduser())
        managed = self.managed_status()
        if managed["status"] == "ready":
            return str(self.managed_python())
        if not getattr(sys, "frozen", False):
            return sys.executable
        return None

    def status(self) -> dict[str, Any]:
        managed = self.managed_status()
        executable = self.python()
        if executable is None:
            integration = "unconfigured"
            diagnostic = (
                "install the managed Edge TTS integration or configure an external Python; "
                "the frozen app does not bundle LGPL code"
            )
        elif self.last_error:
            integration = "error"
            diagnostic = self.last_error
        elif self.package_version:
            integration = "ready"
            diagnostic = None
        else:
            integration = "unknown"
            diagnostic = "check the external Edge TTS integration before using it"
        acknowledged = self.config.tts_edge_risk_ack_version >= EDGE_RISK_ACK_VERSION
        if not acknowledged and integration != "error":
            diagnostic = (
                "acknowledge the unofficial Microsoft service and privacy disclosure "
                "before Edge TTS can send text"
            )
        return {
            "id": "edge",
            "available": bool(executable and acknowledged and integration == "ready"),
            "integration": integration,
            "diagnostic": diagnostic,
            "python": executable or "",
            "package_version": self.package_version,
            "tested_version": bool(
                self.package_version and self.package_version.startswith(EDGE_TESTED_VERSION_PREFIX)
            ),
            "last_probe_at": self.last_probe_at,
            "risk_acknowledged": acknowledged,
            "retry_after": self.retry_after or None,
            "managed": managed,
            "using_managed": bool(
                executable and Path(executable) == self.managed_python()
            ),
            "catalog": self.catalog.snapshot(selected=self.config.tts_edge_voice),
        }

    def configuration_changed(self) -> None:
        """Drop conclusions tied to the previous interpreter or service options."""

        self.last_error = None
        self.last_error_code = None
        self.last_probe_at = None
        self.package_version = None
        self.failure_count = 0
        self.retry_after = 0.0

    def start_managed_install(self) -> bool:
        """Start one staged managed install; return False when one is already live."""

        if self._install_task is not None and not self._install_task.done():
            return False
        operation_id = uuid.uuid4().hex
        prior_state = dict(self._install_state)
        self._write_install_state(
            {
                "status": "installing",
                "phase": "queued",
                "operation_id": operation_id,
                "error": None,
                "updated_at": time.time(),
                "previous_ready": prior_state.get("status") == "ready",
                "previous_version": prior_state.get("version"),
                "previous_installed_at": prior_state.get("installed_at"),
            }
        )
        self._install_task = asyncio.create_task(
            self._install_managed(operation_id, prior_state),
            name=f"edge-tts-install-{operation_id[:8]}",
        )
        return True

    async def wait_install(self) -> None:
        if self._install_task is not None:
            await asyncio.gather(self._install_task, return_exceptions=True)

    async def stop(self) -> None:
        if self._install_task is None or self._install_task.done():
            return
        self._install_task.cancel()
        await asyncio.gather(self._install_task, return_exceptions=True)

    def _install_phase(self, operation_id: str, phase: str) -> None:
        self._write_install_state(
            {
                **self._install_state,
                "status": "installing",
                "phase": phase,
                "operation_id": operation_id,
                "error": None,
                "updated_at": time.time(),
            }
        )
        log.info("edge tts managed install phase operation=%s phase=%s", operation_id, phase)

    def _record_install_error(
        self, operation_id: str, message: str, prior_state: dict[str, Any]
    ) -> None:
        prior_ready = (
            prior_state.get("status") == "ready" and self.managed_python().is_file()
        )
        state = (
            {
                **prior_state,
                "status": "ready",
                "phase": None,
                "operation_id": operation_id,
                "last_install_error": message,
                "updated_at": time.time(),
            }
            if prior_ready
            else {
                "status": "error",
                "phase": None,
                "operation_id": operation_id,
                "error": message,
                "updated_at": time.time(),
            }
        )
        try:
            self._write_install_state(state)
        except OSError:
            log.error(
                "edge tts managed install state write failed operation=%s",
                operation_id,
                exc_info=True,
            )

    async def _run_install_command(
        self, argv: list[str], *, label: str, operation_id: str
    ) -> None:
        try:
            outcome = await run_bounded(
                argv,
                label=label,
                timeout_seconds=EDGE_INSTALL_TIMEOUT_SECONDS,
                output_limit=EDGE_INSTALL_OUTPUT_LIMIT,
                operation_id=operation_id,
            )
        except OSError as exc:
            raise EdgeTtsError("install_spawn_failed", f"could not start {label}: {exc}") from exc
        if outcome.timed_out:
            raise EdgeTtsError("install_timeout", f"{label} timed out")
        if outcome.truncated:
            raise EdgeTtsError("install_output_too_large", f"{label} returned too much output")
        if outcome.exit_code != 0:
            raw = (outcome.stderr or outcome.stdout).decode("utf-8", errors="replace").strip()
            detail = _safe_error_message(raw[-400:]) if raw else "no diagnostic"
            raise EdgeTtsError(
                "install_failed",
                f"{label} exited with status {outcome.exit_code}: {detail}",
            )
        log.info(
            "edge tts managed command complete operation=%s label=%s duration_ms=%.0f",
            operation_id,
            label,
            outcome.duration_ms,
        )

    def _activate_managed(self, staging: Path) -> Path | None:
        previous = self.integration_directory / "previous"
        if previous.exists():
            shutil.rmtree(previous)
        if self.managed_directory.exists():
            os.replace(self.managed_directory, previous)
        try:
            os.replace(staging, self.managed_directory)
        except BaseException:
            if previous.exists() and not self.managed_directory.exists():
                os.replace(previous, self.managed_directory)
            raise
        return previous if previous.exists() else None

    async def _preflight_index_reachable(self) -> None:
        """Fail fast, and distinguish "cannot connect" from "cannot verify".

        Those two are one line apart in a stack trace and are completely different
        problems. A TLS-verification failure on a clean Windows machine is a trust
        gap that `tls_trust` exists to close, and a user told only "install failed"
        has no way to know that; a refused connection is a proxy or an offline
        host. Both are reported here rather than fifteen lines into uv's output.
        """
        import ssl

        import aiohttp

        from .tls_trust import trusting_connector

        try:
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            async with aiohttp.ClientSession(
                timeout=timeout, connector=trusting_connector()
            ) as session:
                async with session.head(PYPI_SIMPLE_INDEX, allow_redirects=True) as response:
                    if response.status >= 500:
                        raise EdgeTtsError(
                            "install_index_unavailable",
                            f"{PYPI_SIMPLE_INDEX} answered {response.status}; "
                            "PyPI may be having an outage. Try again shortly.",
                        )
        except (ssl.SSLError, aiohttp.ClientSSLError) as exc:
            raise EdgeTtsError(
                "install_tls_untrusted",
                f"could not verify the TLS certificate of {PYPI_SIMPLE_INDEX}: {exc}. "
                "This machine's certificate trust store cannot validate a public "
                "certificate, which usually means a TLS-inspecting proxy whose "
                "certificate authority is not installed here.",
            ) from exc
        except (aiohttp.ClientError, OSError, TimeoutError) as exc:
            raise EdgeTtsError(
                "install_index_unreachable",
                f"could not reach {PYPI_SIMPLE_INDEX}: {exc}. The managed installation "
                "downloads Edge TTS from PyPI, so it needs outbound HTTPS.",
            ) from exc

    async def _install_managed(
        self, operation_id: str, prior_state: dict[str, Any]
    ) -> None:
        started = time.monotonic()
        staging = self.integration_directory / f".staging-{operation_id}"
        previous: Path | None = None
        activated = False
        try:
            plan = select_provision_plan()
            if plan is None:
                raise EdgeTtsError(
                    "provisioner_not_found",
                    "the managed Edge TTS installation needs either uv, or a real "
                    "Python interpreter it can build a virtual environment with. "
                    "The frozen desktop app has no usable interpreter of its own, so "
                    "install uv - or point Settings at an external Python that "
                    "already has edge-tts.",
                )
            # Before the multi-minute part, not after it. An install that spends
            # 80 seconds building an environment and only then discovers it cannot
            # reach PyPI has thrown that work away and reports the failure at the
            # step furthest from its cause. One HEAD against the index answers
            # both halves at once - routing and TLS trust - in about as long as it
            # takes to fail.
            self._install_phase(operation_id, "checking_connectivity")
            await self._preflight_index_reachable()
            self.integration_directory.mkdir(parents=True, exist_ok=True)
            if staging.exists():
                shutil.rmtree(staging)
            async with self._operation_lock:
                self._install_phase(operation_id, "creating_environment")
                try:
                    await self._run_install_command(
                        plan.create_argv(staging),
                        label=f"Edge TTS environment creation ({plan.kind})",
                        operation_id=operation_id,
                    )
                except EdgeTtsError as exc:
                    raise _classify_environment_failure(plan, exc) from exc
                staging_python = self.managed_python(staging)
                if not staging_python.is_file():
                    raise EdgeTtsError(
                        "install_invalid",
                        f"{plan.kind} completed without creating the managed Python "
                        "interpreter",
                    )
                self._install_phase(operation_id, "installing_package")
                await self._run_install_command(
                    plan.install_argv(staging_python),
                    label=f"Edge TTS package installation ({plan.kind})",
                    operation_id=operation_id,
                )
                self._install_phase(operation_id, "verifying")
                try:
                    payload = await self._invoke_unlocked(
                        str(staging_python),
                        "status",
                        timeout_seconds=EDGE_VERIFY_TIMEOUT_SECONDS,
                        record_version=False,
                        correlation_id=operation_id,
                    )
                except EdgeTtsError as exc:
                    # A bare "Edge TTS status timed out" is what an operator saw
                    # after 80 seconds of environment and package work was thrown
                    # away, and it sent them looking for a network fault that was
                    # not there. Say what the step actually does, so the next
                    # reader does not repeat the diagnosis.
                    if exc.code != "timeout":
                        raise
                    raise EdgeTtsError(
                        "install_verify_timeout",
                        f"the installed Edge TTS did not finish loading within "
                        f"{EDGE_VERIFY_TIMEOUT_SECONDS:.0f}s. This step only imports the "
                        f"package and reads its version - it makes no network request - so "
                        f"the usual cause is a slow disk or a virus scanner reading the new "
                        f"files for the first time. Retrying is often enough.",
                    ) from exc
                installed = str(payload.get("version") or "")
                if installed != EDGE_TTS_VERSION:
                    raise EdgeTtsError(
                        "install_version_mismatch",
                        f"expected Edge TTS {EDGE_TTS_VERSION}, found {installed or 'unknown'}",
                    )
                self._install_phase(operation_id, "activating")
                previous = self._activate_managed(staging)
                activated = True
                now = time.time()
                self._write_install_state(
                    {
                        "status": "ready",
                        "phase": None,
                        "operation_id": operation_id,
                        "version": installed,
                        "installed_at": now,
                        "updated_at": now,
                        "error": None,
                        "last_install_error": None,
                    }
                )
                self.package_version = installed
                self._remember_success()
                if previous is not None:
                    try:
                        shutil.rmtree(previous)
                    except OSError:
                        log.warning(
                            "edge tts previous environment cleanup failed operation=%s",
                            operation_id,
                            exc_info=True,
                        )
            log.info(
                "edge tts managed install complete operation=%s version=%s seconds=%.1f",
                operation_id,
                EDGE_TTS_VERSION,
                time.monotonic() - started,
            )
        except asyncio.CancelledError:
            self._record_install_error(
                operation_id,
                "the managed Edge TTS installation was cancelled",
                prior_state,
            )
            log.warning("edge tts managed install cancelled operation=%s", operation_id)
            raise
        except (EdgeTtsError, OSError) as exc:
            message = _safe_error_message(exc)
            if activated and previous is not None and previous.exists():
                try:
                    if self.managed_directory.exists():
                        shutil.rmtree(self.managed_directory)
                    os.replace(previous, self.managed_directory)
                except OSError:
                    log.error(
                        "edge tts managed install rollback failed operation=%s",
                        operation_id,
                        exc_info=True,
                    )
            self._record_install_error(operation_id, message, prior_state)
            log.warning(
                "edge tts managed install failed operation=%s error=%s",
                operation_id,
                message,
            )
        finally:
            if staging.exists():
                try:
                    shutil.rmtree(staging)
                except OSError:
                    log.warning(
                        "edge tts staging cleanup failed operation=%s", operation_id, exc_info=True
                    )

    async def _invoke(
        self,
        operation: str,
        *arguments: str,
        timeout_seconds: float = EDGE_BRIDGE_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        executable = self.python()
        if executable is None:
            raise EdgeTtsError(
                "integration_unconfigured",
                "install the managed Edge TTS integration or configure an external Python",
            )
        async with self._operation_lock:
            return await self._invoke_unlocked(
                executable,
                operation,
                *arguments,
                timeout_seconds=timeout_seconds,
            )

    async def _invoke_unlocked(
        self,
        executable: str,
        operation: str,
        *arguments: str,
        timeout_seconds: float = EDGE_BRIDGE_TIMEOUT_SECONDS,
        record_version: bool = True,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            outcome = await run_bounded(
                [executable, str(self.bridge), operation, *arguments],
                label=f"edge-tts {operation}",
                timeout_seconds=timeout_seconds,
                output_limit=EDGE_CATALOG_LIMIT if operation == "voices" else EDGE_STATUS_LIMIT,
                stderr_limit=32 * 1024,
                operation_id=correlation_id or f"edge-tts:{operation}",
            )
        except OSError as exc:
            raise EdgeTtsError(
                "integration_missing", f"could not start Edge TTS Python: {exc}"
            ) from exc
        if outcome.timed_out:
            raise EdgeTtsError("timeout", f"Edge TTS {operation} timed out")
        if outcome.stdout_truncated:
            raise EdgeTtsError("response_too_large", "Edge TTS returned too much data")
        try:
            payload = json.loads(outcome.stdout.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError) as exc:
            raw_detail = outcome.stderr.decode("utf-8", errors="replace").strip()
            detail = _safe_error_message(raw_detail) if raw_detail else ""
            suffix = f": {detail}" if detail else ""
            raise EdgeTtsError(
                "helper_invalid", f"Edge TTS helper returned invalid data{suffix}"
            ) from exc
        if not isinstance(payload, dict):
            raise EdgeTtsError("helper_invalid", "Edge TTS helper returned a non-object response")
        if payload.get("ok") and outcome.exit_code != 0:
            raise EdgeTtsError(
                "helper_failed",
                f"Edge TTS helper exited with status {outcome.exit_code}",
            )
        if not payload.get("ok"):
            status = payload.get("status")
            error_type = str(payload.get("error_type") or "")
            message = _safe_error_message(payload.get("error"))
            if status == 403:
                code = "service_rejected"
                message = "Microsoft rejected the unofficial Edge TTS request (403)"
            elif status == 429:
                code = "throttled"
            elif error_type in {"ClientConnectorError", "ClientOSError", "ServerDisconnectedError"}:
                code = "offline"
            elif error_type in {"ClientConnectorCertificateError", "ClientSSLError", "SSLError"}:
                code = "tls"
            elif error_type == "NoAudioReceived":
                code = "no_audio"
            elif error_type in {"ImportError", "PackageNotFoundError"}:
                code = "integration_missing"
            else:
                code = "service_error"
            raise EdgeTtsError(code, message)
        package_version = str(payload.get("version") or "")[:80]
        if package_version and record_version:
            self.package_version = package_version
        return payload

    def _remember_error(self, exc: EdgeTtsError) -> None:
        self.last_error = str(exc)
        self.last_error_code = exc.code
        self.last_probe_at = time.time()

    def _remember_success(self) -> None:
        self.last_error = None
        self.last_error_code = None
        self.last_probe_at = time.time()
        self.failure_count = 0
        self.retry_after = 0.0

    async def probe(self) -> dict[str, Any]:
        try:
            await self._invoke("status", timeout_seconds=10.0)
        except EdgeTtsError as exc:
            self._remember_error(exc)
        else:
            self._remember_success()
        return self.status()

    async def refresh_voices(self) -> dict[str, Any]:
        try:
            payload = await self._invoke("voices")
            voices = normalize_edge_voices(payload.get("voices"))
            self.catalog.replace(voices, package_version=str(payload.get("version") or ""))
        except EdgeTtsError as exc:
            self.catalog.record_error(str(exc))
            self._remember_error(exc)
            raise
        self._remember_success()
        return self.catalog.snapshot(selected=self.config.tts_edge_voice)

    async def synthesize(
        self,
        profile: TtsProfile,
        text: str,
        destination: Path,
        *,
        automatic: bool,
    ) -> float:
        if self.config.tts_edge_risk_ack_version < EDGE_RISK_ACK_VERSION:
            raise EdgeTtsError(
                "risk_not_acknowledged",
                "acknowledge the unofficial Microsoft service and privacy disclosure first",
            )
        now = time.time()
        if automatic and self.retry_after > now:
            until = time.strftime("%H:%M:%S", time.localtime(self.retry_after))
            raise EdgeTtsError(
                "backoff",
                f"Edge TTS is backing off until {until}",
            )
        self.temporary_directory.mkdir(parents=True, exist_ok=True)
        input_path = self.temporary_directory / f"{destination.stem}.txt"
        input_path.write_text(text, encoding="utf-8")
        try:
            payload = await self._invoke(
                "synthesize",
                "--input",
                str(input_path),
                "--output",
                str(destination),
                "--voice",
                profile.voice,
                "--rate",
                str(int(profile.option("rate_percent", 0))),
                "--volume",
                str(int(profile.option("volume_percent", 0))),
                "--pitch",
                str(int(profile.option("pitch_hz", 0))),
            )
        except EdgeTtsError as exc:
            self.failure_count += 1
            delay = (30.0, 120.0, 600.0)[min(self.failure_count - 1, 2)]
            self.retry_after = time.time() + delay
            self._remember_error(exc)
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass
        self._remember_success()
        size = int(payload.get("bytes") or destination.stat().st_size)
        bitrate = max(1, int(payload.get("bitrate_bps") or 48_000))
        return size * 8 / bitrate
