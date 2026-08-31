"""Out-of-process plugin lifecycle, execution, and EventBus integration."""

from __future__ import annotations

import asyncio
import builtins
import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import secrets
import shutil
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
from packaging.version import InvalidVersion, Version

from . import __version__
from .background_tasks import background
from .bounded_subprocess import run_bounded
from .plugin_manifest import (
    HOST_CAPABILITIES,
    CommandSpec,
    PluginEventHook,
    PluginManifest,
    PluginManifestError,
    current_platform,
    parse_plugin_manifest,
    plugin_content_digest,
)
from .plugin_store import PluginStore
from .spawn_contract import base_session_env

log = logging.getLogger(__name__)

COMMAND_OUTPUT_LIMIT = 64 * 1024
GLOBAL_CONCURRENCY = 16
PER_PLUGIN_CONCURRENCY = 4
EVENT_QUEUE_SIZE = 256
CONTEXT_LIMIT = 32 * 1024
MARKETPLACE_URL = (
    "https://api.github.com/search/repositories?q=topic%3Aswe-mux-plugin&sort=updated&per_page=50"
)
MARKETPLACE_CATALOG_URL = "https://swemux.dev/plugins/catalog.json"
GITHUB_API = "https://api.github.com"
PLUGIN_EVENT_LOOP = "plugin-events"


class PluginError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class PluginToken:
    plugin_id: str
    version: str
    permissions: frozenset[str]
    contribution: str
    expires_at: float | None
    session_id: str | None = None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-") or "plugin"


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _github_repository(source: str) -> tuple[str, str] | None:
    if "://" not in source:
        parts = [part for part in source.split("/") if part]
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix(".git")
        return None
    parsed = urlparse(source)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def plugin_compatibility(manifest: PluginManifest) -> str:
    if current_platform() not in manifest.platforms:
        return f"unsupported on {current_platform()}"
    machine = platform.machine().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }
    architecture = aliases.get(machine, machine)
    declared = {aliases.get(item.lower(), item.lower()) for item in manifest.architectures}
    if declared and architecture not in declared:
        return f"unsupported on architecture {architecture}"
    missing = set(manifest.requires) - HOST_CAPABILITIES
    if missing:
        return f"host capabilities unavailable: {', '.join(sorted(missing))}"
    try:
        if Version(__version__) < Version(manifest.min_swe_mux_version):
            return f"requires swe-mux {manifest.min_swe_mux_version} or newer"
    except InvalidVersion:
        return "host version cannot be compared"
    return ""


def plugin_approval_digest(manifest: PluginManifest, content_digest: str) -> str:
    return hashlib.sha256(f"{manifest.security_digest}:{content_digest}".encode()).hexdigest()


def inspect_plugin_path(path: str | Path) -> dict[str, Any]:
    manifest = parse_plugin_manifest(path)
    content_digest = plugin_content_digest(manifest.path.parent)
    return {
        "manifest": manifest.snapshot(),
        "security_digest": manifest.security_digest,
        "content_digest": content_digest,
        "approval_digest": plugin_approval_digest(manifest, content_digest),
        "diagnostic": plugin_compatibility(manifest),
        "full_trust": True,
    }


