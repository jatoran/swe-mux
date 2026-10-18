"""The supervisor's revision moves whenever its source closure does.

`DAEMON_LOAD_ISOLATION.md` item 16. Installed copies used to replace the
supervisor only when its protocol changed (`update_install.py`), so a supervisor
improvement that kept the protocol - items 12 and 13 are exactly that - would
never have reached anyone. The updater now compares `SUPERVISOR_REVISION` too,
which only works if the number really moves with the code: a changed closure
under an old revision is a supervisor update that nobody is ever offered.

The pin is a content digest of the closure, not the rebuild gate's
`supervisor_source_hash`, which mixes in the build machine's package versions and
therefore never matches across machines.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from swe_mux import supervisor
from tests.support.supervisor_wire import FakeReaper

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def build_desktop() -> Iterator[object]:
    sys.path.insert(0, str(ROOT / "packaging"))
    try:
        import build_desktop as module

        yield module
    finally:
        sys.path.remove(str(ROOT / "packaging"))


def test_the_supervisor_revision_is_pinned_to_its_source_closure(build_desktop: object) -> None:
    pins_file = build_desktop.SUPERVISOR_REVISIONS_FILE  # type: ignore[attr-defined]
    pins: dict[str, str] = json.loads(pins_file.read_text(encoding="utf-8"))["revisions"]
    digest = build_desktop.supervisor_content_digest()  # type: ignore[attr-defined]
    revision = supervisor.SUPERVISOR_REVISION
    following = revision + 1

    assert sorted(int(key) for key in pins) == list(range(1, revision + 1)), (
        "one pin per revision, from 1 to SUPERVISOR_REVISION, none skipped"
    )
    assert pins[str(revision)] == digest, (
        f"The supervisor's source closure changed since revision {revision} was pinned. "
        f"Set SUPERVISOR_REVISION = {following} in src/swe_mux/supervisor.py and add "
        f'"{following}": "{digest}" to packaging/supervisor_revisions.json. Never rewrite '
        "an existing pin: installed copies decide whether to offer a supervisor update by "
        "comparing revisions, so a changed closure under an old number never reaches them."
    )
    assert len(set(pins.values())) == len(pins), "two revisions pin the same content"


def test_the_pin_ignores_line_endings_and_the_revision_line_and_nothing_else(
    build_desktop: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "supervisor.py"
    other = tmp_path / "pty_host.py"
    monkeypatch.setattr(build_desktop, "SUPERVISOR_SOURCES", (source, other))
    digest = build_desktop.supervisor_content_digest  # type: ignore[attr-defined]

    other.write_bytes(b"reader = 1\n")
    source.write_bytes(b"PROTOCOL_VERSION = 1\nSUPERVISOR_REVISION = 1\nfanout = 1\n")
    pinned = digest()
    # A Windows checkout's line endings are the same content.
    source.write_bytes(b"PROTOCOL_VERSION = 1\r\nSUPERVISOR_REVISION = 1\r\nfanout = 1\r\n")
    assert digest() == pinned
    # A bump alone changes nothing a pin covers, so pinning it twice is caught.
    source.write_bytes(b"PROTOCOL_VERSION = 1\nSUPERVISOR_REVISION = 7\nfanout = 1\n")
    assert digest() == pinned
    # Any real change, in any file of the closure, is a different digest.
    source.write_bytes(b"PROTOCOL_VERSION = 1\nSUPERVISOR_REVISION = 1\nfanout = 2\n")
    assert digest() != pinned
    source.write_bytes(b"PROTOCOL_VERSION = 1\nSUPERVISOR_REVISION = 1\nfanout = 1\n")
    other.write_bytes(b"reader = 2\n")
    assert digest() != pinned


def test_the_pins_file_is_outside_the_closure_it_pins(build_desktop: object) -> None:
    """Otherwise adding a pin would change the digest it records."""
    names = {path.name for path in build_desktop.SUPERVISOR_SOURCES}  # type: ignore[attr-defined]
    assert "supervisor_revisions.json" not in names


async def test_the_running_supervisor_states_its_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor, "create_reaper", FakeReaper)
    server = supervisor.SupervisorServer(tmp_path / "config.toml", tmp_path)
    await server.start()
    try:
        recorded = json.loads(supervisor.discovery_path(tmp_path).read_text(encoding="utf-8"))
        assert recorded["revision"] == supervisor.SUPERVISOR_REVISION
    finally:
        server.exit_event.set()
        await server._teardown()
