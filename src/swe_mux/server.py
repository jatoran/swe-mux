from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .adapters import BackendAdapter, ClaudeAdapter, CodexAdapter, ShellAdapter
from .config import Config
from .event_bus import EventBus
from .git_monitor import GitMonitor, _git
from .history import HistoryIndex
from .launchers import create_agent_shims
from .meta_hooks import MetaHookEngine
from .reconcile import reconcile_external_history
from .session import SessionManager
from .spaces import SpaceManager
from .transcript_view import parse_transcript
from .win_jobobj import ReaperJob

log = logging.getLogger(__name__)
Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def hook_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove hook-owned envelope keys that collide with EventBus metadata."""
    return {
        key: value
        for key, value in payload.items()
        if key not in {"session_id", "source", "event_type"}
    }


def json_response(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


@web.middleware
async def error_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except KeyError as exc:
        return json_response({"error": f"not found: {exc.args[0]}"}, 404)
    except (ValueError, TypeError) as exc:
        return json_response({"error": str(exc)}, 400)
    except Exception:
        log.exception("unhandled request error")
        return json_response({"error": "internal server error"}, 500)


@web.middleware
async def auth_middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
    config: Config = request.app["config"]
    if (
        not config.requires_auth
        or request.path in {"/", "/api/health"}
        or request.path.startswith("/assets/")
        or request.path.startswith("/api/hooks/")
        or request.path.endswith("/promote")
    ):
        return await handler(request)
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not supplied:
        supplied = request.query.get("token", "")
    if not secrets.compare_digest(supplied, config.token):
        raise web.HTTPUnauthorized(
            text=json.dumps({"error": "invalid bearer token"}), content_type="application/json"
        )
    return await handler(request)


def create_app(config: Config, *, frontend_dir: Path | None = None) -> web.Application:
    app = web.Application(
        middlewares=[error_middleware, auth_middleware], client_max_size=2 * 1024 * 1024
    )
    app["config"] = config
    app["frontend_dir"] = frontend_dir or Path(__file__).parent / "static"
    app.cleanup_ctx.append(runtime_context)
    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/health", health),
            web.get("/api/config", get_config),
            web.get("/api/keybindings", get_keybindings),
            web.get("/api/sessions", list_sessions),
            web.post("/api/sessions", spawn_session),
            web.get("/api/sessions/{sid}", get_session),
            web.patch("/api/sessions/{sid}", patch_session),
            web.delete("/api/sessions/{sid}", delete_session),
            web.post("/api/sessions/{sid}/input", session_input),
            web.post("/api/sessions/{sid}/broadcast-set", broadcast_set),
            web.post("/api/sessions/{sid}/promote", promote_session),
            web.get("/api/spaces", list_spaces),
            web.post("/api/spaces", create_space),
            web.patch("/api/spaces/{sid}", patch_space),
            web.delete("/api/spaces/{sid}", delete_space),
            web.get("/api/history", list_history),
            web.get("/api/history/{sid}/transcript", history_transcript),
            web.post("/api/history/{sid}/resume", resume_history),
            web.get("/api/events", list_events),
            web.get("/api/notifications", list_notifications),
            web.post("/api/hooks/{sid}", hook_ingress),
            web.get("/api/git/worktrees", list_worktrees),
            web.post("/api/git/worktrees", create_worktree),
            web.delete("/api/git/worktrees", remove_worktree),
            web.post("/api/reveal", reveal_path),
            web.get("/pty/{sid}", pty_ws),
            web.get("/events", events_ws),
        ]
    )
    assets = app["frontend_dir"] / "assets"
    if assets.is_dir():
        app.router.add_static("/assets", assets)
    return app


async def runtime_context(app: web.Application):  # type: ignore[no-untyped-def]
    config: Config = app["config"]
    history = HistoryIndex(config.database_path)
    events = EventBus(history.append_event)
    spaces = SpaceManager(history)
    await spaces.start()
    reaper = ReaperJob()
    adapters: dict[str, BackendAdapter] = {
        "shell": ShellAdapter(config.shell_exe),
        "claude": ClaudeAdapter(config.claude_exe, config.data_dir),
        "codex": CodexAdapter(config.codex_exe, notify=True),
    }
    child_env = create_agent_shims(config, adapters["claude"].settings_path)  # type: ignore[attr-defined]
    sessions = SessionManager(
        adapters,
        reaper,
        history,
        events,
        config.scrollback_bytes,
        f"http://127.0.0.1:{config.port}",
        child_env,
    )
    git_monitor = GitMonitor(sessions, events, config.git_poll_seconds)
    hooks = MetaHookEngine(config.data_dir / "hooks.toml", events, sessions)
    git_monitor.start()
    hooks.start()
    reconcile_task: asyncio.Task[int] | None = None
    if config.reconcile_external_history:
        reconcile_task = asyncio.create_task(
            reconcile_external_history(history), name="history-reconcile"
        )
    app.update(
        history=history,
        events=events,
        spaces=spaces,
        sessions=sessions,
        reaper=reaper,
        git_monitor=git_monitor,
        hooks=hooks,
    )
    yield
    if reconcile_task:
        if not reconcile_task.done():
            reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
    await hooks.stop()
    await git_monitor.stop()
    await sessions.shutdown()
    history.close()
    reaper.close()


async def index(request: web.Request) -> web.StreamResponse:
    path: Path = request.app["frontend_dir"] / "index.html"
    if not path.exists():
        return web.Response(
            text="swe-mux frontend is not built. Run: cd frontend; npm install; npm run build",
            content_type="text/plain",
        )
    return web.FileResponse(path)


async def health(request: web.Request) -> web.Response:
    sessions: SessionManager = request.app.get("sessions")
    live = sum(s.pty.isalive() for s in sessions.sessions.values()) if sessions else 0
    return json_response({"ok": True, "live_sessions": live, "version": "0.1.0"})


async def get_config(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    return json_response(config.public_dict())


async def get_keybindings(request: web.Request) -> web.Response:
    defaults = {
        "ctrl+alt+t": "session.spawnShell",
        "ctrl+shift+p": "palette.open",
        "ctrl+shift+f": "terminal.find",
        "ctrl+alt+arrowright": "pane.next",
        "ctrl+alt+arrowleft": "pane.previous",
        "ctrl+alt+1": "space.activate(1)",
        "ctrl+alt+2": "space.activate(2)",
        "ctrl+alt+3": "space.activate(3)",
        "ctrl+alt+4": "space.activate(4)",
        "ctrl+alt+5": "space.activate(5)",
        "ctrl+alt+6": "space.activate(6)",
        "ctrl+alt+7": "space.activate(7)",
        "ctrl+alt+8": "space.activate(8)",
        "ctrl+alt+9": "space.activate(9)",
    }
    path = request.app["config"].data_dir / "keybindings.json"
    rejected: dict[str, str] = {}
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid keybindings.json: {exc}") from exc
        for chord, command in supplied.items():
            normalized = str(chord).lower().replace(" ", "")
            if "+" not in normalized or normalized.split("+")[-1] in {"", "ctrl", "shift", "alt"}:
                rejected[chord] = "bindings require a modifier and non-modifier key"
            elif normalized in {"ctrl+w", "ctrl+t", "ctrl+n"}:
                rejected[chord] = "browser-reserved chord"
            else:
                defaults[normalized] = str(command)
    return json_response({"bindings": defaults, "rejected": rejected})


async def list_sessions(request: web.Request) -> web.Response:
    manager: SessionManager = request.app["sessions"]
    sessions = [s.record.snapshot() for s in manager.sessions.values()]
    for field in ("space_id", "state", "backend"):
        value = request.query.get(field.removesuffix("_id") if field == "space_id" else field)
        if value:
            sessions = [s for s in sessions if s[field] == value]
    return json_response(sessions)


async def spawn_session(request: web.Request) -> web.Response:
    body = await request.json()
    manager: SessionManager = request.app["sessions"]
    spaces: SpaceManager = request.app["spaces"]
    space_id = body.get("space") or "default"
    if space_id not in spaces.spaces:
        raise ValueError(f"unknown space: {space_id}")
    session = await manager.spawn(
        backend=body.get("backend", request.app["config"].default_backend),
        name=body.get("name"),
        cwd=body.get("cwd"),
        space_id=space_id,
        exe=body.get("exe"),
        args=body.get("exe_args") or [],
    )
    return json_response(session.record.snapshot(), 201)


async def get_session(request: web.Request) -> web.Response:
    return json_response(
        request.app["sessions"].resolve(request.match_info["sid"]).record.snapshot()
    )


async def patch_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    body = await request.json()
    if "name" in body:
        session.record.name = str(body["name"]).strip() or session.record.name
        session.record.auto_named = False
    if "space" in body:
        if body["space"] not in request.app["spaces"].spaces:
            raise ValueError("unknown space")
        session.record.space_id = body["space"]
    if "pin" in body:
        session.record.pinned_attention = bool(body["pin"])
    await request.app["events"].emit("session_updated", session_id=session.record.id)
    return json_response(session.record.snapshot())


async def delete_session(request: web.Request) -> web.Response:
    manager: SessionManager = request.app["sessions"]
    session = manager.resolve(request.match_info["sid"])
    await manager.stop(session.record.id)
    manager.sessions.pop(session.record.id, None)
    return json_response({"ok": True})


async def session_input(request: web.Request) -> web.Response:
    body = await request.json()
    session = request.app["sessions"].resolve(request.match_info["sid"])
    session.pty.write(str(body.get("data", "")))
    return json_response({"ok": True})


async def broadcast_set(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    session.record.broadcast = bool((await request.json()).get("include", True))
    return json_response(session.record.snapshot())


async def promote_session(request: web.Request) -> web.Response:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    promoted = await request.app["sessions"].promote(
        session.record.id, str(body["backend"]), str(body["native_id"])
    )
    return json_response(promoted.record.snapshot())


async def list_spaces(request: web.Request) -> web.Response:
    spaces: SpaceManager = request.app["spaces"]
    return json_response([s.snapshot() for s in spaces.spaces.values()])


async def create_space(request: web.Request) -> web.Response:
    body = await request.json()
    space = await request.app["spaces"].create(str(body.get("name") or "New space"))
    return json_response(space.snapshot(), 201)


async def patch_space(request: web.Request) -> web.Response:
    space = await request.app["spaces"].update(request.match_info["sid"], **await request.json())
    return json_response(space.snapshot())


async def delete_space(request: web.Request) -> web.Response:
    sid = request.match_info["sid"]
    manager: SessionManager = request.app["sessions"]
    if any(s.record.space_id == sid and s.pty.isalive() for s in manager.sessions.values()):
        raise ValueError("move or kill live sessions before deleting this space")
    await request.app["spaces"].delete(sid)
    return json_response({"ok": True})


async def list_history(request: web.Request) -> web.Response:
    rows = await request.app["history"].history(
        request.query.get("q", ""), request.query.get("backend")
    )
    return json_response(rows)


async def history_transcript(request: web.Request) -> web.Response:
    row = await request.app["history"].history_entry(request.match_info["sid"])
    if not row:
        raise KeyError(request.match_info["sid"])
    transcript = row.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return json_response({"entry": row, "messages": []})
    messages = parse_transcript(Path(transcript), str(row["backend"]))
    return json_response({"entry": row, "messages": messages})


async def resume_history(request: web.Request) -> web.Response:
    rows = await request.app["history"].history(limit=10000)
    row = next((r for r in rows if r["id"] == request.match_info["sid"]), None)
    if not row:
        raise KeyError(request.match_info["sid"])
    body = await request.json() if request.can_read_body else {}
    session = await request.app["sessions"].spawn(
        backend=row["backend"],
        name=body.get("name") or f"{row['name']} resumed",
        cwd=row["cwd"],
        space_id=body.get("space") or "default",
        resume_native_id=row["native_id"],
    )
    return json_response(session.record.snapshot(), 201)


async def list_events(request: web.Request) -> web.Response:
    return json_response(
        await request.app["history"].events(
            float(request.query.get("since", 0)), request.query.get("session")
        )
    )


async def list_notifications(request: web.Request) -> web.Response:
    hooks: MetaHookEngine = request.app["hooks"]
    return json_response(hooks.notifications)


async def hook_ingress(request: web.Request) -> web.Response:
    peer = request.transport.get_extra_info("peername") if request.transport else None
    host = peer[0] if peer else ""
    if host not in {"127.0.0.1", "::1"}:
        raise web.HTTPForbidden(text="hook ingress is loopback-only")
    sid = request.match_info["sid"]
    session = request.app["sessions"].resolve(sid)
    supplied = request.headers.get("X-Mux-Hook-Secret", "")
    if not secrets.compare_digest(supplied, session.hook_secret):
        raise web.HTTPForbidden(text="invalid hook secret")
    body = await request.json()
    event_type = str(body.get("event") or body.get("type") or "hook")
    payload = body.get("payload") or {}
    event_payload = hook_event_payload(payload)
    await request.app["events"].emit(
        event_type, session_id=session.record.id, source="hook", **event_payload
    )
    previous = session.record.state
    semantic: str | None = None
    if event_type == "SessionStart":
        session.record.state = "idle"
        session.record.state_detail = None
    elif event_type in {"UserPromptSubmit", "turn_started"}:
        session.record.state = "working"
        session.record.state_detail = None
        semantic = "turn_started"
    elif event_type == "PreToolUse":
        session.record.state = "working"
        tool = payload.get("tool_name") or payload.get("name") or "tool"
        session.record.state_detail = str(tool)
        semantic = "tool_use"
    elif event_type in {"PostToolUse", "PostToolUseFailure"}:
        session.record.state = "working"
        session.record.state_detail = None
    elif event_type in {"PermissionRequest", "approval_needed", "approval-requested"}:
        session.record.state = "awaiting"
        tool = payload.get("tool_name") or payload.get("message") or "approval"
        session.record.state_detail = str(tool)
        semantic = "approval_needed"
    elif event_type == "Notification":
        notification = str(payload.get("notification_type") or "")
        if notification in {"permission_prompt", "elicitation_dialog", "idle_prompt"}:
            session.record.state = "awaiting"
            session.record.state_detail = str(payload.get("message") or notification)
            semantic = "approval_needed"
    elif event_type in {"Stop", "turn_ended", "agent-turn-complete"}:
        session.record.state = "idle"
        session.record.state_detail = None
        semantic = "turn_ended"
    if semantic and semantic != event_type:
        await request.app["events"].emit(
            semantic, session_id=session.record.id, source="hook", **event_payload
        )
    if session.record.state != previous:
        await request.app["events"].emit(
            "state_changed",
            session_id=session.record.id,
            source="hook",
            previous=previous,
            state=session.record.state,
            detail=session.record.state_detail,
        )
    return json_response({"ok": True})


async def list_worktrees(request: web.Request) -> web.Response:
    cwd = request.query.get("cwd") or str(Path.cwd())
    code, output = await _git(cwd, "worktree", "list", "--porcelain")
    if code:
        return json_response([])
    items: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            if current:
                items.append(current)
                current = {}
        elif " " in line:
            key, value = line.split(" ", 1)
            current[key] = value
        else:
            current[line] = True
    return json_response(items)


async def create_worktree(request: web.Request) -> web.Response:
    body = await request.json()
    cwd, path = str(body["cwd"]), str(Path(body["path"]).resolve())
    args = ["worktree", "add"]
    if branch := body.get("branch"):
        args.extend(["-b", str(branch)])
    args.append(path)
    if start_point := body.get("start_point"):
        args.append(str(start_point))
    code, output = await _git(cwd, *args)
    if code:
        raise ValueError(output or "git worktree add failed")
    return json_response({"ok": True, "path": path}, 201)


async def remove_worktree(request: web.Request) -> web.Response:
    body = await request.json()
    args = ["worktree", "remove"]
    if body.get("force"):
        args.append("--force")
    args.append(str(body["path"]))
    code, output = await _git(str(body["cwd"]), *args)
    if code:
        raise ValueError(output or "git worktree remove failed")
    return json_response({"ok": True})


async def reveal_path(request: web.Request) -> web.Response:
    path = Path((await request.json())["path"]).resolve()
    if not path.exists():
        raise ValueError("path does not exist")
    await asyncio.create_subprocess_exec("explorer.exe", str(path))
    return json_response({"ok": True})


async def pty_ws(request: web.Request) -> web.WebSocketResponse:
    session = request.app["sessions"].resolve(request.match_info["sid"])
    connection_id = secrets.token_urlsafe(12)
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2 * 1024 * 1024)
    await ws.prepare(request)
    await ws.send_json({"type": "state", "snapshot": session.record.snapshot()})
    replay = session.scrollback.bytes()
    if replay:
        await ws.send_json({"type": "replay_start"})
        await ws.send_bytes(replay)
        await ws.send_json({"type": "replay_end"})
    queue = session.subscribe()

    async def sender() -> None:
        while True:
            chunk = await queue.get()
            if chunk == b"":
                await ws.send_json({"type": "exit"})
                return
            await ws.send_bytes(chunk)

    task = asyncio.create_task(sender())
    try:
        async for message in ws:
            if message.type == WSMsgType.BINARY:
                if session.input_owner == connection_id:
                    session.pty.write(message.data)
            elif message.type == WSMsgType.TEXT:
                frame = json.loads(message.data)
                if frame.get("type") == "claim_input":
                    session.input_owner = connection_id
                    await ws.send_json({"type": "input_owner", "active": True})
                elif frame.get("type") == "input" and session.input_owner == connection_id:
                    data = str(frame.get("data", ""))
                    session.pty.write(data)
                    if frame.get("broadcast"):
                        for other in request.app["sessions"].sessions.values():
                            if (
                                other is not session
                                and other.record.broadcast
                                and other.pty.isalive()
                            ):
                                other.pty.write(data)
                elif frame.get("type") == "resize":
                    session.pty.resize(int(frame["cols"]), int(frame["rows"]))
    finally:
        task.cancel()
        if session.input_owner == connection_id:
            session.input_owner = None
        session.unsubscribe(queue)
    return ws


async def events_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    bus: EventBus = request.app["events"]
    queue = bus.subscribe()
    try:
        while True:
            await ws.send_json((await queue.get()).snapshot())
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        bus.unsubscribe(queue)
    return ws
