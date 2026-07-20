from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import swe_mux.reconcile as reconcile
import swe_mux.server as server
import swe_mux.tailscale as tailscale
import swe_mux.transcript_view as tv
from swe_mux.history import HistoryIndex
from swe_mux.reconcile import reconcile_external_history

RULES_TOML = 'version=1\n[[rule]]\nid="diag"\non="turn_ended"\ndo=[{kind="notify",message="hi"}]\n'


def _bump_mtime(path: Path) -> None:
    st = path.stat()
    os.utime(path, (st.st_mtime + 5, st.st_mtime + 5))


# --------------------------------------------------------------------------- #
# transcript_view.parse_transcript_cached
# --------------------------------------------------------------------------- #
def test_transcript_cache_hits_and_invalidates_on_change(tmp_path: Path, monkeypatch) -> None:
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8"
    )
    calls = {"n": 0}
    real = tv.parse_transcript

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "parse_transcript", counting)

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


def test_transcript_cache_keys_on_backend_and_max_bytes(tmp_path: Path, monkeypatch) -> None:
    tv._cache.clear()
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8"
    )
    calls = {"n": 0}
    real = tv.parse_transcript

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(tv, "parse_transcript", counting)
    tv.parse_transcript_cached(path, "claude")
    tv.parse_transcript_cached(path, "claude", max_bytes=1024)
    tv.parse_transcript_cached(path, "codex")
    assert calls["n"] == 3  # backend and max_bytes each key a distinct entry


def test_transcript_cache_propagates_missing_file(tmp_path: Path) -> None:
    tv._cache.clear()
    with pytest.raises(OSError):
        tv.parse_transcript_cached(tmp_path / "nope.jsonl", "claude")


# --------------------------------------------------------------------------- #
# server._load_repo_rule_entry (repository rules mtime cache)
# --------------------------------------------------------------------------- #
def test_repo_rule_entry_caches_by_mtime(tmp_path: Path, monkeypatch) -> None:
    server._repo_rules_cache.clear()
    root = tmp_path / "proj"
    (root / ".swe-mux").mkdir(parents=True)
    rules_path = root / ".swe-mux" / "rules.toml"
    rules_path.write_text(RULES_TOML, encoding="utf-8")
    calls = {"n": 0}
    real = server.parse_rules

    def counting(text, *, source):
        calls["n"] += 1
        return real(text, source=source)

    monkeypatch.setattr(server, "parse_rules", counting)

    e1 = server._load_repo_rule_entry("scope-1", str(root))
    e2 = server._load_repo_rule_entry("scope-1", str(root))
    assert calls["n"] == 1  # parsed once
    assert e1["valid"] is True and e1["execution"] == "inert"
    assert e1["rules"][0]["source"] == "repository-inert"
    assert e2["project_scope_id"] == "scope-1"

    # A cache hit still reflects the caller's fresh project id.
    e3 = server._load_repo_rule_entry("scope-2", str(root))
    assert calls["n"] == 1
    assert e3["project_scope_id"] == "scope-2"

    rules_path.write_text(RULES_TOML, encoding="utf-8")
    _bump_mtime(rules_path)
    server._load_repo_rule_entry("scope-1", str(root))
    assert calls["n"] == 2  # re-parsed after change

    # No rules.toml -> skipped (None), matching the original not-is_file() behaviour.
    assert server._load_repo_rule_entry("scope-x", str(tmp_path / "empty")) is None


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

    def counting(path: Path, backend: str):
        calls["n"] += 1
        return real(path, backend)

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
    server._fs_roots_cache = None
    probes = {"n": 0}

    def fake_probe():
        probes["n"] += 1
        return ["C:\\"]

    monkeypatch.setattr(server, "_probe_drive_roots", fake_probe)
    request = SimpleNamespace(remote="127.0.0.1")

    await server.filesystem_roots(request)  # type: ignore[arg-type]
    await server.filesystem_roots(request)  # type: ignore[arg-type]
    assert probes["n"] == 1  # cached within TTL

    assert server._fs_roots_cache is not None
    server._fs_roots_cache = (0.0, server._fs_roots_cache[1])
    await server.filesystem_roots(request)  # type: ignore[arg-type]
    assert probes["n"] == 2  # re-probed after expiry
    server._fs_roots_cache = None
