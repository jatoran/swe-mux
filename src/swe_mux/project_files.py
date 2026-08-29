from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import logging
import math
import os
import re
import stat
import time
import tomllib
import uuid
from collections import deque
from collections.abc import Collection, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .agent_authority import ENVELOPE_COMPACT, ENVELOPE_FULL
from .automation_registry import REGISTRY as AUTOMATION_REGISTRY
from .git_projects import ProjectIdentity, rebase_identity, resolve_project
from .harness import is_agent_harness
from .leaf_names import validate_leaf_name
from .nested_worktrees import nested_worktree_paths

PROJECT_CONFIG_VERSION = 1
LEGACY_AUTOMATION_IDS = frozenset({"project_card"})
PROJECT_CONFIG_FIELDS = {
    "default_shell_profile",
    "default_agent_profiles",
    "preferred_backend",
    "resource_open_mode",
    "prompt_library_scope",
    "notification_sounds_enabled",
    "ignore_patterns",
    "automations",
    "worktree",
    # Scan timeline's run grant belongs to one provider conversation and defaults
    # off, so a Project that wants a timeline had to be re-armed after every
    # `/clear`, every new session, every rollover. This says "yes, always" for
    # this Project. It only ever creates a grant for a run that has none: a run
    # the human switched off stays off.
    "scan_timeline_auto_enable",
    # Phase 7.6: the authority level for agent session control in this Project,
    # once the `session_control` automation is opted in (itself on by default
    # since 2026-08-25). "granted" (the default when unset) lets an agent act
    # directly inside the daemon's bounds; "draft" makes an interrupt/end write
    # an inert request a human approves. The on/off is the automation opt-in;
    # this only distinguishes draft from granted.
    "session_control_grant",
    # Phase 7.6 follow-on: the authority level for agent-initiated spawn INTO this
    # Project (`mux.requestSpawn`). Same shape as session_control_grant and gated
    # by the same `session_control` automation. "granted" (the default when
    # unset, flipped 2026-08-25) lets an agent create a session here directly,
    # inside a per-origin budget; "draft" restores the Phase 5 behaviour - the
    # call writes an inert Fleet Queue request a human approves. Authority is by
    # target Project, as for interrupt/end.
    "spawn_grant",
    # Phase 14: the authority level for agent-initiated landing of a worktree branch
    # into this Project's trunk, once the `land_queue` automation is opted in.
    # "draft" (the default) makes `request_land` write an inert request a human
    # approves; "granted" lets the pipeline start on the agent's word, inside a
    # per-origin hourly budget. Its own field rather than a level of
    # `session_control_grant` for the same reason it is its own automation: that one
    # acts on a session, this one moves a repository's trunk.
    "land_grant",
    # Whether an agent in another session may have a message delivered into a
    # *running* turn here (`mux.notify(delivery="now")`). "granted" (the default
    # when unset, flipped 2026-08-25) allows a mid-turn write, still only when
    # the readiness tracker's separate interject predicate is satisfied and the
    # receiving session has not opted out for its run. "off" restores the
    # behaviour that existed before the mode did: every agent message waits for
    # this session's queue and its readiness contract. Deliberately its own
    # field rather than a level of `session_control_grant`: being written to
    # mid-turn is a property of a working repository, and reusing an actuation
    # grant for it would grant it to every Project that wanted interrupt/end.
    "interject_grant",
    # How much metadata a delivered agent message carries into the receiving
    # session's prompt. "full" states the whole relay policy, "compact" (the
    # built-in default) keeps the four facts a receiver acts on, "bare" delivers
    # the body alone and is textually indistinguishable from the operator
    # typing. It is an authority field in the same sense as the four above -
    # `bare` is the widest latitude, because an agent that need not announce
    # itself has more of it - which is why a malformed config resolves to
    # "full" here while it resolves to "draft"/"off" there. Attribution is not
    # lost at any level: the queue, the audit trail, and Fleet Queue record the
    # sender regardless, and what the level decides is only what the receiving
    # *model* is told.
    "message_envelope",
    # Which tool-permission requests mux may answer for sessions in this Project
    # while a conversation holds the `allowlisted` mode, as `Tool` /
    # `Tool(pattern)` rules. The rules live here rather than on the session
    # because "reading this repo's .claude config is fine" is a property of the
    # codebase, and because a rules editor is the wrong thing to hand someone in
    # the moment they want to switch a mode on. Unset means the built-in
    # `approvals.DEFAULT_ALLOW_RULES`; an explicit empty list means "nothing".
    "approval_allow",
    # The strongest mode a session in this Project may hold, whatever the
    # operator picks. A repository can therefore refuse `allow_all` outright
    # without depending on everyone remembering not to select it.
    "approval_ceiling",
}
#: The two authority levels a grant field may hold. "off" is not a value here -
#: it is the absence of the `session_control` automation opt-in.
SESSION_CONTROL_GRANTS = ("draft", "granted")
#: What `message_envelope` may hold, **widest disclosure first** so it reads in
#: the order a person thinks about it. `agent_authority.AUTHORITY_FIELDS` holds
#: the same three in the opposite order, because comparison there is by
#: latitude; a test pins the two against each other so they cannot drift.
MESSAGE_ENVELOPES = ("full", "compact", "bare")
#: What `interject_grant` may hold. "draft" is absent on purpose: a drafted
#: interject is a contradiction - a human arming it later delivers it to whatever
#: turn is running *then*, which is an ordinary queued message and already exists.
INTERJECT_GRANTS = ("off", "granted")
#: Values `approval_ceiling` may hold, weakest first. Mirrors
#: `models.APPROVAL_MODES` and is asserted equal to it in the test suite; it is
#: restated rather than imported to keep this module free of model imports.
APPROVAL_CEILINGS = ("wait", "allowlisted", "allow_all")
MAX_PROJECT_APPROVAL_RULES = 256
# Read and discarded, never written. The scan-timeline dollar budget was a
# per-project field; it is one global setting now, because a cap that lives in
# a committed file nobody opens is a cap nobody can find when it stops the
# feature. Tolerated here so an existing checkout - including one whose
# `.swe-mux/config.toml` is committed and shared - still parses; the next write
# drops the key.
LEGACY_PROJECT_FIELDS = frozenset({"scan_timeline_daily_budget_usd"})
FORBIDDEN_PROJECT_FIELDS = {
    "token",
    "bind",
    "host",
    "port",
    "data_dir",
    "hooks",
    "executable",
    "command",
}
_SAFE_NOTE_ID = re.compile(r"[A-Za-z0-9._-]{1,120}\Z")
DEFAULT_NOTE_STORAGE_ID = "project"
GLOBAL_SCRATCHPAD_ID = "scratchpad"
MAX_NOTE_TITLE_CHARS = 160
MAX_NOTE_SUMMARIES = 500
NOTE_EXCERPT_CHARS = 240
MAX_NOTE_MARKDOWN_BYTES = 1024 * 1024
MAX_NOTE_FILE_BYTES = MAX_NOTE_MARKDOWN_BYTES + 8192

log = logging.getLogger(__name__)

PROJECT_TEXT_FILE_MAX_BYTES = 2 * 1024 * 1024
PROJECT_IMAGE_FILE_MAX_BYTES = 16 * 1024 * 1024
PROJECT_IMAGE_MAX_DIMENSION = 16_384
PROJECT_IMAGE_MAX_PIXELS = 25_000_000
PROJECT_IMAGE_MAX_FRAMES = 200
PROJECT_IMAGE_MAX_TOTAL_PIXELS = 100_000_000

_IMAGE_FORMATS: dict[str, tuple[str, frozenset[str]]] = {
    "PNG": ("image/png", frozenset({".png"})),
    "JPEG": ("image/jpeg", frozenset({".jpg", ".jpeg", ".jpe"})),
    "GIF": ("image/gif", frozenset({".gif"})),
    "WEBP": ("image/webp", frozenset({".webp"})),
}
_IMAGE_EXTENSIONS = frozenset(
    extension for _mime, extensions in _IMAGE_FORMATS.values() for extension in extensions
)
_DELIMITED_EXTENSIONS = {".csv": ",", ".tsv": "\t"}
_RESERVED_PROJECT_RESOURCE_NAMES = frozenset({".git", ".swe-mux"})

PROJECT_LOCAL_GITIGNORE = """\
# Local swe-mux data. Portable Project files beside these remain visible to Git.
/notes/
/attachments/
/seeds/
/preview-shots/
/observations.json
"""


class ProjectFileRevisionConflict(ValueError):
    """The caller inspected a different file revision than the one now on disk."""


class ProjectImageUnavailable(ValueError):
    """A Project file is not an allowlisted, bounded image response."""


class ProjectResourceExists(ValueError):
    """An exclusive Project file/folder create collided with an existing entry."""


class ProjectNoteProtected(ValueError):
    """A Project keeps at least one note, so its final note cannot be deleted."""


class ProjectConfigConflict(ValueError):
    """A field the caller edited moved on disk since the caller read it.

    Narrower than a whole-file revision mismatch on purpose. `.swe-mux/config.toml`
    is written by several surfaces that own disjoint keys - the Projects editor's
    three sections, a grant gate, the land queue's verify command, the configurator,
    the file browser's ignore - so a file-level guard reports every one of them as a
    collision with every other, which is a conflict the operator cannot act on and
    did not cause. Only the keys the caller actually named are compared.

    Kept a `ValueError` carrying "changed externally" so the routes that string-match
    that phrase still answer 409, and a distinct type so the surface can say *which*
    fields collided and hand back the current file instead of asking for a reload.
    """

    def __init__(self, fields: Sequence[str], current: dict[str, Any]) -> None:
        super().__init__(
            "project config changed externally in "
            + ", ".join(fields)
            + "; reload before saving"
        )
        self.fields = list(fields)
        self.current = current


def revision(data: bytes | None) -> str:
    return hashlib.sha256(data or b"").hexdigest()[:24] if data is not None else "missing"


def _unread_revision() -> str:
    # Oversized files are deliberately never hashed: a revision calculation must not turn a
    # metadata-only refusal into a complete read of an arbitrarily large file.
    return "unavailable"


