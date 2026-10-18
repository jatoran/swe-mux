"""The terminal gateway's Tailscale Serve mount beside the daemon's root route.

A page on the HTTPS address cannot open the gateway's own plain-HTTP port, so Serve
carries it at `/gateway` on the same 443 listener as the daemon. That puts two
handlers on the listener every ownership decision in `test_serve_route_ownership.py`
reads, so the first thing pinned here is that the mount can never be read as the root:
the gateway's health is not a daemon's, and mistaking one for the other would call a
live daemon's address abandoned and take it.

The rest pins the mount's own rule: it follows the root route, is reconciled only by
the daemon that owns that route, never touches a foreign handler, and never rides
along onto a listener Funnel exposes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from swe_mux import tailscale
from swe_mux.bounded_subprocess import ProcessOutcome

_HOST = "desktop.taild42d36.ts.net:443"


def _outcome(exit_code: int = 0, stderr: bytes = b"") -> ProcessOutcome:
    return ProcessOutcome(
        exit_code=exit_code,
        stdout=b"",
        stderr=stderr,
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=1.0,
        timed_out=False,
    )


def _with_gateway(root: int, gateway: int, *, gateway_first: bool = True) -> dict[str, Any]:
    """A Serve config whose 443 listener has both handlers, in a chosen order."""
    handlers = [("/gateway", gateway), ("/", root)]
    if not gateway_first:
        handlers.reverse()
    return {
        "Web": {
            _HOST: {
                "Handlers": {
                    path: {"Proxy": f"http://127.0.0.1:{port}"} for path, port in handlers
                }
            }
        }
    }


@pytest.mark.parametrize("gateway_first", [True, False])
def test_the_gateway_mount_is_never_read_as_the_root_route(gateway_first: bool) -> None:
    config = _with_gateway(8765, 8766, gateway_first=gateway_first)
    assert tailscale._loopback_target_port(config, 443) == 8765
    assert tailscale._web_target_matches(config, 443, 8765)
    # A daemon whose port happens to be the gateway's must not see the address as its own.
    assert not tailscale._web_target_matches(config, 443, 8766)
    assert tailscale._mount_loopback_port(config, 443, tailscale.GATEWAY_SERVE_PATH) == 8766


async def test_a_running_owner_is_found_behind_its_gateway_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The liveness probe asks the daemon at the root, never the gateway at the mount."""
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")

    async def status(_exe: str, command: str) -> tuple[Any | None, str]:
        if command == "serve":
            return _with_gateway(8765, 8766), ""
        return None, "not configured"

    monkeypatch.setattr(tailscale, "_status", status)
    probed: list[int] = []

    async def alive(port: int) -> bool:
        probed.append(port)
        return port == 8765

    monkeypatch.setattr(tailscale, "_swemux_daemon_alive", alive)
    result = await tailscale.enable_mobile_voice_serve(18823)
    assert result["status"] == "error"
    assert probed == [8765]


class _FakeServe:
    """`tailscale serve` against one in-memory listener, mutated the way the CLI does.

    Measured on 1.102.2 against a scratch port: re-setting `/` keeps other handlers,
    `--set-path=/gateway <target>` replaces that handler in place, `--set-path=/gateway
    off` removes only it, and removing a handler that is absent exits 1.
    """

    def __init__(
        self,
        handlers: dict[str, str] | None,
        *,
        funnel: bool = False,
        fail: bool = False,
        drop: bool = False,
    ) -> None:
        self.handlers = handlers
        self.funnel = funnel
        self.fail = fail
        self.drop = drop
        self.commands: list[tuple[str, ...]] = []

    def payload(self) -> dict[str, Any]:
        if self.handlers is None:
            # What `serve status --json` prints, exit 0, on a node with no Serve config.
            return {}
        return {
            "Web": {
                _HOST: {
                    "Handlers": {path: {"Proxy": target} for path, target in self.handlers.items()}
                }
            }
        }

    async def status(self, _exe: str, command: str) -> tuple[Any | None, str]:
        if command == "funnel":
            if self.funnel:
                return {"AllowFunnel": {_HOST: True}}, ""
            return None, "not configured"
        return self.payload(), ""

    async def run(self, argv: Sequence[str], **_kwargs: Any) -> ProcessOutcome:
        self.commands.append(tuple(argv))
        args = list(argv[2:])
        path = next((a.split("=", 1)[1] for a in args if a.startswith("--set-path=")), "/")
        if self.fail:
            return _outcome(1, b"boom")
        assert self.handlers is not None
        if args[-1] == "off":
            if path not in self.handlers:
                return _outcome(1, b"Error: failed to remove web serve: handler does not exist")
            del self.handlers[path]
        elif not self.drop:
            self.handlers[path] = args[-1]
        return _outcome()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")
        monkeypatch.setattr(tailscale, "_status", self.status)
        monkeypatch.setattr(tailscale, "run_bounded", self.run)


async def test_the_gateway_is_mounted_beside_this_daemons_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "added", result
    assert result["mount_port"] == 8766
    assert serve.commands == [
        (
            "tailscale",
            "serve",
            "--bg",
            "--https=443",
            "--set-path=/gateway",
            "http://127.0.0.1:8766",
        )
    ]
    # The root route is untouched.
    assert serve.handlers == {"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"}


