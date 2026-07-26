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
from .subprocess_flags import background_creation_flags

Provider = Literal["claude", "codex"]
CurrentAccountState = Literal["saved", "external", "signed_out", "unreadable"]
# How an identity was learned, weakest last. Only "token" is derived from the
# credential itself and may therefore authorize a credential write.
IdentitySource = Literal["token", "cli", "file"]
MatchKind = Literal["digest", "verified_identity", "weak_identity"]

PROVIDERS: tuple[Provider, ...] = ("claude", "codex")
MANIFEST_VERSION = 2
POLL_SECONDS = 15 * 60
STALE_SECONDS = 30 * 60
IDENTITY_PROBE_COOLDOWN_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
LOGIN_TIMEOUT_SECONDS = 5 * 60
CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
CLAUDE_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"

IDENTITY_FIELDS = ("email", "provider_account_id", "organization")
IDENTITY_STRENGTH: dict[str, int] = {"file": 1, "cli": 2, "token": 3}
# A credential blob's owner never changes, so a digest→identity entry stays valid
# forever; the map is bounded only to keep the manifest small.
IDENTITY_CACHE_LIMIT = 64
AUDIT_FILE_NAME = "credential-events.jsonl"
AUDIT_MAX_BYTES = 2_000_000
LIVE_SESSION_STATES = ("starting", "running", "working", "idle", "awaiting")


class ProviderAccountError(RuntimeError):
    pass


class ProviderAccountConflict(ProviderAccountError):
    """A request would bind credentials to the wrong account, or needs a force flag."""


def _blank_identity() -> dict[str, Any]:
    return {"email": None, "provider_account_id": None, "organization": None, "source": None}