class PluginManager:
    def __init__(
        self,
        *,
        data_dir: Path,
        database_path: Path,
        events: Any,
        sessions: Any,
        projects: Any,
        port: int,
    ) -> None:
        self.data_dir = data_dir / "plugins"
        self.sources = self.data_dir / "sources"
        self.configs = self.data_dir / "config"
        self.states = self.data_dir / "state"
        self.staging = self.data_dir / ".staging"
        self.rollback = self.data_dir / ".rollback"
        for directory in (self.sources, self.configs, self.states, self.staging, self.rollback):
            directory.mkdir(parents=True, exist_ok=True)
        self.store = PluginStore(database_path)
        self.events = events
        self.sessions = sessions
        self.projects = projects
        self.port = port
        self.execution_enabled = True
        self._global = asyncio.Semaphore(GLOBAL_CONCURRENCY)
        self._per_plugin: dict[str, asyncio.Semaphore] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._event_queue: asyncio.Queue[Any] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._tokens: dict[str, PluginToken] = {}
        self._last_event_run: dict[tuple[str, str], float] = {}
        self._event_seen: dict[str, float] = {}

    async def start(self) -> None:
        self.execution_enabled = await self.store.execution_enabled()
        await self.refresh_all()
        self._event_queue = self.events.subscribe(name="plugins")
        self._event_task = asyncio.create_task(self._event_loop(), name="plugin-events")
        task = asyncio.create_task(self._run_startup_hooks(), name="plugin-startup")
        self._track(task)
        log.info(
            "plugin host started plugins=%d execution_enabled=%s",
            len(await self.store.list()),
            self.execution_enabled,
        )

    async def stop(self) -> None:
        self.execution_enabled = False
        if self._event_queue is not None:
            self.events.unsubscribe(self._event_queue)
        if self._event_task is not None:
            self._event_task.cancel()
        for task in tuple(self._tasks):
            task.cancel()
        await asyncio.gather(
            *(tuple(self._tasks) + ((self._event_task,) if self._event_task else ())),
            return_exceptions=True,
        )
        self._tokens.clear()
        await self.store.close()
        log.info("plugin host stopped")

    def _track(self, task: asyncio.Task[Any]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _directories(self, plugin_id: str) -> tuple[Path, Path]:
        key = _safe_name(plugin_id)
        config_dir, state_dir = self.configs / key, self.states / key
        config_dir.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        return config_dir, state_dir

    def _compatible(self, manifest: PluginManifest) -> str:
        return plugin_compatibility(manifest)

    @staticmethod
    def _approval_digest(manifest: PluginManifest, content_digest: str) -> str:
        return plugin_approval_digest(manifest, content_digest)

    async def _load(
        self, record: dict[str, Any], *, require_enabled: bool = False
    ) -> PluginManifest:
        if require_enabled and (not record["enabled"] or record["lifecycle"] != "enabled"):
            raise PluginError("plugin_disabled", f"plugin {record['id']} is not enabled")
        try:
            manifest = parse_plugin_manifest(record["manifest_path"])
        except PluginManifestError as exc:
            await self.store.set_state(
                record["id"], enabled=False, lifecycle="invalid", diagnostic=str(exc)
            )
            raise PluginError("invalid_manifest", str(exc)) from exc
        diagnostic = self._compatible(manifest)
        if diagnostic:
            await self.store.set_state(
                record["id"], enabled=False, lifecycle="incompatible", diagnostic=diagnostic
            )
            raise PluginError("incompatible", diagnostic)
        if manifest.id != record["id"]:
            diagnostic = f"manifest identity changed from {record['id']} to {manifest.id}"
            await self.store.set_state(
                record["id"], enabled=False, lifecycle="changed", diagnostic=diagnostic
            )
            raise PluginError("manifest_changed", diagnostic)
        if manifest.name != record["name"] or manifest.version != record["version"]:
            await self.store.set_state(
                record["id"], name=manifest.name, version=manifest.version
            )
        content_digest = await asyncio.to_thread(plugin_content_digest, manifest.path.parent)
        approval_digest = self._approval_digest(manifest, content_digest)
        if approval_digest != record["approved_digest"] and record["enabled"]:
            diagnostic = "security-relevant manifest content changed; inspect and approve it again"
            await self.store.set_state(
                record["id"], enabled=False, lifecycle="changed", diagnostic=diagnostic
            )
            raise PluginError("approval_stale", diagnostic)
        return manifest

    async def refresh_all(self) -> None:
        for record in await self.store.list():
            try:
                manifest = await self._load(record)
                content_digest = await asyncio.to_thread(
                    plugin_content_digest, manifest.path.parent
                )
                changes: dict[str, Any] = {
                    "name": manifest.name,
                    "version": manifest.version,
                    "diagnostic": "",
                }
                if (
                    manifest.digest != record["manifest_digest"]
                    or content_digest != record["content_digest"]
                ):
                    changes.update(
                        enabled=False,
                        lifecycle="changed",
                        manifest_digest=manifest.digest,
                        security_digest=manifest.security_digest,
                        content_digest=content_digest,
                        diagnostic="manifest changed; inspect and approve it again",
                    )
                await self.store.set_state(record["id"], **changes)
            except PluginError:
                continue

    async def list(self) -> dict[str, Any]:
        await self.refresh_all()
        plugins = []
        for record in await self.store.list():
            item = dict(record)
            try:
                manifest = parse_plugin_manifest(record["manifest_path"])
                content_digest = plugin_content_digest(manifest.path.parent)
                item["manifest"] = manifest.snapshot()
                item["approval_current"] = self._approval_digest(
                    manifest, content_digest
                ) == record["approved_digest"] and record["lifecycle"] not in {
                    "changed",
                    "invalid",
                    "incompatible",
                }
            except PluginManifestError:
                item["manifest"] = None
                item["approval_current"] = False
            config_dir, state_dir = self._directories(record["id"])
            item.update(config_dir=str(config_dir), state_dir=str(state_dir))
            plugins.append(item)
        return {
            "execution_enabled": self.execution_enabled,
            "host_capabilities": sorted(HOST_CAPABILITIES),
            "plugins": plugins,
        }

    async def status(self) -> dict[str, Any]:
        self._prune_tokens()
        records = await self.store.list()
        return {
            "execution_enabled": self.execution_enabled,
            "installed": len(records),
            "enabled": sum(1 for item in records if item["enabled"]),
            "degraded": sum(
                1 for item in records if item["lifecycle"] in {"invalid", "incompatible", "changed"}
            ),
            "commands_in_flight": len(self._tasks),
            "runtime_tokens": len(self._tokens),
            "event_subscribed": self._event_queue is not None,
        }

    async def inspect(self, path: str | Path) -> dict[str, Any]:
        return inspect_plugin_path(path)

    async def link(
        self, path: str | Path, *, approve: bool = False, enable: bool = False
    ) -> dict[str, Any]:
        manifest = parse_plugin_manifest(path)
        content_digest = await asyncio.to_thread(plugin_content_digest, manifest.path.parent)
        diagnostic = self._compatible(manifest)
        existing = await self.store.get(manifest.id)
        if existing and existing["source_kind"] != "link":
            raise PluginError(
                "source_conflict", "uninstall the managed plugin before linking a directory"
            )
        approved = self._approval_digest(manifest, content_digest) if approve else ""
        lifecycle = (
            "enabled"
            if approve and enable and not diagnostic
            else "approved"
            if approve
            else "inspected"
        )
        record = await self.store.put(
            {
                "id": manifest.id,
                "name": manifest.name,
                "version": manifest.version,
                "enabled": lifecycle == "enabled",
                "lifecycle": lifecycle,
                "source_kind": "link",
                "source_ref": str(manifest.path.parent),
                "root": str(manifest.path.parent),
                "manifest_path": str(manifest.path),
                "manifest_digest": manifest.digest,
                "content_digest": content_digest,
                "security_digest": manifest.security_digest,
                "approved_digest": approved,
                "diagnostic": diagnostic,
            }
        )
        log.info(
            "plugin linked plugin_id=%s root=%s approved=%s enabled=%s",
            manifest.id,
            manifest.path.parent,
            approve,
            record["enabled"],
        )
        if record["enabled"]:
            await self._run_plugin_startup(record, manifest)
        return record

    async def install(
        self, source: str, *, ref: str = "", approve: bool = False, enable: bool = False
    ) -> dict[str, Any]:
        operation = uuid.uuid4().hex
        stage = self.staging / operation
        stage.mkdir(parents=True, exist_ok=False)
        source_path = Path(source)
        source_ref = source
        requested_ref = ref
        selected_ref = ref
        resolved_ref = ""
        plugin_subdir = ""
        try:
            if source_path.exists():
                if ref:
                    raise PluginError("invalid_ref", "a ref applies only to a Git source")
                await asyncio.to_thread(
                    shutil.copytree, source_path.resolve(), stage / "source", dirs_exist_ok=True
                )
            else:
                if "://" in source:
                    repo = source
                else:
                    parts = [part for part in source.split("/") if part]
                    if len(parts) < 2:
                        raise PluginError(
                            "invalid_source", "GitHub source must be owner/repository[/subdir]"
                        )
                    repo = f"https://github.com/{parts[0]}/{parts[1]}.git"
                    plugin_subdir = "/".join(parts[2:])
                if ref == "latest":
                    selected_ref = await self._latest_release_ref(source)
                argv = ["git", "clone", "--filter=blob:none", "--depth", "1"]
                if selected_ref:
                    argv += ["--branch", selected_ref]
                argv += [repo, str(stage / "source")]
                result = await run_bounded(
                    argv,
                    label="plugin install",
                    timeout_seconds=180,
                    output_limit=COMMAND_OUTPUT_LIMIT,
                    operation_id=operation,
                )
                if result.exit_code != 0:
                    raise PluginError(
                        "acquisition_failed",
                        (result.stderr or result.stdout).decode("utf-8", "replace"),
                    )
                head = await run_bounded(
                    ["git", "-C", str(stage / "source"), "rev-parse", "HEAD"],
                    label="plugin revision",
                    timeout_seconds=15,
                    output_limit=4096,
                )
                if head.exit_code == 0:
                    resolved_ref = head.stdout.decode().strip()
            candidate_root = (stage / "source" / plugin_subdir).resolve()
            if not _within(stage / "source", candidate_root):
                raise PluginError("invalid_source", "plugin subdirectory escapes the checkout")
            manifest = parse_plugin_manifest(candidate_root)
            existing = await self.store.get(manifest.id)
            if existing and existing["source_kind"] == "link":
                raise PluginError(
                    "source_conflict", "unlink the developer directory before managed installation"
                )
            container = self.sources / _safe_name(manifest.id)
            container.mkdir(parents=True, exist_ok=True)
            target = container / manifest.digest[:16]
            if target.exists():
                await asyncio.to_thread(shutil.rmtree, candidate_root, True)
            else:
                os.replace(candidate_root, target)
            installed_manifest = parse_plugin_manifest(target)
            content_digest = await asyncio.to_thread(plugin_content_digest, target)
            diagnostic = self._compatible(installed_manifest)
            record = await self.store.put(
                {
                    "id": installed_manifest.id,
                    "name": installed_manifest.name,
                    "version": installed_manifest.version,
                    "enabled": approve and enable and not diagnostic,
                    "lifecycle": "enabled"
                    if approve and enable and not diagnostic
                    else "approved"
                    if approve
                    else "inspected",
                    "source_kind": "managed",
                    "source_ref": source_ref,
                    "requested_ref": requested_ref,
                    "selected_ref": selected_ref,
                    "resolved_ref": resolved_ref,
                    "root": str(target),
                    "manifest_path": str(installed_manifest.path),
                    "manifest_digest": installed_manifest.digest,
                    "content_digest": content_digest,
                    "security_digest": installed_manifest.security_digest,
                    "approved_digest": (
                        self._approval_digest(installed_manifest, content_digest) if approve else ""
                    ),
                    "previous_root": (
                        existing["root"]
                        if existing
                        and Path(existing["root"]).is_dir()
                        and Path(existing["root"]) != target
                        else ""
                    ),
                    "diagnostic": diagnostic,
                }
            )
            log.info(
                "plugin installed plugin_id=%s source=%s requested_ref=%s selected_ref=%s "
                "resolved_ref=%s enabled=%s",
                installed_manifest.id,
                source,
                requested_ref,
                selected_ref,
                resolved_ref,
                record["enabled"],
            )
            if record["enabled"]:
                await self._run_plugin_startup(record, installed_manifest)
            return record
        finally:
            if stage.exists():
                await asyncio.to_thread(shutil.rmtree, stage, True)

    async def approve(self, plugin_id: str, *, enable: bool = True) -> dict[str, Any]:
        record = await self._record(plugin_id)
        manifest = await self._load(record)
        content_digest = await asyncio.to_thread(plugin_content_digest, manifest.path.parent)
        diagnostic = self._compatible(manifest)
        if diagnostic:
            raise PluginError("incompatible", diagnostic)
        updated = await self.store.set_state(
            plugin_id,
            name=manifest.name,
            version=manifest.version,
            approved_digest=self._approval_digest(manifest, content_digest),
            security_digest=manifest.security_digest,
            manifest_digest=manifest.digest,
            content_digest=content_digest,
            enabled=enable,
            lifecycle="enabled" if enable else "approved",
            diagnostic="",
        )
        assert updated is not None
        if enable:
            await self._run_plugin_startup(updated, manifest)
        log.info("plugin approved plugin_id=%s enabled=%s", plugin_id, enable)
        return updated

    async def update(
        self,
        plugin_id: str,
        *,
        ref: str | None = None,
        approve: bool = False,
        enable: bool = False,
    ) -> dict[str, Any]:
        record = await self._record(plugin_id)
        if record["source_kind"] != "managed":
            raise PluginError("not_managed", "linked plugins update in their working directory")
        requested_ref = record["requested_ref"] if ref is None else ref
        return await self.install(
            record["source_ref"], ref=requested_ref, approve=approve, enable=enable
        )

    async def _latest_release_ref(self, source: str) -> str:
        repository = _github_repository(source)
        if repository is None:
            raise PluginError(
                "invalid_ref", "the 'latest' release channel requires a GitHub repository source"
            )
        owner, name = repository
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"swe-mux/{__version__}",
        }
        url = f"{GITHUB_API}/repos/{owner}/{name}/releases/latest"
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(url, allow_redirects=False) as response:
                    if response.status != 200:
                        raise PluginError(
                            "release_unavailable",
                            f"GitHub returned HTTP {response.status} for the latest release",
                        )
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PluginError("release_unavailable", str(exc)) from exc
        tag = payload.get("tag_name") if isinstance(payload, dict) else None
        if not isinstance(tag, str) or not tag.strip():
            raise PluginError("release_unavailable", "GitHub returned no latest release tag")
        log.info(
            "plugin release channel resolved source=%s requested_ref=latest selected_ref=%s",
            source,
            tag,
        )
        return tag

    async def enable(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        record = await self._record(plugin_id)
        manifest = await self._load(record)
        content_digest = await asyncio.to_thread(plugin_content_digest, manifest.path.parent)
        if enabled and self._approval_digest(manifest, content_digest) != record["approved_digest"]:
            raise PluginError("approval_required", "inspect and approve the current manifest first")
        updated = await self.store.set_state(
            plugin_id,
            enabled=enabled,
            lifecycle="enabled" if enabled else "disabled",
            diagnostic="",
        )
        assert updated is not None
        if not enabled:
            self._revoke(plugin_id)
            for task in tuple(self._tasks):
                if task.get_name().startswith(f"plugin:{plugin_id}:"):
                    task.cancel()
        else:
            await self._run_plugin_startup(updated, manifest)
        log.info("plugin enablement changed plugin_id=%s enabled=%s", plugin_id, enabled)
        return updated

    async def rollback_plugin(self, plugin_id: str) -> dict[str, Any]:
        record = await self._record(plugin_id)
        previous = Path(record["previous_root"])
        if record["source_kind"] != "managed" or not previous.is_dir():
            raise PluginError("no_rollback", "no managed rollback is available")
        current = Path(record["root"])
        manifest = parse_plugin_manifest(previous)
        content_digest = await asyncio.to_thread(plugin_content_digest, previous)
        updated = await self.store.set_state(
            plugin_id,
            version=manifest.version,
            manifest_path=str(manifest.path),
            manifest_digest=manifest.digest,
            content_digest=content_digest,
            security_digest=manifest.security_digest,
            approved_digest="",
            enabled=False,
            lifecycle="inspected",
            root=str(previous),
            previous_root=str(current) if current.is_dir() else "",
            diagnostic="rollback restored; approval required",
        )
        assert updated is not None
        self._revoke(plugin_id)
        return updated

    async def uninstall(self, plugin_id: str, *, purge: bool = False) -> dict[str, Any]:
        record = await self._record(plugin_id)
        live_panes = [
            session.record.id
            for session in self.sessions.sessions.values()
            if session.record.plugin_id == plugin_id
            and session.record.state not in {"exited", "crashed"}
        ]
        if live_panes:
            raise PluginError(
                "plugin_in_use",
                "stop the plugin panes before uninstalling: " + ", ".join(live_panes),
            )
        await self.store.set_state(plugin_id, enabled=False, lifecycle="disabled")
        self._revoke(plugin_id)
        for task in tuple(self._tasks):
            if task.get_name().startswith(f"plugin:{plugin_id}:"):
                task.cancel()
        removed = await self.store.remove(plugin_id)
        if record["source_kind"] == "managed":
            container = self.sources / _safe_name(plugin_id)
            if _within(self.sources, container):
                await asyncio.to_thread(shutil.rmtree, container, True)
        if purge:
            config_dir, state_dir = self._directories(plugin_id)
            await asyncio.to_thread(shutil.rmtree, config_dir, True)
            await asyncio.to_thread(shutil.rmtree, state_dir, True)
        log.info("plugin uninstalled plugin_id=%s purged=%s", plugin_id, purge)
        return removed or record

    async def _record(self, plugin_id: str) -> dict[str, Any]:
        record = await self.store.get(plugin_id)
        if not record:
            raise PluginError("plugin_not_found", f"unknown plugin {plugin_id}")
        return record

    async def invoke_action(
        self, plugin_id: str, action_id: str, context: dict[str, Any], *, source: str = "user"
    ) -> dict[str, Any]:
        record = await self._record(plugin_id)
        manifest = await self._load(record, require_enabled=True)
        action = next((item for item in manifest.actions if item.id == action_id), None)
        if action is None:
            raise PluginError("action_not_found", f"unknown action {action_id}")
        self._validate_context(action.contexts, context)
        return await self._run_command(
            record, manifest, "action", action.id, action.command, context, source
        )

    async def open_pane(
        self, plugin_id: str, pane_id: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        record = await self._record(plugin_id)
        manifest = await self._load(record, require_enabled=True)
        pane = next((item for item in manifest.panes if item.id == pane_id), None)
        if pane is None:
            raise PluginError("pane_not_found", f"unknown pane {pane_id}")
        self._validate_context(pane.contexts, context)
        project_id = str(context.get("project_id") or "")
        project = self.projects.projects.get(project_id)
        if project is None:
            raise PluginError("project_required", "a registered project_id is required")
        existing = next(
            (
                item
                for item in self.sessions.sessions.values()
                if item.record.project_id == project.id
                and item.record.plugin_id == manifest.id
                and item.record.plugin_entrypoint_id == pane.id
                and item.record.state not in {"exited", "crashed"}
                and not item.record.inactive
            ),
            None,
        )
        if existing is not None:
            has_live_grant = any(
                grant.session_id == existing.record.id for grant in self._tokens.values()
            )
            if has_live_grant:
                snapshot = existing.record.snapshot()
                snapshot["spawn_env"] = {}
                log.info(
                    "plugin pane focused plugin_id=%s pane_id=%s session_id=%s project_id=%s",
                    plugin_id,
                    pane_id,
                    existing.record.id,
                    project.id,
                )
                return {"session": snapshot, "placement": pane.placement, "reused": True}
            # The PTY can outlive the daemon generation that issued its callback token.
            # Launching the tool again is an explicit request for a usable pane, so replace
            # that stale process instead of focusing a UI whose callbacks can only fail.
            await self.sessions.stop(existing.record.id, reason="plugin pane reopened")
        env, token = self._environment(record, manifest, "pane", pane.id, context, lifetime=None)
        command, cwd = self._resolve_command(manifest, pane.command)
        session = await self.sessions.spawn(
            backend="shell",
            name=pane.title,
            cwd=project.root,
            project_id=project.id,
            exe=command[0],
            args=list(command[1:]),
            extra_env=env,
            retain_extra_env=False,
            project_label=project.name,
            start_cwd=str(cwd),
            completion_mode="interactive",
        )
        session.record.plugin_id = manifest.id
        session.record.plugin_version = manifest.version
        session.record.plugin_entrypoint_id = pane.id
        session.record.plugin_placement = pane.placement
        self._tokens[token].session_id = session.record.id
        log.info(
            "plugin pane opened plugin_id=%s pane_id=%s session_id=%s project_id=%s",
            plugin_id,
            pane_id,
            session.record.id,
            project.id,
        )
        snapshot = session.record.snapshot()
        snapshot["spawn_env"] = {}
        return {"session": snapshot, "placement": pane.placement, "reused": False}

    async def link_handlers(self) -> builtins.list[dict[str, Any]]:
        handlers: builtins.list[dict[str, Any]] = []
        for record in await self.store.list():
            if not record["enabled"]:
                continue
            try:
                manifest = await self._load(record, require_enabled=True)
            except PluginError:
                continue
            for item in manifest.link_handlers:
                if item.platforms and current_platform() not in item.platforms:
                    continue
                handlers.append({"plugin_id": manifest.id, **asdict(item)})
        return handlers

    async def activate_link(
        self, plugin_id: str, handler_id: str, url: str, context: dict[str, Any]
    ) -> dict[str, Any]:
        record = await self._record(plugin_id)
        manifest = await self._load(record, require_enabled=True)
        handler = next((item for item in manifest.link_handlers if item.id == handler_id), None)
        if handler is None or re.search(handler.pattern, url) is None:
            raise PluginError("link_not_matched", "the enabled handler does not match this URL")
        context = {**context, "clicked_url": url, "link_handler_id": handler.id}
        return await self.invoke_action(plugin_id, handler.action, context, source="link")

    def authorize(self, token: str, permission: str) -> PluginToken:
        self._prune_tokens()
        grant = self._tokens.get(token)
        if grant is None or (grant.expires_at is not None and grant.expires_at < time.time()):
            self._tokens.pop(token, None)
            raise PluginError("invalid_token", "plugin token is invalid or expired")
        if grant.session_id is not None:
            session = self.sessions.sessions.get(grant.session_id)
            if session is None or session.record.state in {"exited", "crashed"}:
                self._tokens.pop(token, None)
                raise PluginError("invalid_token", "plugin pane token outlived its session")
        if permission not in grant.permissions:
            raise PluginError("permission_denied", f"plugin lacks {permission}")
        return grant

    def _revoke(self, plugin_id: str) -> None:
        for token, grant in tuple(self._tokens.items()):
            if grant.plugin_id == plugin_id:
                self._tokens.pop(token, None)

    def _prune_tokens(self) -> None:
        now = time.time()
        for token, grant in tuple(self._tokens.items()):
            expired = grant.expires_at is not None and grant.expires_at < now
            session = (
                self.sessions.sessions.get(grant.session_id)
                if grant.session_id is not None
                else None
            )
            pane_ended = grant.session_id is not None and (
                session is None or session.record.state in {"exited", "crashed"}
            )
            if expired or pane_ended:
                self._tokens.pop(token, None)

    async def set_execution_enabled(self, enabled: bool) -> None:
        self.execution_enabled = enabled
        await self.store.set_execution_enabled(enabled)
        if not enabled:
            for record in await self.store.list():
                self._revoke(record["id"])
            for task in tuple(self._tasks):
                if task.get_name().startswith("plugin:"):
                    task.cancel()
        log.warning("plugin execution policy changed enabled=%s", enabled)

    def _validate_context(self, allowed: tuple[str, ...], context: dict[str, Any]) -> None:
        kind = str(context.get("context") or ("project" if context.get("project_id") else "global"))
        if kind not in allowed:
            raise PluginError("invalid_context", f"contribution is not available in {kind} context")
        encoded = json.dumps(context, separators=(",", ":")).encode()
        if len(encoded) > CONTEXT_LIMIT:
            raise PluginError("context_too_large", "plugin invocation context exceeds 32 KiB")

    def _resolve_command(
        self, manifest: PluginManifest, spec: CommandSpec
    ) -> tuple[tuple[str, ...], Path]:
        root = manifest.path.parent.resolve()
        cwd = (root / spec.cwd).resolve()
        if not _within(root, cwd) or not cwd.is_dir():
            raise PluginError("invalid_cwd", "plugin command cwd is unavailable")
        command = builtins.list(spec.command)
        executable = Path(command[0])
        if not executable.is_absolute() and (
            "/" in command[0] or "\\" in command[0] or command[0].startswith(".")
        ):
            candidate = (root / executable).resolve()
            if not _within(root, candidate):
                raise PluginError("invalid_executable", "plugin executable escapes its source root")
            command[0] = str(candidate)
        return tuple(command), cwd

    def _environment(
        self,
        record: dict[str, Any],
        manifest: PluginManifest,
        kind: str,
        contribution_id: str,
        context: dict[str, Any],
        *,
        lifetime: float | None = 300.0,
    ) -> tuple[dict[str, str], str]:
        self._prune_tokens()
        config_dir, state_dir = self._directories(manifest.id)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = PluginToken(
            manifest.id,
            manifest.version,
            frozenset(manifest.permissions),
            f"{kind}:{contribution_id}",
            None if lifetime is None else time.time() + lifetime,
        )
        base = base_session_env(os.environ, "shell")
        for key in tuple(base):
            if key.startswith(("MUX_", "SWEMUX_")) or key.endswith(
                ("_TOKEN", "_SECRET", "_API_KEY")
            ):
                base.pop(key, None)
        base.update(
            {
                "SWEMUX_PLUGIN_ID": manifest.id,
                "SWEMUX_PLUGIN_VERSION": manifest.version,
                "SWEMUX_PLUGIN_ROOT": str(manifest.path.parent),
                "SWEMUX_PLUGIN_CONFIG_DIR": str(config_dir),
                "SWEMUX_PLUGIN_STATE_DIR": str(state_dir),
                "SWEMUX_PLUGIN_CONTRIBUTION_KIND": kind,
                "SWEMUX_PLUGIN_CONTRIBUTION_ID": contribution_id,
                "SWEMUX_PLUGIN_CONTEXT_JSON": json.dumps(context, separators=(",", ":")),
                "SWEMUX_PLUGIN_TOKEN": token,
                "SWEMUX_API_URL": f"http://127.0.0.1:{self.port}/api/plugins/callback",
                "SWEMUX_BIN_PATH": shutil.which("swemux") or sys.executable,
            }
        )
        return base, token

    async def _run_command(
        self,
        record: dict[str, Any],
        manifest: PluginManifest,
        kind: str,
        contribution_id: str,
        spec: CommandSpec,
        context: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        if not self.execution_enabled:
            raise PluginError("execution_disabled", "plugin execution is disabled globally")
        if spec.platforms and current_platform() not in spec.platforms:
            raise PluginError("unsupported_platform", "contribution is unavailable on this host")
        command, cwd = self._resolve_command(manifest, spec)
        env, token = self._environment(record, manifest, kind, contribution_id, context)
        env.update(spec.env)
        log_id = f"plog_{uuid.uuid4().hex}"
        correlation = uuid.uuid4().hex
        started = time.time()
        await self.store.log_started(
            {
                "id": log_id,
                "plugin_id": manifest.id,
                "contribution_kind": kind,
                "contribution_id": contribution_id,
                "invocation_source": source,
                "correlation_id": correlation,
                "context": context,
                "started_at": started,
            }
        )
        semaphore = self._per_plugin.setdefault(
            manifest.id, asyncio.Semaphore(PER_PLUGIN_CONCURRENCY)
        )
        try:
            async with self._global, semaphore:
                outcome = await run_bounded(
                    command,
                    label=f"plugin {manifest.id}/{contribution_id}",
                    timeout_seconds=spec.timeout_seconds,
                    output_limit=COMMAND_OUTPUT_LIMIT,
                    cwd=cwd,
                    env=env,
                    operation_id=correlation,
                )
            status = (
                "timed_out"
                if outcome.timed_out
                else "succeeded"
                if outcome.exit_code == 0
                else "failed"
            )
            result = {
                "id": log_id,
                "plugin_id": manifest.id,
                "contribution_kind": kind,
                "contribution_id": contribution_id,
                "correlation_id": correlation,
                "outcome": status,
                "exit_code": outcome.exit_code,
                "duration_ms": outcome.duration_ms,
                "stdout": outcome.stdout.decode("utf-8", "replace"),
                "stderr": outcome.stderr.decode("utf-8", "replace"),
                "stdout_truncated": outcome.stdout_truncated,
                "stderr_truncated": outcome.stderr_truncated,
            }
            await self.store.log_finished(log_id, **result)
            log.info(
                "plugin command finished plugin_id=%s kind=%s contribution_id=%s "
                "outcome=%s duration_ms=%.1f correlation_id=%s",
                manifest.id,
                kind,
                contribution_id,
                status,
                outcome.duration_ms,
                correlation,
            )
            return result
        except asyncio.CancelledError:
            await self.store.log_finished(
                log_id,
                outcome="cancelled",
                diagnostic="cancelled",
                duration_ms=(time.time() - started) * 1000,
            )
            raise
        except Exception as exc:
            await self.store.log_finished(
                log_id,
                outcome="failed",
                diagnostic=str(exc),
                duration_ms=(time.time() - started) * 1000,
            )
            log.exception(
                "plugin command failed plugin_id=%s kind=%s contribution_id=%s correlation_id=%s",
                manifest.id,
                kind,
                contribution_id,
                correlation,
            )
            raise
        finally:
            self._tokens.pop(token, None)

    async def _run_plugin_startup(self, record: dict[str, Any], manifest: PluginManifest) -> None:
        for item in manifest.startup:
            task = asyncio.create_task(
                self._run_command(
                    record,
                    manifest,
                    "startup",
                    item.id,
                    item.command,
                    {"context": "global", "event": "startup"},
                    "startup",
                ),
                name=f"plugin:{manifest.id}:startup:{item.id}",
            )
            self._track(task)

    async def _run_startup_hooks(self) -> None:
        await asyncio.sleep(1.0)
        for record in await self.store.list():
            if not record["enabled"]:
                continue
            try:
                await self._run_plugin_startup(
                    record, await self._load(record, require_enabled=True)
                )
            except PluginError:
                continue

    async def _event_loop(self) -> None:
        assert self._event_queue is not None
        while True:
            event = await self._event_queue.get()
            with background.iteration(PLUGIN_EVENT_LOOP):
                await self._dispatch_event(event)

    async def _dispatch_event(self, event: Any) -> None:
        if not self.execution_enabled:
            return
        snapshot = event.snapshot()
        event_key = f"{event.seq}:{event.type}:{event.ts}"
        if event_key in self._event_seen:
            return
        self._event_seen[event_key] = time.time()
        if len(self._event_seen) > 2000:
            cutoff = time.time() - 3600
            self._event_seen = {key: ts for key, ts in self._event_seen.items() if ts >= cutoff}
        for record in await self.store.list():
            if not record["enabled"] or event.payload.get("plugin_id") == record["id"]:
                continue
            try:
                manifest = await self._load(record, require_enabled=True)
            except PluginError:
                continue
            for hook in manifest.events:
                if hook.on != event.type or not self._event_matches(hook, snapshot):
                    continue
                key = (manifest.id, hook.id)
                now = time.monotonic()
                if now - self._last_event_run.get(key, 0.0) < hook.rate_limit_seconds:
                    continue
                self._last_event_run[key] = now
                context = {"context": "global", "event": snapshot}
                task = asyncio.create_task(
                    self._run_command(
                        record, manifest, "event", hook.id, hook.command, context, "event"
                    ),
                    name=f"plugin:{manifest.id}:event:{hook.id}",
                )
                self._track(task)

    @staticmethod
    def _event_matches(hook: PluginEventHook, snapshot: dict[str, Any]) -> bool:
        values = {
            "type": str(snapshot.get("type", "")),
            "source": str(snapshot.get("source", "")),
            "session_id": str(snapshot.get("session_id") or ""),
        }
        values.update(
            {
                str(key): str(value)
                for key, value in (snapshot.get("payload") or {}).items()
                if isinstance(value, str | int | float | bool)
            }
        )
        return all(
            fnmatch.fnmatchcase(values.get(key, ""), pattern) for key, pattern in hook.match.items()
        )

    async def marketplace(self) -> dict[str, Any]:
        timeout = aiohttp.ClientTimeout(total=10)
        headers = {"Accept": "application/vnd.github+json", "User-Agent": f"swe-mux/{__version__}"}
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(MARKETPLACE_CATALOG_URL, allow_redirects=False) as response:
                    if response.status != 200:
                        raise PluginError(
                            "marketplace_unavailable",
                            f"catalog returned HTTP {response.status}",
                        )
                    payload = await response.json()
            repositories = self._catalog_repositories(payload)
            log.info(
                "plugin marketplace catalog loaded source=%s repositories=%s generated_at=%s",
                MARKETPLACE_CATALOG_URL,
                len(repositories),
                payload.get("generated_at", ""),
            )
            return {
                "unreviewed": True,
                "repositories": repositories,
                "source": "swemux-catalog",
                "generated_at": payload.get("generated_at"),
            }
        except (aiohttp.ClientError, TimeoutError, PluginError, ValueError, TypeError) as exc:
            log.warning(
                "plugin marketplace catalog unavailable; falling back to GitHub topic: %s", exc
            )
        return await self._topic_marketplace(client_timeout=timeout, headers=headers)

    @staticmethod
    def _catalog_repositories(payload: Any) -> builtins.list[dict[str, Any]]:
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise PluginError("marketplace_unavailable", "catalog schema is unsupported")
        plugins = payload.get("plugins")
        if not isinstance(plugins, list):
            raise PluginError("marketplace_unavailable", "catalog plugin list is missing")
        repositories = []
        for item in plugins[:50]:
            if not isinstance(item, dict):
                continue
            repository = item.get("repository")
            manifest = item.get("manifest")
            if not isinstance(repository, dict) or not isinstance(manifest, dict):
                continue
            full_name = repository.get("full_name")
            if not isinstance(full_name, str) or "/" not in full_name:
                continue
            repositories.append(
                {
                    "name": repository.get("name"),
                    "full_name": full_name,
                    "owner": repository.get("owner"),
                    "description": (
                        manifest.get("description") or repository.get("description") or ""
                    ),
                    "stars": repository.get("stars", 0),
                    "language": repository.get("language"),
                    "updated_at": repository.get("updated_at"),
                    "url": repository.get("url"),
                    "license": manifest.get("license") or repository.get("license"),
                    "unreviewed": True,
                    "official": bool(item.get("official")),
                    "plugin_id": manifest.get("id"),
                    "plugin_name": manifest.get("name"),
                    "plugin_version": manifest.get("version"),
                    "permissions": manifest.get("permissions") or [],
                    "requires": manifest.get("requires") or [],
                    "platforms": manifest.get("platforms") or [],
                    "runtime_requirements": manifest.get("runtime_requirements") or [],
                    "indexed_ref": item.get("indexed_ref"),
                    "install_ref": item.get("install_ref") or "",
                    "release_url": item.get("release_url"),
                }
            )
        return repositories

    async def _topic_marketplace(
        self, *, client_timeout: aiohttp.ClientTimeout, headers: dict[str, str]
    ) -> dict[str, Any]:
        try:
            async with aiohttp.ClientSession(timeout=client_timeout, headers=headers) as session:
                async with session.get(MARKETPLACE_URL, allow_redirects=False) as response:
                    if response.status != 200:
                        raise PluginError(
                            "marketplace_unavailable", f"GitHub returned HTTP {response.status}"
                        )
                    payload = await response.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise PluginError("marketplace_unavailable", str(exc)) from exc
        repositories = []
        for item in payload.get("items", [])[:50]:
            if item.get("fork") or item.get("archived"):
                continue
            repositories.append(
                {
                    "name": item.get("name"),
                    "full_name": item.get("full_name"),
                    "owner": (item.get("owner") or {}).get("login"),
                    "description": item.get("description") or "",
                    "stars": item.get("stargazers_count", 0),
                    "language": item.get("language"),
                    "updated_at": item.get("updated_at"),
                    "url": item.get("html_url"),
                    "license": ((item.get("license") or {}).get("spdx_id")),
                    "unreviewed": True,
                }
            )
        log.info("plugin marketplace GitHub topic loaded repositories=%s", len(repositories))
        return {"unreviewed": True, "repositories": repositories, "source": "github-topic"}