def _read_regular_file_bounded(target: Path, limit: int) -> tuple[bytes | None, int]:
    """Read one regular file without ever crossing ``limit`` bytes.

    ``Path.read_bytes`` checks no bound while reading. Inspecting a sparse or multi-gigabyte
    file through the browser must remain a cheap refusal, including when the file grows between
    path resolution and open.
    """

    try:
        with target.open("rb") as handle:
            file_stat = os.fstat(handle.fileno())
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError("project file is not a regular file")
            if file_stat.st_size > limit:
                return None, file_stat.st_size
            data = handle.read(limit + 1)
    except OSError as exc:
        raise ValueError(f"unable to read project file: {exc}") from exc
    if len(data) > limit:
        return None, len(data)
    return data, len(data)


def _unsupported_presentation(reason: str) -> dict[str, Any]:
    return {"kind": "unsupported", "reason": reason}


def image_inspection_available() -> bool:
    """Whether Pillow is importable, so image presentation is a real capability here.

    Pillow ships wheels for every supported target, so this is normally true
    everywhere. It is a capability check rather than a platform check because the
    two are different questions: a build can legitimately omit Pillow, and the
    correct behaviour then is a file that presents as unsupported, not a daemon
    that will not import. Answering it wrong in the other direction is worse -
    silently claiming image support that is not there.
    """
    return _pillow() is not None


def _pillow() -> Any | None:
    """The Pillow ``Image`` module, or None when Pillow is not installed."""
    global _PIL_IMAGE, _PIL_CHECKED
    if not _PIL_CHECKED:
        _PIL_CHECKED = True
        try:
            from PIL import Image as _image

            _PIL_IMAGE = _image
        except ImportError:
            log.info("Pillow is not installed; project image presentation is unavailable")
            _PIL_IMAGE = None
    return _PIL_IMAGE


_PIL_IMAGE: Any | None = None
_PIL_CHECKED = False


def _inspect_image_bytes(data: bytes, suffix: str) -> dict[str, Any]:
    """Return bounded metadata for an explicitly image-named file.

    Pillow is only asked to parse the narrow extension allowlist. Its decoded format must agree
    with the extension, and both per-frame and cumulative decoded sizes are capped before any
    browser receives the bytes.
    """

    Image = _pillow()
    if Image is None:
        return _unsupported_presentation("image-inspection-unavailable")
    UnidentifiedImageError = Image.UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = str(image.format or "").upper()
            allowed = _IMAGE_FORMATS.get(image_format)
            if allowed is None:
                return _unsupported_presentation("image-format-not-allowed")
            mime, extensions = allowed
            if suffix not in extensions:
                return _unsupported_presentation("image-signature-extension-mismatch")
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1) or 1)
            if width <= 0 or height <= 0:
                return _unsupported_presentation("invalid-image-dimensions")
            if width > PROJECT_IMAGE_MAX_DIMENSION or height > PROJECT_IMAGE_MAX_DIMENSION:
                return _unsupported_presentation("image-dimension-limit")
            if width * height > PROJECT_IMAGE_MAX_PIXELS:
                return _unsupported_presentation("image-pixel-limit")
            if frames > PROJECT_IMAGE_MAX_FRAMES:
                return _unsupported_presentation("image-frame-limit")
            total_pixels = 0
            for frame_index in range(frames):
                image.seek(frame_index)
                frame_width, frame_height = image.size
                if frame_width <= 0 or frame_height <= 0:
                    return _unsupported_presentation("invalid-image-dimensions")
                if (
                    frame_width > PROJECT_IMAGE_MAX_DIMENSION
                    or frame_height > PROJECT_IMAGE_MAX_DIMENSION
                    or frame_width * frame_height > PROJECT_IMAGE_MAX_PIXELS
                ):
                    return _unsupported_presentation("image-frame-pixel-limit")
                total_pixels += frame_width * frame_height
                if total_pixels > PROJECT_IMAGE_MAX_TOTAL_PIXELS:
                    return _unsupported_presentation("image-animation-pixel-limit")
        # Reopen for structural verification. ``verify`` invalidates its image object and is
        # therefore deliberately separate from the frame metadata walk.
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (
        EOFError,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        SyntaxError,
        ValueError,
    ):
        return _unsupported_presentation("invalid-image")
    return {
        "kind": "image",
        "mime": mime,
        "width": width,
        "height": height,
        "frames": frames,
    }


def _read_project_image(
    root: str | Path, relative_path: str
) -> tuple[dict[str, Any], bytes | None]:
    target = project_path(root, relative_path)
    suffix = Path(relative_path.replace("\\", "/")).suffix.casefold()
    data, size = _read_regular_file_bounded(target, PROJECT_IMAGE_FILE_MAX_BYTES)
    if data is None:
        return (
            {
                "path": relative_path,
                "revision": _unread_revision(),
                "status": "too-large",
                "size": size,
                "presentation": {
                    "kind": "image",
                    "reason": "encoded-size-limit",
                },
            },
            None,
        )
    presentation = _inspect_image_bytes(data, suffix)
    status = "viewable" if presentation["kind"] == "image" else "unsupported"
    return (
        {
            "path": target.relative_to(Path(root).resolve()).as_posix(),
            "revision": revision(data),
            "status": status,
            "size": size,
            "presentation": presentation,
        },
        data if status == "viewable" else None,
    )


def safe_note_filename(identity: str) -> str:
    if _SAFE_NOTE_ID.fullmatch(identity):
        return identity
    if not identity or len(identity) > 512 or any(ord(character) < 32 for character in identity):
        raise ValueError("note identity must be a non-empty printable string up to 512 characters")
    return f"id-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def note_path(root: str | Path, identity: str) -> Path:
    mux_dir = Path(root).resolve() / ".swe-mux" / "notes"
    if identity == DEFAULT_NOTE_STORAGE_ID:
        return mux_dir / "project.md"
    return mux_dir / "items" / f"{safe_note_filename(identity)}.md"


def ensure_project_local_data_ignored(root: str | Path) -> Path:
    """Create the Project-local ignore manifest without rewriting one the user owns."""

    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise FileNotFoundError(f"Project folder is unavailable: {project_root}")
    mux_root = project_root / ".swe-mux"
    mux_root.mkdir(parents=True, exist_ok=True)
    if mux_root.is_symlink() or not mux_root.is_dir():
        raise ValueError("Project swe-mux storage path is unsafe")
    ignore = mux_root / ".gitignore"
    try:
        with ignore.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(PROJECT_LOCAL_GITIGNORE)
        log.info("Project local-data Git ignore initialized root=%s path=%s", root, ignore)
    except FileExistsError:
        pass
    if ignore.is_symlink() or not ignore.is_file():
        raise ValueError("Project local-data ignore file is unsafe")
    return ignore


def ensure_project_notes_ignored(root: str | Path) -> Path:
    """Keep all Project-owned note storage out of Git."""
    project_root = Path(root).resolve()
    if not project_root.is_dir():
        raise FileNotFoundError(f"Project folder is unavailable: {project_root}")
    notes_root = project_root / ".swe-mux" / "notes"
    notes_root.mkdir(parents=True, exist_ok=True)
    if notes_root.is_symlink() or not notes_root.is_dir():
        raise ValueError("Project note storage path is unsafe")
    ignore = notes_root / ".gitignore"
    try:
        with ignore.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write("*\n")
        log.info("Project notes Git ignore initialized root=%s path=%s", root, ignore)
    except FileExistsError:
        pass
    if ignore.is_symlink() or not ignore.is_file():
        raise ValueError("Project note ignore file is unsafe")
    return ignore


def note_exists(root: str | Path, identity: str) -> bool:
    return note_path(root, identity).is_file()


NOTE_HEADER_PREFIX = "---\nswe_mux_note = 1\n"


def normalize_note_title(value: str, *, default: str | None = None) -> str:
    title = " ".join(value.split())
    if not title:
        if default is None:
            raise ValueError("note title must not be empty")
        title = default
    if len(title) > MAX_NOTE_TITLE_CHARS:
        raise ValueError(f"note title must be {MAX_NOTE_TITLE_CHARS} characters or fewer")
    return title


def note_header(
    identity: str,
    title: str,
    *,
    kind: str = "notes",
    created_at: float | None = None,
    origin_session_id: str | None = None,
) -> str:
    """Build the hidden metadata header for one durable note."""
    lines = [
        "---",
        "swe_mux_note = 1",
        f"kind = {json.dumps(kind)}",
        f"id = {json.dumps(identity)}",
        f"title = {json.dumps(normalize_note_title(title, default='Untitled note'))}",
        f"created_at = {float(created_at if created_at is not None else time.time())}",
    ]
    if origin_session_id:
        lines.append(f"origin_session_id = {json.dumps(origin_session_id)}")
    return "\n".join([*lines, "---", ""])


def _note_metadata(text: str) -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith(NOTE_HEADER_PREFIX):
        return {}
    boundary = normalized.find("\n---\n", 4)
    if boundary < 0:
        return {}
    try:
        value = tomllib.loads(normalized[4:boundary])
    except tomllib.TOMLDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _note_body(text: str) -> str:
    """Return a note's text with its identity header removed.

    Newlines are normalized first because the header is matched byte-exactly.
    `read_note` decodes raw bytes to hash the file, so unlike the `read_text`
    callers it gets no universal-newline translation, and a note rewritten with
    CRLF — by Git checking it out under `core.autocrlf`, or by any external
    editor — would otherwise fail the match and render its header as body text.

    The strip loops because that failure used to compound: a CRLF header the
    editor could see was round-tripped back into `write_note`, which did not
    recognize it either and prepended a second one. Peeling every header heals
    a file that already accumulated more than one.
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    while body.startswith(NOTE_HEADER_PREFIX):
        boundary = body.find("\n---\n", 4)
        if boundary < 0:
            # A header with no closing fence is the whole file; it has no body.
            return ""
        body = body[boundary + 5 :]
    return body


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _create_note_file(path: Path, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(header)
    except FileExistsError:
        pass


def _note_title_from_body(body: str, fallback: str) -> str:
    for line in body.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if candidate:
            return normalize_note_title(candidate[:MAX_NOTE_TITLE_CHARS], default=fallback)
    return normalize_note_title(fallback, default="Untitled note")


def _stored_note_title(metadata: dict[str, Any], body: str, fallback: str) -> str:
    title = " ".join(str(metadata.get("title") or "").split())
    return title[:MAX_NOTE_TITLE_CHARS] if title else _note_title_from_body(body, fallback)


def _stored_note_created_at(value: object, fallback: float) -> float:
    candidate: str | float = value if isinstance(value, (str, int, float)) else fallback
    try:
        created_at = float(candidate or fallback)
    except (TypeError, ValueError):
        return fallback
    return created_at if math.isfinite(created_at) else fallback


def _note_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime if path.is_file() else 0
    except OSError:
        return 0


def _move_note_aside(destination: Path, path: Path) -> Path:
    """Move one note file to a recoverable location without overwriting anything there."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination = destination.with_name(
            f"{destination.stem}-{uuid.uuid4().hex[:8]}{destination.suffix}"
        )
    os.replace(path, destination)
    return destination