def _merge_identity(*identities: dict[str, Any] | None) -> dict[str, Any]:
    """Combine identity readings, strongest source winning per field."""
    merged = _blank_identity()
    ranks: dict[str, int] = dict.fromkeys(IDENTITY_FIELDS, 0)
    best = 0
    for identity in identities:
        if not identity:
            continue
        rank = IDENTITY_STRENGTH.get(str(identity.get("source")), 0)
        for key in IDENTITY_FIELDS:
            value = _string(identity.get(key))
            if value is not None and rank >= ranks[key]:
                merged[key] = value
                ranks[key] = rank
        if any(_string(identity.get(key)) for key in IDENTITY_FIELDS):
            best = max(best, rank)
    merged["source"] = next(
        (name for name, rank in IDENTITY_STRENGTH.items() if rank == best), None
    )
    return merged


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
            "identities": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return self._empty_manifest()
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_manifest()
        if not isinstance(value, dict) or value.get("version") not in {1, MANIFEST_VERSION}:
            return self._empty_manifest()
        manifest: dict[str, Any] = self._migrate_v1(value) if value.get("version") == 1 else value
        manifest.setdefault("selected", {"claude": None, "codex": None})
        manifest.setdefault("accounts", [])
        manifest.setdefault("quota", {})
        manifest.setdefault("identities", {})
        return manifest

    @staticmethod
    def _migrate_v1(value: dict[str, Any]) -> dict[str, Any]:
        """Carry v1 accounts forward while retiring unverified identity keys.

        v1 recorded the Claude *organization* UUID as ``provider_account_id``.
        That is the wrong granularity (two logins can share an organization) and
        was never derived from the credential, so it is dropped rather than
        trusted; the next refresh re-derives a real account UUID from the token.
        Codex IDs come from the credential's own token claims and are kept.
        """
        accounts = value.get("accounts")
        for item in accounts if isinstance(accounts, list) else []:
            account = _record(item)
            if account.get("provider") == "codex" and _string(account.get("provider_account_id")):
                account["identity_source"] = "token"
            else:
                account["provider_account_id"] = None
                account["identity_source"] = "file" if _string(account.get("email")) else None
            account.pop("identity_verified_at", None)
            account.pop("identity_verified_digest", None)
        value["version"] = MANIFEST_VERSION
        value["identities"] = {}
        return value

    def _write(self) -> None:
        _atomic_write(
            self.manifest_path,
            (json.dumps(self._manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )

    def _accounts(self) -> list[dict[str, Any]]:
        accounts = self._manifest.get("accounts")
        return accounts if isinstance(accounts, list) else []

    # ---- credential audit trail -------------------------------------------------

    @property
    def audit_path(self) -> Path:
        return self.root / AUDIT_FILE_NAME

    def _audit(
        self,
        action: str,
        *,
        provider: Provider,
        account_id: str | None = None,
        matched_by: str | None = None,
        old_digest: str | None = None,
        new_digest: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append a non-secret record of every credential-affecting decision.

        Silent credential rewrites were previously undiagnosable after the fact;
        this is the only durable evidence of which login landed in which slot.
        """
        entry = {
            "at": time.time(),
            "action": action,
            "provider": provider,
            "account_id": account_id,
            "matched_by": matched_by,
            "old_digest": (old_digest or "")[:16] or None,
            "new_digest": (new_digest or "")[:16] or None,
            "detail": detail,
        }
        try:
            path = self.audit_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > AUDIT_MAX_BYTES:
                os.replace(path, path.with_suffix(path.suffix + ".1"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, sort_keys=True) + "\n")
        except OSError:
            pass

    def audit_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-max(1, limit) :]:
            try:
                entries.append(_record(json.loads(line)))
            except json.JSONDecodeError:
                continue
        return entries

    # ---- digest → identity map --------------------------------------------------

    def _identities(self) -> dict[str, Any]:
        value = self._manifest.get("identities")
        if not isinstance(value, dict):
            value = {}
            self._manifest["identities"] = value
        return value

    @staticmethod
    def _identity_slot(provider: Provider, digest: str) -> str:
        return f"{provider}:{digest}"

    def _recall_identity(self, provider: Provider, digest: str) -> dict[str, Any] | None:
        entry = _record(self._identities().get(self._identity_slot(provider, digest)))
        return entry or None

    def _remember_identity(
        self, provider: Provider, digest: str, identity: dict[str, Any]
    ) -> None:
        if not identity or not any(_string(identity.get(key)) for key in IDENTITY_FIELDS):
            return
        entries = self._identities()
        entries[self._identity_slot(provider, digest)] = {
            key: _string(identity.get(key)) for key in IDENTITY_FIELDS
        } | {"source": _string(identity.get("source")), "verified_at": time.time()}
        if len(entries) > IDENTITY_CACHE_LIMIT:
            ordered = sorted(
                entries.items(), key=lambda item: _number(_record(item[1]).get("verified_at")) or 0
            )
            for key, _value in ordered[: len(entries) - IDENTITY_CACHE_LIMIT]:
                entries.pop(key, None)

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

    def _identity(self, provider: Provider, auth: dict[str, Any]) -> dict[str, Any]:
        """Identity readable from the credential file without any network call.

        Codex credentials carry their own account claims, so the reading is bound
        to the token and counts as verified. Claude credential files carry no
        identity at all in current releases; any legacy field found there is
        display metadata only and must never key a credential write.
        """
        if provider == "claude":
            oauth = _record(auth.get("claudeAiOauth"))
            return {
                "email": _string(oauth.get("email")),
                "provider_account_id": None,
                "organization": _string(oauth.get("organizationName")),
                "source": "file" if _string(oauth.get("email")) else None,
            }
        tokens = _record(auth.get("tokens"))
        payload = _jwt_payload(_string(tokens.get("id_token")) or _string(tokens.get("idToken")))
        auth_claims = _record(payload.get("https://api.openai.com/auth"))
        profile = _record(payload.get("https://api.openai.com/profile"))
        account_id = (
            _string(tokens.get("account_id"))
            or _string(tokens.get("accountId"))
            or _string(auth_claims.get("chatgpt_account_id"))
        )
        return {
            "email": _string(payload.get("email")) or _string(profile.get("email")),
            "provider_account_id": account_id,
            "organization": _string(auth_claims.get("workspace_name"))
            or _string(profile.get("workspace_name")),
            "source": "token" if account_id else "file",
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

    @staticmethod
    def _verified_key(record: dict[str, Any], *, source_key: str) -> str | None:
        """The only key allowed to authorize writing credentials into a slot."""
        if _string(record.get(source_key)) != "token":
            return None
        value = _string(record.get("provider_account_id"))
        return value.casefold() if value else None

    def _account_verified_key(self, account: dict[str, Any]) -> str | None:
        return self._verified_key(account, source_key="identity_source")

    def _identity_verified_key(self, identity: dict[str, Any]) -> str | None:
        return self._verified_key(identity, source_key="source")

    async def _status_identity(self, provider: Provider) -> dict[str, Any]:
        """Weak identity from the provider CLI's cached profile.

        This reads global CLI state (`~/.claude.json`), not the credential, so it
        can name a different account than the token in `.credentials.json`. It is
        used for display and relink hints only.
        """
        if provider != "claude":
            return _blank_identity()
        try:
            output = await self._run_command(
                provider, ["auth", "status", "--json"], timeout_seconds=15
            )
            status = _record(json.loads(output))
        except (ProviderAccountError, json.JSONDecodeError):
            return _blank_identity()
        email = _string(status.get("email"))
        organization = _string(status.get("orgName")) or _string(status.get("organizationName"))
        return {
            "email": email,
            # Deliberately not an account key: this is an organization UUID and
            # several logins can share one.
            "provider_account_id": None,
            "organization": organization,
            "source": "cli" if email or organization else None,
        }

    async def _verify_token_identity(
        self, provider: Provider, auth: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Resolve who a credential actually belongs to, by asking with that credential.

        Returns ``(identity, rotated_auth)``. This is the authoritative identity:
        it is derived from the token itself rather than from machine-global state
        that any other process can rewrite.
        """
        if provider == "codex":
            identity = self._identity("codex", auth)
            return (identity if self._identity_verified_key(identity) else None), None
        oauth = _record(auth.get("claudeAiOauth"))
        token = _string(oauth.get("accessToken"))
        if not token:
            return None, None
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-code/2.1.0",
        }
        rotated: dict[str, Any] | None = None
        try:
            status, payload = await self._json_request("GET", CLAUDE_PROFILE_URL, headers=headers)
            if status in {400, 401, 403} and _string(oauth.get("refreshToken")):
                rotated = await self._refresh_claude_auth(auth)
                if rotated:
                    refreshed = _string(_record(rotated.get("claudeAiOauth")).get("accessToken"))
                    headers["Authorization"] = f"Bearer {refreshed}"
                    status, payload = await self._json_request(
                        "GET", CLAUDE_PROFILE_URL, headers=headers
                    )
        except (aiohttp.ClientError, TimeoutError):
            return None, rotated
        if status < 200 or status >= 300:
            return None, rotated
        account = _record(payload.get("account"))
        organization = _record(payload.get("organization"))
        account_uuid = _string(account.get("uuid"))
        if not account_uuid:
            return None, rotated
        return {
            "email": _string(account.get("email")),
            "provider_account_id": account_uuid,
            "organization": _string(organization.get("name")),
            "source": "token",
        }, rotated

    def _public_account(self, account: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account["id"])
        quota = _record(_record(self._manifest.get("quota")).get(account_id))
        return {
            key: account.get(key)
            for key in (
                "id",
                "provider",
                "label",
                "email",
                "provider_account_id",
                "organization",
                "identity_source",
                "identity_verified_at",
                "created_at",
                "updated_at",
            )
        } | {"quota": quota or None, "conflict": self._conflicts().get(account_id)}

    @staticmethod
    def _current_status(
        state: CurrentAccountState,
        *,
        account_id: str | None = None,
        identity: dict[str, Any] | None = None,
        match_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        public_identity = identity or {}
        return {
            "state": state,
            "account_id": account_id,
            "email": _string(public_identity.get("email")),
            "provider_account_id": _string(public_identity.get("provider_account_id")),
            "organization": _string(public_identity.get("organization")),
            "identity_source": _string(public_identity.get("source")),
            "match_hint": match_hint,
        }

    def _conflicts(self) -> dict[str, dict[str, Any]]:
        """Saved slots that resolve to one and the same provider account.

        Two slots holding one account is silent damage: both poll the same quota
        and read as two independent logins. Once identities are token-verified it
        becomes detectable, so it is surfaced instead of averaged over.
        """
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for account in self._accounts():
            key = self._account_verified_key(account)
            if key:
                groups.setdefault((str(account.get("provider")), key), []).append(account)
        selected = _record(self._manifest.get("selected"))
        result: dict[str, dict[str, Any]] = {}
        for (provider, key), members in groups.items():
            if len(members) < 2:
                continue
            primary = next(
                (item for item in members if item.get("id") == selected.get(provider)), None
            ) or min(members, key=lambda item: _number(item.get("created_at")) or 0.0)
            for member in members:
                result[str(member["id"])] = {
                    "kind": "duplicate_account",
                    "provider_account_id": key,
                    "primary_id": str(primary["id"]),
                    "is_primary": member is primary,
                    "account_ids": [str(item["id"]) for item in members],
                }
        return result

    def _match_hint(
        self, provider: Provider, identity: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Name the saved slot a weak identity points at, without acting on it."""
        identity_key = self._identity_key(identity)
        if identity_key is None:
            return None
        matches = [
            item
            for item in self._accounts()
            if item.get("provider") == provider and self._identity_key(item) == identity_key
        ]
        if len(matches) != 1:
            return None
        return {
            "account_id": str(matches[0]["id"]),
            "label": matches[0].get("label"),
            "reason": identity_key[0],
        }

    def _matching_account(
        self, provider: Provider, digest: str, identity: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, MatchKind | None]:
        """Resolve live credentials to a saved slot, reporting how strong the match is.

        Only ``digest`` (byte-identical) and ``verified_identity`` (the credential
        itself named its owner) are strong enough to move credentials. A weak
        match is reported so the UI can offer an explicit relink, and is never
        acted on automatically: acting on one is what lets a rotation belonging to
        account A overwrite the saved snapshot of account B.
        """
        exact = next(
            (
                item
                for item in self._accounts()
                if item.get("provider") == provider and item.get("auth_digest") == digest
            ),
            None,
        )
        if exact is not None:
            return exact, "digest"
        verified = self._identity_verified_key(identity)
        if verified is not None:
            matches = [
                item
                for item in self._accounts()
                if item.get("provider") == provider
                and self._account_verified_key(item) == verified
            ]
            if len(matches) == 1:
                return matches[0], "verified_identity"
            if matches:
                return None, None
        if self._match_hint(provider, identity) is not None:
            return None, "weak_identity"
        return None, None

    def _sync_managed_auth(
        self,
        provider: Provider,
        account: dict[str, Any],
        content: bytes,
        digest: str,
        identity: dict[str, Any],
        *,
        matched_by: str,
    ) -> bool:
        managed_path = self._managed_auth_path(provider, str(account["id"]))
        previous_digest = _string(account.get("auth_digest"))
        try:
            managed_matches = managed_path.read_bytes() == content
        except OSError:
            managed_matches = False
        if not managed_matches:
            self._backup_managed_auth(managed_path)
            try:
                _atomic_write(managed_path, content)
            except OSError:
                return False
            self._audit(
                "managed_auth_written",
                provider=provider,
                account_id=str(account["id"]),
                matched_by=matched_by,
                old_digest=previous_digest,
                new_digest=digest,
            )
        changed = account.get("auth_digest") != digest
        for key in IDENTITY_FIELDS:
            value = _string(identity.get(key))
            if value and account.get(key) != value:
                account[key] = value
                changed = True
        if _string(identity.get("source")) == "token":
            account["identity_source"] = "token"
            account["identity_verified_at"] = time.time()
            account["identity_verified_digest"] = digest
            changed = True
        elif not _string(account.get("identity_source")) and _string(identity.get("source")):
            account["identity_source"] = _string(identity.get("source"))
            changed = True
        if changed:
            account["auth_digest"] = digest
            account["updated_at"] = time.time()
        return True

    @staticmethod
    def _backup_managed_auth(path: Path) -> None:
        """Keep the credential a slot held before it is replaced."""
        if not path.is_file():
            return
        try:
            shutil.copy2(path, path.with_name(path.name + ".prev"))
        except OSError:
            pass

    def _reconcile_current(self, *, write: bool = True) -> None:
        """Make the live system auth files authoritative without replacing them.

        Runs on every snapshot, so it must never write credentials on a guess: an
        unrecognized live login is reported as external and left alone until it is
        either verified against the provider or explicitly relinked by the user.
        """
        selected_value: dict[str, Any] = (
            self._manifest["selected"]
            if isinstance(self._manifest.get("selected"), dict)
            else dict.fromkeys(PROVIDERS)
        )
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
            cached = self._identity_cache.get(provider)
            if cached is not None and cached[0] != digest:
                self._identity_cache.pop(provider, None)
                cached = None
            # Weakest first; _merge_identity lets the strongest source win per field.
            identity = _merge_identity(
                self._identity(provider, auth),
                cached[1] if cached else None,
                self._recall_identity(provider, digest),
            )
            account, matched_by = self._matching_account(provider, digest, identity)
            if account is not None:
                account_changed = account.get("auth_digest") != digest or any(
                    _string(identity.get(key)) is not None
                    and account.get(key) != _string(identity.get(key))
                    for key in IDENTITY_FIELDS
                )
                assert matched_by is not None
                if not self._sync_managed_auth(
                    provider, account, content, digest, identity, matched_by=matched_by
                ):
                    account = None
                elif account_changed:
                    changed = True

            account_id = str(account["id"]) if account is not None else None
            if selected_value.get(provider) != account_id:
                selected_value[provider] = account_id
                changed = True
            if account is None:
                current[provider] = self._current_status(
                    "external",
                    identity=identity,
                    match_hint=self._match_hint(provider, identity)
                    if matched_by == "weak_identity"
                    else None,
                )
            else:
                public_identity = _merge_identity(
                    {key: account.get(key) for key in IDENTITY_FIELDS}
                    | {"source": account.get("identity_source")},
                    identity,
                )
                current[provider] = self._current_status(
                    "saved", account_id=account_id, identity=public_identity
                )
        self._current = current
        if changed and write:
            self._write()

    async def reconcile_current(
        self, *, force_identity_probe: bool = False
    ) -> dict[str, Any]:
        """Reconcile live auth, identifying an unrecognized login against the provider."""
        async with self._mutation_lock:
            self._reconcile_current()
            previous = {
                provider: _record(self._current.get(provider)).get("account_id")
                for provider in PROVIDERS
            }
            for provider in PROVIDERS:
                if _record(self._current.get(provider)).get("state") != "external":
                    continue
                await self._identify_live_login(
                    provider, force_identity_probe=force_identity_probe
                )
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

    async def _identify_live_login(
        self, provider: Provider, *, force_identity_probe: bool = False
    ) -> None:
        """Learn who the unrecognized live credentials belong to.

        The provider is asked using the live credential itself, so the answer is
        bound to that exact token rather than to machine-global CLI state. The
        digest is re-checked afterwards because a concurrent provider process can
        rotate the file mid-probe; a reading is only cached against the digest it
        was actually derived from.
        """
        for _attempt in range(2):
            try:
                before, auth = self._read_json_auth(self._system_auth_path(provider))
            except ProviderAccountError:
                return
            before_digest = hashlib.sha256(before).hexdigest()
            if self._recall_identity(provider, before_digest):
                return
            probe_attempt = self._identity_probe_attempts.get(provider)
            if (
                not force_identity_probe
                and probe_attempt is not None
                and probe_attempt[0] == before_digest
                and time.monotonic() - probe_attempt[1] < IDENTITY_PROBE_COOLDOWN_SECONDS
            ):
                return
            self._identity_probe_attempts[provider] = (before_digest, time.monotonic())
            verified, _rotated = await self._verify_token_identity(provider, auth)
            status_identity = (
                await self._status_identity(provider) if verified is None else _blank_identity()
            )
            try:
                after, _ = self._read_json_auth(self._system_auth_path(provider))
            except ProviderAccountError:
                return
            if before_digest != hashlib.sha256(after).hexdigest():
                continue
            if verified is not None:
                self._remember_identity(provider, before_digest, verified)
                self._audit(
                    "identity_verified",
                    provider=provider,
                    new_digest=before_digest,
                    matched_by="token",
                    detail=_string(verified.get("email")),
                )
            elif self._identity_key(status_identity) is not None:
                # Display and relink hint only; never strong enough to move credentials.
                self._identity_cache[provider] = (before_digest, status_identity)
            return

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
            digest = hashlib.sha256(content).hexdigest()
            verified, _rotated = await self._verify_token_identity(provider, auth)
            identity = _merge_identity(
                self._identity(provider, auth),
                await self._status_identity(provider) if verified is None else None,
                verified,
            )
            if verified is not None:
                self._remember_identity(provider, digest, verified)
            verified_key = self._identity_verified_key(identity)
            identity_key = self._identity_key(identity)
            account: dict[str, Any] | None = None
            if replace_id:
                account = self._account(replace_id)
                if account.get("provider") != provider:
                    raise ProviderAccountError("account provider does not match")
                owner = next(
                    (
                        item
                        for item in self._accounts()
                        if item.get("id") != replace_id
                        and item.get("provider") == provider
                        and verified_key is not None
                        and self._account_verified_key(item) == verified_key
                    ),
                    None,
                )
                if owner is not None:
                    raise ProviderAccountConflict(
                        f"those credentials belong to the saved account "
                        f"'{owner.get('label')}'; capture into that account instead"
                    )
            # Verified identity first: it is the only key that reliably keeps one
            # provider account in exactly one slot.
            if account is None and verified_key is not None:
                account = next(
                    (
                        item
                        for item in self._accounts()
                        if item.get("provider") == provider
                        and self._account_verified_key(item) == verified_key
                    ),
                    None,
                )
            if account is None and verified_key is None and identity_key:
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
            created = account is None
            if account is None:
                account = {
                    "id": uuid.uuid4().hex,
                    "provider": provider,
                    "created_at": now,
                }
                self._accounts().append(account)
            account.update({key: identity.get(key) for key in IDENTITY_FIELDS})
            account.update(
                {
                    "label": (label or "").strip()
                    or account.get("label")
                    or identity.get("email")
                    or identity.get("organization")
                    or f"{provider.title()} account",
                    "updated_at": now,
                    "auth_digest": digest,
                    "identity_source": _string(identity.get("source")),
                }
            )
            if verified is not None:
                account["identity_verified_at"] = now
                account["identity_verified_digest"] = digest
            else:
                account.pop("identity_verified_at", None)
                account.pop("identity_verified_digest", None)
            managed_path = self._managed_auth_path(provider, str(account["id"]))
            self._backup_managed_auth(managed_path)
            _atomic_write(managed_path, content)
            _record(self._manifest["selected"])[provider] = account["id"]
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "captured" if created else "recaptured",
                provider=provider,
                account_id=str(account["id"]),
                matched_by=_string(identity.get("source")) or "none",
                new_digest=digest,
                detail=_string(identity.get("email")),
            )
        await self.events.emit(
            "provider_account_captured",
            source="provider_accounts",
            provider=provider,
            account_id=account["id"],
        )
        await self.refresh(str(account["id"]))
        return self.snapshot()

    def live_sessions(self, provider: Provider) -> list[str]:
        """IDs of running sessions for a provider, which share the system auth file."""
        manager = self.sessions
        sessions = getattr(manager, "sessions", None) if manager is not None else None
        if not isinstance(sessions, dict):
            return []
        live: list[str] = []
        for session_id, session in sessions.items():
            record = getattr(session, "record", None)
            if record is None or str(getattr(record, "backend", "")) != provider:
                continue
            if str(getattr(record, "state", "")) in LIVE_SESSION_STATES:
                live.append(str(session_id))
        return live

    async def select(
        self, provider_value: str, account_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        provider = _provider(provider_value)
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            active = _record(self._manifest.get("selected")).get(provider)
            live = self.live_sessions(provider)
            if live and active != account_id and not force:
                # Those processes hold the outgoing account's refresh token and
                # write rotations straight back into the shared credential file,
                # which is how one account's token ends up in another's slot.
                raise ProviderAccountConflict(
                    f"{len(live)} live {provider} session(s) still run under the current "
                    f"login and can rotate its token back over this switch; end them or "
                    f"switch with force"
                )
            content, _ = self._read_json_auth(self._managed_auth_path(provider, account_id))
            previous_digest = _string(account.get("auth_digest"))
            _atomic_write(self._system_auth_path(provider), content)
            account["auth_digest"] = hashlib.sha256(content).hexdigest()
            account["updated_at"] = time.time()
            _record(self._manifest["selected"])[provider] = account_id
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "selected",
                provider=provider,
                account_id=account_id,
                matched_by="forced" if force and live else "user",
                old_digest=previous_digest,
                new_digest=_string(account.get("auth_digest")),
                detail=f"{len(live)} live session(s)" if live else None,
            )
        await self.events.emit(
            "provider_account_selected",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )
        await self.refresh(account_id)
        return self.snapshot()

    async def adopt(self, provider_value: str, account_id: str) -> dict[str, Any]:
        """Bind the live system login to a saved account on explicit user request.

        This is the deliberate version of what reconciliation used to do on its
        own from a weak email match. Ownership is checked first, so a relink can
        never file one provider account's credentials under another's slot.
        """
        provider = _provider(provider_value)
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            content, auth = self._read_json_auth(self._system_auth_path(provider))
            digest = hashlib.sha256(content).hexdigest()
            verified, _rotated = await self._verify_token_identity(provider, auth)
            after, _ = self._read_json_auth(self._system_auth_path(provider))
            if hashlib.sha256(after).hexdigest() != digest:
                raise ProviderAccountError("the system login changed during relink; try again")
            identity = _merge_identity(self._identity(provider, auth), verified)
            verified_key = self._identity_verified_key(identity)
            if verified_key is not None:
                self._remember_identity(provider, digest, identity)
                owner = next(
                    (
                        item
                        for item in self._accounts()
                        if item.get("id") != account_id
                        and item.get("provider") == provider
                        and self._account_verified_key(item) == verified_key
                    ),
                    None,
                )
                if owner is not None:
                    raise ProviderAccountConflict(
                        f"the live login belongs to the saved account "
                        f"'{owner.get('label')}', not '{account.get('label')}'"
                    )
                existing = self._account_verified_key(account)
                if existing is not None and existing != verified_key:
                    raise ProviderAccountConflict(
                        f"'{account.get('label')}' is a different provider account than the "
                        f"live login; save the live login as its own account instead"
                    )
            if not self._sync_managed_auth(
                provider, account, content, digest, identity, matched_by="adopt"
            ):
                raise ProviderAccountError("the account snapshot could not be written")
            _record(self._manifest["selected"])[provider] = account_id
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "adopted",
                provider=provider,
                account_id=account_id,
                matched_by=_string(identity.get("source")) or "none",
                new_digest=digest,
                detail=_string(identity.get("email")),
            )
        await self.events.emit(
            "provider_account_selected",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )
        await self.refresh(account_id)
        return self.snapshot()

    async def purge_telemetry(
        self, provider_value: str, account_id: str, *, since: float | None = None
    ) -> dict[str, Any]:
        """Discard durable samples a slot recorded while holding other credentials."""
        provider = _provider(provider_value)
        account = self._account(account_id)
        if account.get("provider") != provider:
            raise ProviderAccountError("account provider does not match")
        if self.telemetry is None or not hasattr(self.telemetry, "purge_account"):
            raise ProviderAccountError("telemetry is not available")
        removed = await self.telemetry.purge_account(provider, account_id, since=since)
        self._audit(
            "telemetry_purged",
            provider=provider,
            account_id=account_id,
            detail=f"since={since!r} removed={removed}",
        )
        return {"removed": removed, "since": since} | self.snapshot()

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
            digest = _string(account.get("auth_digest"))
            if digest:
                self._identities().pop(self._identity_slot(provider, digest), None)
            account_dir = self.root / provider / account_id
            if account_dir.is_dir() and account_dir.parent == self.root / provider:
                shutil.rmtree(account_dir)
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "removed",
                provider=provider,
                account_id=account_id,
                old_digest=digest,
                detail=_string(account.get("email")),
            )
        if self.telemetry is not None and hasattr(self.telemetry, "purge_account"):
            await self.telemetry.purge_account(provider, account_id)
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
                creationflags=background_creation_flags(),
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
                conflict = self._conflicts().get(str(account["id"]))
                if conflict and not conflict["is_primary"]:
                    # Polling a duplicate reports the primary's numbers a second
                    # time, which is exactly the mirrored-usage illusion.
                    self._mark_conflicted(account, conflict)
                    continue
                await self._refresh_one(account)
        return self.snapshot()

    def _mark_conflicted(self, account: dict[str, Any], conflict: dict[str, Any]) -> None:
        account_id = str(account["id"])
        _record(self._manifest["quota"])[account_id] = {
            "session": None,
            "weekly": None,
            "status": "conflict",
            "error": (
                "this slot holds the same provider account as "
                f"{conflict['primary_id']}; quota polling is suspended"
            ),
            "attempted_at": time.time(),
        }
        self._write()

    async def verify_identities(self) -> dict[str, Any]:
        """Re-derive every saved account's owner from its own credentials."""
        async with self._refresh_lock:
            for account in list(self._accounts()):
                await self._verify_account_identity(account, force=True)
            self._reconcile_current(write=False)
            self._write()
        return self.snapshot()

    async def _verify_account_identity(
        self, account: dict[str, Any], *, force: bool = False
    ) -> dict[str, Any] | None:
        """Confirm a saved slot's owner, re-verifying whenever its credential changed."""
        account_id = str(account["id"])
        provider = _provider(str(account["provider"]))
        digest = _string(account.get("auth_digest"))
        if (
            not force
            and _string(account.get("identity_source")) == "token"
            and digest is not None
            and account.get("identity_verified_digest") == digest
        ):
            return None
        try:
            _content, auth = self._read_json_auth(self._managed_auth_path(provider, account_id))
        except ProviderAccountError:
            return None
        verified, _rotated = await self._verify_token_identity(provider, auth)
        if verified is None:
            return None
        previous = _string(account.get("provider_account_id"))
        account.update({key: verified.get(key) for key in IDENTITY_FIELDS})
        account["identity_source"] = "token"
        account["identity_verified_at"] = time.time()
        account["identity_verified_digest"] = digest
        if digest:
            self._remember_identity(provider, digest, verified)
        new_key = _string(verified.get("provider_account_id"))
        corrected = bool(previous and new_key and previous.casefold() != new_key.casefold())
        self._audit(
            "identity_corrected" if corrected else "identity_verified",
            provider=provider,
            account_id=account_id,
            matched_by="token",
            old_digest=digest,
            detail=f"{previous} -> {new_key}" if corrected else _string(verified.get("email")),
        )
        return verified

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
        quota: dict[str, Any]
        try:
            content, auth = self._read_json_auth(self._managed_auth_path(provider, account_id))
            # Re-derive the owner whenever this slot's credential changed, so a
            # rotation that swapped in another account's token is caught here
            # rather than showing up as two accounts with identical quota.
            await self._verify_account_identity(account)
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
                # Stamps the sample with the account it actually describes, so a
                # slot that later changes hands cannot silently re-attribute it.
                provider_account_uuid=self._account_verified_key(account),
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
                    creationflags=background_creation_flags(),
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
