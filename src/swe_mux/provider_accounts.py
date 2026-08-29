from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, assert_never, cast

import aiohttp

from .background_tasks import background
from .bounded_subprocess import run_bounded
from .event_bus import EventBus
from .harness import descriptor, provider_account_harnesses
from .host_platform import IS_WINDOWS
from .logsetup import bound_request_id, current_request_id, new_request_id
from .models import MuxEvent
from .shim_paths import ExecutableResolution, combine_resolutions, resolve_executable
from .subprocess_flags import background_creation_flags, reap_process_tree

log = logging.getLogger(__name__)

#: What one provider CLI invocation may hold in memory. Login and status output
#: is a few lines; the cap exists because how much a third-party CLI decides to
#: print is not a number this daemon gets to assume.
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
#: How much of a failed invocation's tail reaches an operator, in the error and in
#: `daemon.log` alike. One number rather than a repeated literal so the log can
#: never quietly carry more of a third-party CLI's output than the error does.
DIAGNOSTIC_TAIL_CHARS = 500
QUOTA_POLL_LOOP = "provider-quota-poll"
QUOTA_TURN_REFRESH_LOOP = "provider-quota-turn-refresh"
SELECTION_GUARD_LOOP = "provider-selection-guard"
LOGIN_LOOP_PREFIX = "provider-login"
_REPLACE_RETRIES = 4
_REPLACE_RETRY_DELAY_SECONDS = 0.05

# The harnesses whose provider credentials mux manages, as a closed set.
#
# Deliberately a `Literal` and not `str`. Every path in this module writes or reads
# an authentication file, and a name that reached it without a branch would fall
# into whichever `else` happened to be written first - a Codex-shaped credential
# write for a harness that is not Codex. Closing the type makes `_provider_profile`
# a compile-time obligation instead.
#
# The set is derived from `provider_account_management` on the descriptors, and
# `test_provider_accounts` asserts the two agree: declaring that capability on a
# third harness fails there, and widening this literal to satisfy it then fails
# `assert_never` until every provider-shaped branch handles it.
ManagedProvider = Literal["claude", "codex"]
Provider = ManagedProvider
CurrentAccountState = Literal["saved", "external", "signed_out", "unreadable"]
# How an identity was learned, weakest last. Only "token" is derived from the
# credential itself and may therefore authorize a credential write.
IdentitySource = Literal["token", "cli", "file"]
MatchKind = Literal["digest", "verified_identity", "weak_identity"]

# Derived from the descriptors so the capability has one home, then narrowed to the
# closed literal above. The cast is what `test_managed_providers_match_the_registry`
# checks: if a third harness declares `provider_account_management`, that test fails
# rather than this cast quietly admitting a name no branch here handles.
PROVIDERS: tuple[ManagedProvider, ...] = cast(
    "tuple[ManagedProvider, ...]", provider_account_harnesses()
)
MANIFEST_VERSION = 2
POLL_SECONDS = 15 * 60
STALE_SECONDS = 30 * 60
IDENTITY_PROBE_COOLDOWN_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 10
LOGIN_TIMEOUT_SECONDS = 5 * 60
# How long a *succeeded* sign-in keeps reporting itself after it finished. The
# account appearing in the list is the real confirmation; this window only has to
# outlast the round trip that lets a client say "signed in" rather than silently
# growing a row. A *failed* sign-in has no such window — it carries the only copy
# of the error a caller will ever see, so it stays until it is dismissed or until
# another sign-in for that provider replaces it.
LOGIN_SUCCESS_LINGER_SECONDS = 30.0
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
# Per-account snapshot of the Claude CLI's cached profile block (`oauthAccount`
# in ~/.claude.json). The CLI shows identity (/status) from this block, not from
# the credential file, and refetches it at most daily — so a credential switch
# alone leaves every surface naming the previous account for up to a day.
OAUTH_SNAPSHOT_FILE = "oauth-account.json"
LIVE_SESSION_STATES = ("starting", "running", "working", "idle", "awaiting")
# How long a switch made under live sessions defends itself against a token
# refresh that was already in flight when the swap landed. Such a refresh
# completes with the outgoing account's refresh token and writes the result back
# over the shared credential file, which silently undoes the switch. Everything
# else follows the switch on its own, so the window only has to outlast one
# in-flight refresh round trip.
SELECTION_GUARD_SECONDS = 60.0
SELECTION_GUARD_POLL_SECONDS = 2.0


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