def _archive_legacy_note(root: Path, path: Path, category: str) -> None:
    _move_note_aside(root / ".swe-mux" / "notes" / "legacy" / category / path.name, path)


def migrate_legacy_notes(
    root: str | Path,
    *,
    legacy_titles: dict[str, str] | None = None,
) -> dict[str, int]:
    """Convert terminal-owned notes into the flat Project note collection.

    Non-empty notes keep their stable IDs and record the former terminal ID only
    as provenance. Empty notes were artifacts of the old one-click terminal chip;
    they are archived without creating empty collection entries. Originals are
    moved into the recoverable ``notes/legacy`` tree after the new file is durable.
    """
    project_root = Path(root).resolve()
    if not project_root.is_dir():
        return {"migrated": 0, "archived_empty": 0}
    ensure_project_notes_ignored(project_root)
    legacy_dir = project_root / ".swe-mux" / "notes" / "sessions"
    titles = legacy_titles or {}
    migrated = 0
    archived_empty = 0
    try:
        paths = list(legacy_dir.glob("*.md"))
    except OSError:
        return {"migrated": 0, "archived_empty": 0}
    for path in paths:
        try:
            stat_result = path.stat()
            data, size = _read_regular_file_bounded(path, MAX_NOTE_FILE_BYTES)
            if data is None:
                log.warning("legacy note exceeds migration limit path=%s bytes=%d", path, size)
                continue
            text = data.decode("utf-8", errors="replace")
            metadata = _note_metadata(text)
            body = _note_body(text)
            legacy_id = str(metadata.get("id") or path.stem)
            if not body.strip():
                _archive_legacy_note(project_root, path, "empty-session-notes")
                archived_empty += 1
                continue
            owner_title = titles.get(legacy_id)
            date = time.strftime("%Y-%m-%d", time.localtime(stat_result.st_mtime))
            title = (
                normalize_note_title(f"{owner_title} - {date}"[:MAX_NOTE_TITLE_CHARS])
                if owner_title
                else _note_title_from_body(body, f"Imported note - {date}")
            )
            destination_id = legacy_id
            destination = note_path(project_root, destination_id)
            already_migrated = False
            if destination.exists():
                existing = destination.read_text(encoding="utf-8", errors="replace")
                existing_metadata = _note_metadata(existing)
                already_migrated = (
                    existing_metadata.get("origin_session_id") == legacy_id
                    and _note_body(existing) == body
                )
                if not already_migrated:
                    destination_id = str(uuid.uuid4())
                    destination = note_path(project_root, destination_id)
            if not already_migrated:
                stored = (
                    note_header(
                        destination_id,
                        title,
                        created_at=stat_result.st_ctime,
                        origin_session_id=legacy_id,
                    )
                    + body
                )
                _atomic_write(destination, stored.encode("utf-8"))
            _archive_legacy_note(project_root, path, "session-notes")
            migrated += 1
        except (OSError, ValueError):
            log.exception("legacy note migration failed path=%s", path)
    if migrated or archived_empty:
        log.info(
            "legacy project notes migrated root=%s migrated=%d archived_empty=%d",
            project_root,
            migrated,
            archived_empty,
        )
    return {"migrated": migrated, "archived_empty": archived_empty}


def project_note_count(root: str | Path) -> int:
    """Count the Project's note files without reading, migrating, or capping them.

    `project_note_summaries` answers the same question, but only by reading and
    bounding every file to build a listing. Deletion needs one number, and it
    needs the untruncated one: the summary cap would make a Project past the cap
    look like it had exactly that many notes.
    """
    notes_root = Path(root).resolve() / ".swe-mux" / "notes"
    total = 1 if (notes_root / "project.md").is_file() else 0
    try:
        total += sum(1 for path in (notes_root / "items").glob("*.md") if path.is_file())
    except OSError:
        pass
    return total


def project_note_summaries(
    root: str | Path,
    *,
    default_note_id: str,
    default_title: str,
    legacy_titles: dict[str, str] | None = None,
    migrate_legacy: bool = True,
) -> list[dict[str, Any]]:
    """List every Project-owned note, newest first, including empty explicit notes."""
    project_root = Path(root).resolve()
    if migrate_legacy:
        migrate_legacy_notes(project_root, legacy_titles=legacy_titles)
    notes_root = project_root / ".swe-mux" / "notes"
    entries: list[tuple[Path, str, str]] = []
    default_path = notes_root / "project.md"
    if default_path.is_file():
        entries.append((default_path, default_note_id, default_title))
    try:
        item_paths = list((notes_root / "items").glob("*.md"))
        item_paths.sort(key=_note_mtime, reverse=True)
        entries.extend(
            (path, path.stem, "Untitled note")
            for path in item_paths[: MAX_NOTE_SUMMARIES - len(entries)]
        )
    except OSError:
        pass
    result: list[dict[str, Any]] = []
    for path, path_id, fallback_title in entries:
        try:
            stat_result = path.stat()
            data, size = _read_regular_file_bounded(path, MAX_NOTE_FILE_BYTES)
            if data is None:
                result.append(
                    {
                        "note_id": default_note_id if path == default_path else path_id,
                        "title": fallback_title,
                        "created_at": stat_result.st_ctime,
                        "updated_at": stat_result.st_mtime,
                        "bytes": size,
                        "revision": _unread_revision(),
                        "excerpt": "",
                        "origin_session_id": None,
                    }
                )
                continue
            text = data.decode("utf-8", errors="replace")
            metadata = _note_metadata(text)
            body = _note_body(text)
        except (OSError, ValueError):
            continue
        identity = str(metadata.get("id") or path_id)
        title = _stored_note_title(metadata, body, fallback_title)
        excerpt = " ".join(body.split())
        result.append(
            {
                "note_id": default_note_id if path == default_path else identity,
                "title": title,
                "created_at": _stored_note_created_at(
                    metadata.get("created_at"), stat_result.st_ctime
                ),
                "updated_at": stat_result.st_mtime,
                "bytes": stat_result.st_size,
                "revision": revision(data),
                "excerpt": excerpt[:NOTE_EXCERPT_CHARS],
                "origin_session_id": metadata.get("origin_session_id"),
            }
        )
    result.sort(key=lambda item: float(item["updated_at"]), reverse=True)
    return result[:MAX_NOTE_SUMMARIES]


