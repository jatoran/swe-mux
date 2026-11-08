"""Session links into the desktop app: the shared vocabulary, the shell, and the daemon."""

from __future__ import annotations

import ipaddress
import json
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from swe_mux import app_keys as keys
from swe_mux import desktop as desktop_module
from swe_mux.config import Config
from swe_mux.desktop import DesktopRuntime, _run_desktop_shell
from swe_mux.desktop_links import (
    LINK_INBOX_DIR,
    PROTOCOL_COMMAND_KEY,
    LinkHandoff,
    LinkHandoffRegistry,
    SessionLink,
    command_executable,
    delivery_script,
    drain_links,
    link_handler_installed,
    page_url,
    parse_session_link,
    post_link,
    protocol_command,
    register_link_protocol,
    session_link_url,
)
from swe_mux.routes import desktop_integration
from swe_mux.routes.desktop_integration import peer_is_this_host

SESSION = "f23a3d11-30db-4045-ad5a-09634d449639"
HANDOFF = "Zk3xY0abcdEFGHijklMNOPqrstuvWXyz"


# -- links ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        f"swe-mux://open?session={SESSION}",
        f"swe-mux://open/?session={SESSION}",
        f"SWE-MUX://OPEN?session={SESSION}",
        f"swe-mux:open?session={SESSION}",
        f"swe-mux://open?session={SESSION}&unrelated=1",
    ],
)
def test_a_session_link_reads_in_every_form_a_launcher_produces(raw: str) -> None:
    assert parse_session_link(raw) == SessionLink(session_id=SESSION)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        f"https://open?session={SESSION}",
        f"swe-mux://close?session={SESSION}",
        f"swe-mux://open/extra?session={SESSION}",
        f"swe-mux://user@open?session={SESSION}",
        "swe-mux://open",
        "swe-mux://open?session=",
        "swe-mux://open?session=../../etc",
        "swe-mux://open?session=a%20b",
        f"swe-mux://open?session={SESSION}&session=other",
        f"swe-mux://open?session={SESSION}&handoff=short",
        f"swe-mux://open?session={SESSION}&handoff={HANDOFF}&handoff={HANDOFF}",
        f"swe-mux://open?session={'x' * 600}",
    ],
)
def test_anything_else_is_refused_whole(raw: str) -> None:
    """Any web page can launch the scheme, so half of an untrusted link is not a link."""
    assert parse_session_link(raw) is None


def test_the_link_forms_round_trip() -> None:
    link = SessionLink(session_id=SESSION, handoff=HANDOFF)
    assert parse_session_link(session_link_url(link)) == link
    assert parse_session_link(session_link_url(SessionLink(SESSION))) == SessionLink(SESSION)
    assert page_url("http://127.0.0.1:8765/", link) == (
        f"http://127.0.0.1:8765/?session={SESSION}&handoff={HANDOFF}"
    )


def test_the_delivery_script_carries_the_link_as_data() -> None:
    script = delivery_script(SessionLink(SESSION, HANDOFF), "http://127.0.0.1:8765")
    assert json.dumps({"session_id": SESSION, "handoff": HANDOFF}) in script
    assert '"__swemuxOpenSessionLink"' in script
    # Where the page has no hook the window navigates to the page URL instead.
    assert json.dumps(page_url("http://127.0.0.1:8765", SessionLink(SESSION, HANDOFF))) in script


# -- the shell's registration -----------------------------------------------------


def test_the_protocol_command_runs_this_shell_with_this_config(tmp_path: Path) -> None:
    config = tmp_path / "my config.toml"
    frozen = protocol_command(config, executable=r"C:\Apps\swe-mux\swe-mux.exe", frozen=True)
    assert frozen == rf'C:\Apps\swe-mux\swe-mux.exe --config "{config}" --open-link "%1"'
    assert command_executable(frozen) == r"C:\Apps\swe-mux\swe-mux.exe"
    source = protocol_command(config, executable=str(tmp_path / "python.exe"), frozen=False)
    assert " -m swe_mux.desktop --config " in source
    assert source.endswith(' --open-link "%1"')
    assert command_executable('"C:\\Program Files\\swe-mux.exe" --open-link "%1"') == (
        "C:\\Program Files\\swe-mux.exe"
    )


