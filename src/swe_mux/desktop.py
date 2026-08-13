from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .desktop_window_state import (
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    MIN_WINDOW_HEIGHT,
    MIN_WINDOW_WIDTH,
    WINDOW_STATE_NAME,
    DesktopWindowState,
    WindowStateRecorder,
    fit_window_state,
    load_window_state,
    save_window_state,
)
from .lifecycle import ledger
from .logsetup import enable_crash_tracebacks
from .subprocess_flags import popen_outside_job
from .win_jobobj import process_in_job

CONTROL_TOKEN_ENV = "SWE_MUX_DESKTOP_CONTROL_TOKEN"
CONTROL_TOKEN_NAME = "desktop-control.token"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "swe-mux"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
MB_OK = 0x00000000
MB_ICONWARNING = 0x00000030
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
# How long a freshly spawned daemon may take to answer `/api/health` before the
# tray stops waiting for it and brings the window up anyway. A daemon binds its
# port only after opening its databases and reattaching supervised sessions, and
# a start right after a redeploy pays for that with a cold page cache: measured
# 2026-08-06, the same daemon took 14s warm and 36s on the start that followed a
# bundle rebuild. The old 30s budget expired inside that window and killed the
# tray while the daemon it had spawned went on to serve normally, which is why
# this mirrors `packaging/redeploy_desktop.py`: generous for a slow start, and
# irrelevant to a real failure because a child that exits is detected at once.
DAEMON_HEALTH_TIMEOUT_SECONDS = 300.0
# Cadence of the post-start health poll that loads the window once a daemon that
# outlived the budget finally answers.
DAEMON_HEALTH_POLL_SECONDS = 0.5
# The tray-menu restart waits far less, and for a different reason: pystray runs
# a menu action on its message thread, so waiting there freezes the tray. It also
# has nothing to wait for - the window already exists and its SPA reconnects to
# the returning daemon by itself. Long enough to catch a daemon that dies on
# contact, short enough that the tray stays usable if one does not.
DAEMON_RESTART_WAIT_SECONDS = 30.0


def local_url(config: Config) -> str:
    host = f"[{config.host}]" if ":" in config.host else config.host
    return f"http://{host}:{config.port}"


def instance_key(config_path: Path) -> str:
    normalized = str(config_path.resolve()).casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:20]


