from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import swe_mux.reconcile as reconcile
import swe_mux.tailscale as tailscale
import swe_mux.transcript_view as tv
from swe_mux.history import HistoryIndex, transcript_path_key
from swe_mux.models import SessionRecord
from swe_mux.reconcile import reconcile_external_history
from swe_mux.routes import automation as automation_routes
from swe_mux.routes import projects as projects_routes

RULES_TOML = 'version=1\n[[rule]]\nid="diag"\non="turn_ended"\ndo=[{kind="notify",message="hi"}]\n'


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, (st.st_mtime + 5, st.st_mtime + 5))


# --------------------------------------------------------------------------- #
# transcript_view.parse_transcript_cached
#
# The counter patches `_parse_transcript_counted` rather than `parse_transcript`:
# that is what the cache calls, because a cached entry has to carry the abandoned
# count alongside the messages (a dropped message leaves nothing to count later).
# Patching the public wrapper would count zero parses and pass for the wrong reason.
# --------------------------------------------------------------------------- #
def test_transcript_cache_hits_and_invalidates_on_change(tmp_path: Path, monkeypatch) -> None:
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8"
    )
    calls = {"n": 0}
    real = tv._parse_transcript_counted

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "_parse_transcript_counted", counting)

    first = tv.parse_transcript_cached(path, "claude")
    second = tv.parse_transcript_cached(path, "claude")
    assert calls["n"] == 1  # second call is a cache hit
    assert first is second  # shared list returned
    assert first[0]["content"][0]["text"] == "hi"

    path.write_text(
        json.dumps({"type": "user", "message": {"content": "changed"}}) + "\n", encoding="utf-8"
    )
    _bump_mtime(path)
    third = tv.parse_transcript_cached(path, "claude")
    assert calls["n"] == 2  # mtime changed -> re-parsed
    assert third[0]["content"][0]["text"] == "changed"


def test_transcript_cache_invalidates_on_size_at_an_unchanged_mtime(
    tmp_path: Path, monkeypatch
) -> None:
    # Windows mtime granularity is a timer tick, so a transcript appended twice
    # inside one tick keeps the same st_mtime_ns. A mtime-only key then serves
    # the pre-append parse — the one watermark in the codebase that did not pair
    # mtime with size.
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8"
    )
    stamp = path.stat()
    calls = {"n": 0}
    real = tv._parse_transcript_counted

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "_parse_transcript_counted", counting)
    assert len(tv.parse_transcript_cached(path, "claude")) == 1

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "user", "message": {"content": "again"}}) + "\n")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_mtime_ns == stamp.st_mtime_ns

    assert len(tv.parse_transcript_cached(path, "claude")) == 2
    assert calls["n"] == 2


def test_transcript_parse_skips_claude_sidechain_records(tmp_path: Path) -> None:
    # Subagent turns are written into the root transcript tagged isSidechain.
    # Indexing them pollutes history search, and letting one win min/max makes a
    # subagent's clock the run's first/last message time.
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2025-01-01T00:00:00Z",
                        "message": {"content": "root ask"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2025-01-01T00:00:05Z",
                        "isSidechain": True,
                        "message": {"content": [{"type": "text", "text": "subagent chatter"}]},
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    messages = tv.parse_transcript(path, "claude")
    assert [message["content"][0]["text"] for message in messages] == ["root ask"]
    summary = tv.transcript_time_summary(path, "claude")
    assert summary["last_message_ts"] == "2025-01-01T00:00:00Z"


def test_transcript_cache_keys_on_backend_and_max_bytes(tmp_path: Path, monkeypatch) -> None:
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8"
    )
    calls = {"n": 0}
    real = tv._parse_transcript_counted

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "_parse_transcript_counted", counting)
    tv.parse_transcript_cached(path, "claude")
    tv.parse_transcript_cached(path, "claude", max_bytes=1024)
    tv.parse_transcript_cached(path, "codex")
    assert calls["n"] == 3  # backend and max_bytes each key a distinct entry


def test_transcript_cache_propagates_missing_file(tmp_path: Path) -> None:
    tv._cache.clear()
    with pytest.raises(OSError):
        tv.parse_transcript_cached(tmp_path / "nope.jsonl", "claude")


