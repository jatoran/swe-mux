"""The durable record of a note that wrote itself.

The incident (2026-08-19 → 2026-08-21): one note held open in two live views saved about once
a second for minutes at a time, the stored revision alternating between two values. Every write
was individually legitimate, so the only evidence was an access log read days later. The
browser's guards end such an episode now (`noteEditGuard.ts`); this is where it lands so the
next one is attributable at the moment it happens.
"""

from __future__ import annotations

import json
import logging

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux.project_files import note_save_loop_sample
from swe_mux.server import error_middleware, note_save_loop_diagnostic


def _app() -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app.router.add_post("/notes/save-loop-diagnostic", note_save_loop_diagnostic)
    return app


def test_save_loop_sample_bounds_every_client_supplied_field(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="swe_mux.project_files"):
        sample = note_save_loop_sample(
            {
                "kind": "paused",
                "resource": "p" * 400,
                "revision": "r" * 200,
                "commits": 10**9,
                "window_ms": "not a number",
            }
        )

    assert sample["kind"] == "paused"
    assert len(sample["resource"]) == 256
    assert len(sample["revision"]) == 64
    assert sample["commits"] == 100_000
    assert sample["window_ms"] == 0
    # Durable, structured, and searchable: this is the whole point of the endpoint.
    logged = [record for record in caplog.records if record.message.startswith("note save loop")]
    assert len(logged) == 1
    assert json.loads(logged[0].message.removeprefix("note save loop ")) == sample


def test_save_loop_sample_records_a_harmless_echo_episode_too(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The guard that ends the observed loop ends it silently; the report is the trace."""
    with caplog.at_level(logging.WARNING, logger="swe_mux.project_files"):
        sample = note_save_loop_sample(
            {
                "kind": "echo",
                "resource": "p1 note:plan",
                "revision": "0314be",
                "commits": 6,
                "window_ms": 10_000,
            }
        )

    assert sample == {
        "kind": "echo",
        "resource": "p1 note:plan",
        "revision": "0314be",
        "commits": 6,
        "window_ms": 10_000,
    }
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.parametrize(
    "raw",
    ["not an object", {"kind": "typing"}, {"kind": ""}, {}],
)
def test_save_loop_sample_refuses_a_shape_it_cannot_attribute(raw: object) -> None:
    with pytest.raises(ValueError):
        note_save_loop_sample(raw)


async def test_save_loop_endpoint_logs_a_report_and_rejects_a_bad_one() -> None:
    async with TestClient(TestServer(_app())) as client:
        accepted = await client.post(
            "/notes/save-loop-diagnostic",
            json={
                "kind": "paused",
                "resource": "p1 note:plan",
                "revision": "4693eb",
                "commits": 6,
                "window_ms": 10_000,
            },
        )
        refused = await client.post("/notes/save-loop-diagnostic", json={"kind": "typing"})

        assert accepted.status == 200
        assert await accepted.json() == {
            "kind": "paused",
            "resource": "p1 note:plan",
            "revision": "4693eb",
            "commits": 6,
            "window_ms": 10_000,
        }
        assert refused.status == 400
        assert "paused or echo" in (await refused.json())["error"]