def project_path(root: str | Path, relative_path: str = "") -> Path:
    project_root = Path(root).resolve()
    relative = Path(relative_path.replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("project file path must stay inside the project root")
    target = (project_root / relative).resolve()
    if target != project_root and not target.is_relative_to(project_root):
        raise ValueError("project file path resolves outside the project root")
    return target


def _validate_project_resource_name(name: str) -> None:
    """Validate one Windows-safe leaf without normalizing what the user typed."""

    validate_leaf_name(
        name,
        label="project resource name",
        reserved_names=_RESERVED_PROJECT_RESOURCE_NAMES,
    )


def create_project_resource(
    root: str | Path,
    parent: str,
    name: str,
    kind: str,
) -> dict[str, Any]:
    """Create exactly one empty file or directory, exclusively and inside a Project."""

    if kind not in {"file", "directory"}:
        raise ValueError("project resource kind must be file or directory")
    _validate_project_resource_name(name)
    parent_parts = Path(parent.replace("\\", "/")).parts
    if any(part.casefold() in _RESERVED_PROJECT_RESOURCE_NAMES for part in parent_parts):
        raise ValueError("project control directories cannot be modified")

    project_root = Path(root).resolve()
    try:
        parent_path = project_path(project_root, parent)
        parent_is_directory = parent_path.is_dir()
    except (OSError, RuntimeError) as exc:
        raise ValueError("project resource parent is invalid or unavailable") from exc
    if not parent_is_directory:
        raise ValueError("project resource parent must be an existing folder")
    # The parent is already resolved and contained, while the validated leaf cannot carry a
    # separator. Keeping the final component unresolved also makes an existing symlink a plain
    # exclusive-create collision instead of following it outside the Project.
    target = parent_path / name
    try:
        if kind == "file":
            with target.open("x", encoding="utf-8", newline="\n"):
                pass
        else:
            target.mkdir()
    except FileExistsError as exc:
        raise ProjectResourceExists(f"project resource already exists: {name}") from exc
    except FileNotFoundError as exc:
        raise ValueError("project resource parent no longer exists") from exc
    except PermissionError as exc:
        raise ValueError(f"project resource could not be created: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"project resource could not be created: {exc}") from exc

    requested_parent = Path(parent.replace("\\", "/")).as_posix()
    if requested_parent == ".":
        requested_parent = ""
    requested_path = (Path(requested_parent) / name).as_posix()
    return {
        "name": name,
        # Preserve the browser's logical in-Project path when a safe symlink aliases another
        # folder inside the root. The resolved parent above remains the mutation authority.
        "path": requested_path,
        "parent": requested_parent,
        "kind": kind,
        "size": 0 if kind == "file" else None,
    }


def ignored_project_path(relative_path: str | Path, patterns: Sequence[str]) -> bool:
    normalized = Path(str(relative_path).replace("\\", "/")).as_posix().strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().replace("\\", "/").strip("/")
        if not pattern:
            continue
        if "/" in pattern:
            if fnmatch.fnmatchcase(normalized, pattern) or fnmatch.fnmatchcase(
                normalized, f"*/{pattern}"
            ):
                return True
        elif any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
    return False


def project_automations(root: str | Path) -> dict[str, bool]:
    """Read a project's explicit automation opt-ins, or an empty map."""
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            automations = values.get("automations")
            if isinstance(automations, dict):
                return {str(key): bool(value) for key, value in automations.items()}
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        pass
    return {}


def project_session_control_grant(root: str | Path) -> str:
    """The agent-session-control authority level for a Project.

    Returns "draft" or "granted"; "off" is not returned here: whether the
    capability exists at all is the automation opt-in, which the caller checks
    separately. Unset defaults to "granted" (flipped 2026-08-25 - this install
    runs default-enabled, and a Project lowers it by writing "draft"). A
    malformed or *unreadable* config is different from an absent field and
    falls back to the fail-closed "draft": corruption must never widen what an
    explicit "draft" narrowed.
    """
    return _authority_grant(root, "session_control_grant", SESSION_CONTROL_GRANTS, "draft")


def project_spawn_grant(root: str | Path) -> str:
    """The agent-spawn authority level for a Project: "draft" or "granted".

    "granted" (the default when the field is unset, flipped 2026-08-25) lets an
    agent create a session in this Project directly; writing "draft" restores
    the Phase 5 inert-request behaviour. Whether the capability exists at all
    is the `session_control` automation opt-in, checked separately by the
    caller. A malformed or unreadable config falls back to the fail-closed
    "draft" rather than to the default, so corruption cannot widen an explicit
    "draft".
    """
    return _authority_grant(root, "spawn_grant", SESSION_CONTROL_GRANTS, "draft")


def read_project_authority(root: str | Path, field: str) -> tuple[str | None, bool]:
    """One authority field as `(explicit value or None, whether it could be read)`.

    The Project layer alone: it applies no install default and no ceiling, so
    "unset" comes back as None rather than as a guess. That distinction is the
    whole basis of the layering in `agent_authority` - an install default may
    only reach a field nobody decided, so a reader that collapsed unset into a
    default would make every Project look pinned.

    `readable` is False for a config that exists but cannot be parsed, and True
    for one that is absent entirely. Those are genuinely different: a repository
    with no `.swe-mux/config.toml` has not decided anything, while one whose
    file is corrupt has decisions in it that nobody can read, and only the
    second must fail closed.
    """
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if not path.is_file():
            return None, True
        values = parse_project_config(path.read_bytes())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        return None, False
    value = values.get(field)
    return (str(value) if isinstance(value, str) else None), True


def _authority_grant(
    root: str | Path, field: str, allowed: tuple[str, ...], failed: str
) -> str:
    """Read one authority grant with default-granted / fail-closed semantics.

    Three answers, deliberately distinct: an explicit value in a valid config is
    honoured; a valid config (or no config at all) with the field unset means
    the install default, "granted"; a config that cannot be read or parsed
    means `failed` - the narrow answer - because a corrupt file must degrade to
    less authority, never more.

    This is the **Project layer only** and is not what the daemon enforces:
    since 2026-08-29 the services resolve through
    `agent_authority.resolve_authority`, which layers the install default and
    ceiling over this reading. It survives as the answer to "what does this
    repository itself say", which is what the Projects registry displays and
    what the tests below pin.
    """
    explicit, readable = read_project_authority(root, field)
    if not readable:
        return failed
    if explicit in allowed:
        return str(explicit)
    return "granted"


def project_land_grant(root: str | Path) -> str:
    """The agent-initiated-landing authority level for a Project.

    "draft" (the default and the fallback for a malformed config) makes an agent's
    `request_land` write an inert request a human approves; "granted" lets the
    pipeline start on the agent's word. Whether the capability exists at all is the
    `land_queue` automation opt-in, checked separately by the caller.

    The Project layer only; see `_authority_grant` on what the daemon actually
    enforces.
    """
    explicit, _readable = read_project_authority(root, "land_grant")
    if explicit in SESSION_CONTROL_GRANTS:
        return str(explicit)
    return "draft"


def project_interject_grant(root: str | Path) -> str:
    """Whether agents may have messages delivered mid-turn to this Project.

    "granted" (the default when the field is unset, flipped 2026-08-25) or
    "off". A malformed or unreadable config falls back to the fail-closed
    "off" rather than to the default, so corruption cannot widen an explicit
    "off". Read per call rather than cached because the answer is a standing
    permission a person edits by hand, and a stale "granted" is the direction
    that costs something.
    """
    return _authority_grant(root, "interject_grant", INTERJECT_GRANTS, "off")


def project_message_envelope(root: str | Path) -> str:
    """How much metadata a delivered agent message carries into this Project.

    "compact" when unset, "full" when the config cannot be read. The fallback is
    the *most* disclosed level rather than the least, which is the opposite
    direction to the grants above and is the point: the failure this guards is a
    repository whose configuration nobody can parse silently delivering peer
    messages that look exactly like its operator's.

    The Project layer only; see `_authority_grant`.
    """
    explicit, readable = read_project_authority(root, "message_envelope")
    if not readable:
        return ENVELOPE_FULL
    if explicit in MESSAGE_ENVELOPES:
        return str(explicit)
    return ENVELOPE_COMPACT


def project_approval_rules(root: str | Path) -> list[str] | None:
    """This Project's declared auto-approval allowlist.

    ``None`` means the Project declared nothing and the built-in defaults apply;
    ``[]`` means it declared an empty list, which is a real and different answer
    ("approve nothing automatically here"). A malformed config returns ``None``
    rather than a partial list, so a broken file cannot widen the allowlist.
    """
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            rules = values.get("approval_allow")
            if isinstance(rules, list):
                return [str(rule).strip() for rule in rules if str(rule).strip()]
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        pass
    return None


def project_approval_ceiling(root: str | Path) -> str:
    """The strongest approval mode a session in this Project may hold.

    Defaults to ``allow_all`` (no Project-level restriction) so the feature's
    bounds come from the install switch and the per-conversation grant rather
    than from every repository having to opt in. A malformed config falls back
    to the *safe* end instead, because an unreadable ceiling is not evidence of
    permission.
    """
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            ceiling = values.get("approval_ceiling")
            if ceiling in APPROVAL_CEILINGS:
                return str(ceiling)
            return "allow_all"
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        return "wait"
    return "allow_all"


def effective_project_ignores(root: str | Path, global_patterns: list[str]) -> list[str]:
    patterns = list(global_patterns)
    path = Path(root) / ".swe-mux" / "config.toml"
    try:
        if path.is_file():
            values = parse_project_config(path.read_bytes())
            patterns.extend(values.get("ignore_patterns", []))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError):
        pass
    return list(dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip()))


def resolve_pruned_paths(
    project_root: str | Path, pruned_paths: Collection[str] | None
) -> frozenset[str]:
    """Normalize an explicit prune set, or discover this Project's nested worktrees.

    `None` means "ask Git", which is what every production caller wants and is why it is the
    default: a route that has to remember to pass this is a route that will one day forget,
    and the symptom of forgetting is a browser quietly listing a second copy of the whole
    repository. An explicit collection - including an empty one - overrides, so a test can
    pin the answer without a repository.
    """
    if pruned_paths is None:
        return nested_worktree_paths(project_root)
    return frozenset(
        normalized
        for path in pruned_paths
        if (normalized := str(path).replace("\\", "/").strip("/"))
    )


def _within_pruned(relative_path: str, pruned: Collection[str]) -> bool:
    """Whether a Project-relative path *is* a pruned root or lies beneath one."""
    if not pruned:
        return False
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    if normalized in pruned:
        return True
    return any(normalized.startswith(f"{root}/") for root in pruned)


def list_project_directory(
    root: str | Path,
    relative_path: str = "",
    *,
    ignore_patterns: list[str] | None = None,
    pruned_paths: Collection[str] | None = None,
) -> dict[str, Any]:
    project_root = Path(root).resolve()
    pruned = resolve_pruned_paths(project_root, pruned_paths)
    target = project_path(project_root, relative_path)
    if not target.is_dir():
        raise ValueError("project path is not a folder")
    items: list[dict[str, Any]] = []
    try:
        children = sorted(
            target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())
        )
    except OSError as exc:
        raise ValueError(f"unable to browse project folder: {exc}") from exc
    ignore = ignore_patterns or [".git"]
    visible_children = [
        child
        for child in children
        if not ignored_project_path(
            (relative := child.relative_to(project_root).as_posix()), ignore
        )
        and not _within_pruned(relative, pruned)
    ]
    for child in visible_children[:2000]:
        relative = child.relative_to(project_root).as_posix()
        try:
            is_dir = child.is_dir()
            size = None if is_dir else child.stat().st_size
        except OSError:
            continue
        items.append(
            {
                "name": child.name,
                "path": relative,
                "kind": "directory" if is_dir else "file",
                "size": size,
            }
        )
    parent = None if target == project_root else target.parent.relative_to(project_root).as_posix()
    return {
        "path": target.relative_to(project_root).as_posix() if target != project_root else "",
        "parent": parent,
        "items": items,
        "truncated": len(visible_children) > 2000,
    }


def list_project_directories(
    root: str | Path,
    relative_paths: Sequence[str],
    *,
    ignore_patterns: list[str] | None = None,
    pruned_paths: Collection[str] | None = None,
) -> dict[str, Any]:
    """List several folders in one call so a restored tree loads in one round trip.

    A path that no longer resolves to a folder (deleted, renamed, or now a file)
    is silently omitted rather than raising: the browser treats a missing entry
    as "this folder is gone" and prunes it from the persisted expand state, which
    is exactly the self-healing behaviour we want for a stale saved tree.
    """

    project_root = Path(root).resolve()
    # Resolved once for the whole batch rather than per folder: this fans out to a
    # thousand directories, and the nested-worktree lookup is a Git subprocess behind a
    # short-lived cache. One call per batch is the difference between a cache that
    # amortizes and a cache that is merely not wrong.
    pruned = resolve_pruned_paths(project_root, pruned_paths)
    directories: dict[str, Any] = {}
    for relative in dict.fromkeys(relative_paths):
        try:
            directories[relative] = list_project_directory(
                project_root, relative, ignore_patterns=ignore_patterns, pruned_paths=pruned
            )
        except ValueError:
            continue
    return {"directories": directories}