async def test_a_correct_mount_runs_no_command(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "unchanged"
    assert serve.commands == []


async def test_a_moved_gateway_port_moves_the_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 9000)
    assert result["status"] == "updated"
    assert result["mount_port"] == 9000
    assert serve.handlers == {"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:9000"}


async def test_a_disabled_gateway_takes_its_mount_away(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, None)
    assert result["status"] == "removed"
    assert serve.commands == [("tailscale", "serve", "--https=443", "--set-path=/gateway", "off")]
    assert serve.handlers == {"/": "http://127.0.0.1:8765"}


async def test_a_disabled_gateway_with_no_mount_runs_no_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI exits 1 removing an absent handler, so the absence is checked first."""
    serve = _FakeServe({"/": "http://127.0.0.1:8765"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, None)
    assert result["status"] == "unchanged"
    assert serve.commands == []


@pytest.mark.parametrize("gateway_port", [8766, None])
async def test_the_mount_follows_the_root_route_only(
    monkeypatch: pytest.MonkeyPatch, gateway_port: int | None
) -> None:
    """A daemon that does not own the address moves nothing - not even to clean up."""
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(18823, gateway_port)
    assert result["status"] == "skipped"
    assert serve.commands == []


@pytest.mark.parametrize("gateway_port", [8766, None])
async def test_a_foreign_handler_at_the_mount_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, gateway_port: int | None
) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://10.0.0.5:3000"})
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, gateway_port)
    assert result["status"] == "skipped"
    assert "another route" in str(result["diagnostic"])
    assert serve.commands == []


async def test_a_funneled_listener_gets_no_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765"}, funnel=True)
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "skipped"
    assert "Funnel" in str(result["diagnostic"])
    assert serve.commands == []


async def test_no_serve_config_is_not_this_daemons_to_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve = _FakeServe(None)
    serve.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "skipped"
    assert serve.commands == []


async def test_no_tailscale_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: None)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "skipped"


async def test_an_unreadable_serve_config_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tailscale, "tailscale_executable", lambda: "tailscale")

    async def status(_exe: str, _command: str) -> tuple[Any | None, str]:
        return None, "failed to connect to local tailscaled"

    async def run(_argv: Sequence[str], **_kwargs: Any) -> ProcessOutcome:  # pragma: no cover
        raise AssertionError("nothing may be changed on a config that could not be read")

    monkeypatch.setattr(tailscale, "_status", status)
    monkeypatch.setattr(tailscale, "run_bounded", run)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "error"
    assert "tailscaled" in str(result["diagnostic"])


async def test_a_failed_or_unretained_mount_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    failing = _FakeServe({"/": "http://127.0.0.1:8765"}, fail=True)
    failing.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "error"
    assert "boom" in str(result["diagnostic"])

    dropping = _FakeServe({"/": "http://127.0.0.1:8765"}, drop=True)
    dropping.install(monkeypatch)
    result = await tailscale.reconcile_gateway_serve(8765, 8766)
    assert result["status"] == "error"
    assert "did not retain" in str(result["diagnostic"])


async def test_remote_status_reports_the_mount(monkeypatch: pytest.MonkeyPatch) -> None:
    serve = _FakeServe({"/": "http://127.0.0.1:8765", "/gateway": "http://127.0.0.1:8766"})
    serve.install(monkeypatch)

    async def ipv4(_exe: str | None = None) -> str | None:
        return "100.64.1.2"

    async def plain(_exe: str) -> Any | None:
        return None

    monkeypatch.setattr(tailscale, "tailscale_ipv4", ipv4)
    monkeypatch.setattr(tailscale, "_plain_status", plain)
    status = await tailscale._probe_tailscale_status(8765)
    assert status["gateway_mount_port"] == 8766
    assert status["mobile_voice_configured"] is True


async def test_startup_mounts_the_gateway_only_after_the_root_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from swe_mux import __main__ as entry

    async def ready(_app: Any) -> None:
        return None

    calls: list[tuple[int, int | None]] = []

    async def reconcile(port: int, gateway_port: int | None) -> dict[str, object]:
        calls.append((port, gateway_port))
        return {"status": "added", "mount_port": gateway_port, "diagnostic": ""}

    outcome: dict[str, object] = {"status": "ready", "url": "https://desktop.taild42d36.ts.net/"}

    async def enable(_port: int) -> dict[str, object]:
        return dict(outcome)

    monkeypatch.setattr(entry, "wait_runtime_ready", ready)
    monkeypatch.setattr(entry, "enable_mobile_voice_serve", enable)
    monkeypatch.setattr(entry, "reconcile_gateway_serve", reconcile)
    await entry._auto_enable_mobile_voice(object(), 8765, 8766)  # type: ignore[arg-type]
    assert calls == [(8765, 8766)]

    outcome["status"] = "error"
    await entry._auto_enable_mobile_voice(object(), 8765, 8766)  # type: ignore[arg-type]
    assert calls == [(8765, 8766)]