class FakeRegistry:
    """Just enough of `winreg` to exercise registration without touching HKCU."""

    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.writes = 0

    @staticmethod
    def _key(path: str) -> Any:
        class Key:
            name = path

            def __enter__(self) -> Key:
                return self

            def __exit__(self, *_: object) -> None:
                return None

        return Key()

    def OpenKey(self, _root: str, path: str) -> Any:  # noqa: N802 - winreg's name
        if not any(key == path for key, _ in self.values):
            raise OSError("missing")
        return self._key(path)

    def CreateKey(self, _root: str, path: str) -> Any:  # noqa: N802
        return self._key(path)

    def QueryValueEx(self, key: Any, name: str) -> tuple[str, int]:  # noqa: N802
        try:
            return self.values[(key.name, name)], self.REG_SZ
        except KeyError:
            raise OSError("missing") from None

    def SetValueEx(self, key: Any, name: str, _reserved: int, _kind: int, value: str) -> None:  # noqa: N802
        self.writes += 1
        self.values[(key.name, name)] = value


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> FakeRegistry:
    registry = FakeRegistry()
    module = types.ModuleType("winreg")
    for name in ("HKEY_CURRENT_USER", "REG_SZ"):
        setattr(module, name, getattr(registry, name))
    for name in ("OpenKey", "CreateKey", "QueryValueEx", "SetValueEx"):
        setattr(module, name, getattr(registry, name))
    monkeypatch.setitem(sys.modules, "winreg", module)
    monkeypatch.setattr(sys, "platform", "win32")
    return registry


def test_registration_writes_once_and_names_a_program_that_exists(
    fake_registry: FakeRegistry, tmp_path: Path
) -> None:
    program = tmp_path / "swe-mux.exe"
    program.write_bytes(b"")
    config = tmp_path / "config.toml"
    assert not link_handler_installed()

    written = register_link_protocol(config, executable=str(program), frozen=True)
    assert written == protocol_command(config, executable=str(program), frozen=True)
    assert fake_registry.values[(PROTOCOL_COMMAND_KEY, "")] == written
    assert fake_registry.values[(r"Software\Classes\swe-mux", "URL Protocol")] == ""
    assert link_handler_installed()

    # A start that changes nothing writes nothing.
    writes = fake_registry.writes
    assert register_link_protocol(config, executable=str(program), frozen=True) is None
    assert fake_registry.writes == writes

    # An executable that is gone (an uninstalled copy) is not an installed handler.
    program.unlink()
    assert not link_handler_installed()


# -- the second launch's mailbox -----------------------------------------------------


def test_the_mailbox_hands_links_over_oldest_first_and_once(tmp_path: Path) -> None:
    post_link(tmp_path, SessionLink("first"), now=1000.0)
    post_link(tmp_path, SessionLink("second", HANDOFF), now=1001.0)
    drained = drain_links(tmp_path, now=1002.0)
    assert drained.links == [SessionLink("first"), SessionLink("second", HANDOFF)]
    assert drained.dropped == {}
    assert drain_links(tmp_path, now=1003.0).links == []
    assert list((tmp_path / LINK_INBOX_DIR).iterdir()) == []


def test_the_mailbox_drops_what_it_must_not_apply(tmp_path: Path) -> None:
    inbox = tmp_path / LINK_INBOX_DIR
    post_link(tmp_path, SessionLink("stale"), now=0.0)
    post_link(tmp_path, SessionLink("fresh"), now=1000.0)
    (inbox / "00000000000000000001-bad.json").write_text("{not json", encoding="utf-8")
    forged = {"session_id": "<script>", "handoff": None, "posted_at": 1000.0}
    (inbox / "00000000000000000002-forged.json").write_text(json.dumps(forged), encoding="utf-8")
    drained = drain_links(tmp_path, now=1001.0, max_age_seconds=120.0)
    assert drained.links == [SessionLink("fresh")]
    assert drained.dropped == {"stale": 1, "malformed": 2}
    assert list(inbox.iterdir()) == []


def test_a_burst_cannot_grow_the_mailbox_without_bound(tmp_path: Path) -> None:
    for index in range(5):
        post_link(tmp_path, SessionLink(f"s{index}"), now=1000.0 + index)
    drained = drain_links(tmp_path, now=1010.0, max_entries=3)
    assert [link.session_id for link in drained.links] == ["s0", "s1", "s2"]
    assert drained.dropped == {"overflow": 2}


# -- the hand-off registry ---------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_handoff_is_pending_until_the_desktop_page_reports_it() -> None:
    registry = LinkHandoffRegistry(clock=Clock())
    handoff = registry.create(SESSION)
    assert registry.get(handoff.handoff_id) is handoff
    assert handoff.as_dict()["state"] == "pending"
    registry.mark_opened(handoff.handoff_id, found=True)
    assert handoff.as_dict() == {
        "handoff_id": handoff.handoff_id,
        "session_id": SESSION,
        "state": "opened",
        "found": True,
    }
    # The first report is the one that stands.
    registry.mark_opened(handoff.handoff_id, found=False)
    assert handoff.found is True
    assert registry.mark_opened("unknown-handoff-id-0000", found=True) is None
    with pytest.raises(ValueError):
        registry.create("<script>")