# --------------------------------------------------------------------------- #
# automation_routes._load_repo_rule_entry (repository rules mtime cache)
# --------------------------------------------------------------------------- #
def test_repo_rule_entry_caches_by_mtime(tmp_path: Path, monkeypatch) -> None:
    automation_routes._repo_rules_cache.clear()
    root = tmp_path / "proj"
    (root / ".swe-mux").mkdir(parents=True)
    rules_path = root / ".swe-mux" / "rules.toml"
    rules_path.write_text(RULES_TOML, encoding="utf-8")
    calls = {"n": 0}
    real = automation_routes.parse_rules

    def counting(text, *, source):
        calls["n"] += 1
        return real(text, source=source)

    monkeypatch.setattr(automation_routes, "parse_rules", counting)

    e1 = automation_routes._load_repo_rule_entry("scope-1", str(root))
    e2 = automation_routes._load_repo_rule_entry("scope-1", str(root))
    assert calls["n"] == 1  # parsed once
    assert e1["valid"] is True and e1["execution"] == "inert"
    assert e1["rules"][0]["source"] == "repository-inert"
    assert e2["project_scope_id"] == "scope-1"

    # A cache hit still reflects the caller's fresh project id.
    e3 = automation_routes._load_repo_rule_entry("scope-2", str(root))
    assert calls["n"] == 1
    assert e3["project_scope_id"] == "scope-2"

    rules_path.write_text(RULES_TOML, encoding="utf-8")
    _bump_mtime(rules_path)
    automation_routes._load_repo_rule_entry("scope-1", str(root))
    assert calls["n"] == 2  # re-parsed after change

    # No rules.toml -> skipped (None), matching the original not-is_file() behaviour.
    assert automation_routes._load_repo_rule_entry("scope-x", str(tmp_path / "empty")) is None


