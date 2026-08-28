"""Access to the foreign-host `config.toml` corpus under `tests/fixtures/`.

The corpus is a set of realistic states a configuration file reaches when it
outlives the host that wrote it (see the README beside the files). It lives on
disk rather than as strings inside one test because more than one work package
consumes it, and because a file is what the loader actually takes.

Everything here copies before it hands a path back. `load_config` writes healed
values *into* the file it was given, so a test pointed at the checkout's copy
would rewrite the fixture and quietly disarm every test after it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "foreign_host_configs"

#: Every fixture in the corpus, by the name a test refers to it as.
FOREIGN_HOST_CONFIGS: tuple[str, ...] = (
    "windows_authored",
    "posix_authored",
    "ancient_schema",
)


def fixture_source(name: str) -> Path:
    """The checkout's copy of one fixture. Read it; never load it in place."""
    path = FIXTURE_DIR / f"{name}.toml"
    if not path.is_file():
        raise FileNotFoundError(f"no foreign-host config fixture named {name!r} at {path}")
    return path


def foreign_host_config(name: str, destination: Path) -> Path:
    """Copy one fixture into `destination` and return the path to load.

    `destination` is a directory a test owns - `tmp_path`, normally. The file is
    named `config.toml` there because several loader behaviours key off the
    file's own location rather than off its contents (`data_dir` reconciliation
    among them), and a fixture loaded under its corpus name would be answering a
    question no real install asks.
    """
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "config.toml"
    shutil.copyfile(fixture_source(name), target)
    return target