#: How many files one search may look at before it gives up. The bound exists so a huge
#: tree cannot stall the daemon; which files it spends itself on is decided by the
#: breadth-first order below, not by this number.
SEARCH_MAX_FILES = 20000
#: Total bytes one content search may read, and the largest single file it will open.
SEARCH_MAX_BYTES = 64 * 1024 * 1024
SEARCH_PER_FILE_BYTES = 1024 * 1024


def _search_one_file(
    project_root: Path,
    relative: str,
    name: str,
    needle: str,
    *,
    want_names: bool,
    want_contents: bool,
) -> tuple[dict[str, Any] | None, int]:
    """Test one file, returning its result row (or `None`) and the bytes it cost to read."""
    if want_names and needle in name.casefold():
        return {"name": name, "path": relative, "match": "name", "line": None, "snippet": None}, 0
    if not want_contents:
        return None, 0
    target = project_root / relative
    try:
        if target.stat().st_size > SEARCH_PER_FILE_BYTES:
            return None, 0
        data = target.read_bytes()
    except OSError:
        return None, 0
    if b"\x00" in data:
        return None, len(data)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None, len(data)
    index = text.casefold().find(needle)
    if index == -1:
        return None, len(data)
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return (
        {
            "name": name,
            "path": relative,
            "match": "content",
            "line": text.count("\n", 0, index) + 1,
            "snippet": text[start : end if end != -1 else len(text)].strip()[:200],
        },
        len(data),
    )


