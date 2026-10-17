"""The open-file name worker never strands a thread when its caller gives up."""

from __future__ import annotations

import os
import threading

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="the per-process handle query is Windows-only"
)


def test_a_worker_abandoned_just_after_it_answered_still_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race from `DAEMON_LOAD_ISOLATION.md` item 3, forced rather than hoped for.

    The caller's 100 ms wait expires, the worker answers a moment later, checks
    `retired` before the caller has set it, and goes back to its inbox. The caller
    then abandons it. Before the fix nothing ever arrived in that inbox again, so
    the thread blocked forever, counted toward `MAX_STUCK_WORKERS`, and quarantined
    its pid for the life of the daemon (the live daemon reported `stuck_workers: 2`).
    """
    from swe_mux import win_process_files as wpf

    monkeypatch.setattr(wpf, "_final_path", lambda handle: "C:\\answered.txt")
    monkeypatch.setattr(wpf, "_CloseHandle", lambda handle: True)
    worker = wpf._NameWorker()
    query = wpf._Query(0)
    worker.inbox.put(query)
    assert query.done.wait(5), "the worker answered"
    # The worker is now back in `inbox.get()` with `retired` still false: exactly
    # the state a caller whose wait expired a moment too early abandons it in.
    pid = 0x7FFF_FFF0
    with monkeypatch.context() as patch:
        patch.setattr(wpf, "_stuck_workers", [])
        patch.setattr(wpf, "_active_worker", worker)
        wpf._abandon(worker, pid)
        worker.thread.join(5)
        assert not worker.thread.is_alive(), (
            "an abandoned worker must not wait on its inbox forever"
        )
        assert wpf._quarantined(pid) is False, "a worker that exited quarantines nothing"


def test_a_worker_abandoned_mid_query_exits_once_the_kernel_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other order: the caller gives up while the name query is still running."""
    from swe_mux import win_process_files as wpf

    release = threading.Event()
    entered = threading.Event()

    def blocking_final_path(handle: int) -> str | None:
        entered.set()
        release.wait(10)
        return None

    monkeypatch.setattr(wpf, "_final_path", blocking_final_path)
    monkeypatch.setattr(wpf, "_CloseHandle", lambda handle: True)
    worker = wpf._NameWorker()
    query = wpf._Query(0)
    worker.inbox.put(query)
    assert entered.wait(5)
    pid = 0x7FFF_FFF1
    with monkeypatch.context() as patch:
        patch.setattr(wpf, "_stuck_workers", [])
        patch.setattr(wpf, "_active_worker", worker)
        wpf._abandon(worker, pid)
        assert wpf._quarantined(pid) is True, "still stuck in the kernel: quarantined"
        release.set()
        assert query.done.wait(5), "the late answer is still delivered and its handle closed"
        worker.thread.join(5)
        assert not worker.thread.is_alive()
        assert wpf._quarantined(pid) is False