def test_handoffs_expire_and_the_unopened_ones_are_reported() -> None:
    clock = Clock()
    expired: list[LinkHandoff] = []
    registry = LinkHandoffRegistry(
        ttl_seconds=10.0, capacity=2, clock=clock, on_expired=expired.append
    )
    old = registry.create(SESSION)
    clock.now = 11.0
    assert registry.get(old.handoff_id) is None
    assert expired == [old]
    # Capacity evicts the oldest even before it expires.
    first = registry.create("a")
    registry.create("b")
    registry.create("c")
    assert registry.get(first.handoff_id) is None
    assert expired[-1] is first


# -- which browser is told to try ---------------------------------------------------


def test_only_this_host_is_told_to_try_the_desktop_app() -> None:
    own = frozenset({ipaddress.ip_address("100.64.152.41"), ipaddress.ip_address("192.168.1.20")})
    assert peer_is_this_host("127.0.0.1", own)
    assert peer_is_this_host("::1", own)
    # This PC opening the Tailscale URL arrives as its own tailnet address.
    assert peer_is_this_host("100.64.152.41", own)
    assert peer_is_this_host("::ffff:192.168.1.20", own)
    # A phone or another PC on the tailnet arrives as itself.
    assert not peer_is_this_host("100.78.135.4", own)
    assert not peer_is_this_host("unknown", own)


def handoff_app() -> web.Application:
    app = web.Application()
    app[keys.DESKTOP_LINK_HANDOFFS] = LinkHandoffRegistry()
    app.add_routes(desktop_integration.ROUTES)
    return app


@pytest.mark.asyncio
async def test_the_handoff_routes_carry_a_tab_from_start_to_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_integration, "handoff_refusal", lambda _peer: None)
    client = TestClient(TestServer(handoff_app()))
    await client.start_server()
    try:
        started = await client.post("/api/desktop/link-handoffs", json={"session_id": SESSION})
        body = await started.json()
        assert started.status == 200 and body["attempt"] is True
        handoff_id = body["handoff_id"]
        assert parse_session_link(body["url"]) == SessionLink(SESSION, handoff_id)

        pending = await client.get(f"/api/desktop/link-handoffs/{handoff_id}")
        assert (await pending.json())["state"] == "pending"
        opened = await client.post(
            f"/api/desktop/link-handoffs/{handoff_id}/opened", json={"found": True}
        )
        assert opened.status == 200
        polled = await (await client.get(f"/api/desktop/link-handoffs/{handoff_id}")).json()
        assert polled["state"] == "opened" and polled["found"] is True

        unknown = await client.get("/api/desktop/link-handoffs/Unknown0000000000000000")
        assert unknown.status == 404 and (await unknown.json())["state"] == "unknown"
        malformed = await client.post("/api/desktop/link-handoffs", json={"session_id": "../x"})
        assert malformed.status == 400
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_a_tab_that_cannot_hand_off_is_told_why_and_nothing_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_integration, "handoff_refusal", lambda _peer: "other_device")
    app = handoff_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post("/api/desktop/link-handoffs", json={"session_id": SESSION})
        assert await response.json() == {"attempt": False, "reason": "other_device"}
        assert app[keys.DESKTOP_LINK_HANDOFFS]._items == {}
    finally:
        await client.close()


def test_the_refusal_order_is_platform_then_device_then_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", False)
    assert desktop_integration.handoff_refusal("127.0.0.1") == "unsupported"
    monkeypatch.setattr(desktop_integration, "IS_WINDOWS", True)
    monkeypatch.setattr(desktop_integration, "local_interface_addresses", lambda: frozenset())
    monkeypatch.setattr(desktop_integration, "link_handler_installed", lambda: False)
    assert desktop_integration.handoff_refusal("100.78.135.4") == "other_device"
    assert desktop_integration.handoff_refusal("127.0.0.1") == "not_installed"
    monkeypatch.setattr(desktop_integration, "link_handler_installed", lambda: True)
    assert desktop_integration.handoff_refusal("127.0.0.1") is None


# -- the shell --------------------------------------------------------------------------