@dataclass(frozen=True, slots=True)
class _ProviderProfile:
    """The provider-shaped facts every managed-account path needs.

    Gathered into one exhaustive dispatch so they cannot drift apart: the auth file
    a credential is written to, the argv that signs in, and the endpoint quota is
    read from all have to describe the same provider.
    """

    auth_file_name: str
    system_auth_dir: str
    login_args: tuple[str, ...]
    usage_url: str


def _provider_profile(provider: ManagedProvider) -> _ProviderProfile:
    if provider == "claude":
        return _ProviderProfile(
            auth_file_name=".credentials.json",
            system_auth_dir=".claude",
            login_args=("auth", "login", "--claudeai"),
            usage_url=CLAUDE_USAGE_URL,
        )
    if provider == "codex":
        return _ProviderProfile(
            auth_file_name="auth.json",
            system_auth_dir=".codex",
            login_args=("login",),
            usage_url=CODEX_USAGE_URL,
        )
    assert_never(provider)


def _provider(value: str) -> Provider:
    if value not in PROVIDERS:
        raise ProviderAccountError("provider is not a managed harness")
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
    # Windows fails the replace outright while any process holds the destination
    # open (antivirus, a backup agent, an editor). The lock is transient; a short
    # bounded retry turns a hard write failure into a delay.
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SECONDS * (attempt + 1))


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
        executables: dict[str, str] | None = None,
        poll_seconds: float = POLL_SECONDS,
        telemetry: Any | None = None,
        sessions: Any | None = None,
        turn_refresh_enabled: bool = False,
        turn_refresh_min_seconds: float = 300.0,
    ) -> None:
        self.data_dir = data_dir
        self.home = home or Path.home()
        self.events = events
        configured = executables or {}
        self.executables: dict[Provider, str] = {
            provider: configured.get(provider, descriptor(provider).executable)
            for provider in PROVIDERS
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
        # provider -> (pinned account id, monotonic deadline) for a switch that
        # is still defending itself against the outgoing login.
        self._selection_guard: dict[Provider, tuple[str, float]] = {}
        # provider -> the running or last-finished interactive sign-in. It lives
        # here rather than in whoever's HTTP request started it, because the
        # thing being described is a child process this daemon owns for up to
        # five minutes: the browser that started it may close, reload, or be a
        # phone, and every other client should still be able to see that a login
        # is in flight and how it ended.
        self._login: dict[Provider, dict[str, Any]] = {}
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
        profile = _provider_profile(provider)
        return self.root / provider / account_id / profile.auth_file_name

    def _system_auth_path(self, provider: Provider) -> Path:
        profile = _provider_profile(provider)
        return self.home / profile.system_auth_dir / profile.auth_file_name

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

    # ---- Claude cached-profile snapshot (oauthAccount in ~/.claude.json) ---------

    def _claude_config_path(self) -> Path:
        return self.home / ".claude.json"

    def _oauth_snapshot_path(self, account_id: str) -> Path:
        return self.root / "claude" / account_id / OAUTH_SNAPSHOT_FILE

    def _read_claude_config(self) -> dict[str, Any] | None:
        """The CLI's main config as a dict, or None when absent or unparseable.

        Unparseable is deliberately indistinguishable from a decision not to
        touch the file: this config is owned and constantly rewritten by the
        CLI, and writing anything over content we could not read would clobber
        state we never saw.
        """
        try:
            value = json.loads(self._claude_config_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _snapshot_oauth_account(self, account: dict[str, Any]) -> None:
        """Keep the account's cached CLI profile block beside its credentials.

        Only a block whose ``accountUuid`` equals this slot's token-verified
        owner is saved: the block describes machine-global CLI state, and after
        a fast login sequence it can still name a different account than the
        credential being captured. A stale snapshot would later be restored as
        truth, which is the exact confusion this feature removes.
        """
        if account.get("provider") != "claude":
            return
        verified = self._account_verified_key(account)
        if verified is None:
            return
        config = self._read_claude_config()
        block = _record(config.get("oauthAccount")) if config else {}
        uuid_value = _string(block.get("accountUuid"))
        if uuid_value is None or uuid_value.casefold() != verified:
            return
        content = (json.dumps(block, indent=2, sort_keys=True) + "\n").encode("utf-8")
        path = self._oauth_snapshot_path(str(account["id"]))
        try:
            if path.is_file() and path.read_bytes() == content:
                return
            _atomic_write(path, content)
        except OSError:
            pass

    def _restore_oauth_account(self, account: dict[str, Any]) -> None:
        """Make the CLI's cached profile agree with the account just selected.

        Without this, every CLI surface that shows identity (``/status``, the
        browser bridge) keeps naming the outgoing account for up to a day after
        a switch, because the CLI trusts its cached ``oauthAccount`` while its
        24h freshness gate holds. Restoring the saved block corrects it
        immediately; when no snapshot exists, a minimal verified-identity block
        without ``profileFetchedAt`` fails that gate and forces the CLI to
        refetch and correct itself on the next session start.

        Only the ``oauthAccount`` key is touched, a block already naming the
        right account is left alone (the CLI's own copy is at least as fresh),
        and an unreadable config is never overwritten.
        """
        if account.get("provider") != "claude":
            return
        verified = self._account_verified_key(account)
        if verified is None:
            return
        config = self._read_claude_config()
        if config is None:
            return
        current = _string(_record(config.get("oauthAccount")).get("accountUuid"))
        if current is not None and current.casefold() == verified:
            return
        block: dict[str, Any] | None = None
        matched_by = "snapshot"
        try:
            saved = json.loads(
                self._oauth_snapshot_path(str(account["id"])).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            saved = None
        if isinstance(saved, dict):
            saved_uuid = _string(saved.get("accountUuid"))
            if saved_uuid is not None and saved_uuid.casefold() == verified:
                block = saved
        if block is None:
            matched_by = "verified_identity"
            block = {
                key: value
                for key, value in {
                    "accountUuid": _string(account.get("provider_account_id")),
                    "emailAddress": _string(account.get("email")),
                    "organizationName": _string(account.get("organization")),
                }.items()
                if value is not None
            }
        config["oauthAccount"] = block
        try:
            _atomic_write(
                self._claude_config_path(),
                json.dumps(config, ensure_ascii=False).encode("utf-8"),
            )
        except OSError:
            return
        self._audit(
            "oauth_profile_restored",
            provider="claude",
            account_id=str(account["id"]),
            matched_by=matched_by,
            detail=_string(account.get("email")),
        )

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
            "login": {provider: self._login_state(provider) for provider in PROVIDERS},
            "login_commands": {provider: self._login_command(provider) for provider in PROVIDERS},
        }

    def _login_command(self, provider: Provider) -> str:
        """What "sign in" will actually run, for a surface that has to say so.

        Derived from the *configured* executable and the profile's own login argv,
        because both halves are this daemon's to know: the browser used to compile
        its own copy of the sentence, which would still have read `claude auth login
        --claudeai` on an install that had pointed `harness_exe` somewhere else.
        """
        return " ".join([self.executables[provider], *_provider_profile(provider).login_args])

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
            # A newer deliberate selection retires any guard still defending an
            # older switch, so the two never fight over the credential file.
            self._selection_guard.pop(provider, None)
            self._snapshot_oauth_account(account)
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

    async def select(self, provider_value: str, account_id: str) -> dict[str, Any]:
        """Switch the live login, including for sessions already running.

        Provider processes re-read the shared credential file when its mtime
        changes, so a switch reaches every live session of that provider without
        restarting anything. It is therefore never refused: the one case that
        used to justify refusing it — the outgoing login rotating its token back
        over the swap — is handled afterwards by the selection guard instead.
        """
        provider = _provider(provider_value)
        async with self._mutation_lock:
            account = self._account(account_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
            active = _record(self._manifest.get("selected")).get(provider)
            live = self.live_sessions(provider)
            content, _ = self._read_json_auth(self._managed_auth_path(provider, account_id))
            previous_digest = _string(account.get("auth_digest"))
            _atomic_write(self._system_auth_path(provider), content)
            account["auth_digest"] = hashlib.sha256(content).hexdigest()
            account["updated_at"] = time.time()
            _record(self._manifest["selected"])[provider] = account_id
            self._restore_oauth_account(account)
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "selected",
                provider=provider,
                account_id=account_id,
                matched_by="user",
                old_digest=previous_digest,
                new_digest=_string(account.get("auth_digest")),
                detail=f"{len(live)} live session(s)" if live else None,
            )
            if live and active != account_id:
                self._arm_selection_guard(provider, account_id)
            else:
                self._selection_guard.pop(provider, None)
        await self.events.emit(
            "provider_account_selected",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )
        await self.refresh(account_id)
        return self.snapshot()

    def _arm_selection_guard(self, provider: Provider, account_id: str) -> None:
        """Hold a fresh switch against a straggling rotation from the old login.

        A refresh already in flight when the swap lands completes with the
        outgoing account's refresh token and writes that result into the shared
        credential file, reverting the switch with nothing to show for it. The
        guard re-applies the selection for a bounded window, and only when the
        live login resolves to a *different saved account* — an unidentified
        login is left alone rather than overwritten from a stale snapshot.
        """
        self._selection_guard[provider] = (
            account_id,
            time.monotonic() + SELECTION_GUARD_SECONDS,
        )
        background.start(
            f"{SELECTION_GUARD_LOOP}-{provider}",
            lambda: self._selection_guard_loop(provider),
        )

    async def _selection_guard_loop(self, provider: Provider) -> None:
        name = f"{SELECTION_GUARD_LOOP}-{provider}"
        while True:
            pin = self._selection_guard.get(provider)
            if pin is None or time.monotonic() >= pin[1]:
                if pin is not None:
                    self._selection_guard.pop(provider, None)
                return
            await asyncio.sleep(SELECTION_GUARD_POLL_SECONDS)
            with background.iteration(name):
                await self._reassert_selection(provider)

    async def _reassert_selection(self, provider: Provider) -> None:
        pin = self._selection_guard.get(provider)
        if pin is None or time.monotonic() >= pin[1]:
            return
        account_id = pin[0]
        await self.reconcile_current()
        current = _record(self._current.get(provider))
        # Only a live login that reconciliation positively resolved to another
        # saved account is a reverted switch. "external" covers both an
        # unidentifiable rotation and one this host is offline to verify;
        # re-applying a snapshot over either can destroy a valid newer token.
        if current.get("state") != "saved" or current.get("account_id") == account_id:
            return
        async with self._mutation_lock:
            if self._selection_guard.get(provider) != pin:
                return
            try:
                account = self._account(account_id)
                content, _ = self._read_json_auth(self._managed_auth_path(provider, account_id))
            except ProviderAccountError:
                self._selection_guard.pop(provider, None)
                return
            reverted_to = _string(current.get("account_id"))
            _atomic_write(self._system_auth_path(provider), content)
            account["auth_digest"] = hashlib.sha256(content).hexdigest()
            account["updated_at"] = time.time()
            _record(self._manifest["selected"])[provider] = account_id
            self._restore_oauth_account(account)
            self._reconcile_current(write=False)
            self._write()
            self._audit(
                "selection_reasserted",
                provider=provider,
                account_id=account_id,
                matched_by="selection_guard",
                new_digest=_string(account.get("auth_digest")),
                detail=f"a live session rotated {reverted_to} back over the switch",
            )
        await self.events.emit(
            "provider_account_selected",
            source="provider_accounts",
            provider=provider,
            account_id=account_id,
        )

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
            self._selection_guard.pop(provider, None)
            self._snapshot_oauth_account(account)
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

    @staticmethod
    def _login_task_name(provider: Provider) -> str:
        return f"{LOGIN_LOOP_PREFIX}-{provider}"

    def _login_state(self, provider: Provider) -> dict[str, Any] | None:
        """The public view of one provider's sign-in, or nothing to report.

        Reading never mutates: a success that has outlived its linger window
        simply stops being reported, and the record itself is dropped the next
        time a sign-in for that provider starts. A read path that pruned would
        make two clients polling the same daemon disagree about which of them
        saw the success.
        """
        record = self._login.get(provider)
        if record is None:
            return None
        finished = _number(record.get("finished_at"))
        if (
            record.get("state") == "succeeded"
            and finished is not None
            and time.time() - finished > LOGIN_SUCCESS_LINGER_SECONDS
        ):
            return None
        return dict(record)

    async def start_login(
        self, provider_value: str, *, label: str | None = None, replace_id: str | None = None
    ) -> dict[str, Any]:
        """Begin an interactive sign-in and return at once.

        The provider CLI can hold the terminal for the full `LOGIN_TIMEOUT_SECONDS`
        while a human finishes an OAuth flow in a browser, which is far too long to
        keep an HTTP request open: whoever asked used to own the only copy of the
        outcome, so closing the panel, reloading, or asking from a second device
        lost it. The run is a supervised background task and its progress is part
        of `snapshot()`, so every client sees the same one.
        """
        provider = _provider(provider_value)
        running = self._login.get(provider)
        if running is not None and running.get("state") == "running":
            raise ProviderAccountConflict(f"a {provider} sign-in is already running")
        if replace_id:
            # Validate before spawning anything: a bad `replace_id` should be a
            # rejected request, not a browser window the operator then has to
            # finish before being told the target slot does not exist.
            account = self._account(replace_id)
            if account.get("provider") != provider:
                raise ProviderAccountError("account provider does not match")
        request_id = current_request_id() or new_request_id()
        self._login[provider] = {
            "provider": provider,
            "state": "running",
            "started_at": time.time(),
            "finished_at": None,
            "account_id": None,
            "label": None,
            "error": None,
            "replacing": replace_id,
            "request_id": request_id,
        }
        background.start(
            self._login_task_name(provider),
            lambda: self._run_login(provider, label, replace_id, request_id),
        )
        return self.snapshot()

    async def _run_login(
        self,
        provider: Provider,
        label: str | None,
        replace_id: str | None,
        request_id: str,
    ) -> None:
        """Run one sign-in to its end and record how it ended. Never raises.

        The task supervisor restarts a loop that fails, which is right for a poll
        and exactly wrong here: relaunching a provider's interactive login because
        the last one exited nonzero would reopen a browser the operator just
        cancelled. Returning normally on both outcomes is what tells the
        supervisor this task finished on purpose.
        """
        # No `background.iteration` guard: it times wall clock inside the block, and
        # this block is almost entirely a wait on a human in a browser. A five-minute
        # login would report as this daemon's costliest loop while the event loop sat
        # idle throughout, which is the exact misreading that guard's docstring warns
        # about. It also has nothing to catch - every outcome is handled here.
        with bound_request_id(request_id):
            try:
                await self.login_and_capture(provider, label=label, replace_id=replace_id)
            except asyncio.CancelledError:
                # `run_bounded` has already reaped the CLI on its way out. Record
                # the cancellation as a terminal state rather than leaving a
                # "running" that no process backs, then let the cancellation
                # continue to the supervisor.
                self._finish_login(provider, error=f"{provider} sign-in cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 - the record is the error's only home
                log.warning(
                    "provider_login_failed",
                    extra={"provider": provider, "error": str(exc)},
                )
                self._finish_login(provider, error=str(exc))
                return
            selected = _record(self._manifest.get("selected")).get(provider)
            account = next(
                (item for item in self._accounts() if str(item.get("id")) == str(selected)),
                None,
            )
            self._finish_login(
                provider,
                account_id=None if account is None else str(account["id"]),
                label=None if account is None else _string(account.get("label")),
            )

    def _finish_login(
        self,
        provider: Provider,
        *,
        account_id: str | None = None,
        label: str | None = None,
        error: str | None = None,
    ) -> None:
        record = self._login.get(provider)
        if record is None or record.get("state") != "running":
            # Dismissed or superseded while the CLI was still running; the newer
            # record is the one clients are watching and must not be overwritten.
            return
        record.update(
            {
                "state": "failed" if error else "succeeded",
                "finished_at": time.time(),
                "account_id": account_id,
                "label": label,
                "error": error,
            }
        )

    async def dismiss_login(self, provider_value: str) -> dict[str, Any]:
        """Cancel a running sign-in, or clear one that has already finished.

        Both are the same gesture from the operator's side - "I am done with
        this" - and collapsing them into one endpoint means a client never has to
        decide which it is against state that may have changed since it rendered.
        """
        provider = _provider(provider_value)
        record = self._login.get(provider)
        if record is not None and record.get("state") == "running":
            # Cancelling reaps the provider CLI: `run_bounded` kills the process
            # tree on any exception leaving it, `CancelledError` included.
            await background.stop(self._login_task_name(provider))
        self._login.pop(provider, None)
        return self.snapshot()

    async def login_and_capture(
        self, provider_value: str, *, label: str | None = None, replace_id: str | None = None
    ) -> dict[str, Any]:
        provider = _provider(provider_value)
        args = list(_provider_profile(provider).login_args)
        # An interactive login is an administrative operation a human waited five
        # minutes on; it is worth a line whether or not it worked. The argv is the
        # provider profile's own fixed login arguments, so it carries nothing the
        # user typed and no credential.
        log.info(
            "provider_login_started",
            extra={"provider": provider, "argv": args, "replacing": replace_id},
        )
        await self._run_command(provider, args, timeout_seconds=LOGIN_TIMEOUT_SECONDS)
        snapshot = await self.capture_current(provider, label=label, replace_id=replace_id)
        log.info("provider_login_captured", extra={"provider": provider})
        return snapshot

    def _resolve_executable(self, provider: Provider) -> ExecutableResolution:
        """Resolve the configured provider CLI, or carry back why nothing was.

        `resolve_executable` never returns the mux's own ~/.mux/bin agent shim - a
        daemon whose PATH inherited a session's shim dir would otherwise run
        login/status through the shim, which recurses into itself - and never a
        Windows binary reached through WSL interop.

        The suffix-stripping retry is deliberately **unconditional**, where it used
        to be gated on `os.name == "nt"`. That guard pointed exactly the wrong way.
        On Windows an `.exe` suffix is at least plausible and PATHEXT usually has
        the answer, so the retry mostly repaired a config that was only half wrong.
        On POSIX an `.exe` suffix is *certainly* wrong, and that is the one host
        where the repair never ran: a config authored on Windows and carried to a
        WSL Ubuntu install produced `Could not start codex: [Errno 2] No such file
        or directory: 'codex.exe'` while a perfectly good `codex` sat on the same
        PATH (2026-08-28). npm-installed CLIs commonly expose only
        `codex.cmd`/`claude.cmd`, or a bare extensionless `codex`, whichever host
        this is.
        """
        configured = self.executables[provider]
        resolution = resolve_executable(configured)
        if not resolution.usable and Path(configured).suffix.casefold() == ".exe":
            stem = str(Path(configured).with_suffix(""))
            resolution = combine_resolutions(resolution, resolve_executable(stem))
        return resolution

    def _spawn_command(
        self, provider: Provider, args: list[str], *, windows: bool | None = None
    ) -> list[str]:
        """The argv for one provider CLI invocation.

        ``windows`` exists so the COMSPEC branch can be exercised from either host;
        it is the same seam `launchers.resolve_npm_shim_pty_command` already uses,
        and it defaults to the real answer.
        """
        return self._command_for(provider, self._resolve_executable(provider), args, windows)

    def _command_for(
        self,
        provider: Provider,
        resolution: ExecutableResolution,
        args: list[str],
        windows: bool | None = None,
    ) -> list[str]:
        if resolution.reason in {"mux_shim", "windows_interop"}:
            # A refusal is not an absence: something of that name *is* installed and
            # would run. Falling through to exec the configured value anyway is how
            # the shim used to recurse into itself, and on WSL it is how a Windows
            # CLI would be driven from a Linux daemon - the exact thing the
            # resolver refused a line earlier.
            log.error(
                "provider_cli_refused %s",
                resolution.describe(),
                extra={
                    "provider": provider,
                    "configured": self.executables[provider],
                    "reason": resolution.reason,
                    "rejected": resolution.rejected,
                },
            )
            raise ProviderAccountError(f"Could not start {provider}: {resolution.describe()}")
        # `not_found` still falls back to the configured value rather than refusing:
        # an operator may have named something this daemon's PATH cannot see, and
        # the OSError that follows now arrives with the resolution attached
        # (`_run_command`) instead of bare.
        executable = resolution.path or self.executables[provider]
        if windows is None:
            windows = IS_WINDOWS
        if windows and Path(executable).suffix.casefold() in {".cmd", ".bat"}:
            return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", executable, *args]
        return [executable, *args]

    async def _run_command(
        self, provider: Provider, args: list[str], *, timeout_seconds: float
    ) -> str:
        """Run one provider CLI command and return its stdout.

        The cap is new: a login flow that decides to stream a QR code, a progress
        bar, or a stack trace is not this daemon's to size, and the diagnostic slice
        below reads the *tail*, which head-and-tail truncation preserves.

        Every failure here is logged before it is raised. Until 2026-08-28 none of
        them were: a provider CLI that could not start, timed out, or exited
        nonzero existed only in the HTTP response body of whoever happened to ask,
        and `daemon.log` held nothing about it at all. What is logged is the
        *resolution* and the failure, never the payload.
        """
        resolution = self._resolve_executable(provider)
        command = self._command_for(provider, resolution, args)
        log.debug(
            "provider_command_started",
            extra={
                "provider": provider,
                "argv": list(args),
                "executable": command[0],
                "timeout_seconds": timeout_seconds,
            },
        )
        try:
            outcome = await run_bounded(
                command,
                label=f"provider-{provider}",
                timeout_seconds=timeout_seconds,
                output_limit=MAX_COMMAND_OUTPUT_BYTES,
                stderr_limit=MAX_COMMAND_OUTPUT_BYTES,
                # The request that asked for the login or the status read, or the
                # id the quota poll minted for its own iteration. A login that
                # times out at 300s is exactly the run somebody has to find in the
                # log afterwards, and it used to log `operation_id=None`.
                operation_id=current_request_id() or None,
            )
        except OSError as exc:
            log.error(
                "provider_command_unstartable %s: %s",
                resolution.describe(),
                exc,
                extra={
                    "provider": provider,
                    "configured": self.executables[provider],
                    "reason": resolution.reason,
                    "resolved": resolution.path,
                    "argv": list(args),
                },
            )
            raise ProviderAccountError(
                f"Could not start {provider}: {exc} ({resolution.describe()})"
            ) from exc
        if outcome.timed_out:
            log.warning(
                "provider_command_timed_out",
                extra={
                    "provider": provider,
                    "executable": command[0],
                    "argv": list(args),
                    "timeout_seconds": timeout_seconds,
                },
            )
            raise ProviderAccountError(f"{provider} login timed out")
        output = outcome.stdout.decode(errors="replace").strip()
        diagnostic = outcome.stderr.decode(errors="replace").strip()
        if outcome.exit_code:
            detail = (
                diagnostic[-DIAGNOSTIC_TAIL_CHARS:]
                or output[-DIAGNOSTIC_TAIL_CHARS:]
                or f"exit code {outcome.exit_code}"
            )
            log.warning(
                "provider_command_failed",
                extra={
                    "provider": provider,
                    "executable": command[0],
                    "argv": list(args),
                    "exit_code": outcome.exit_code,
                    # stderr only, bounded to the same tail the operator-facing
                    # error already carries. A provider CLI's *stdout* is where a
                    # token or a credential blob would be, so it is never logged
                    # even when it is the only thing a failure printed.
                    "stderr_tail": diagnostic[-DIAGNOSTIC_TAIL_CHARS:],
                },
            )
            raise ProviderAccountError(f"{provider} command failed: {detail}")
        return output

    def start(self) -> None:
        # Supervised: a single transient failure (a Windows lock on the manifest
        # replace, a SQLite busy timeout) used to end quota polling, reset
        # detection and managed-token rotation for the daemon's lifetime.
        if self._task is None:
            self._task = background.start(QUOTA_POLL_LOOP, self._loop)
        if self._event_task is None:
            self._event_queue = self.events.subscribe(name="provider-accounts")
            self._event_task = background.start(QUOTA_TURN_REFRESH_LOOP, self._event_refresh_loop)

    async def stop(self) -> None:
        await background.stop(QUOTA_POLL_LOOP)
        self._task = None
        await background.stop(QUOTA_TURN_REFRESH_LOOP)
        self._event_task = None
        self._selection_guard.clear()
        for provider in PROVIDERS:
            await background.stop(f"{SELECTION_GUARD_LOOP}-{provider}")
            # An interactive login outlives nothing: it is a child holding this
            # daemon's pipes, and a daemon that stops without reaping it leaves a
            # provider CLI attached to a dead parent.
            await background.stop(self._login_task_name(provider))
        self._login.clear()
        if self._event_queue:
            self.events.unsubscribe(self._event_queue)
            self._event_queue = None
        if self._http:
            await self._http.close()
            self._http = None

    async def _loop(self) -> None:
        await asyncio.sleep(2)
        while True:
            # One id per poll, for the same reason the git monitor mints one: the
            # provider CLI runs this iteration starts have no request behind them,
            # and a timed-out `provider-claude` line with no identifier cannot be
            # joined to the poll that produced it.
            with background.iteration(QUOTA_POLL_LOOP), bound_request_id(new_request_id()):
                await self.refresh()
            await asyncio.sleep(self.poll_seconds)

    async def _event_refresh_loop(self) -> None:
        assert self._event_queue is not None
        while True:
            event = await self._event_queue.get()
            try:
                with (
                    background.iteration(QUOTA_TURN_REFRESH_LOOP),
                    bound_request_id(new_request_id()),
                ):
                    await self._maybe_refresh_for_turn(event)
            finally:
                self._event_queue.task_done()

    async def _maybe_refresh_for_turn(self, event: MuxEvent) -> None:
        if (
            not self.turn_refresh_enabled
            or event.type != "turn_ended"
            or event.payload.get("scope", "root") != "root"
            or not event.session_id
        ):
            return
        now = time.monotonic()
        if now - self._last_event_refresh < self.turn_refresh_min_seconds:
            return
        session = self.sessions.sessions.get(event.session_id) if self.sessions else None
        provider = str(session.record.backend) if session else ""
        if provider not in PROVIDERS:
            return
        account_id = _record(self._manifest.get("selected")).get(provider)
        if not account_id:
            return
        self._last_event_refresh = now
        await self.refresh(str(account_id))

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
            is_selected = _record(self._manifest.get("selected")).get(provider) == account_id
            if provider == "claude":
                if is_selected:
                    # The CLI refreshes this block itself while it runs; folding
                    # each refetch back into the snapshot keeps the copy restored
                    # on the next switch as fresh as the CLI's own.
                    self._snapshot_oauth_account(account)
                # Rotating the selected account's token while live sessions run
                # under it races the CLI's own rotation of the same refresh
                # token; the loser of that race writes a dead credential. The
                # live process owns the refresh — a 401 here just leaves quota
                # stale until the CLI rotates and reconciliation syncs the slot.
                allow_refresh = not (is_selected and self.live_sessions(provider))
                quota, updated = await self._fetch_claude(auth, allow_refresh=allow_refresh)
            else:
                quota, updated = await self._fetch_codex(auth, account_id)
            if updated is not None:
                system_still_matches = self._system_matches_account(provider, account)
                content = (json.dumps(updated, separators=(",", ":")) + "\n").encode()
                managed_path = self._managed_auth_path(provider, account_id)
                previous_digest = str(account.get("auth_digest") or "")
                current_digest = hashlib.sha256(
                    managed_path.read_bytes() if managed_path.is_file() else b""
                ).hexdigest()
                if previous_digest and current_digest != previous_digest:
                    # The slot changed hands while this refresh was in flight (a
                    # capture landed a different login). Writing the rotated old
                    # credential over it would silently undo that.
                    self._audit(
                        "rotation_skipped",
                        provider=provider,
                        account_id=account_id,
                        detail="slot credential changed during refresh",
                    )
                else:
                    # Same backup + audit trail as every other credential write.
                    # A background rotation is exactly the silent rewrite the
                    # audit log exists to explain after the fact.
                    self._backup_managed_auth(managed_path)
                    _atomic_write(managed_path, content)
                    digest = hashlib.sha256(content).hexdigest()
                    self._audit(
                        "rotated_auth_written",
                        provider=provider,
                        account_id=account_id,
                        matched_by="token_refresh",
                        old_digest=previous_digest or current_digest,
                        new_digest=digest,
                    )
                    if (
                        _record(self._manifest.get("selected")).get(provider) == account_id
                        and system_still_matches
                    ):
                        _atomic_write(self._system_auth_path(provider), content)
                    account["auth_digest"] = digest
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
        self, auth: dict[str, Any], *, allow_refresh: bool = True
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
        if status in {400, 401, 403} and not allow_refresh:
            raise ProviderAccountError(
                f"Claude quota request failed (HTTP {status}); a live session owns this "
                f"token's rotation, retrying after it refreshes"
            )
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
        # ignore_cleanup_errors: a straggler from the killed app-server tree can
        # briefly hold auth.json open; a leaked temp dir must not turn a
        # completed quota read into a WinError 32 refresh failure.
        with tempfile.TemporaryDirectory(
            prefix="swe-mux-codex-quota-", ignore_cleanup_errors=True
        ) as temporary:
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
                await reap_process_tree(process)
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
                        "params": {"clientInfo": {"name": "swe-mux", "version": "0.1.2"}},
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
                # The app-server never exits on its own (it waits on our stdin),
                # so this must take the whole cmd->node->codex tree down or the
                # refresh coroutine deadlocks holding the refresh lock.
                await reap_process_tree(process)
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
