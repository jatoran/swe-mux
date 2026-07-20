from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

import aiohttp

from .event_bus import EventBus

Provider = Literal["claude", "codex"]
CurrentAccountState = Literal["saved", "external", "signed_out", "unreadable"]

PROVIDERS: tuple[Provider, ...] = ("claude", "codex")
MANIFEST_VERSION = 1
POLL_SECONDS = 15 * 60
STALE_SECONDS = 30 * 60
IDENTITY_PROBE_COOLDOWN_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
LOGIN_TIMEOUT_SECONDS = 5 * 60
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


class ProviderAccountError(RuntimeError):
    pass


def _provider(value: str) -> Provider:
    if value not in PROVIDERS:
        raise ProviderAccountError("provider must be claude or codex")
    return value


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _jwt_payload(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    encoded = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        return _record(json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8")))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def _reset_timestamp(value: object) -> float | None:
    numeric = _number(value)
    if numeric is not None:
        return numeric / 1000 if numeric > 10_000_000_000 else numeric
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return time.mktime(time.strptime(value.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z"))
    except ValueError:
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None


def _scoped_weekly_window(payload: dict[str, Any], display_name: str) -> dict[str, Any] | None:
    """Extract a per-model weekly cap from the ``limits`` array.

    Claude exposes model-scoped weekly quotas (e.g. Fable) as ``weekly_scoped``
    entries with ``scope.model.display_name`` rather than a dedicated top-level
    field, so they are matched by display name here.
    """
    for entry in payload.get("limits") or []:
        item = _record(entry)
        if item.get("group") != "weekly":
            continue
        model = _record(_record(item.get("scope")).get("model"))
        name = _string(model.get("display_name"))
        if name is None or name.lower() != display_name.lower():
            continue
        percent = _number(item.get("percent"))
        if percent is None:
            return None
        return {
            "used_percent": min(100.0, max(0.0, percent)),
            "window_minutes": 10080,
            "resets_at": _reset_timestamp(item.get("resets_at")),
        }
    return None


def _window(raw: object, minutes: int, *, backend: bool = False) -> dict[str, Any] | None:
    item = _record(raw)
    percent = _number(item.get("used_percent" if backend else "utilization"))
    if percent is None and not backend:
        percent = _number(item.get("used_percentage"))
    if percent is None:
        return None
    if backend:
        duration = _number(item.get("limit_window_seconds"))
        if duration and duration > 0:
            minutes = math.ceil(duration / 60)
    reset_key = "reset_at" if backend else "resets_at"
    return {
        "used_percent": min(100.0, max(0.0, percent)),
        "window_minutes": minutes,
        "resets_at": _reset_timestamp(item.get(reset_key)),
    }


# Codex reports quota windows positionally (primary/secondary), but the split it
# uses changes over time — e.g. the 5-hour window was temporarily removed, leaving
# only a weekly window in the primary slot. Classifying each window by its real
# duration keeps the session/weekly mapping accurate and self-heals automatically
# if the provider reinstates a different split. Windows up to a day are the rolling
# "session" bucket; anything longer is the "weekly" bucket.
SESSION_WINDOW_MAX_MINUTES = 24 * 60


def _classify_windows(
    *windows: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    session: dict[str, Any] | None = None
    weekly: dict[str, Any] | None = None
    for window in windows:
        if not window:
            continue
        minutes = window.get("window_minutes") or 0
        if 0 < minutes <= SESSION_WINDOW_MAX_MINUTES:
            if session is None or minutes < (session.get("window_minutes") or 0):
                session = window
        elif weekly is None or minutes > (weekly.get("window_minutes") or 0):
            weekly = window
    return session, weekly


class ProviderAccountManager:
    """Owns provider credential snapshots, global selection, and quota polling.

    Only auth files are copied. Provider configuration, skills, transcripts, and
    project state remain in their normal shared directories.
    """

    def __init__(
        self,
        data_dir: Path,
        events: EventBus,
        *,
        home: Path | None = None,
        claude_exe: str = "claude.exe",
        codex_exe: str = "codex.exe",
        poll_seconds: float = POLL_SECONDS,
        telemetry: Any | None = None,
        sessions: Any | None = None,
        turn_refresh_enabled: bool = False,
        turn_refresh_min_seconds: float = 300.0,
    ) -> None:
        self.data_dir = data_dir
        self.home = home or Path.home()
        self.events = events
        self.executables: dict[Provider, str] = {
            "claude": claude_exe,
            "codex": codex_exe,
        }
        self.poll_seconds = poll_seconds
        self.telemetry = telemetry
        self.sessions = sessions
        self.turn_refresh_enabled = turn_refresh_enabled
        self.turn_refresh_min_seconds = turn_refresh_min_seconds
        self.root = data_dir / "provider-accounts"
        self.manifest_path = data_dir / "provider-accounts.json"
        self._manifest = self._load()
        self._identity_cache: dict[Provider, tuple[str, dict[str, Any]]] = {}
        self._identity_probe_attempts: dict[Provider, tuple[str, float]] = {}
        self._current: dict[Provider, dict[str, Any]] = {}
        self._reconcile_current()
        self._mutation_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._event_queue: asyncio.Queue[Any] | None = None
        self._last_event_refresh = 0.0
        self._http: aiohttp.ClientSession | None = None

    def _empty_manifest(self) -> dict[str, Any]:
        return {
            "version": MANIFEST_VERSION,
            "selected": {"claude": None, "codex": None},
            "accounts": [],
            "quota": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return self._empty_manifest()
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_manifest()
        if not isinstance(value, dict) or value.get("version") != MANIFEST_VERSION:
            return self._empty_manifest()
        value.setdefault("selected", {"claude": None, "codex": None})
        value.setdefault("accounts", [])
        value.setdefault("quota", {})
        return value

    def _write(self) -> None:
        _atomic_write(
            self.manifest_path,
            (json.dumps(self._manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    def _accounts(self) -> list[dict[str, Any]]:
        accounts = self._manifest.get("accounts")
        return accounts if isinstance(accounts, list) else []

    def _account(self, account_id: str) -> dict[str, Any]:
        account = next((item for item in self._accounts() if item.get("id") == account_id), None)
        if not account:
            raise ProviderAccountError("provider account not found")
        return account

    def _managed_auth_path(self, provider: Provider, account_id: str) -> Path:
        return (
            self.root
            / provider
            / account_id
            / (".credentials.json" if provider == "claude" else "auth.json")
        )

    def _system_auth_path(self, provider: Provider) -> Path:
        return (
            self.home / ".claude" / ".credentials.json"
            if provider == "claude"
            else self.home / ".codex" / "auth.json"
        )

    def _read_json_auth(self, path: Path) -> tuple[bytes, dict[str, Any]]:
        try:
            content = path.read_bytes()
            parsed = json.loads(content)
        except FileNotFoundError as exc:
            raise ProviderAccountError(f"No signed-in credentials found at {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderAccountError(f"Credentials at {path} are unreadable") from exc
        if not isinstance(parsed, dict):
            raise ProviderAccountError(f"Credentials at {path} are malformed")
        return content, parsed

    def _identity(self, provider: Provider, auth: dict[str, Any]) -> dict[str, str | None]:
        if provider == "claude":
            oauth = _record(auth.get("claudeAiOauth"))
            return {
                "email": _string(oauth.get("email")),
                "provider_account_id": _string(oauth.get("organizationUuid"))
                or _string(oauth.get("organizationId")),
                "organization": _string(oauth.get("organizationName")),
            }
        tokens = _record(auth.get("tokens"))
        payload = _jwt_payload(_string(tokens.get("id_token")) or _string(tokens.get("idToken")))
        auth_claims = _record(payload.get("https://api.openai.com/auth"))
        profile = _record(payload.get("https://api.openai.com/profile"))
        return {
            "email": _string(payload.get("email")) or _string(profile.get("email")),
            "provider_account_id": _string(tokens.get("account_id"))
            or _string(tokens.get("accountId"))
            or _string(auth_claims.get("chatgpt_account_id")),
            "organization": _string(auth_claims.get("workspace_name"))
            or _string(profile.get("workspace_name")),
        }

    @staticmethod
    def _identity_key(account: dict[str, Any]) -> tuple[str, str] | None:
        account_id = _string(account.get("provider_account_id"))
        if account_id:
            return ("id", account_id.casefold())
        email = _string(account.get("email"))
        if email:
            return ("email", email.casefold())
        return None

    async def _status_identity(self, provider: Provider) -> dict[str, str | None]:
        if provider != "claude":
            return {"email": None, "provider_account_id": None, "organization": None}
        try:
            output = await self._run_command(
                provider, ["auth", "status", "--json"], timeout_seconds=15
            )
            status = _record(json.loads(output))
        except (ProviderAccountError, json.JSONDecodeError):
            return {"email": None, "provider_account_id": None, "organization": None}
        return {
            "email": _string(status.get("email")),
            "provider_account_id": _string(status.get("organizationUuid"))
            or _string(status.get("organizationId")),
            "organization": _string(status.get("organizationName")),
        }

    def _public_account(self, account: dict[str, Any]) -> dict[str, Any]:
        quota = _record(_record(self._manifest.get("quota")).get(str(account["id"])))
        return {
            key: account.get(key)
            for key in (
                "id",
                "provider",
                "label",
                "email",
                "provider_account_id",
                "organization",
                "created_at",
                "updated_at",
            )
        } | {"quota": quota or None}

    @staticmethod
    def _current_status(
        state: CurrentAccountState,
        *,
        account_id: str | None = None,
        identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_identity = identity or {}
        return {
            "state": state,
            "account_id": account_id,
            "email": _string(public_identity.get("email")),
            "provider_account_id": _string(public_identity.get("provider_account_id")),
            "organization": _string(public_identity.get("organization")),
        }

    def _matching_account(
        self, provider: Provider, digest: str, identity: dict[str, Any]
    ) -> dict[str, Any] | None:
        exact = next(
            (
                item
                for item in self._accounts()
                if item.get("provider") == provider and item.get("auth_digest") == digest
            ),
            None,
        )
        if exact is not None:
            return exact
        identity_key = self._identity_key(identity)
        if identity_key is None:
            return None
        matches = [
            item
            for item in self._accounts()
            if item.get("provider") == provider and self._identity_key(item) == identity_key
        ]
        return matches[0] if len(matches) == 1 else None

    def _sync_managed_auth(
        self,
        provider: Provider,
        account: dict[str, Any],
        content: bytes,
        digest: str,
        identity: dict[str, Any],
    ) -> bool:
        managed_path = self._managed_auth_path(provider, str(account["id"]))
        try:
            managed_matches = managed_path.read_bytes() == content
        except OSError:
            managed_matches = False
        if not managed_matches:
            try:
                _atomic_write(managed_path, content)
            except OSError:
                return False
        changed = account.get("auth_digest") != digest
        for key in ("email", "provider_account_id", "organization"):
            value = _string(identity.get(key))
            if value and account.get(key) != value:
                account[key] = value
                changed = True
        if changed:
            account["auth_digest"] = digest
            account["updated_at"] = time.time()
        return True

    def _reconcile_current(self, *, write: bool = True) -> None:
        """Make the live system auth files authoritative without replacing them."""
        selected_value = self._manifest.get("selected")
        if not isinstance(selected_value, dict):
            selected_value = {provider: None for provider in PROVIDERS}
            self._manifest["selected"] = selected_value
        changed = False
        current: dict[Provider, dict[str, Any]] = {}
        for provider in PROVIDERS:
            auth_path = self._system_auth_path(provider)
            try:
                content, auth = self._read_json_auth(auth_path)
            except ProviderAccountError:
                state: CurrentAccountState = "unreadable" if auth_path.exists() else "signed_out"
                current[provider] = self._current_status(state)
                if selected_value.get(provider) is not None:
                    selected_value[provider] = None
                    changed = True
                continue

            digest = hashlib.sha256(content).hexdigest()
            identity = self._identity(provider, auth)
            cached_identity = self._identity_cache.get(provider)
            if cached_identity and cached_identity[0] == digest:
                identity = {
                    key: cached_identity[1].get(key) or identity.get(key)
                    for key in ("email", "provider_account_id", "organization")
                }
            elif cached_identity:
                self._identity_cache.pop(provider, None)
            account = self._matching_account(provider, digest, identity)
            if account is not None:
                account_changed = account.get("auth_digest") != digest or any(
                    _string(identity.get(key)) is not None
                    and account.get(key) != _string(identity.get(key))
                    for key in ("email", "provider_account_id", "organization")
                )
                if not self._sync_managed_auth(provider, account, content, digest, identity):
                    account = None
                elif account_changed:
                    changed = True

            account_id = str(account["id"]) if account is not None else None
            if selected_value.get(provider) != account_id:
                selected_value[provider] = account_id
                changed = True
            if account is None:
                current[provider] = self._current_status("external", identity=identity)
            else:
                public_identity = {
                    key: identity.get(key) or account.get(key)
                    for key in ("email", "provider_account_id", "organization")
                }
                current[provider] = self._current_status(
                    "saved", account_id=account_id, identity=public_identity
                )
        self._current = current
        if changed and write:
            self._write()

    async def reconcile_current(
        self, *, force_identity_probe: bool = False
    ) -> dict[str, Any]:
        """Reconcile live auth, resolving identity-free credentials when needed."""
        async with self._mutation_lock:
            self._reconcile_current()
            previous = {
                provider: _record(self._current.get(provider)).get("account_id")
                for provider in PROVIDERS
            }
            for provider in PROVIDERS:
                current = self._current.get(provider, {})
                if current.get("state") != "external" or self._identity_key(current):
                    continue
                for _attempt in range(2):
                    try:
                        before, _ = self._read_json_auth(self._system_auth_path(provider))
                    except ProviderAccountError:
                        break
                    before_digest = hashlib.sha256(before).hexdigest()
                    probe_attempt = self._identity_probe_attempts.get(provider)
                    if (
                        not force_identity_probe
                        and probe_attempt is not None
                        and probe_attempt[0] == before_digest
                        and time.monotonic() - probe_attempt[1]
                        < IDENTITY_PROBE_COOLDOWN_SECONDS
                    ):
                        break
                    self._identity_probe_attempts[provider] = (
                        before_digest,
                        time.monotonic(),
                    )
                    status_identity = await self._status_identity(provider)
                    if self._identity_key(status_identity) is None:
                        break
                    try:
                        after, _ = self._read_json_auth(self._system_auth_path(provider))
                    except ProviderAccountError:
                        break
                    after_digest = hashlib.sha256(after).hexdigest()
                    if before_digest != after_digest:
                        continue
                    self._identity_cache[provider] = (after_digest, status_identity)
                    break
            self._reconcile_current()
            selected = dict(_record(self._manifest.get("selected")))

        for provider in PROVIDERS:
            account_id = selected.get(provider)
            if account_id is not None and account_id != previous.get(provider):
                await self.events.emit(
                    "provider_account_reconciled",
                    source="provider_accounts",
                    provider=provider,
                    account_id=account_id,
                )
        return self.snapshot()

    async def reconcile_startup(self) -> dict[str, Any]:
        """Reconcile live auth before background provider work begins."""
        return await self.reconcile_current(force_identity_probe=True)

    def _system_matches_account(self, provider: Provider, account: dict[str, Any]) -> bool:
        try:
            content, _ = self._read_json_auth(self._system_auth_path(provider))
        except ProviderAccountError:
            return False
        return hashlib.sha256(content).hexdigest() == account.get("auth_digest")

    def snapshot(self) -> dict[str, Any]:
        self._reconcile_current()
        selected = _record(self._manifest.get("selected"))
        return {
            "providers": list(PROVIDERS),
            "selected": {provider: selected.get(provider) for provider in PROVIDERS},
            "current": {provider: self._current[provider] for provider in PROVIDERS},
            "accounts": [self._public_account(account) for account in self._accounts()],
            "poll_minutes": self.poll_seconds / 60,
            "stale_minutes": STALE_SECONDS / 60,
            "refreshing": self._refresh_lock.locked(),
        }

    async def capture_current(
        self, provider_value: str, *, label: str | None = None, replace_id: str | None = None
    ) -> dict[str, Any]:
        provider = _provider(provider_value)
        async with self._mutation_lock:
            content, auth = self._read_json_auth(self._system_auth_path(provider))
            identity = self._identity(provider, auth)
            status_identity = await self._status_identity(provider)
            identity = {
                key: status_identity.get(key) or identity.get(key)
                for key in ("email", "provider_account_id", "organization")
            }
            digest = hashlib.sha256(content).hexdigest()
            identity_key = self._identity_key(identity)
            account: dict[str, Any] | None = None
            if replace_id:
                account = self._account(replace_id)
                if account.get("provider") != provider:
                    raise ProviderAccountError("account provider does not match")
            if account is None and identity_key:
                account = next(
                    (
                        item
                        for item in self._accounts()
                        if item.get("provider") == provider
                        and self._identity_key(item) == identity_key
                    ),
                    None,
                )
            if account is None:
                account = next(
                    (
                        item
                        for item in self._accounts()
                        if item.get("provider") == provider and item.get("auth_digest") == digest
                    ),
                    None,
                )
            now = time.time()
            if account is None:
                account = {
                    "id": uuid.uuid4().hex,
                    "provider": provider,
                    "created_at": now,
                }
                self._accounts().append(account)
            account.update(identity)
            account.update(
                {
                    "label": (label or "").strip()
                    or identity.get("email")
                    or identity.get("organization")
                    or f"{provider.title()} account",
                    "updated_at": now,
                    "auth_digest": digest,
                }
            )
            _atomic_write(self._managed_auth_path(provider, str(account["id"])), content)
            _record(self._manifest["selected"])[provider] = account["id"]
            self._reconcile_current(write=False)
            self._write()
        await self.events.emit(
            "provider_account_captured",
            source="provider_accounts",
            provider=provider,
            account_id=account["id"],
        )
        await self.refresh(str(account["id"]))
        return self.snapshot()

    async def select(self, provider_value: str, account_id: str) -> dict[str, Any]:
        provider = _provider(provider_value)
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            content, _ = self._read_json_auth(self._managed_auth_path(provider, account_id))
            _atomic_write(self._system_auth_path(provider), content)
            account["auth_digest"] = hashlib.sha256(content).hexdigest()
            account["updated_at"] = time.time()
            _record(self._manifest["selected"])[provider] = account_id
            self._reconcile_current(write=False)
            self._write()
        await self.events.emit(
            "provider_account_selected",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )
        await self.refresh(account_id)
        return self.snapshot()

    async def remove(self, provider_value: str, account_id: str) -> dict[str, Any]:
        provider = _provider(provider_value)
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            self._manifest["accounts"] = [
                item for item in self._accounts() if item.get("id") != account_id
            ]
            selected = _record(self._manifest["selected"])
            if selected.get(provider) == account_id:
                selected[provider] = None
            _record(self._manifest["quota"]).pop(account_id, None)
            account_dir = self.root / provider / account_id
            if account_dir.is_dir() and account_dir.parent == self.root / provider:
                shutil.rmtree(account_dir)
            self._reconcile_current(write=False)
            self._write()
        await self.events.emit(
            "provider_account_removed",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )
        return self.snapshot()

    async def rename(self, provider_value: str, account_id: str, label: str) -> dict[str, Any]:
        provider = _provider(provider_value)
        clean_label = label.strip()
        if not clean_label:
            raise ProviderAccountError("account label must not be empty")
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            account["label"] = clean_label
            account["updated_at"] = time.time()
            self._write()
        return self.snapshot()

    async def login_and_capture(
        self, provider_value: str, *, label: str | None = None, replace_id: str | None = None
    ) -> dict[str, Any]:
        provider = _provider(provider_value)
        args = ["auth", "login", "--claudeai"] if provider == "claude" else ["login"]
        await self._run_command(provider, args, timeout_seconds=LOGIN_TIMEOUT_SECONDS)
        return await self.capture_current(provider, label=label, replace_id=replace_id)

    def _spawn_command(self, provider: Provider, args: list[str]) -> list[str]:
        configured = self.executables[provider]
        executable = shutil.which(configured)
        if executable is None and os.name == "nt" and Path(configured).suffix.casefold() == ".exe":
            # npm-installed CLIs commonly expose only codex.cmd/claude.cmd even
            # when the mux's compatibility default still names an .exe.
            executable = shutil.which(str(Path(configured).with_suffix("")))
        executable = executable or configured
        if os.name == "nt" and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", executable, *args]
        return [executable, *args]

    async def _run_command(
        self, provider: Provider, args: list[str], *, timeout_seconds: float
    ) -> str:
        command = self._spawn_command(provider, args)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ProviderAccountError(f"Could not start {provider}: {exc}") from exc
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ProviderAccountError(f"{provider} login timed out") from exc
        output = stdout.decode(errors="replace").strip()
        diagnostic = stderr.decode(errors="replace").strip()
        if process.returncode:
            detail = diagnostic[-500:] or output[-500:] or f"exit code {process.returncode}"
            raise ProviderAccountError(f"{provider} command failed: {detail}")
        return output

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="provider-quota-poll")
        if self._event_task is None:
            self._event_queue = self.events.subscribe()
            self._event_task = asyncio.create_task(
                self._event_refresh_loop(), name="provider-quota-turn-refresh"
            )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._event_task:
            self._event_task.cancel()
            await asyncio.gather(self._event_task, return_exceptions=True)
            self._event_task = None
        if self._event_queue:
            self.events.unsubscribe(self._event_queue)
            self._event_queue = None
        if self._http:
            await self._http.close()
            self._http = None

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            await self.refresh()
            await asyncio.sleep(self.poll_seconds)

    async def _event_refresh_loop(self) -> None:
        assert self._event_queue is not None
        while True:
            event = await self._event_queue.get()
            try:
                if (
                    not self.turn_refresh_enabled
                    or event.type != "turn_ended"
                    or event.payload.get("scope", "root") != "root"
                    or not event.session_id
                ):
                    continue
                now = time.monotonic()
                if now - self._last_event_refresh < self.turn_refresh_min_seconds:
                    continue
                session = self.sessions.sessions.get(event.session_id) if self.sessions else None
                provider = str(session.record.backend) if session else ""
                if provider not in PROVIDERS:
                    continue
                account_id = _record(self._manifest.get("selected")).get(provider)
                if not account_id:
                    continue
                self._last_event_refresh = now
                await self.refresh(str(account_id))
            finally:
                self._event_queue.task_done()

    async def refresh(
        self, account_id: str | None = None, *, force_identity_probe: bool = False
    ) -> dict[str, Any]:
        await self.reconcile_current(force_identity_probe=force_identity_probe)
        async with self._refresh_lock:
            accounts = (
                [self._account(account_id)]
                if account_id
                else sorted(
                    self._accounts(),
                    key=lambda item: (
                        item.get("id")
                        != _record(self._manifest.get("selected")).get(str(item.get("provider")))
                    ),
                )
            )
            for account in list(accounts):
                await self._refresh_one(account)
        return self.snapshot()

    async def _session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
            )
        return self._http

    async def _json_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        session = await self._session()
        async with session.request(method, url, headers=headers, data=data) as response:
            try:
                payload = _record(await response.json(content_type=None))
            except (aiohttp.ContentTypeError, json.JSONDecodeError):
                payload = {}
            return response.status, payload

    async def _refresh_one(self, account: dict[str, Any]) -> None:
        account_id = str(account["id"])
        provider = _provider(str(account["provider"]))
        now = time.time()
        try:
            content, auth = self._read_json_auth(self._managed_auth_path(provider, account_id))
            if provider == "claude":
                quota, updated = await self._fetch_claude(auth)
            else:
                quota, updated = await self._fetch_codex(auth, account_id)
            if updated is not None:
                system_still_matches = self._system_matches_account(provider, account)
                content = (json.dumps(updated, separators=(",", ":")) + "\n").encode()
                _atomic_write(self._managed_auth_path(provider, account_id), content)
                if (
                    _record(self._manifest.get("selected")).get(provider) == account_id
                    and system_still_matches
                ):
                    _atomic_write(self._system_auth_path(provider), content)
                account["auth_digest"] = hashlib.sha256(content).hexdigest()
                account["updated_at"] = now
            quota.update({"status": "ready", "error": None, "refreshed_at": now})
            _record(self._manifest["quota"])[account_id] = quota
        except (ProviderAccountError, aiohttp.ClientError, TimeoutError, OSError) as exc:
            quota = _record(_record(self._manifest["quota"]).get(account_id))
            last_success = _number(quota.get("refreshed_at"))
            retained = last_success is not None and now - last_success <= STALE_SECONDS
            if not retained:
                quota = {"session": None, "weekly": None}
            quota.update(
                {
                    "status": "stale" if retained else "error",
                    "error": str(exc),
                    "attempted_at": now,
                }
            )
            _record(self._manifest["quota"])[account_id] = quota
        self._reconcile_current(write=False)
        self._write()
        public_quota = _record(self._manifest["quota"])[account_id]
        if self.telemetry is not None:
            await self.telemetry.record_quota_sample(
                provider=provider,
                account_id=account_id,
                quota=public_quota,
                sampled_at=now,
                account_active=(
                    _record(self._manifest.get("selected")).get(provider) == account_id
                ),
                auth_state=str(_record(self._current.get(provider)).get("state") or "unknown"),
            )
        await self.events.emit(
            "provider_quota_refreshed",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
            status=public_quota.get("status"),
        )

    async def _fetch_claude(
        self, auth: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        oauth = _record(auth.get("claudeAiOauth"))
        token = _string(oauth.get("accessToken"))
        if not token:
            raise ProviderAccountError("Claude OAuth access token is missing")
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/2.1.0",
        }
        status, payload = await self._json_request("GET", CLAUDE_USAGE_URL, headers=headers)
        updated: dict[str, Any] | None = None
        if status in {400, 401, 403} and _string(oauth.get("refreshToken")):
            updated = await self._refresh_claude_auth(auth)
            if updated:
                new_token = _string(_record(updated.get("claudeAiOauth")).get("accessToken"))
                headers["Authorization"] = f"Bearer {new_token}"
                status, payload = await self._json_request("GET", CLAUDE_USAGE_URL, headers=headers)
        if status < 200 or status >= 300:
            raise ProviderAccountError(f"Claude quota request failed (HTTP {status})")
        return {
            "session": _window(payload.get("five_hour"), 300),
            "weekly": _window(payload.get("seven_day"), 10080),
            "fable": _scoped_weekly_window(payload, "Fable"),
            "source": "oauth",
        }, updated

    async def _refresh_claude_auth(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        oauth = _record(auth.get("claudeAiOauth"))
        refresh_token = _string(oauth.get("refreshToken"))
        if not refresh_token:
            return None
        status, payload = await self._json_request(
            "POST",
            CLAUDE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLAUDE_CLIENT_ID,
            },
        )
        access_token = _string(payload.get("access_token"))
        if status < 200 or status >= 300 or not access_token:
            return None
        updated = dict(auth)
        new_oauth = dict(oauth)
        new_oauth["accessToken"] = access_token
        if rotated := _string(payload.get("refresh_token")):
            new_oauth["refreshToken"] = rotated
        if (expires := _number(payload.get("expires_in"))) is not None:
            new_oauth["expiresAt"] = int((time.time() + expires) * 1000)
        if scopes := _string(payload.get("scope")):
            new_oauth["scopes"] = scopes.split()
        updated["claudeAiOauth"] = new_oauth
        return updated

    async def _fetch_codex(
        self, auth: dict[str, Any], account_id: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        tokens = _record(auth.get("tokens"))
        access_token = _string(tokens.get("access_token"))
        if not access_token:
            raise ProviderAccountError("Codex OAuth access token is missing")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "codex-cli",
            "OpenAI-Beta": "codex-1",
            "originator": "Codex Desktop",
        }
        if provider_account_id := _string(tokens.get("account_id")):
            headers["ChatGPT-Account-Id"] = provider_account_id
        status, payload = await self._json_request("GET", CODEX_USAGE_URL, headers=headers)
        updated: dict[str, Any] | None = None
        if status in {400, 401, 403}:
            rpc_quota, refreshed = await self._fetch_codex_rpc(auth, account_id)
            if rpc_quota:
                return rpc_quota, refreshed
        if status < 200 or status >= 300 or not _string(payload.get("plan_type")):
            raise ProviderAccountError(f"Codex quota request failed (HTTP {status})")
        limits = _record(payload.get("rate_limit"))
        session, weekly = _classify_windows(
            _window(limits.get("primary_window"), 300, backend=True),
            _window(limits.get("secondary_window"), 10080, backend=True),
        )
        return {
            "session": session,
            "weekly": weekly,
            "plan": _string(payload.get("plan_type")),
            "source": "backend",
        }, updated

    async def _fetch_codex_rpc(
        self, auth: dict[str, Any], account_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        with tempfile.TemporaryDirectory(prefix="swe-mux-codex-quota-") as temporary:
            codex_home = Path(temporary)
            _atomic_write(
                codex_home / "auth.json",
                (json.dumps(auth, separators=(",", ":")) + "\n").encode(),
            )
            config_path = self.home / ".codex" / "config.toml"
            if config_path.is_file():
                shutil.copy2(config_path, codex_home / "config.toml")
            command = self._spawn_command(
                "codex", ["-s", "read-only", "-a", "untrusted", "app-server"]
            )
            env = dict(os.environ)
            env["CODEX_HOME"] = str(codex_home)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
            except OSError:
                return None, None
            stdin = process.stdin
            stdout = process.stdout
            if stdin is None or stdout is None:
                process.kill()
                await process.wait()
                return None, None

            async def send(message: dict[str, Any]) -> None:
                stdin.write((json.dumps(message) + "\n").encode())
                await stdin.drain()

            async def receive(message_id: int) -> dict[str, Any]:
                while line := await stdout.readline():
                    try:
                        message = _record(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                    if message.get("id") == message_id:
                        return message
                return {}

            try:
                await send(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"clientInfo": {"name": "swe-mux", "version": "0.1.0"}},
                    }
                )
                initialized = await asyncio.wait_for(receive(1), HTTP_TIMEOUT_SECONDS)
                if initialized.get("error"):
                    return None, None
                await send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                await send(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "account/rateLimits/read",
                        "params": {},
                    }
                )
                message = await asyncio.wait_for(receive(2), HTTP_TIMEOUT_SECONDS)
            except TimeoutError:
                return None, None
            finally:
                process.kill()
                await process.wait()
            if message.get("error"):
                return None, None
            wrapper = _record(message.get("result"))
            limits = _record(wrapper.get("rateLimits"))
            session, weekly = _classify_windows(
                self._rpc_window(limits.get("primary"), 300),
                self._rpc_window(limits.get("secondary"), 10080),
            )
            result = {
                "session": session,
                "weekly": weekly,
                "source": "app-server",
            }
            refreshed_path = codex_home / "auth.json"
            try:
                _, refreshed = self._read_json_auth(refreshed_path)
            except ProviderAccountError:
                refreshed = None
            return result, refreshed

    @staticmethod
    def _rpc_window(raw: object, minutes: int) -> dict[str, Any] | None:
        item = _record(raw)
        percent = _number(item.get("usedPercent"))
        if percent is None:
            return None
        # Prefer the window's real duration when the app-server reports it, so
        # duration-based classification stays accurate; fall back to the
        # positional default otherwise.
        duration = _number(item.get("windowMinutes")) or _number(item.get("windowSizeMinutes"))
        seconds = _number(item.get("windowSizeSeconds")) or _number(item.get("limitWindowSeconds"))
        if seconds and seconds > 0:
            duration = seconds / 60
        return {
            "used_percent": min(100.0, max(0.0, percent)),
            "window_minutes": math.ceil(duration) if duration and duration > 0 else minutes,
            "resets_at": _reset_timestamp(item.get("resetsAt")),
        }