def load_or_create_control_token(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / CONTROL_TOKEN_NAME
    try:
        existing = path.read_text(encoding="ascii").strip()
    except OSError:
        existing = ""
    if len(existing) >= 32:
        return existing
    token = secrets.token_urlsafe(32)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(token + "\n", encoding="ascii")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return token


def _rotate_console_log(path: Path) -> None:
    """Keep the previous daemon's console output as ``.1`` instead of growing forever.

    The redirect is a crash catcher, so the generation that matters is the one
    from the daemon that just died; one rotated copy preserves it. Skipped when
    the file is still held open (a predecessor that has not finished exiting).
    """
    try:
        if path.is_file() and path.stat().st_size > 0:
            os.replace(path, path.with_suffix(".log.1"))
    except OSError:
        pass


def health_snapshot(url: str, *, timeout: float = 1.0) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") is True else None


def request_daemon_shutdown(
    url: str, token: str, *, timeout: float = 5.0, mode: str = "quit"
) -> bool:
    """Intent-signaled daemon shutdown: "quit" reaps sessions, "restart" preserves
    supervisor-owned sessions for the next daemon to reattach."""
    request = urllib.request.Request(
        f"{url}/api/desktop/shutdown",
        data=json.dumps({"mode": mode}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status) == 202
    except (OSError, urllib.error.URLError):
        return False


def daemon_command(
    config_path: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> list[str]:
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    config_args = ["--config", str(config_path)]
    if frozen:
        return [executable, "--daemon-child", *config_args]
    return [executable, "-m", "swe_mux", *config_args]


def startup_command(
    config_path: Path,
    *,
    executable: str | None = None,
    frozen: bool | None = None,
) -> str:
    executable = executable or sys.executable
    frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if frozen:
        parts = [executable, "--hidden", "--config", str(config_path)]
    else:
        python = Path(executable)
        pythonw = python.with_name("pythonw.exe")
        parts = [str(pythonw if pythonw.exists() else python), "-m", "swe_mux.desktop"]
        parts.extend(["--hidden", "--config", str(config_path)])
    return subprocess.list2cmdline(parts)


def create_tray_image(size: int = 64):  # type: ignore[no-untyped-def]
    from PIL import Image, ImageDraw

    scale = max(1, size // 16)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2 * scale, 2 * scale, size - 2 * scale, size - 2 * scale),
        radius=3 * scale,
        fill=(9, 10, 12, 255),
        outline=(139, 212, 80, 255),
        width=max(1, scale),
    )
    draw.line(
        (5 * scale, 6 * scale, 8 * scale, 8 * scale, 5 * scale, 10 * scale),
        fill=(139, 212, 80, 255),
        width=max(2, scale),
        joint="curve",
    )
    draw.line(
        (9 * scale, 11 * scale, 12 * scale, 11 * scale),
        fill=(217, 221, 226, 255),
        width=max(1, scale),
    )
    return image


def show_desktop_warning(title: str, message: str) -> None:
    """Show tray-owned feedback even while the WebView is hidden."""
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            title,
            MB_OK | MB_ICONWARNING | MB_SETFOREGROUND | MB_TOPMOST,
        )
    else:
        print(f"swe-mux: {message}", file=sys.stderr)


class WindowsSingleInstance:
    def __init__(self, key: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("the swe-mux desktop shell currently supports Windows only")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.kernel32.CreateMutexW.restype = ctypes.c_void_p
        self.kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        self.kernel32.CreateEventW.restype = ctypes.c_void_p
        self.kernel32.SetEvent.argtypes = [ctypes.c_void_p]
        self.kernel32.SetEvent.restype = ctypes.c_bool
        self.kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        self.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel32.CloseHandle.restype = ctypes.c_bool
        self.mutex = self.kernel32.CreateMutexW(None, False, f"Local\\swe-mux-desktop-{key}")
        if not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        self.event = self.kernel32.CreateEventW(
            None, False, False, f"Local\\swe-mux-desktop-show-{key}"
        )
        if not self.event:
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def signal_existing(self) -> None:
        if self.event:
            self.kernel32.SetEvent(self.event)

    def wait_for_activation(self, timeout_ms: int = 500) -> bool:
        return bool(
            self.event
            and self.kernel32.WaitForSingleObject(self.event, timeout_ms) == WAIT_OBJECT_0
        )

    def close(self) -> None:
        if getattr(self, "event", None):
            self.kernel32.CloseHandle(self.event)
            self.event = None
        if getattr(self, "mutex", None):
            self.kernel32.CloseHandle(self.mutex)
            self.mutex = None


class DesktopRuntime:
    def __init__(self, config: Config, *, hidden: bool = False) -> None:
        self.config = config
        self.hidden = hidden
        self.url = local_url(config)
        self.child: subprocess.Popen[bytes] | None = None
        self.window: Any = None
        self.icon: Any = None
        self.window_state_path = config.data_dir / WINDOW_STATE_NAME
        self.window_state_recorder: WindowStateRecorder | None = None
        self.exiting = False
        self.stop = threading.Event()
        assert config.config_path is not None
        self.instance = WindowsSingleInstance(instance_key(config.config_path))
        self.token = load_or_create_control_token(config.data_dir)
        enable_crash_tracebacks(config.data_dir)
        if process_in_job():
            # The tray has no console; the ledger is the only place this
            # warning can survive. A tray inside a session's kill-on-close Job
            # dies (with its daemon) when that session is removed.
            ledger(
                config.data_dir,
                f"tray pid {os.getpid()} started inside a Windows Job object "
                "(launched from within a session?); it and its daemon may be "
                "terminated when that Job closes",
            )

    def ensure_daemon(self, *, wait_seconds: float | None = None) -> bool:
        """Start the daemon unless one is already serving; report whether it answered.

        **A daemon that is still running has not failed - it is still starting.**
        Only a child that actually exits is a failure, and it is raised as one
        immediately rather than after the budget. Exhausting the budget with the
        child alive returns False, which leaves the caller to keep the tray up
        and load the window once health arrives.

        The budget resolves at call time rather than as a default argument value,
        so `DAEMON_HEALTH_TIMEOUT_SECONDS` stays the single authority for it.
        """
        budget = DAEMON_HEALTH_TIMEOUT_SECONDS if wait_seconds is None else wait_seconds
        if health_snapshot(self.url) is not None:
            return True
        assert self.config.config_path is not None
        from .spawn_contract import scrub_claude_session_markers

        # Scrubbed so a shell launched from inside an agent session cannot pass
        # parent-Claude child-session markers down to every terminal.
        environment = scrub_claude_session_markers(os.environ)
        environment[CONTROL_TOKEN_ENV] = self.token
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log_path = self.config.data_dir / "desktop-daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_console_log(log_path)
        with log_path.open("ab", buffering=0) as log:
            # cwd anchors in the data dir: an Explorer launch inherits
            # cwd=dist/swe-mux, and any long-lived process anchored there locks
            # the dist tree against session-preserving rebuilds. Breakaway
            # spawn: a tray relaunched from inside a session sits in that
            # session's kill-on-close Job, and the daemon must not inherit it.
            self.child = popen_outside_job(
                daemon_command(self.config.config_path),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(self.config.data_dir),
                creationflags=flags,
            )
        ledger(self.config.data_dir, f"tray spawned daemon pid {self.child.pid}")
        self._watch_daemon_exit(self.child)
        started = time.monotonic()
        deadline = started + budget
        while time.monotonic() < deadline:
            if health_snapshot(self.url, timeout=0.5) is not None:
                ledger(
                    self.config.data_dir,
                    f"daemon pid {self.child.pid} answered health after "
                    f"{time.monotonic() - started:.1f}s",
                )
                return True
            code = self.child.poll()
            if code is not None:
                raise RuntimeError(
                    f"The swe-mux daemon exited during startup (code {code}) after "
                    f"{time.monotonic() - started:.1f}s. See {log_path}."
                )
            time.sleep(0.15)
        ledger(
            self.config.data_dir,
            f"daemon pid {self.child.pid} is still starting after "
            f"{budget:.0f}s; the tray will load the window when it answers",
        )
        return False

    def _watch_daemon_exit(self, child: subprocess.Popen[bytes]) -> None:
        """Ledger the daemon's exit code; external kills leave no other trace.

        A daemon terminated by a closing Job object or taskkill writes nothing
        of its own, so the tray — which holds the process handle — is the only
        observer that can record how the child actually ended.
        """

        def wait() -> None:
            code = child.wait()
            ledger(
                self.config.data_dir,
                f"daemon pid {child.pid} exited with code {code}",
            )

        threading.Thread(target=wait, name="mux-daemon-exit-watch", daemon=True).start()

    def show(self, *_: object) -> None:
        if self.window is None or self.exiting:
            return
        self.window.show()
        self.window.restore()

    def _load_window_state(self, webview: Any) -> DesktopWindowState | None:
        try:
            saved = load_window_state(self.window_state_path)
        except FileNotFoundError:
            ledger(
                self.config.data_dir,
                f"desktop window state absent; using {DEFAULT_WINDOW_WIDTH}x"
                f"{DEFAULT_WINDOW_HEIGHT} centered",
            )
            return None
        except (OSError, ValueError) as exc:
            ledger(
                self.config.data_dir,
                f"desktop window state invalid; using defaults ({exc})",
            )
            return None

        try:
            restored = fit_window_state(saved, webview.screens)
        except Exception as exc:
            ledger(
                self.config.data_dir,
                f"desktop monitor inventory unavailable; restoring saved bounds unchanged ({exc})",
            )
            restored = saved
        if restored != saved:
            try:
                save_window_state(self.window_state_path, restored)
            except OSError as exc:
                ledger(
                    self.config.data_dir,
                    f"desktop window state adjustment could not be saved ({exc})",
                )
            else:
                ledger(
                    self.config.data_dir,
                    "desktop window state fitted to current monitors "
                    f"x={restored.x} y={restored.y} width={restored.width} "
                    f"height={restored.height} maximized={restored.maximized}",
                )
        else:
            ledger(
                self.config.data_dir,
                "desktop window state restored "
                f"x={restored.x} y={restored.y} width={restored.width} "
                f"height={restored.height} maximized={restored.maximized}",
            )
        return restored

    def _window_state_saved(self, state: DesktopWindowState) -> None:
        ledger(
            self.config.data_dir,
            "desktop window state saved "
            f"x={state.x} y={state.y} width={state.width} "
            f"height={state.height} maximized={state.maximized}",
        )

    def _window_state_failed(self, exc: Exception) -> None:
        ledger(self.config.data_dir, f"desktop window state save failed ({exc})")

    def _flush_window_state(self) -> None:
        recorder = getattr(self, "window_state_recorder", None)
        if recorder is not None:
            recorder.flush()

    def _capture_initial_window_state(self, *_: object) -> None:
        recorder = self.window_state_recorder
        if recorder is None:
            return
        try:
            x = self.window.x
            y = self.window.y
            width = self.window.width
            height = self.window.height
        except Exception as exc:
            ledger(
                self.config.data_dir,
                f"desktop initial window geometry unavailable ({exc})",
            )
            return
        recorder.moved(x, y)
        recorder.resized(width, height)

    def open_external(self, *_: object) -> None:
        webbrowser.open(self.url, new=2)

    def startup_enabled(self, _item: object | None = None) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        except OSError:
            return False
        assert self.config.config_path is not None
        return str(value) == startup_command(self.config.config_path)

    def toggle_startup(self, icon: object, _item: object) -> None:
        import winreg

        assert self.config.config_path is not None
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if self.startup_enabled():
                with suppress(OSError):
                    winreg.DeleteValue(key, RUN_VALUE)
            else:
                winreg.SetValueEx(
                    key,
                    RUN_VALUE,
                    0,
                    winreg.REG_SZ,
                    startup_command(self.config.config_path),
                )
        update = getattr(icon, "update_menu", None)
        if update:
            update()

    def close_to_tray(self) -> bool | None:
        if self.exiting:
            return None
        self._flush_window_state()
        self.window.hide()
        return False

    def hide_minimized(self) -> None:
        if not self.exiting:
            self.window.hide()

    def restart_daemon(self, *_: object) -> None:
        """Replace the daemon process while supervisor-owned sessions keep running.

        The session-preserving half of "reload with my changes": the daemon is
        asked to detach (mode=restart), then a fresh daemon is spawned and
        reattaches to the supervisor's live sessions.
        """
        if self.exiting:
            return
        if health_snapshot(self.url) is not None:
            if not request_daemon_shutdown(self.url, self.token, mode="restart"):
                show_desktop_warning(
                    "Restart failed",
                    "The daemon did not accept the restart request. It may not have "
                    "been started with desktop control.",
                )
                return
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and health_snapshot(self.url, timeout=0.25):
                time.sleep(0.1)
            if self.child is not None and self.child.poll() is None:
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.terminate()
            self.child = None
        try:
            if not self.ensure_daemon(wait_seconds=DAEMON_RESTART_WAIT_SECONDS):
                show_desktop_warning(
                    "Daemon still starting",
                    "The replacement daemon has not answered its health check yet. "
                    "The window reconnects on its own once it does.",
                )
        except RuntimeError as exc:
            show_desktop_warning("Restart failed", str(exc))

    def _reap_supervisor(self) -> None:
        """Reap supervised sessions ourselves when the daemon runs out of budget.

        The tray holds the config, so it can do what the daemon did not finish.
        The supervisor's Job close is the authoritative reap; history
        finalization is worth losing next to leaving agents running invisibly.
        """
        try:
            import asyncio

            from .supervisor_client import kill_server

            if asyncio.run(kill_server(self.config)):
                ledger(self.config.data_dir, "tray reaped the PTY supervisor on quit")
        except Exception as exc:
            ledger(self.config.data_dir, f"tray could not reap the PTY supervisor: {exc}")

    def quit(self, *_: object) -> None:
        if self.exiting:
            return
        self._flush_window_state()
        stopped = request_daemon_shutdown(self.url, self.token, mode="quit")
        if stopped:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and health_snapshot(
                self.url, timeout=0.25
            ) is not None:
                time.sleep(0.1)
            if (
                health_snapshot(self.url, timeout=0.25) is not None
                and self.child is not None
                and self.child.poll() is None
            ):
                # The daemon overran its budget mid-teardown, so the supervisor
                # may never have received `reap_all_and_exit`. Terminating the
                # daemon alone would leave the supervisor and its remaining
                # agents running headless — the user believes everything quit
                # while sessions keep consuming quota and reappear next launch.
                self._reap_supervisor()
                self.child.terminate()
        elif self.child is None and health_snapshot(self.url) is not None:
            show_desktop_warning(
                "Daemon still running",
                "This daemon was not started with desktop control. The desktop window will "
                "close, but the existing daemon and its terminals will remain running.",
            )
        elif self.child is not None and self.child.poll() is None:
            self.child.terminate()
        self.exiting = True
        self.stop.set()
        if self.icon is not None:
            self.icon.stop()
        if self.window is not None:
            self.window.destroy()

    def activation_loop(self) -> None:
        while not self.stop.is_set():
            if self.instance.wait_for_activation():
                self.show()

    def load_when_healthy(self) -> None:
        """Load the window once a daemon that outlived the start budget answers.

        Runs on the WebView's post-start thread, so the tray, the activation
        signal and the window all exist while it waits. The window was created
        against a daemon that was not serving yet and is showing WebView2's
        connection error until this reload lands. Only a daemon that exits ends
        the wait, because uptime alone is never evidence of failure.
        """
        while not self.stop.is_set():
            if health_snapshot(self.url, timeout=0.5) is not None:
                ledger(self.config.data_dir, "daemon answered health; loading the window")
                window = self.window
                if window is not None:
                    window.load_url(self.url)
                return
            child = self.child
            if child is not None and child.poll() is not None:
                ledger(
                    self.config.data_dir,
                    f"daemon pid {child.pid} exited (code {child.returncode}) before it "
                    "ever answered health",
                )
                show_desktop_warning(
                    "swe-mux daemon stopped",
                    f"The daemon exited during startup (code {child.returncode}). "
                    f"See {self.config.data_dir / 'desktop-daemon.log'}.",
                )
                return
            time.sleep(DAEMON_HEALTH_POLL_SECONDS)

    def run(self) -> None:
        try:
            import pystray
            import webview
        except ImportError as exc:
            raise RuntimeError(
                "Desktop dependencies are missing. Install with: uv sync --extra desktop"
            ) from exc

        # A daemon that has not answered yet still gets a tray and a window: the
        # shell is the only way to reach the app, and exiting here used to strand
        # the user with a healthy daemon and nothing to talk to it.
        healthy = self.ensure_daemon()
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        window_state = self._load_window_state(webview)
        self.window_state_recorder = WindowStateRecorder(
            self.window_state_path,
            window_state,
            on_saved=self._window_state_saved,
            on_error=self._window_state_failed,
        )
        self.window = webview.create_window(
            "swe-mux",
            self.url,
            width=window_state.width if window_state else DEFAULT_WINDOW_WIDTH,
            height=window_state.height if window_state else DEFAULT_WINDOW_HEIGHT,
            x=window_state.x if window_state else None,
            y=window_state.y if window_state else None,
            min_size=(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT),
            hidden=self.hidden,
            maximized=window_state.maximized if window_state else False,
            text_select=True,
            background_color="#090a0c",
        )
        self.window.events.closing += self.close_to_tray
        self.window.events.shown += self._capture_initial_window_state
        self.window.events.moved += self.window_state_recorder.moved
        self.window.events.resized += self.window_state_recorder.resized
        self.window.events.maximized += self.window_state_recorder.maximized
        self.window.events.minimized += self.window_state_recorder.minimized
        self.window.events.restored += self.window_state_recorder.restored
        self.window.events.minimized += self.hide_minimized
        menu_items = [
            pystray.MenuItem("Open swe-mux", self.show, default=True),
            pystray.MenuItem("Open in browser", self.open_external),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Start with Windows", self.toggle_startup, checked=self.startup_enabled
            ),
        ]
        if self.config.pty_supervisor_enabled:
            # Only meaningful with the PTY supervisor: without it a daemon
            # restart kills every session, so the item would be a footgun.
            menu_items.append(
                pystray.MenuItem("Restart daemon (keep sessions)", self.restart_daemon)
            )
        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit swe-mux", self.quit),
            ]
        )
        self.icon = pystray.Icon(
            "swe-mux",
            create_tray_image(),
            "swe-mux",
            menu=pystray.Menu(*menu_items),
        )
        threading.Thread(target=self.icon.run, name="swe-mux-tray", daemon=True).start()
        threading.Thread(
            target=self.activation_loop, name="swe-mux-activate", daemon=True
        ).start()
        try:
            webview.start(
                None if healthy else self.load_when_healthy,
                debug=False,
                private_mode=False,
                storage_path=str(self.config.data_dir / "webview"),
            )
        finally:
            self.stop.set()
            if self.window_state_recorder is not None:
                self.window_state_recorder.close()
            if self.icon is not None:
                self.icon.stop()
            self.instance.close()


