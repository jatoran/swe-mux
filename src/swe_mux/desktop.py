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


def health_snapshot(url: str, *, timeout: float = 1.0) -> dict[str, object] | None:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) and payload.get("ok") is True else None


def request_daemon_shutdown(url: str, token: str, *, timeout: float = 5.0) -> bool:
    request = urllib.request.Request(
        f"{url}/api/desktop/shutdown",
        data=b"{}",
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
        self.exiting = False
        self.stop = threading.Event()
        assert config.config_path is not None
        self.instance = WindowsSingleInstance(instance_key(config.config_path))
        self.token = load_or_create_control_token(config.data_dir)

    def ensure_daemon(self) -> None:
        if health_snapshot(self.url) is not None:
            return
        assert self.config.config_path is not None
        environment = os.environ.copy()
        environment[CONTROL_TOKEN_ENV] = self.token
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        log_path = self.config.data_dir / "desktop-daemon.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab", buffering=0) as log:
            self.child = subprocess.Popen(
                daemon_command(self.config.config_path),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if health_snapshot(self.url, timeout=0.5) is not None:
                return
            if self.child.poll() is not None:
                break
            time.sleep(0.15)
        code = self.child.poll()
        raise RuntimeError(
            f"The swe-mux daemon did not start (exit {code}). See {log_path}."
        )

    def show(self, *_: object) -> None:
        if self.window is None or self.exiting:
            return
        self.window.show()
        self.window.restore()

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
        self.window.hide()
        return False

    def hide_minimized(self) -> None:
        if not self.exiting:
            self.window.hide()

    def quit(self, *_: object) -> None:
        if self.exiting:
            return
        stopped = request_daemon_shutdown(self.url, self.token)
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

    def run(self) -> None:
        try:
            import pystray
            import webview
        except ImportError as exc:
            raise RuntimeError(
                "Desktop dependencies are missing. Install with: uv sync --extra desktop"
            ) from exc

        self.ensure_daemon()
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.window = webview.create_window(
            "swe-mux",
            self.url,
            width=1440,
            height=920,
            min_size=(760, 480),
            hidden=self.hidden,
            text_select=True,
            background_color="#090a0c",
        )
        self.window.events.closing += self.close_to_tray
        self.window.events.minimized += self.hide_minimized
        self.icon = pystray.Icon(
            "swe-mux",
            create_tray_image(),
            "swe-mux",
            menu=pystray.Menu(
                pystray.MenuItem("Open swe-mux", self.show, default=True),
                pystray.MenuItem("Open in browser", self.open_external),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Start with Windows", self.toggle_startup, checked=self.startup_enabled
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit swe-mux", self.quit),
            ),
        )
        threading.Thread(target=self.icon.run, name="swe-mux-tray", daemon=True).start()
        threading.Thread(
            target=self.activation_loop, name="swe-mux-activate", daemon=True
        ).start()
        try:
            webview.start(
                private_mode=False,
                storage_path=str(self.config.data_dir / "webview"),
            )
        finally:
            self.stop.set()
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
    if module_name not in {
        "swe_mux.hook_client",
        "swe_mux.agent_launcher",
        "swe_mux.action_runner",
    }:
        return False
    previous = sys.argv
    sys.argv = [module_name, *values[2:]]
    try:
        result: int | None = None
        if module_name == "swe_mux.hook_client":
            from .hook_client import main as hook_main

            hook_main()
        elif module_name == "swe_mux.agent_launcher":
            from .agent_launcher import main as launcher_main

            launcher_main()
        else:
            from .action_runner import main as action_main

            result = action_main()
    finally:
        sys.argv = previous
    if isinstance(result, int):
        raise SystemExit(result)
    return True


def main(argv: Sequence[str] | None = None) -> None:
    arguments = list(argv if argv is not None else sys.argv[1:])
    if dispatch_internal_module(arguments):
        return
    if arguments[:1] == ["--daemon-child"]:
        from .__main__ import main as daemon_main

        daemon_main(arguments[1:])
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