def search_project_files(
    root: str | Path,
    query: str,
    *,
    mode: str = "names",
    ignore_patterns: list[str] | None = None,
    pruned_paths: Collection[str] | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    """Recursively find files by name and/or UTF-8 content beneath the project root.

    The walk reuses the same ignore rules as the browser, prunes ignored directories and
    nested worktrees, and is bounded on every axis (files visited, bytes read, per-file size,
    and result count) so a huge tree cannot stall the daemon. Content matching skips binary
    and oversized files and reports the first matching line as a trimmed snippet. Name
    matches sort before content matches; within each group results are path-ordered.

    **The walk is breadth-first, and that is load-bearing rather than a style choice.**
    `os.walk` is depth-first, so it spends the whole file budget on whichever subtree sorts
    first - a dot-directory, always - and can exhaust it without ever reaching `src/`. That
    is not a degraded search but a silent wrong answer: the results looked like a complete
    list of matches and the real ones had never been visited. Breadth-first spends the budget
    on the shallowest files first, which is both the order a person expects and the order in
    which a Project's own code beats its vendored and generated trees.

    A truncated result says *which* bound bit (`truncated_reason`) and, when it was the file
    budget, where the walk stopped (`stopped_at`). "Refine your query" is useless advice
    against a budget that a narrower query would exhaust in exactly the same place, and a
    reader who is told that will retype the query instead of fixing the ignore list.
    """
    project_root = Path(root).resolve()
    needle = query.strip().casefold()
    if not needle:
        return {
            "items": [],
            "truncated": False,
            "truncated_reason": None,
            "stopped_at": None,
            "scanned_files": 0,
            "scanned_bytes": 0,
        }
    ignore = ignore_patterns or [".git"]
    pruned = resolve_pruned_paths(project_root, pruned_paths)
    want_names = mode in ("names", "both")
    want_contents = mode in ("contents", "both")
    items: list[dict[str, Any]] = []
    truncated_reason: str | None = None
    stopped_at: str | None = None
    scanned_files = 0
    scanned_bytes = 0
    frontier: deque[str] = deque([""])
    while frontier and truncated_reason is None:
        prefix = frontier.popleft()
        directory = project_root / prefix if prefix else project_root
        subdirectories: list[str] = []
        files: list[tuple[str, str]] = []
        try:
            with os.scandir(directory) as entries:
                listing = sorted(entries, key=lambda entry: entry.name.casefold())
        except OSError:
            # A folder that vanished or refuses to be read is skipped, not fatal: a search
            # over a live tree races every agent editing it.
            continue
        for entry in listing:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            if ignored_project_path(relative, ignore) or _within_pruned(relative, pruned):
                continue
            try:
                is_directory = entry.is_dir()
                is_link = entry.is_symlink()
            except OSError:
                continue
            if is_directory:
                # A symlinked directory is listed but never descended into, matching
                # `os.walk(followlinks=False)`: following one can loop forever, and a link
                # out of the Project is a path this browser has no authority over.
                if not is_link:
                    subdirectories.append(relative)
            else:
                files.append((entry.name, relative))
        for name, relative in files:
            scanned_files += 1
            if scanned_files > SEARCH_MAX_FILES:
                truncated_reason = "files"
                stopped_at = prefix
                break
            row, cost = _search_one_file(
                project_root,
                relative,
                name,
                needle,
                want_names=want_names,
                want_contents=want_contents and scanned_bytes < SEARCH_MAX_BYTES,
            )
            scanned_bytes += cost
            if row is not None:
                items.append(row)
                if len(items) >= limit:
                    truncated_reason = "results"
                    break
        frontier.extend(subdirectories)
    items.sort(key=lambda item: (item["match"] != "name", item["path"].casefold()))
    return {
        "items": items,
        "truncated": truncated_reason is not None,
        "truncated_reason": truncated_reason,
        "stopped_at": stopped_at,
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
    }


def read_project_file(root: str | Path, relative_path: str) -> dict[str, Any]:
    target = project_path(root, relative_path)
    suffix = Path(relative_path.replace("\\", "/")).suffix.casefold()
    if suffix in _IMAGE_EXTENSIONS:
        payload, _data = _read_project_image(root, relative_path)
        return payload
    data, size = _read_regular_file_bounded(target, PROJECT_TEXT_FILE_MAX_BYTES)
    presentation: dict[str, Any]
    delimiter = _DELIMITED_EXTENSIONS.get(suffix)
    if delimiter is not None:
        presentation = {"kind": "delimited", "delimiter": delimiter}
    else:
        presentation = {"kind": "text"}
    if data is None:
        return {
            "path": relative_path,
            "revision": _unread_revision(),
            "status": "too-large",
            "size": size,
            "presentation": presentation,
        }
    if b"\x00" in data:
        return {
            "path": relative_path,
            "revision": revision(data),
            "status": "binary",
            "size": size,
            "presentation": _unsupported_presentation("binary-content"),
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": relative_path,
            "revision": revision(data),
            "status": "binary",
            "size": size,
            "presentation": _unsupported_presentation("not-utf-8"),
        }
    return {
        "path": target.relative_to(Path(root).resolve()).as_posix(),
        "revision": revision(data),
        "status": "ready" if os.access(target, os.W_OK) else "read-only",
        "size": size,
        "text": text,
        "presentation": presentation,
    }


#: Suffixes a static preview will accept as its *entry* document. Deliberately not
#: every renderable type: a preview is a page, and offering it on a stylesheet or a
#: lone image would produce a viewport showing something the file tab already shows
#: better. Anything at all may be fetched as a *subresource* of that page.
STATIC_PREVIEW_ENTRY_SUFFIXES = frozenset({".html", ".htm", ".xhtml"})


def is_static_preview_entry(relative_path: str) -> bool:
    """Whether this path may be opened as a static preview's entry document."""
    return Path(relative_path.replace("\\", "/")).suffix.casefold() in STATIC_PREVIEW_ENTRY_SUFFIXES


def read_static_preview_file(
    doc_root: str | Path, relative_path: str, entry: str, limit: int
) -> tuple[bytes | None, str, int]:
    """Resolve one path under a static preview's served directory and read it.

    ``relative_path`` is the proxy tail. An empty tail is the preview's own entry
    document, and a directory resolves to its ``index.html`` - the two
    conventions that let a page's ``href="subdir/"`` work the way it does on any
    other static server.

    Containment is enforced by ``project_path``, which rejects absolute paths and
    ``..`` segments before resolving and then re-checks the resolved target
    against the root, so neither a crafted tail nor a symlink inside the
    directory can reach outside it.

    The ordering of those two is worth stating, because it differs by host. The
    route-separator strip runs *first*, so on POSIX a tail that looks like an
    absolute path is already root-relative by the time the absolute check sees
    it: it is re-rooted under the served directory and hits or misses there,
    never followed. Only a Windows drive-lettered tail survives the strip and is
    refused outright. Both are refused; they raise different exceptions, so the
    guarantee to assert against is that the named file never comes back, not
    which exception said so.

    Returns ``(data, path relative to doc_root, size)``. ``data`` is ``None``
    when the file is larger than ``limit``, mirroring
    ``_read_regular_file_bounded``: the caller answers 413 rather than the daemon
    materialising an arbitrarily large file to discover it should not have.

    Raises ``ValueError`` for a path outside the served directory and
    ``FileNotFoundError`` when nothing regular is there.
    """

    root = Path(doc_root).resolve()
    requested = relative_path.strip("/") or entry
    target = project_path(root, requested)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise FileNotFoundError(requested)
    data, size = _read_regular_file_bounded(target, limit)
    return data, target.relative_to(root).as_posix(), size


def read_project_image_content(
    root: str | Path, relative_path: str, expected_revision: str
) -> tuple[bytes, dict[str, Any]]:
    if not expected_revision:
        raise ValueError("image content requires a file revision")
    suffix = Path(relative_path.replace("\\", "/")).suffix.casefold()
    if suffix not in _IMAGE_EXTENSIONS:
        raise ProjectImageUnavailable("project file is not an allowlisted image type")
    payload, data = _read_project_image(root, relative_path)
    if payload["revision"] != expected_revision:
        raise ProjectFileRevisionConflict("project file changed since it was inspected")
    if payload["status"] != "viewable" or data is None:
        reason = str(payload.get("presentation", {}).get("reason") or payload["status"])
        raise ProjectImageUnavailable(f"project image is unavailable: {reason}")
    return data, payload


def write_project_file(
    root: str | Path, relative_path: str, text: str, expected_revision: str
) -> dict[str, Any]:
    suffix = Path(relative_path.replace("\\", "/")).suffix.casefold()
    if suffix in _IMAGE_EXTENSIONS:
        raise ValueError("image files are read-only")
    data = text.encode("utf-8")
    if len(data) > PROJECT_TEXT_FILE_MAX_BYTES:
        raise ValueError("project file exceeds the 2 MiB editor limit")
    target = project_path(root, relative_path)
    current, _size = _read_regular_file_bounded(target, PROJECT_TEXT_FILE_MAX_BYTES)
    if current is None:
        raise ValueError("existing project file exceeds the 2 MiB editor limit")
    if revision(current) != expected_revision:
        raise ValueError("project file changed externally; reload before saving")
    _atomic_write(target, data)
    return read_project_file(root, relative_path)


async def project_status(
    cwd: str | Path, *, canonical_root: str | Path | None = None
) -> tuple[ProjectIdentity, Path]:
    """Resolve the identity to report and the ``.swe-mux`` directory to use.

    ``canonical_root`` is the explicit Project root when the caller already knows
    which Project owns the request. It is authoritative for paths: without it, a
    Project registered inside a larger worktree re-resolves to the enclosing
    toplevel and reads/writes that Project's notes, config, and observations.
    """
    project = await resolve_project(cwd)
    if canonical_root is not None:
        project = rebase_identity(project, canonical_root)
    return project, Path(project.root) / ".swe-mux"


def parse_project_config(data: bytes) -> dict[str, Any]:
    parsed = tomllib.loads(data.decode("utf-8"))
    version = parsed.pop("version", None)
    if version != PROJECT_CONFIG_VERSION:
        raise ValueError(f"project config version must be {PROJECT_CONFIG_VERSION}")
    for legacy in LEGACY_PROJECT_FIELDS:
        parsed.pop(legacy, None)
    forbidden = sorted(set(parsed) & FORBIDDEN_PROJECT_FIELDS)
    unknown = sorted(set(parsed) - PROJECT_CONFIG_FIELDS)
    if forbidden:
        raise ValueError(f"forbidden project fields: {', '.join(forbidden)}")
    if unknown:
        raise ValueError(f"unknown project fields: {', '.join(unknown)}")
    if "default_shell_profile" in parsed and not isinstance(parsed["default_shell_profile"], str):
        raise ValueError("default_shell_profile must be a string")
    if "default_agent_profiles" in parsed:
        # A *selection*, never a definition. The committed file may name a launch
        # profile the user defined locally; it may not carry argv of its own. Argv
        # for an agent CLI is an authority field (`--dangerously-skip-permissions`
        # lives there), so repository-supplied argv would be a real escalation while
        # naming a locally-authored profile is the same kind of statement
        # `preferred_backend` already makes.
        agent_profiles = parsed["default_agent_profiles"]
        if not isinstance(agent_profiles, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in agent_profiles.items()
        ):
            raise ValueError("default_agent_profiles must be a table of backend to profile id")
        unknown_backends = sorted(key for key in agent_profiles if not is_agent_harness(key))
        if unknown_backends:
            raise ValueError(
                f"default_agent_profiles names unregistered harnesses: "
                f"{', '.join(unknown_backends)}"
            )
    preferred_backend = parsed.get("preferred_backend")
    if (
        preferred_backend is not None
        and preferred_backend != "shell"
        and not is_agent_harness(preferred_backend)
    ):
        raise ValueError("preferred_backend must be shell or a registered agent")
    if parsed.get("resource_open_mode") not in {None, "dock", "popout"}:
        raise ValueError("resource_open_mode must be dock or popout")
    if parsed.get("prompt_library_scope") not in {None, "off", "global", "project", "both"}:
        raise ValueError("prompt_library_scope must be off, global, project, or both")
    if parsed.get("session_control_grant") not in {None, *SESSION_CONTROL_GRANTS}:
        raise ValueError("session_control_grant must be draft or granted")
    if parsed.get("spawn_grant") not in {None, *SESSION_CONTROL_GRANTS}:
        raise ValueError("spawn_grant must be draft or granted")
    if parsed.get("land_grant") not in {None, *SESSION_CONTROL_GRANTS}:
        raise ValueError("land_grant must be draft or granted")
    if parsed.get("interject_grant") not in {None, *INTERJECT_GRANTS}:
        raise ValueError("interject_grant must be off or granted")
    if parsed.get("message_envelope") not in {None, *MESSAGE_ENVELOPES}:
        raise ValueError("message_envelope must be full, compact, or bare")
    if parsed.get("approval_ceiling") not in {None, *APPROVAL_CEILINGS}:
        raise ValueError("approval_ceiling must be wait, allowlisted, or allow_all")
    if "approval_allow" in parsed and (
        not isinstance(parsed["approval_allow"], list)
        or not all(isinstance(item, str) for item in parsed["approval_allow"])
        or len(parsed["approval_allow"]) > MAX_PROJECT_APPROVAL_RULES
        or any(not item.strip() or len(item) > 200 for item in parsed["approval_allow"])
    ):
        raise ValueError(
            "approval_allow must be an array of at most "
            f"{MAX_PROJECT_APPROVAL_RULES} non-empty rule strings"
        )
    if "notification_sounds_enabled" in parsed and not isinstance(
        parsed["notification_sounds_enabled"], bool
    ):
        raise ValueError("notification_sounds_enabled must be a boolean")
    if "scan_timeline_auto_enable" in parsed and not isinstance(
        parsed["scan_timeline_auto_enable"], bool
    ):
        raise ValueError("scan_timeline_auto_enable must be a boolean")
    if "ignore_patterns" in parsed and (
        not isinstance(parsed["ignore_patterns"], list)
        or not all(isinstance(item, str) for item in parsed["ignore_patterns"])
        or len(parsed["ignore_patterns"]) > 256
        or any(not item.strip() or len(item) > 200 for item in parsed["ignore_patterns"])
    ):
        raise ValueError("ignore_patterns must be an array of at most 256 non-empty strings")
    if "automations" in parsed:
        automations = parsed["automations"]
        if not isinstance(automations, dict) or not all(
            isinstance(value, bool) for value in automations.values()
        ):
            raise ValueError("automations must be a table of boolean opt-ins")
        unknown_automations = sorted(
            set(automations) - set(AUTOMATION_REGISTRY) - LEGACY_AUTOMATION_IDS
        )
        if unknown_automations:
            raise ValueError(f"unknown automations: {', '.join(unknown_automations)}")
        parsed["automations"] = {
            key: value for key, value in automations.items() if key in AUTOMATION_REGISTRY
        }
    if "worktree" in parsed:
        worktree = parsed["worktree"]
        if not isinstance(worktree, dict):
            raise ValueError("worktree must be a table")
        unknown_worktree = sorted(set(worktree) - {"setup_command", "verify_command"})
        if unknown_worktree:
            raise ValueError(f"unknown worktree fields: {', '.join(unknown_worktree)}")
        for field in ("setup_command", "verify_command"):
            command = worktree.get(field)
            if command is not None and (
                not isinstance(command, str) or not command.strip() or len(command) > 4096
            ):
                raise ValueError(f"worktree.{field} must be a non-empty string")
    return parsed


def serialize_project_config(values: dict[str, Any]) -> bytes:
    # Retired fields are dropped rather than rejected, so a caller that read a
    # config, changed one thing, and wrote it back does not have to know which
    # keys have since moved to the global settings.
    values = {key: value for key, value in values.items() if key not in LEGACY_PROJECT_FIELDS}
    invalid = sorted(set(values) - PROJECT_CONFIG_FIELDS)
    if invalid:
        raise ValueError(f"unknown project fields: {', '.join(invalid)}")
    lines = [f"version = {PROJECT_CONFIG_VERSION}"]
    for key in (
        "default_shell_profile",
        "preferred_backend",
        "resource_open_mode",
        "prompt_library_scope",
        "session_control_grant",
        "spawn_grant",
        "land_grant",
        "interject_grant",
        "message_envelope",
        "approval_ceiling",
    ):
        if value := values.get(key):
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key} = "{escaped}"')
    if "notification_sounds_enabled" in values:
        lines.append(
            "notification_sounds_enabled = "
            + ("true" if values["notification_sounds_enabled"] else "false")
        )
    if "scan_timeline_auto_enable" in values:
        lines.append(
            "scan_timeline_auto_enable = "
            + ("true" if values["scan_timeline_auto_enable"] else "false")
        )
    if agent_profiles := values.get("default_agent_profiles"):
        pairs = ", ".join(
            f"{json.dumps(str(key))} = {json.dumps(str(value))}"
            for key, value in sorted(agent_profiles.items())
        )
        lines.append(f"default_agent_profiles = {{ {pairs} }}")
    if patterns := values.get("ignore_patterns"):
        encoded = ", ".join(json.dumps(str(pattern)) for pattern in patterns)
        lines.append(f"ignore_patterns = [{encoded}]")
    if "approval_allow" in values and isinstance(values["approval_allow"], list):
        # Written even when empty, because an empty allowlist is a *decision*
        # ("this Project approves nothing automatically") and dropping it would
        # silently restore the built-in defaults on the next read.
        encoded = ", ".join(json.dumps(str(rule)) for rule in values["approval_allow"])
        lines.append(f"approval_allow = [{encoded}]")
    if automations := values.get("automations"):
        pairs = ", ".join(
            f"{key} = {'true' if bool(value) else 'false'}"
            for key, value in sorted(automations.items())
        )
        lines.append(f"automations = {{ {pairs} }}")
    worktree = values.get("worktree")
    if isinstance(worktree, dict):
        declared = [
            (field, str(worktree[field]))
            for field in ("setup_command", "verify_command")
            if worktree.get(field)
        ]
        if declared:
            lines.extend(["", "[worktree]"])
            lines.extend(f"{field} = {json.dumps(value)}" for field, value in declared)
    data = ("\n".join(lines) + "\n").encode("utf-8")
    parse_project_config(data)
    return data


async def read_project_config(
    cwd: str | Path, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "config.toml"
    if not path.exists():
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": False,
            "revision": "missing",
            "values": {},
            "status": "missing",
        }
    try:
        data = path.read_bytes()
        values = parse_project_config(data)
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "values": values,
            "status": status,
        }
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError) as exc:
        try:
            current_data = path.read_bytes() if path.exists() else None
        except OSError:
            current_data = None
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(current_data),
            "values": {},
            "status": "malformed",
            "error": str(exc),
        }