# --------------------------------------------------------------------------- #
# reconcile watermark
# --------------------------------------------------------------------------- #
def _write_claude(home: Path, native_id: str, cwd: str, *, assistant_lines: int = 0) -> Path:
    path = home / ".claude" / "projects" / "project" / f"{native_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "type": "user",
                "sessionId": native_id,
                "cwd": cwd,
                "timestamp": "2026-01-02T03:04:05Z",
                "message": {"content": "hi"},
            }
        )
    ]
    for index in range(assistant_lines):
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "model": "claude-opus-4-8",
                        "usage": {"input_tokens": index},
                        "content": "x",
                    },
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def test_reconcile_watermark_skips_unchanged_transcripts(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    cwd = str(tmp_path / "repo")
    claude = _write_claude(home, "claude-id", cwd)
    history = HistoryIndex(tmp_path / "mux.db")
    calls = {"n": 0}
    real = reconcile.summarize_transcript

    # Signature-agnostic, like the other doubles in this file: pinning the exact
    # parameter list made this stub raise `TypeError` when a store-backed harness
    # added a conversation id, and the reconcile loop's deliberate
    # one-bad-transcript-must-not-abort-everything handler swallowed it into a
    # "skipped" log, so the failure surfaced as a wrong call count rather than
    # as the signature mismatch it was.
    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(reconcile, "summarize_transcript", counting)
    try:
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 1  # parsed on first pass

        # Second pass: unchanged file is skipped, but the discovered count holds.
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 1

        # Appending changes (mtime, size) -> re-parsed.
        _write_claude(home, "claude-id", cwd, assistant_lines=1)
        _bump_mtime(claude)
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 2

        # A brand-new transcript with no watermark is always parsed.
        _write_claude(home, "claude-id-2", cwd)
        assert await reconcile_external_history(history, home) == 2
        assert calls["n"] == 3
    finally:
        history.close()


async def test_reconcile_reads_the_history_map_once_for_changed_known_conversations(
    tmp_path: Path, monkeypatch
) -> None:
    """A changed transcript of a conversation already on file reuses the map's id.

    The store is asked again only when the scan has just created a conversation the
    map could not know. Asking for every changed item was a full `history` scan per
    transcript, which is what a long-running daemon's first reconcile after a restart
    paid for every conversation that grew while it was down.
    """
    home = tmp_path / "home"
    cwd = str(tmp_path / "repo")
    paths = [_write_claude(home, f"claude-id-{index}", cwd) for index in range(3)]
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        assert await reconcile_external_history(history, home) == 3
        first_ids = await history.native_history_ids()

        reads = {"n": 0}
        real = history.native_history_ids

        async def counting() -> dict[tuple[str, str], str]:
            reads["n"] += 1
            return await real()

        monkeypatch.setattr(history, "native_history_ids", counting)
        for index, path in enumerate(paths):
            _write_claude(home, f"claude-id-{index}", cwd, assistant_lines=2)
            _bump_mtime(path)

        assert await reconcile_external_history(history, home) == 3
        assert reads["n"] == 1  # the map taken before the loop, and nothing per item
        assert await real() == first_ids
        # Every changed conversation was still re-indexed under its existing row.
        watermarks = await history.message_index_watermarks()
        assert {first_ids[("claude", f"claude-id-{i}")] for i in range(3)} <= set(watermarks)
    finally:
        history.close()


def _owned_session(native_id: str, cwd: str) -> SessionRecord:
    return SessionRecord(
        f"mux-{native_id}", "owned", "default", "claude", native_id, cwd, "claude.exe", []
    )


async def test_reconcile_never_summarizes_a_conversation_a_mux_session_owns(
    tmp_path: Path, monkeypatch
) -> None:
    """An owned row keeps none of a summary, so reading the transcript for one is waste.

    Before the fix every owned conversation the scan found was read whole at every
    start: it has no external watermark to be skipped by, and `upsert_external`
    discarded what it measured. On the development host that was 1,310
    conversations and 81 s of CPU in the daemon's first five minutes, every start.
    """
    home = tmp_path / "home"
    cwd = str(tmp_path / "repo")
    owned_path = _write_claude(home, "owned-id", cwd, assistant_lines=3)
    _write_claude(home, "external-id", cwd, assistant_lines=1)
    history = HistoryIndex(tmp_path / "mux.db")
    summarized: list[str] = []
    real_summarize = reconcile.summarize_transcript

    def counting(*args, **kwargs):
        summarized.append(str(args[2] if len(args) > 2 else kwargs.get("native_id")))
        return real_summarize(*args, **kwargs)

    indexed: list[str] = []
    real_index = history.index_transcript

    async def counting_index(history_id: str, *args, **kwargs):
        indexed.append(history_id)
        return await real_index(history_id, *args, **kwargs)

    monkeypatch.setattr(reconcile, "summarize_transcript", counting)
    monkeypatch.setattr(history, "index_transcript", counting_index)
    try:
        await history.session_started(_owned_session("owned-id", cwd), str(owned_path))
        assert await reconcile_external_history(history, home) == 2
        assert summarized == ["external-id"], "only the conversation nobody owns is measured"
        assert "mux-owned-id" in indexed, "an owned conversation's message index is still built"

        summarized.clear()
        indexed.clear()
        assert await reconcile_external_history(history, home) == 2
        assert summarized == [] and indexed == [], "nothing changed, so nothing is read"

        # A conversation that grew has its message index refreshed, and still no summary.
        _write_claude(home, "owned-id", cwd, assistant_lines=5)
        _bump_mtime(owned_path)
        assert await reconcile_external_history(history, home) == 2
        assert summarized == [] and indexed == ["mux-owned-id"]
        rows = {row["id"]: row for row in await history.history("owned")}
        assert rows["mux-owned-id"]["external"] in (0, False)
    finally:
        history.close()


def _write_codex(home: Path, native_id: str, cwd: str, *, turns: int = 1) -> Path:
    directory = home / ".codex" / "sessions" / "2026" / "08"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-2026-08-11T00-00-00-{native_id}.jsonl"
    lines = [
        {
            "type": "session_meta",
            "timestamp": "2026-08-11T00:00:00.000Z",
            "payload": {"id": native_id, "cwd": cwd, "model": "gpt-test"},
        }
    ]
    for turn in range(turns):
        lines.append(
            {
                "type": "event_msg",
                "timestamp": "2026-08-11T00:00:01.000Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 100 * (turn + 1)},
                        "model_context_window": 1000,
                    },
                },
            }
        )
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return path


async def test_reconcile_skips_an_unchanged_codex_conversation(
    tmp_path: Path, monkeypatch
) -> None:
    """The message index's watermark for Codex is not the file's stat.

    It is `(0, length across the conversation's forked segments)`, and the skip used
    to compare it with the stat, so no Codex conversation ever counted as current:
    every external one was summarized whole again at every start (481 on the
    development host).
    """
    home = tmp_path / "home"
    path = _write_codex(home, "codex-id", str(tmp_path / "repo"))
    history = HistoryIndex(tmp_path / "mux.db")
    calls = {"n": 0}
    real = reconcile.summarize_transcript

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(reconcile, "summarize_transcript", counting)
    try:
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 1
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 1, "an unchanged Codex conversation is not read again"

        _write_codex(home, "codex-id", str(tmp_path / "repo"), turns=3)
        _bump_mtime(path)
        assert await reconcile_external_history(history, home) == 1
        assert calls["n"] == 2, "a Codex conversation that grew is"
    finally:
        history.close()


