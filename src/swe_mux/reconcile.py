from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, assert_never

from .adapters.claude import encode_cwd, is_conversation_transcript
from .adapters.omp import session_header
from .git_projects import ProjectIdentity, resolve_project
from .harness import HARNESSES, Backend, require_backend, transcript_dialect
from .history import HistoryIndex
from .opencode_store import session_measurements
from .transcript_view import TRANSCRIPT_PARSER_VERSION

log = logging.getLogger(__name__)

# Re-exported for callers that imported it from here; `claude_models` owns it.
from .claude_models import CLAUDE_CONTEXT_WINDOWS, claude_context_window  # noqa: E402,F401


@dataclass(slots=True)
class ExternalTranscript:
    """One conversation discovered outside mux.

    ``path`` is ``None`` for a harness that keeps conversations in a store: there is
    no file, and ``native_id`` is the whole address. Consumers must therefore treat
    the path as optional rather than assume a file exists to stat.
    """

    backend: Backend
    native_id: str
    cwd: str
    created_at: float
    path: Path | None
    mtime_ns: int = 0
    size: int = 0

    @property
    def row_id(self) -> str:
        digest = hashlib.sha256(f"{self.backend}:{self.native_id}".encode()).hexdigest()[:24]
        return f"external:{digest}"


def _timestamp(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return fallback


def _first_events(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(limit), handle, strict=False):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        pass
    return events


def inspect_claude(path: Path) -> ExternalTranscript | None:
    # `<id>.orphaned-<ts>-<hash>.jsonl` is CLI housekeeping, not a conversation:
    # its recovered sessionId is the original's, so indexing it makes the fragment
    # and the real transcript alternate ownership of one history row forever.
    if not is_conversation_transcript(path):
        return None
    events = _first_events(path)
    if not events:
        return None
    if path.parent.name == "subagents" or any(event.get("isSidechain") is True for event in events):
        return None
    cwd = next((str(event["cwd"]) for event in events if event.get("cwd")), "")
    native_id = next(
        (str(event["sessionId"]) for event in events if event.get("sessionId")), path.stem
    )
    if not cwd:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    created = _timestamp(events[0].get("timestamp"), st.st_mtime)
    return ExternalTranscript("claude", native_id, cwd, created, path, st.st_mtime_ns, st.st_size)


def inspect_codex(path: Path) -> ExternalTranscript | None:
    events = _first_events(path)
    for event in events:
        if event.get("type") != "session_meta":
            continue
        payload = event.get("payload") or {}
        if payload.get("parent_thread_id"):
            return None
        native_id, cwd = payload.get("id"), payload.get("cwd")
        if native_id and cwd:
            try:
                st = path.stat()
            except OSError:
                return None
            created = _timestamp(event.get("timestamp"), st.st_mtime)
            return ExternalTranscript(
                "codex", str(native_id), str(cwd), created, path, st.st_mtime_ns, st.st_size
            )
    return None


def _inspector(backend: Backend) -> Callable[[Path], ExternalTranscript | None]:
    """The header reader for this harness's record dialect.

    Chosen by dialect, not by name, so two harnesses writing the same records share
    one inspector: oh-my-pi and pi do, which is why adding pi to discovery needed no
    new reader at all.
    """
    dialect = transcript_dialect(backend)
    if dialect == "claude":
        return inspect_claude
    if dialect == "codex":
        return inspect_codex
    if dialect == "pi":
        return lambda path: inspect_pi_dialect(path, backend)
    if dialect == "opencode" or dialect is None:
        # A store-backed or record-free harness never reaches a file inspector; the
        # caller routes it to `discover_store_conversations` or declines it.
        return lambda path: None
    assert_never(dialect)


def inspect_pi_dialect(path: Path, backend: Backend) -> ExternalTranscript | None:
    """Read one oh-my-pi or pi session file's header into a discovery record.

    Both forks write the same `{"type":"session"}` header carrying `id`, `cwd`, and
    an ISO `timestamp`, which is the whole of what discovery needs, so the reader is
    shared exactly as their transcript dialect is. The backend has to be passed in
    because the header does not name which fork wrote it; the directory the file was
    found under does.
    """
    header = session_header(path)
    if not isinstance(header, dict):
        return None
    native_id = str(header.get("id") or "")
    cwd = str(header.get("cwd") or "")
    if not native_id or not cwd:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    created = _timestamp(header.get("timestamp"), st.st_mtime)
    return ExternalTranscript(backend, native_id, cwd, created, path, st.st_mtime_ns, st.st_size)


def discover_store_conversations(backend: Backend, home: Path | None = None) -> (
    list[ExternalTranscript]
):
    """Discover a store-backed harness's conversations by querying, not walking.

    Returns records with no ``path``, because there is no file: the conversation is
    addressed by ``native_id`` in the harness's own database. Root conversations
    only, because a subagent runs in a child row and is not a conversation of its
    own for History's purposes.
    """
    store = _store_path(backend, home)
    if store is None or not store.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{store.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, directory, time_created, time_updated FROM session"
            " WHERE parent_id IS NULL ORDER BY time_updated DESC"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    found: list[ExternalTranscript] = []
    for row in rows:
        native_id = str(row["id"] or "")
        cwd = str(row["directory"] or "")
        if not native_id or not cwd:
            continue
        created = _timestamp(row["time_created"], 0.0)
        found.append(
            ExternalTranscript(
                backend,
                native_id,
                cwd,
                created,
                None,
                # The watermark a store conversation is valid for, in the same two
                # slots a file's stat occupies. `message_count` is filled by the
                # reader when the row is indexed; discovery only needs the row to be
                # re-examined when the conversation moves, which `time_updated` says.
                mtime_ns=int(row["time_updated"] or 0),
                size=0,
            )
        )
    return found


def _store_path(backend: Backend, home: Path | None) -> Path | None:
    """The store file for ``backend``, honouring an injected home in tests."""
    harness = HARNESSES.get(backend)
    if harness is None or harness.conversation_store_file is None:
        return None
    if home is None:
        return harness.data_home() / harness.conversation_store_file
    # An injected home replaces the user's, which is what keeps a scan in tests off
    # the real machine. The layout under it is the harness's own.
    relative = harness.data_home().relative_to(Path.home())
    return home / relative / harness.conversation_store_file


def _discovery_root(backend: Backend, home: Path | None) -> Path | None:
    """Where this harness's conversations live, under an optionally injected home."""
    harness = HARNESSES.get(backend)
    discovery = harness.conversation_discovery if harness else None
    if harness is None or discovery is None or discovery.subdirectory is None:
        return None
    base = harness.data_home()
    if home is not None:
        try:
            base = home / base.relative_to(Path.home())
        except ValueError:
            # An env-relocated data home outside the user's home cannot be re-rooted
            # onto an injected one; a scoped scan simply finds nothing there.
            return None
    return base.joinpath(*discovery.subdirectory)


def scan_external_transcripts(
    home: Path | None = None,
    *,
    limit: int | None = 2000,
    roots: Iterable[str | Path] | None = None,
    backends: Iterable[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> list[ExternalTranscript]:
    """Discover conversations every registered harness wrote outside mux.

    The layout is read from each descriptor's `conversation_discovery` rather than
    from a list here. The list version named Claude and Codex, so omp, pi, and
    opencode were silently undiscoverable: their conversations existed, were
    readable, and never reached History, with nothing reporting the gap.

    ``roots`` restricts a `cwd_scoped` scan to directories whose encoded name
    matches one of the supplied working-copy roots. Claude stores each session under
    ``projects/<encoded-cwd>/`` and `encode_cwd` is prefix-preserving, so a
    project-scoped backfill reads a handful of directories instead of every
    transcript on the machine (a real user can have tens of thousands).
    Over-matched siblings are still filtered downstream by the actual cwd, so this
    only trades reads, never correctness. A harness whose bucket names are slugs
    rather than a prefix-preserving encoding declares `cwd_scoped=False` and is
    scanned in full, as is Codex, which stores sessions flat by date.

    ``backends`` restricts the scan to a set of harness names, which is how the
    startup reconcile and the on-demand "scan now" both scope themselves to the
    harnesses the user has enabled. ``None`` scans every registered harness. This is
    an import filter, not a capability one: an unlisted harness's own past sessions
    are simply not indexed this run, and enabling it later indexes them on the next
    scan.

    ``should_cancel`` is polled per file in *both* halves of the scan - the
    discovery walk and the indexing pass - so a long scan aborts promptly. That
    matters more than it looks: the walk runs in a worker thread that no caller
    can interrupt, and an abandoned one is joined at the very end of the loop's
    shutdown, so a walk that ignores the token turns every daemon (and every
    in-process test daemon) shutdown into a wait for a full transcript-tree walk.
    ``on_progress`` receives the running count of files examined.
    """
    selected = set(backends) if backends is not None else None
    encoded_roots = (
        [encode_cwd(root).lower() for root in roots] if roots is not None else None
    )
    found: list[ExternalTranscript] = []
    scanned = 0
    for name, harness in HARNESSES.items():
        if should_cancel is not None and should_cancel():
            return found
        if selected is not None and name not in selected:
            continue
        discovery = harness.conversation_discovery
        if discovery is None:
            # A declared refusal: this harness's past conversations are not indexed.
            continue
        backend = require_backend(name)
        if discovery.store:
            found.extend(discover_store_conversations(backend, home))
            continue
        root = _discovery_root(backend, home)
        pattern = discovery.pattern or "*.jsonl"
        inspect = _inspector(backend)
        scoped = discovery.cwd_scoped
        if root is None or not root.exists():
            continue
        if scoped and encoded_roots is not None:
            search_dirs = [
                child
                for child in root.iterdir()
                if child.is_dir()
                and any(child.name.lower().startswith(prefix) for prefix in encoded_roots)
            ]
        else:
            search_dirs = [root]
        discovered: list[tuple[float, Path]] = []
        for directory in search_dirs:
            for path in directory.glob(f"**/{pattern}"):
                # Polled here and not only in the indexing pass below. A real
                # `~/.claude/projects` is tens of thousands of files, and this
                # walk is the half that a shutdown actually lands in.
                if should_cancel is not None and should_cancel():
                    return found
                # Provider transcript cleanup (and antivirus) removes and locks
                # files in these very directories while the scan walks them. One
                # raced file used to abort the whole reconcile with no log, so
                # every remaining transcript stayed unindexed until some later
                # start happened to complete.
                try:
                    discovered.append((path.stat().st_mtime, path))
                except OSError:
                    continue
        discovered.sort(key=lambda item: item[0], reverse=True)
        for _, path in discovered if limit is None else discovered[:limit]:
            if should_cancel is not None and should_cancel():
                return found
            try:
                transcript = inspect(path)
            except OSError:
                transcript = None
            if transcript:
                found.append(transcript)
            scanned += 1
            if on_progress is not None:
                on_progress(scanned)
    return found


async def scan_external_transcripts_async(
    home: Path | None = None,
    *,
    limit: int | None = 2000,
    roots: Iterable[str | Path] | None = None,
    backends: Iterable[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> list[ExternalTranscript]:
    """Run the discovery walk off the loop, and stop it when the caller is cancelled.

    `asyncio.to_thread` cannot interrupt its worker. Cancelling the awaiting task
    only abandons the thread, which keeps walking - and because that thread belongs
    to the event loop's default executor, the abandoned walk is joined by
    `loop.shutdown_default_executor()` at the very end of shutdown, after every log
    handler has already reported a clean stop. A daemon whose startup reconcile was
    still walking therefore waited out an entire scan of the user's transcript tree
    with nothing saying why: measured at 4.5-13.5s per in-process app teardown
    against a real `~/.claude/projects`, and unbounded as that tree grows.

    So cancellation is made cooperative at this seam rather than at each call site:
    every caller that cancels the coroutine - `HistoryScanManager.stop`,
    `HistoryBackfillManager.stop`, the startup reconcile's teardown - releases the
    worker within one polled file, which is the contract `scan_external_transcripts`
    already documents for ``should_cancel``.
    """
    abandoned = threading.Event()

    def cancelled() -> bool:
        return abandoned.is_set() or (should_cancel is not None and should_cancel())

    try:
        return await asyncio.to_thread(
            scan_external_transcripts,
            home,
            limit=limit,
            roots=roots,
            backends=backends,
            should_cancel=cancelled,
            on_progress=on_progress,
        )
    except asyncio.CancelledError:
        # Set on the way out so the worker sees it on its next poll. The partial
        # result it eventually returns is discarded with the abandoned future.
        abandoned.set()
        raise


def summarize_transcript(
    path: Path | None, backend: Backend, native_id: str = "", home: Path | None = None
) -> dict[str, Any]:
    """Measurements for one discovered conversation.

    A file-backed conversation is streamed and its usage records accumulated. A
    store-backed one is a single indexed read of the harness's own running totals,
    which is both cheaper and exact, so it never guesses from a parse.
    """
    summary: dict[str, Any] = {
        "context_window": None,
        "final_context_pct": None,
        "peak_context_pct": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
        "cost_usd": 0.0,
        "provider": None,
        "provider_account_hashes": {},
        "model": None,
        "measurement_source": None,
    }
    peak = 0.0
    final: float | None = None
    store = _store_path(backend, home)
    if store is not None:
        measured = session_measurements(store, native_id)
        if measured is None:
            return summary
        summary.update(
            {
                "tokens_in": measured["tokens_in"],
                "tokens_out": measured["tokens_out"],
                "tokens_cache_read": measured["tokens_cache_read"],
                "tokens_cache_write": measured["tokens_cache_write"],
                "cost_usd": measured["cost_usd"],
                "model": measured["model"],
                "provider": measured["provider"],
                "measurement_source": f"{backend}-database",
            }
        )
        return summary
    if path is None:
        return summary
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return summary
    # Streamed, not read_text().splitlines(): a months-long conversation is
    # hundreds of MB and the whole-file string plus its split list held several
    # multiples of that in the daemon at once.
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            if backend == "claude":
                if event.get("type") != "assistant":
                    continue
                message = event.get("message") or {}
                usage = message.get("usage") or {}
                model = str(message.get("model") or "")
                window = claude_context_window(model)
                current_in = sum(
                    int(usage.get(key, 0))
                    for key in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
                summary["tokens_in"] += current_in
                summary["tokens_out"] += int(usage.get("output_tokens", 0))
                if model:
                    summary["model"] = model
                if window:
                    summary["context_window"] = window
                    final = min(1.0, current_in / window)
                    peak = max(peak, final)
                    summary["measurement_source"] = "claude-transcript-backfill"
            elif backend == "codex":
                payload = event.get("payload") or {}
                if event.get("type") == "session_meta" and payload.get("model"):
                    summary["model"] = str(payload["model"])
                if payload.get("type") == "token_count":
                    info = payload.get("info") or payload
                    total = info.get("total_token_usage") or {}
                    current = info.get("last_token_usage") or total
                    window = int(info.get("model_context_window") or 0)
                    summary["tokens_in"] = int(total.get("input_tokens") or 0)
                    summary["tokens_out"] = int(total.get("output_tokens") or 0)
                    summary["model"] = str(info.get("model") or "") or summary["model"]
                    if window:
                        summary["context_window"] = window
                        final = min(1.0, int(current.get("input_tokens") or 0) / window)
                        peak = max(peak, final)
                        summary["measurement_source"] = "codex-transcript-backfill"
            elif backend == "shell":
                continue
            elif backend == "opencode":
                # No transcript file to back-fill from; opencode's history lives
                # in `opencode.db`.
                continue
            elif backend == "omp" or backend == "pi":
                event_type = event.get("type")
                # `credential_pin` is an oh-my-pi record; upstream pi never
                # writes one, so this simply never matches for pi.
                if event_type == "credential_pin":
                    provider = str(event.get("provider") or "").strip()
                    account_hash = str(event.get("hash") or "").strip().lower()
                    if provider and re.fullmatch(r"[0-9a-f]{64}", account_hash):
                        summary["provider_account_hashes"][provider] = account_hash
                    continue
                if event_type != "message":
                    continue
                message = event.get("message") or {}
                if message.get("role") != "assistant":
                    continue
                usage = message.get("usage") or {}
                summary["tokens_in"] += int(usage.get("input") or 0)
                summary["tokens_out"] += int(usage.get("output") or 0)
                summary["tokens_cache_read"] += int(usage.get("cacheRead") or 0)
                summary["tokens_cache_write"] += int(usage.get("cacheWrite") or 0)
                cost = usage.get("cost") or {}
                summary["cost_usd"] += float(cost.get("total") or 0.0)
                provider = str(message.get("provider") or "").strip()
                model = str(message.get("model") or "").strip()
                if provider:
                    summary["provider"] = provider
                if model:
                    summary["model"] = model
                summary["measurement_source"] = f"{backend}-transcript-backfill"
            else:
                assert_never(backend)
    summary["final_context_pct"] = final
    summary["peak_context_pct"] = peak if final is not None else None
    return summary


@dataclass(slots=True)
class ScanProgress:
    """Progress of one native-history reconcile, for a user-triggered scan.

    ``phase`` is ``"scanning"`` while transcripts are discovered and ``"indexing"``
    while each is read into History. ``scanned`` is the running discovery count, and
    once scanning finishes it is the total to index, against which ``processed`` and
    ``imported`` advance.
    """

    phase: str = "scanning"
    scanned: int = 0
    processed: int = 0
    imported: int = 0


async def reconcile_external_history(
    history: HistoryIndex,
    home: Path | None = None,
    *,
    backends: Iterable[str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[ScanProgress], None] | None = None,
) -> int:
    progress = ScanProgress()

    def report() -> None:
        if on_progress is not None:
            on_progress(progress)

    def scan_progress(count: int) -> None:
        progress.scanned = count
        report()

    transcripts = await scan_external_transcripts_async(
        home, backends=backends, should_cancel=should_cancel, on_progress=scan_progress
    )
    if should_cancel is not None and should_cancel():
        return 0
    progress.phase = "indexing"
    progress.scanned = len(transcripts)
    report()
    # Skip transcripts whose (mtime_ns, size) are unchanged since the last
    # reconcile so unchanged native files are never re-read/re-parsed. The
    # watermark is persisted per external row, so this holds across restarts.
    watermarks = await history.external_watermarks()
    history_ids = await history.native_history_ids()
    message_watermarks = await history.message_index_watermarks()
    projects: dict[str, ProjectIdentity] = {}
    skipped = 0
    for item in transcripts:
        if should_cancel is not None and should_cancel():
            break
        progress.processed += 1
        report()
        history_id = history_ids.get((item.backend, item.native_id))
        messages_current = bool(
            history_id
            and message_watermarks.get(history_id)
            == (item.mtime_ns, item.size, TRANSCRIPT_PARSER_VERSION)
        )
        # Keyed by the transcript path for a file, and by the conversation id for a
        # store, because that is what the row records as its source in each case.
        watermark_key = str(item.path) if item.path else f"{item.backend}:{item.native_id}"
        if watermarks.get(watermark_key) == (item.mtime_ns, item.size) and messages_current:
            continue
        try:
            if item.cwd not in projects:
                projects[item.cwd] = await resolve_project(item.cwd)
            project = projects[item.cwd]
            await history.register_project_scope(project)
            summary = await asyncio.to_thread(
                summarize_transcript, item.path, item.backend, item.native_id, home
            )
            await history.upsert_external(
                row_id=item.row_id,
                native_id=item.native_id,
                backend=item.backend,
                name=Path(item.cwd).name or item.backend,
                cwd=item.cwd,
                spawned_at=item.created_at,
                # Empty for a store-backed conversation, which has no file. Readers
                # take the native id instead; `conversation_is_readable` is the gate.
                transcript_path=str(item.path) if item.path else "",
                repository_id=project.id,
                project_label=project.label,
                project_root=project.root,
                project_scope_id=project.id,
                repo_group_id=project.repo_group_id,
                mtime_ns=item.mtime_ns,
                size=item.size,
                **summary,
            )
            history_id = (await history.native_history_ids()).get((item.backend, item.native_id))
            if history_id:
                await history.index_transcript(
                    history_id, item.path, item.backend, native_id=item.native_id
                )
            progress.imported += 1
            report()
        except asyncio.CancelledError:
            raise
        except Exception:
            # One vanished/locked transcript, or one row that will not index, used
            # to abort the whole scan and leave every remaining external
            # transcript unindexed until some later start happened to complete.
            skipped += 1
            log.warning("external history reconcile skipped %s", item.path, exc_info=True)
    if skipped:
        log.warning("external history reconcile skipped %d transcript(s)", skipped)
    return len(transcripts)