class FakeInstance:
    def __init__(self, running: bool) -> None:
        self.already_running = running
        self.signals = 0
        self.closed = False

    def signal_existing(self) -> None:
        self.signals += 1

    def close(self) -> None:
        self.closed = True


def fake_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, running: bool) -> list[Any]:
    created: list[Any] = []
    config = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")

    class FakeRuntime:
        def __init__(
            self, _config: Config, *, hidden: bool, initial_link: SessionLink | None
        ) -> None:
            self.hidden = hidden
            self.initial_link = initial_link
            self.instance = FakeInstance(running)
            self.ran = False
            created.append(self)

        def run(self) -> None:
            self.ran = True

    monkeypatch.setattr(desktop_module, "load_config", lambda _path: config)
    monkeypatch.setattr(desktop_module, "enable_crash_tracebacks", lambda _dir: None)
    monkeypatch.setattr(desktop_module, "DesktopRuntime", FakeRuntime)
    return created


def test_a_second_launch_with_a_link_leaves_it_for_the_running_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = fake_shell(monkeypatch, tmp_path, running=True)
    _run_desktop_shell(["--open-link", f"swe-mux://open?session={SESSION}&handoff={HANDOFF}"])
    runtime = created[0]
    assert not runtime.ran
    assert runtime.instance.signals == 1 and runtime.instance.closed
    assert drain_links(tmp_path).links == [SessionLink(SESSION, HANDOFF)]


def test_a_cold_launch_with_a_link_opens_visibly_onto_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = fake_shell(monkeypatch, tmp_path, running=False)
    _run_desktop_shell(["--hidden", "--open-link", f"swe-mux://open?session={SESSION}"])
    runtime = created[0]
    assert runtime.ran
    assert runtime.hidden is False, "a link exists to put a session on screen"
    assert runtime.initial_link == SessionLink(SESSION)


def test_an_unreadable_link_still_brings_the_app_forward(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = fake_shell(monkeypatch, tmp_path, running=True)
    _run_desktop_shell(["--open-link", "swe-mux://open?session=<script>"])
    assert created[0].instance.signals == 1
    assert not (tmp_path / LINK_INBOX_DIR).exists()
    assert "desktop link ignored" in (tmp_path / "lifecycle.log").read_text(encoding="utf-8")


def link_runtime(tmp_path: Path, window: Any) -> DesktopRuntime:
    runtime = object.__new__(DesktopRuntime)
    runtime.config = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")
    runtime.url = "http://127.0.0.1:8765"
    runtime.window = window
    runtime.exiting = False
    runtime.stop = threading.Event()
    return runtime


class FakeCore:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    def ExecuteScriptAsync(self, script: str) -> None:  # noqa: N802 - WebView2's name
        self.scripts.append(script)


def fake_window(loaded: list[str]) -> Any:
    # A form whose handle exists and needs no marshalling, so `on_ui_thread` runs inline.
    native = SimpleNamespace(IsHandleCreated=True, InvokeRequired=False)
    return SimpleNamespace(native=native, load_url=loaded.append)


def test_a_running_window_takes_the_link_without_reloading(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    loaded: list[str] = []
    core = FakeCore()
    monkeypatch.setattr(
        desktop_module,
        "await_webview_control",
        lambda *_a, **_k: SimpleNamespace(CoreWebView2=core),
    )
    runtime = link_runtime(tmp_path, fake_window(loaded))
    post_link(tmp_path, SessionLink("first"))
    post_link(tmp_path, SessionLink(SESSION, HANDOFF))
    runtime.deliver_pending_links()
    assert loaded == []
    expected = [("first", None), (SESSION, HANDOFF)]
    assert len(core.scripts) == len(expected)
    for script, (session_id, handoff) in zip(core.scripts, expected, strict=True):
        assert json.dumps({"session_id": session_id, "handoff": handoff}) in script
    ledger_text = (tmp_path / "lifecycle.log").read_text(encoding="utf-8")
    assert f"desktop link delivered session={SESSION} handoff={HANDOFF} via=script" in ledger_text


@pytest.mark.parametrize("control", [None, SimpleNamespace(CoreWebView2=None)])
def test_a_window_with_no_webview_to_script_navigates_onto_the_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, control: Any
) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(desktop_module, "await_webview_control", lambda *_a, **_k: control)
    runtime = link_runtime(tmp_path, fake_window(loaded))
    runtime.deliver_link(SessionLink(SESSION, HANDOFF))
    assert loaded == [page_url(runtime.url, SessionLink(SESSION, HANDOFF))]
    assert "via=navigate" in (tmp_path / "lifecycle.log").read_text(encoding="utf-8")