async def test_reconcile_still_repairs_an_owned_conversation_whose_file_moved(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    cwd = str(tmp_path / "repo")
    found = _write_claude(home, "moved-id", cwd, assistant_lines=1)
    history = HistoryIndex(tmp_path / "mux.db")
    summarized: list[object] = []
    monkeypatch.setattr(
        reconcile, "summarize_transcript", lambda *args, **kwargs: summarized.append(args)
    )
    try:
        gone = tmp_path / "old-place" / "moved-id.jsonl"
        await history.session_started(_owned_session("moved-id", cwd), str(gone))
        assert await reconcile_external_history(history, home) == 1
        owned = await history.owned_transcript_paths()
        assert owned[("claude", "moved-id")] == {transcript_path_key(str(found))}
        assert summarized == []
        watermarks = await history.message_index_watermarks()
        assert "mux-moved-id" in watermarks, "indexed from the file the scan found"
    finally:
        history.close()


async def test_external_watermarks_only_returns_indexed_rows(tmp_path: Path) -> None:
    history = HistoryIndex(tmp_path / "mux.db")
    try:
        # No watermark recorded yet.
        assert await history.external_watermarks() == {}
        await history.upsert_external(
            row_id="external:one",
            native_id="n1",
            backend="claude",
            name="n",
            cwd="c",
            spawned_at=1.0,
            transcript_path="/t/one.jsonl",
            mtime_ns=123,
            size=456,
        )
        marks = await history.external_watermarks()
        assert marks == {"/t/one.jsonl": (123, 456)}
        # A row without a watermark (legacy) is excluded.
        await history.upsert_external(
            row_id="external:two",
            native_id="n2",
            backend="claude",
            name="n",
            cwd="c",
            spawned_at=1.0,
            transcript_path="/t/two.jsonl",
        )
        marks = await history.external_watermarks()
        assert "/t/two.jsonl" not in marks
        # A store-backed conversation records no path; it is keyed the way the
        # reconcile looks it up, not collapsed onto "" with every other one.
        for native_id, updated in (("s1", 11), ("s2", 22)):
            await history.upsert_external(
                row_id=f"external:{native_id}",
                native_id=native_id,
                backend="opencode",
                name="n",
                cwd="c",
                spawned_at=1.0,
                transcript_path="",
                mtime_ns=updated,
                size=0,
            )
        marks = await history.external_watermarks()
        assert marks["opencode:s1"] == (11, 0) and marks["opencode:s2"] == (22, 0)
        assert "" not in marks
    finally:
        history.close()


# --------------------------------------------------------------------------- #
# tailscale + fs-roots TTL caches
# --------------------------------------------------------------------------- #
async def test_tailscale_status_ttl_cache(monkeypatch) -> None:
    tailscale._ts_status_cache.clear()
    probes = {"n": 0}

    async def fake_probe(port: int, *, tailnet_enabled: bool = True):
        probes["n"] += 1
        return {"mode": "loopback", "port": port, "tailnet_urls": []}

    monkeypatch.setattr(tailscale, "_probe_tailscale_status", fake_probe)

    first = await tailscale.tailscale_status(8765, tailnet_enabled=True)
    second = await tailscale.tailscale_status(8765, tailnet_enabled=True)
    assert probes["n"] == 1  # cached within TTL
    assert first == second
    assert first is not second  # deepcopy, never the shared cached dict
    first["tailnet_urls"].append("x")
    assert second["tailnet_urls"] == []  # mutation of one copy does not leak

    # A distinct (port, tailnet_enabled) key probes independently.
    await tailscale.tailscale_status(9000, tailnet_enabled=True)
    assert probes["n"] == 2

    # Force expiry -> re-probe.
    _, value = tailscale._ts_status_cache[(8765, True)]
    tailscale._ts_status_cache[(8765, True)] = (0.0, value)
    await tailscale.tailscale_status(8765, tailnet_enabled=True)
    assert probes["n"] == 3


async def test_fs_roots_probe_is_cached(monkeypatch) -> None:
    projects_routes._fs_roots_cache = None
    probes = {"n": 0}

    def fake_probe():
        probes["n"] += 1
        return ["C:\\"]

    monkeypatch.setattr(projects_routes, "_probe_drive_roots", fake_probe)
    request = SimpleNamespace(remote="127.0.0.1")

    await projects_routes.filesystem_roots(request)  # type: ignore[arg-type]
    await projects_routes.filesystem_roots(request)  # type: ignore[arg-type]
    assert probes["n"] == 1  # cached within TTL

    assert projects_routes._fs_roots_cache is not None
    projects_routes._fs_roots_cache = (0.0, projects_routes._fs_roots_cache[1])
    await projects_routes.filesystem_roots(request)  # type: ignore[arg-type]
    assert probes["n"] == 2  # re-probed after expiry
    projects_routes._fs_roots_cache = None