async def read_project_config_values(
    cwd: str | Path, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    """Just the configured values, or ``{}`` when the file cannot be trusted.

    A named accessor because the envelope `read_project_config` returns is easy to hand
    on whole by mistake, and the mistake is silent: every consumer reads its keys with
    `.get`, finds nothing at the top level, and behaves exactly as it would for a Project
    that configured nothing. That is precisely how `[worktree] verify_command` came to be
    declared, documented, and inert for the land queue's gate.

    A malformed or missing config resolves to no values rather than to a partial read,
    which is the same rule worktree bootstrap already applies.
    """
    config = await read_project_config(cwd, project=project)
    if config.get("status") not in {"ready", "read-only"}:
        return {}
    values = config.get("values")
    return dict(values) if isinstance(values, dict) else {}


async def write_project_config(
    cwd: str | Path,
    values: dict[str, Any],
    expected_revision: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Replace the whole file, guarded by the revision the caller read.

    For a caller that read the file and is writing it back in the same breath -
    every daemon-side writer does exactly this. A caller holding a *cached* read
    across a user's editing session wants `merge_project_config` instead: a
    whole-file guard reports a change to any key as a conflict with every key,
    and this file has six independent writers.
    """
    current = await read_project_config(cwd, project=project)
    if current["revision"] != expected_revision:
        raise ValueError("project config changed externally; reload before saving")
    data = serialize_project_config(values)
    _atomic_write(Path(current["path"]), data)
    return await read_project_config(cwd, project=project)


def normalized_project_values(values: dict[str, Any]) -> dict[str, Any]:
    """`values` as this file would store them and hand them back.

    Round-tripped through the real writer and reader rather than compared raw,
    because the two are not the same map: retired keys are dropped, a falsy string
    or an empty `ignore_patterns` list is not written at all, and `automations`
    loses ids the registry no longer knows. Comparing raw maps would call an unset
    key that arrived as `""` a change from an unset key that arrived as absent, and
    the false conflict that produces is the whole reason this exists. Deriving it
    from the pair that owns the format means it cannot drift from them.
    """
    return parse_project_config(serialize_project_config(values))


async def merge_project_config(
    cwd: str | Path,
    changes: dict[str, Any],
    base: dict[str, Any],
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Write only the named fields, leaving every other field on disk untouched.

    `changes` names the fields to set, with `None` meaning remove. `base` is what
    the caller believed those same fields held when it composed the edit; a field
    whose stored value has moved away from `base` since then is a real collision
    and is refused by name, while a field nobody else touched is applied whatever
    else changed in the file. This is the same rule the configurator's settings
    write already follows (`.docs/design/features/configurator.md`): take
    operations, never a document, so everything the caller did not name survives by
    construction rather than by the caller having read it recently enough.

    A malformed file is refused rather than merged into: `read_project_config`
    reports no values for one, so merging would write the caller's fields over a
    file whose remaining contents were never read - silently discarding whatever
    the operator was in the middle of fixing by hand.
    """
    current = await read_project_config(cwd, project=project)
    if current["status"] == "malformed":
        raise ValueError(
            "this Project's .swe-mux/config.toml cannot be parsed; fix it before saving"
        )
    if current["status"] == "read-only":
        raise ValueError("this Project's .swe-mux/config.toml is read-only")
    # Retired keys are dropped rather than refused, for the same reason
    # `serialize_project_config` drops them: a caller that read a config and changed
    # one thing does not have to know which keys have since moved to global settings.
    requested = {key: value for key, value in changes.items() if key not in LEGACY_PROJECT_FIELDS}
    invalid = sorted(set(requested) - PROJECT_CONFIG_FIELDS)
    if invalid:
        raise ValueError(f"unknown project fields: {', '.join(invalid)}")
    stored = normalized_project_values(dict(current["values"]))
    expected = normalized_project_values(
        {key: value for key, value in base.items() if value is not None}
    )
    conflicts = sorted(key for key in requested if stored.get(key) != expected.get(key))
    if conflicts:
        raise ProjectConfigConflict(conflicts, current)
    values = dict(current["values"])
    for key, value in requested.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
    data = serialize_project_config(values)
    _atomic_write(Path(current["path"]), data)
    return await read_project_config(cwd, project=project)


OBSERVATIONS_VERSION = 1
MAX_OBSERVATIONS = 500
MAX_OBSERVATION_CHARS = 2000
# Phase 5: an inbox item may carry a typed, inert *request* alongside its human
# summary line — today only `mux.requestSpawn` (`ROADMAP.md` Phase 5,
# `CONTROL_PLANE_ROADMAP.md` §7.2/§16). The item is text until a human approves
# it; nothing here starts anything.
OBSERVATION_KINDS = ("note", "spawn_request", "control_request", "land_request")
MAX_SPAWN_REQUEST_PROMPT = 8000
_REQUEST_STRING_FIELDS = (
    "prompt",
    "backend",
    # The model the requester asked its new session's CLI to run. On the allowlist
    # rather than merely written by the requester: this is a closed filter, so a
    # field missing from it survives being written and then vanishes on the way
    # back out - the card would show no model and the approval would spawn an
    # ordinary session, with nothing anywhere saying the request had been dropped.
    "model",
    "name",
    "reason",
    "cwd",
    "from_session",
    "from_name",
    "from_run_id",
    "project_id",
    "status",
    "session_id",
    "decided_by",
    # Phase 7.6 control_request fields, preserved through the same allowlist so a
    # drafted interrupt/end reads back with the target and action a human needs to
    # approve it. Unknown keys are still dropped.
    "action",
    "target_session_id",
    "target_name",
    "outcome",
    # Phase 14 land_request fields. A drafted land is reviewed by *checkout*, not by
    # session, so the human needs the branch and the two roots to judge it - the same
    # allowlist discipline, one more request shape.
    "worktree_root",
    "project_root",
    "branch",
    "request_id",
)


class ObservationsUnreadableError(ValueError):
    """The inbox file exists but cannot be parsed.

    Distinct from "missing" on purpose: an unparseable file (hand edit, merge
    conflict markers) read as an empty list means the next captured note rewrites
    it with one item and silently discards every prior observation.
    """


def _load_observations(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ObservationsUnreadableError(f"observations.json is unreadable: {exc}") from exc
    items = parsed.get("observations") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ObservationsUnreadableError("observations.json has no observations list")
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        entry: dict[str, Any] = {
            "id": item["id"],
            "body": str(item.get("body") or "")[:MAX_OBSERVATION_CHARS],
            "done": bool(item.get("done")),
            "created_at": float(item.get("created_at") or 0.0),
        }
        kind = str(item.get("kind") or "note")
        if kind in OBSERVATION_KINDS and kind != "note":
            entry["kind"] = kind
            entry["request"] = _clean_request(item.get("request"))
        result.append(entry)
    return result


def _clean_request(request: Any) -> dict[str, Any]:
    """Normalize a typed request payload; unknown keys are dropped, not trusted."""
    source = request if isinstance(request, dict) else {}
    cleaned: dict[str, Any] = {}
    for key in _REQUEST_STRING_FIELDS:
        value = source.get(key)
        if isinstance(value, str) and value:
            cleaned[key] = value[:MAX_SPAWN_REQUEST_PROMPT]
    created = source.get("created_at")
    if isinstance(created, int | float):
        cleaned["created_at"] = float(created)
    cleaned.setdefault("status", "pending")
    return cleaned


def _validate_observations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list) or len(items) > MAX_OBSERVATIONS:
        raise ValueError(f"observations must be a list of at most {MAX_OBSERVATIONS} items")
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each observation must be an object")
        identity = item.get("id")
        body = item.get("body")
        if not isinstance(identity, str) or not _SAFE_NOTE_ID.fullmatch(identity):
            raise ValueError("observation id must be a short safe token")
        if identity in seen:
            raise ValueError("observation ids must be unique")
        seen.add(identity)
        if not isinstance(body, str) or not body.strip() or len(body) > MAX_OBSERVATION_CHARS:
            raise ValueError(f"observation body must be 1–{MAX_OBSERVATION_CHARS} characters")
        entry: dict[str, Any] = {
            "id": identity,
            "body": body,
            "done": bool(item.get("done")),
            "created_at": float(item.get("created_at") or 0.0),
        }
        kind = str(item.get("kind") or "note")
        if kind not in OBSERVATION_KINDS:
            raise ValueError(f"observation kind must be one of {', '.join(OBSERVATION_KINDS)}")
        if kind != "note":
            entry["kind"] = kind
            entry["request"] = _clean_request(item.get("request"))
        cleaned.append(entry)
    return cleaned


def _serialize_observations(items: list[dict[str, Any]]) -> bytes:
    return (
        json.dumps({"version": OBSERVATIONS_VERSION, "observations": items}, indent=2) + "\n"
    ).encode("utf-8")


async def read_observations(
    cwd: str | Path, *, project: ProjectIdentity | None = None
) -> dict[str, Any]:
    """Read a Project's capture inbox — lightweight notes-to-self dropped while testing."""
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        data = None
    try:
        observations = _load_observations(path)
    except ObservationsUnreadableError as exc:
        return {
            "project": asdict(project),
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "observations": [],
            "status": "malformed",
            "error": str(exc),
        }
    return {
        "project": asdict(project),
        "path": str(path),
        "exists": path.is_file(),
        "revision": revision(data),
        "observations": observations,
        "status": "ready",
    }


async def append_observation(
    cwd: str | Path,
    body: str,
    *,
    project: ProjectIdentity | None = None,
    kind: str = "note",
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one observation. Append-only capture is conflict-free, so no revision check.

    ``kind``/``request`` carry a typed, inert draft (Phase 5
    ``mux.requestSpawn``). The item is still just a row in the user's own inbox
    until a human approves it.
    """
    text = body.strip()
    if not text:
        raise ValueError("observation body must not be empty")
    if len(text) > MAX_OBSERVATION_CHARS:
        raise ValueError(f"observation body must be {MAX_OBSERVATION_CHARS} characters or fewer")
    if kind not in OBSERVATION_KINDS:
        raise ValueError(f"observation kind must be one of {', '.join(OBSERVATION_KINDS)}")
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    current = _load_observations(path)
    if len(current) >= MAX_OBSERVATIONS:
        raise ValueError(f"observation inbox is full ({MAX_OBSERVATIONS} items); clear some first")
    identity = uuid.uuid4().hex[:16]
    entry: dict[str, Any] = {
        "id": identity,
        "body": text,
        "done": False,
        "created_at": time.time(),
    }
    if kind != "note":
        entry["kind"] = kind
        entry["request"] = _clean_request({**(request or {}), "created_at": time.time()})
    current.append(entry)
    _atomic_write(path, _serialize_observations(_validate_observations(current)))
    result = await read_observations(cwd, project=project)
    result["appended_id"] = identity
    return result


async def update_observation_request(
    cwd: str | Path,
    observation_id: str,
    patch: dict[str, Any],
    *,
    done: bool | None = None,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Record the outcome of a typed request (approved/dismissed) in place.

    Deliberately not revision-checked: this is the daemon writing the result of
    an act it just performed, and losing that record to a concurrent edit of an
    unrelated note would leave an approved request looking pending.
    """
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = mux_dir / "observations.json"
    current = _load_observations(path)
    found = False
    for item in current:
        if item.get("id") != observation_id:
            continue
        found = True
        item["request"] = _clean_request({**(item.get("request") or {}), **patch})
        if done is not None:
            item["done"] = done
    if not found:
        raise ValueError("no such observation")
    _atomic_write(path, _serialize_observations(_validate_observations(current)))
    return await read_observations(cwd, project=project)


async def write_observations(
    cwd: str | Path,
    observations: list[dict[str, Any]],
    expected_revision: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Replace the whole inbox (toggle done, delete, reorder) with a revision check."""
    current = await read_observations(cwd, project=project)
    if current.get("status") == "malformed":
        raise ObservationsUnreadableError(str(current.get("error") or "observations.json"))
    if current["revision"] != expected_revision:
        raise ValueError("observations changed externally; reload before saving")
    cleaned = _validate_observations(observations)
    _atomic_write(Path(current["path"]), _serialize_observations(cleaned))
    return await read_observations(cwd, project=project)


def _read_note_file(
    path: Path,
    identity: str,
    *,
    default_title: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        **base,
        "id": identity,
        "title": normalize_note_title(default_title, default="Untitled note"),
        "created_at": None,
        "origin_session_id": None,
        "path": str(path),
    }
    if not path.exists():
        return {
            **payload,
            "exists": False,
            "bytes": 0,
            "revision": "missing",
            "markdown": "",
            "status": "missing",
        }
    try:
        data, size = _read_regular_file_bounded(path, MAX_NOTE_FILE_BYTES)
        if data is None:
            return {
                **payload,
                "exists": True,
                "bytes": size,
                "revision": _unread_revision(),
                "markdown": "",
                "status": "too-large",
                "error": "note exceeds the 1 MiB editor limit",
            }
        text = data.decode("utf-8")
        metadata = _note_metadata(text)
        markdown = _note_body(text)
        stat_result = path.stat()
        status = "ready" if os.access(path, os.W_OK) else "read-only"
        return {
            **payload,
            "title": _stored_note_title(metadata, markdown, default_title),
            "created_at": _stored_note_created_at(metadata.get("created_at"), stat_result.st_ctime),
            "origin_session_id": metadata.get("origin_session_id"),
            "exists": True,
            "bytes": len(data),
            "revision": revision(data),
            "markdown": markdown,
            "status": status,
        }
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        return {
            **payload,
            "exists": True,
            "bytes": size,
            "revision": "unreadable",
            "markdown": "",
            "status": "malformed",
            "error": str(exc),
        }


async def read_note(
    cwd: str | Path,
    identity: str,
    *,
    default_title: str = "Untitled note",
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    if project is None:
        project, mux_dir = await project_status(cwd)
    else:
        mux_dir = Path(project.root) / ".swe-mux"
    path = (
        mux_dir / "notes" / "project.md"
        if identity == DEFAULT_NOTE_STORAGE_ID
        else mux_dir / "notes" / "items" / f"{safe_note_filename(identity)}.md"
    )
    return _read_note_file(
        path,
        identity,
        default_title=default_title,
        base={"project": asdict(project), "kind": "note"},
    )


def global_note_path(data_dir: str | Path, identity: str) -> Path:
    """Return one global-note path inside the swe-mux data directory."""
    return Path(data_dir).resolve() / "notes" / "items" / f"{safe_note_filename(identity)}.md"


async def read_global_note(
    data_dir: str | Path,
    identity: str,
    *,
    default_title: str = "Untitled note",
) -> dict[str, Any]:
    note = _read_note_file(
        global_note_path(data_dir, identity),
        identity,
        default_title=default_title,
        base={"scope": "global", "kind": "global-note"},
    )
    note["title"] = normalize_note_title(default_title, default="Scratchpad")
    return note


async def write_global_note(
    data_dir: str | Path,
    identity: str,
    markdown: str,
    expected_revision: str,
    *,
    title: str,
) -> dict[str, Any]:
    if len(markdown.encode("utf-8")) > MAX_NOTE_MARKDOWN_BYTES:
        raise ValueError("note exceeds the 1 MiB limit")
    current = await read_global_note(data_dir, identity, default_title=title)
    if current["revision"] != expected_revision:
        raise ValueError("note changed externally; reload before saving")
    body = note_header(
        identity,
        title,
        kind="global-notes",
        created_at=float(current.get("created_at") or time.time()),
    ) + _note_body(markdown)
    _atomic_write(Path(current["path"]), body.encode("utf-8"))
    return await read_global_note(data_dir, identity, default_title=title)


async def write_note(
    cwd: str | Path,
    identity: str,
    markdown: str,
    expected_revision: str,
    *,
    title: str | None = None,
    default_title: str = "Untitled note",
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    if len(markdown.encode("utf-8")) > MAX_NOTE_MARKDOWN_BYTES:
        raise ValueError("note exceeds the 1 MiB limit")
    current = await read_note(cwd, identity, default_title=default_title, project=project)
    if current["revision"] != expected_revision:
        raise ValueError("note changed externally; reload before saving")
    project_root = Path(str(current["project"]["root"]))
    ensure_project_notes_ignored(project_root)
    body = note_header(
        identity,
        normalize_note_title(title or str(current["title"]), default=default_title),
        created_at=float(current.get("created_at") or time.time()),
        origin_session_id=(
            str(current["origin_session_id"]) if current.get("origin_session_id") else None
        ),
    ) + _note_body(markdown)
    _atomic_write(Path(current["path"]), body.encode("utf-8"))
    return await read_note(cwd, identity, default_title=default_title, project=project)


async def initialize_note(
    cwd: str | Path,
    identity: str,
    title: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Create an empty durable note without overwriting an existing note."""
    current = await read_note(cwd, identity, default_title=title, project=project)
    if current["exists"]:
        return current
    path = Path(current["path"])
    project_root = Path(str(current["project"]["root"]))
    ensure_project_notes_ignored(project_root)
    _create_note_file(path, note_header(identity, title))
    return await read_note(cwd, identity, default_title=title, project=project)


async def create_note(
    cwd: str | Path,
    title: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    identity = str(uuid.uuid4())
    return await initialize_note(cwd, identity, normalize_note_title(title), project=project)


async def delete_note(
    cwd: str | Path,
    identity: str,
    expected_revision: str,
    *,
    project: ProjectIdentity | None = None,
) -> dict[str, Any]:
    """Retire one note into the Project's note trash, only at its observed revision.

    Deletion is recoverable rather than forbidden. The file moves into
    `.swe-mux/notes/trash/` instead of being unlinked, which is what makes an
    ordinary two-click delete safe enough to offer from a tab the user is
    reading. The whole `notes/` tree is Git-ignored and Project-local, so the
    retained copy costs nothing and leaks nowhere.

    The single refusal is a Project's last note. Protecting one *particular*
    note instead - the seeded `project.md` - would be invisible in a rail where
    it stays renameable and looks like every other tab, so the rule is about the
    collection rather than about one member of it.
    """
    current = await read_note(cwd, identity, project=project)
    if current["revision"] != expected_revision:
        raise ValueError("note changed externally; reload before deleting")
    if not current["exists"]:
        raise ValueError("note does not exist")
    project_root = Path(str(current["project"]["root"]))
    if project_note_count(project_root) <= 1:
        raise ProjectNoteProtected(
            "a Project keeps at least one note; rename or empty this one instead"
        )
    path = Path(current["path"])
    try:
        trashed = _move_note_aside(project_root / ".swe-mux" / "notes" / "trash" / path.name, path)
    except FileNotFoundError as exc:
        raise ValueError("note changed externally; reload before deleting") from exc
    return {
        "deleted": True,
        "path": str(path),
        "trashed_path": str(trashed),
        "bytes": int(current.get("bytes") or 0),
        "revision": expected_revision,
    }


def note_save_loop_sample(raw: Any) -> dict[str, Any]:
    """Validate and durably log one browser report of a note writing itself.

    The failure this exists for (2026-08-19 → 2026-08-21): one note held open in two live
    views wrote itself about once a second for minutes at a time - 648 writes in a single
    access log, 1904 across the daemon logs - with the stored revision alternating between
    two values. Every write was individually legitimate (each side rebased onto the revision
    it had just been handed), so nothing here could tell the episode from fast typing, and the
    only evidence it had happened was an access log read days later.

    The browser can tell them apart, because it alone knows whether a human touched the note.
    When its guards end an episode (`noteEditGuard.ts`) they report it here, so the next one is
    in `daemon.log` at the moment it happens, against a resource and a revision.

    Bounded and shape-checked like every other client-authored diagnostic: this is evidence
    submitted by a page, so nothing in it is trusted beyond its type.
    """
    if not isinstance(raw, dict):
        raise ValueError("save loop diagnostic must be an object")
    kind = str(raw.get("kind") or "")
    if kind not in {"paused", "echo"}:
        raise ValueError("save loop kind must be paused or echo")

    def bounded_int(value: Any, ceiling: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(number, ceiling))

    sample: dict[str, Any] = {
        "kind": kind,
        # The save-queue key: a Project or global scope and the resource inside it. Truncated
        # rather than parsed - it is evidence, not a lookup.
        "resource": str(raw.get("resource") or "")[:256],
        "revision": str(raw.get("revision") or "")[:64],
        "commits": bounded_int(raw.get("commits"), 100_000),
        "window_ms": bounded_int(raw.get("window_ms"), 86_400_000),
    }
    log.warning("note save loop %s", json.dumps(sample, sort_keys=True))
    return sample
