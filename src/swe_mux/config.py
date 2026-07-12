from __future__ import annotations

import secrets
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""
    loopback_auth: bool = False
    default_backend: str = "shell"
    shell_exe: str = "powershell.exe"
    claude_exe: str = "claude.exe"
    codex_exe: str = "codex.exe"
    scrollback_bytes: int = 5 * 1024 * 1024
    git_poll_seconds: float = 5.0
    reconcile_external_history: bool = True
    data_dir: Path = Path.home() / ".mux"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "mux.db"

    @property
    def requires_auth(self) -> bool:
        return self.loopback_auth or self.host not in {"127.0.0.1", "localhost", "::1"}

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["data_dir"] = str(self.data_dir)
        result.pop("token", None)
        result["requires_auth"] = self.requires_auth
        return result


def load_config(path: Path | None = None) -> Config:
    path = path or Path.home() / ".mux" / "config.toml"
    cfg = Config(data_dir=path.parent)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        for key in Config.__dataclass_fields__:
            if key in raw and key != "data_dir":
                setattr(cfg, key, raw[key])
    if not cfg.token:
        cfg.token = secrets.token_urlsafe(32)
        path.write_text(
            "# swe-mux configuration\n"
            f'host = "{cfg.host}"\nport = {cfg.port}\n'
            f'token = "{cfg.token}"\nloopback_auth = false\n'
            f'default_backend = "{cfg.default_backend}"\n',
            encoding="utf-8",
        )
    return cfg