def show_error(message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(None, message, "swe-mux", 0x10)
    else:
        print(f"swe-mux: {message}", file=sys.stderr)


def desktop_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="swe-mux", description="swe-mux desktop shell")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--hidden", action="store_true", help="start in the system tray")
    return parser


def dispatch_internal_module(arguments: Sequence[str]) -> bool:
    """Emulate the allowlisted ``python -m`` calls emitted by bundled helpers."""
    values = list(arguments)
    if len(values) < 2 or values[0] != "-m":
        return False
    module_name = values[1]
    if module_name not in {"swe_mux.hook_client", "swe_mux.agent_launcher"}:
        return False
    previous = sys.argv
    sys.argv = [module_name, *values[2:]]
    try:
        if module_name == "swe_mux.hook_client":
            from .hook_client import main as hook_main

            hook_main()
        else:
            from .agent_launcher import main as launcher_main

            launcher_main()
    finally:
        sys.argv = previous
    return True


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if dispatch_internal_module(arguments):
        return
    if arguments[:1] == ["--daemon-child"]:
        from .__main__ import main as daemon_main

        daemon_main(arguments[1:])
        return
    if arguments[:1] == ["--supervisor-child"]:
        from .supervisor import main as supervisor_main

        supervisor_main(arguments[1:])
        return
    args = desktop_parser().parse_args(arguments)
    try:
        config = load_config(args.config)
        assert config.config_path is not None
        runtime = DesktopRuntime(config, hidden=args.hidden)
        if runtime.instance.already_running:
            if not args.hidden:
                runtime.instance.signal_existing()
            runtime.instance.close()
            return
        runtime.run()
    except Exception as exc:
        show_error(str(exc))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
