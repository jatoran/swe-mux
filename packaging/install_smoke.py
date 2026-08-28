"""Install a built wheel the way a user would, and prove the install works.

Phase 11 ("artifact-install smoke"). The companion to
`verify_release_artifact.py`: that one reads the artifact, this one *installs*
it and asks the installed copy questions. They are deliberately separate acts,
because every failure below is invisible to a reader of the zip - an entry point
that names a module the wheel does not ship, a package whose import fails on a
clean interpreter, a frontend that is present in the archive and unreachable
from `swe_mux.__file__`.

The isolation is the entire point
--------------------------------
The failure this exists to catch cannot be caught from a checkout, because a
checkout satisfies every one of these checks by itself: `import swe_mux` finds
`src/swe_mux`, the frontend is on disk because someone ran `npm run build` an
hour ago, and `mux` is on PATH from `uv run`. So the interpreter used here must
be one that has never heard of this repository:

- a fresh virtualenv, created outside the checkout;
- `cwd` set into that temporary directory, so nothing is importable by accident;
- `PYTHONPATH`, `PYTHONHOME`, and `VIRTUAL_ENV` stripped from the child
  environment, because an inherited one re-adds exactly what this is isolating
  from.

None of that is trusted on its own. `import-isolation` reads the installed
package's own `__file__` back out of the child and fails unless it resolves
inside the virtualenv - the one check that proves the rest of them were
measuring the install rather than the source tree.

Node is not installed, not needed, and its absence is the point of
`frontend-installed`: the packaged UI has to arrive in the wheel, because
nothing on a user's machine can build it.

Usage
-----
    uv run python packaging/install_smoke.py dist/swe_mux-*.whl
    uv run python packaging/install_smoke.py --json <wheel>

Exit 0 when every check passes, 1 when any fails. Starts no daemon and binds no
port: the daemon owns a fixed port and a single data directory, so a CI job that
started one would be a second writer against whatever else is running.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from verify_release_artifact import (  # noqa: E402 - sibling module, path set above
    Check,
    Report,
    referenced_assets,
    render,
)

# How long any one child gets. The install is the only slow one (it resolves and
# downloads the whole dependency set on a cold cache); the rest are a subprocess
# start plus an import. Generous rather than tight, because a timeout here is
# indistinguishable from a real failure in the log and there is nothing to gain
# from a fast red.
INSTALL_TIMEOUT_SECONDS = 900
PROBE_TIMEOUT_SECONDS = 180

# Read out of the installed package rather than out of the wheel. The paths are
# the same, and that is exactly what is being confirmed: a file present in the
# archive but unpacked somewhere the code cannot reach it is still a missing UI.
INSTALLED_INDEX = ("static", "index.html")
INSTALLED_ASSETS = ("static", "assets")

# One probe rather than several: each child pays an interpreter start, and every
# fact here is read from the same import, so splitting them would only make the
# checks disagree about which interpreter answered.
_PROBE = """
import importlib.metadata
import json
import pathlib
import sys

import swe_mux

package = pathlib.Path(swe_mux.__file__).resolve().parent
static = package / "static"
assets = static / "assets"
print(json.dumps({
    "package": str(package),
    "prefix": sys.prefix,
    "version": importlib.metadata.version("swe-mux"),
    "index_html": (static / "index.html").read_text(encoding="utf-8", errors="replace")
    if (static / "index.html").is_file() else None,
    "assets": sorted(item.name for item in assets.iterdir()) if assets.is_dir() else [],
}))
"""


def _clean_environment() -> dict[str, str]:
    """The parent environment minus everything that re-exposes the checkout."""
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP"):
        environment.pop(name, None)
    return environment


def _run(
    command: list[str], *, cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        cwd=cwd,
        env=_clean_environment(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
        errors="replace",
    )


def _tail(process: subprocess.CompletedProcess[str], limit: int = 400) -> str:
    """The end of a failed child's output - the part that says why."""
    combined = f"{process.stdout}\n{process.stderr}".strip()
    return combined[-limit:] if len(combined) > limit else combined


def _venv_binary(venv: Path, name: str) -> Path:
    """Console-script path, which differs by platform and by nothing else."""
    if sys.platform == "win32":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _create_environment(venv: Path, wheel: Path, workdir: Path) -> Check:
    """`uv venv` + `uv pip install <wheel>`, with no `--no-deps` shortcut.

    The dependency resolution is part of what is being tested: a wheel whose
    metadata names something unresolvable installs on the developer's machine
    (where it is already present) and nowhere else.
    """
    uv = shutil.which("uv")
    if uv is None:
        return Check(
            "install",
            False,
            "`uv` is not on PATH, so no isolated environment could be created.",
            "Install uv (https://docs.astral.sh/uv/) and re-run; CI installs it with "
            "`astral-sh/setup-uv`.",
        )
    created = _run([uv, "venv", str(venv)], cwd=workdir, timeout=PROBE_TIMEOUT_SECONDS)
    if created.returncode != 0:
        return Check(
            "install",
            False,
            f"`uv venv` failed ({created.returncode}): {_tail(created)}",
            "This is an environment failure rather than an artifact one; the wheel was "
            "never installed, so nothing below was measured.",
        )
    installed = _run(
        [uv, "pip", "install", "--python", str(_venv_python(venv)), str(wheel)],
        cwd=workdir,
        timeout=INSTALL_TIMEOUT_SECONDS,
    )
    if installed.returncode != 0:
        return Check(
            "install",
            False,
            f"Installing {wheel.name} into a fresh virtualenv failed "
            f"({installed.returncode}): {_tail(installed)}",
            "Read the resolver output above: a wheel that installs from a checkout and "
            "not into a clean environment is usually a dependency that is only present "
            "because development installed it.",
        )
    return Check("install", True, f"{wheel.name} installed into a fresh virtualenv at {venv}.")


def _venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _check_console_script(venv: Path, workdir: Path, name: str, argument: str) -> Check:
    """One entry point, invoked as the installed script rather than as a module.

    `python -m` would prove the module imports and nothing about `[project.scripts]`,
    which is what a user actually types. `--help` rather than a real run: this must
    start no daemon and bind no port.
    """
    binary = _venv_binary(venv, name)
    if not binary.exists():
        return Check(
            f"entry-point-{name}",
            False,
            f"{binary} was not created by the install, so `{name}` is not on a user's PATH.",
            f"Check that `[project.scripts]` in pyproject.toml still declares `{name}` and "
            "rebuild with `uv build --wheel`.",
        )
    process = _run([str(binary), argument], cwd=workdir, timeout=PROBE_TIMEOUT_SECONDS)
    if process.returncode != 0:
        return Check(
            f"entry-point-{name}",
            False,
            f"`{name} {argument}` exited {process.returncode}: {_tail(process)}",
            "The console script exists but does not run, so the module it names is "
            "missing from the wheel or fails at import time.",
        )
    return Check(
        f"entry-point-{name}",
        True,
        f"`{name} {argument}` exited 0 ({len(process.stdout.splitlines())} lines of output).",
    )


def _probe(venv: Path, workdir: Path) -> tuple[dict[str, Any] | None, Check]:
    """Import the installed package on the clean interpreter and read it back."""
    process = _run(
        [str(_venv_python(venv)), "-c", _PROBE], cwd=workdir, timeout=PROBE_TIMEOUT_SECONDS
    )
    if process.returncode != 0:
        return None, Check(
            "package-import",
            False,
            f"`import swe_mux` failed on the installed interpreter "
            f"({process.returncode}): {_tail(process)}",
            "A module-scope import of something the wheel does not depend on is the usual "
            "cause; it works from a checkout because development installed the extra.",
        )
    try:
        observed: dict[str, Any] = json.loads(process.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None, Check(
            "package-import",
            False,
            f"The import probe produced no readable JSON: {_tail(process)}",
            "The probe printed something other than its report, which means the import "
            "emitted output of its own; a library that prints at import time is a bug in "
            "its own right.",
        )
    return observed, Check(
        "package-import",
        True,
        f"`import swe_mux` succeeded on the installed interpreter; "
        f"version {observed['version']} at {observed['package']}.",
    )


def _check_import_isolation(observed: dict[str, Any], venv: Path) -> Check:
    """The check that makes every other check mean something.

    If the import resolved to the source tree, then the frontend it found is the
    one `npm run build` wrote a minute ago and the smoke proved nothing at all.
    """
    package = Path(observed["package"])
    if venv.resolve() not in package.resolve().parents:
        return Check(
            "import-isolation",
            False,
            f"`swe_mux` imported from {package}, which is outside the virtualenv at "
            f"{venv}. Every other check read the source tree rather than the install.",
            "Run this with a working directory outside the checkout and with PYTHONPATH "
            "unset; if it still resolves outside, the wheel did not install its package.",
        )
    return Check(
        "import-isolation",
        True,
        f"`swe_mux` imported from {package}, inside the virtualenv - so the checks below "
        "read the installed copy and not this checkout.",
    )


def _check_frontend_installed(observed: dict[str, Any]) -> Check:
    """The UI as the daemon would find it: entry file plus every asset it names.

    Same join as `verify_release_artifact`'s `frontend-consistency`, over the
    unpacked tree rather than the zip, and it is not redundant with it: a wheel
    that carries `swe_mux/static/**` at the wrong depth passes there and fails
    here, because the daemon resolves the directory relative to `swe_mux.__file__`.
    """
    index_html = observed["index_html"]
    installed_index = "/".join(INSTALLED_INDEX)
    if index_html is None:
        return Check(
            "frontend-installed",
            False,
            f"{installed_index} does not exist in the installed package, so the daemon "
            "would serve no UI.",
            "The wheel carries no frontend bundle. Build it before building the wheel: "
            "`npm --prefix frontend run build`, then `uv build --wheel`.",
        )
    referenced = referenced_assets(index_html)
    present = set(observed["assets"])
    missing = [name for name in referenced if name not in present]
    if not referenced:
        return Check(
            "frontend-installed",
            False,
            f"The installed {installed_index} references nothing under "
            f"{'/'.join(INSTALLED_ASSETS)}/, so it is not a built bundle.",
            "Build the frontend before the wheel: `npm --prefix frontend run build`, then "
            "`uv build --wheel`.",
        )
    if missing:
        return Check(
            "frontend-installed",
            False,
            f"The installed {installed_index} references {len(missing)} asset(s) that were "
            f"not installed beside it: {', '.join(missing)} "
            f"({len(present)} file(s) under {'/'.join(INSTALLED_ASSETS)}/).",
            "The bundle was split by packaging rather than by the build; check that "
            "`artifacts` in pyproject.toml still covers `src/swe_mux/static/**`.",
        )
    return Check(
        "frontend-installed",
        True,
        f"{installed_index} and all {len(referenced)} asset(s) it references are installed "
        f"({len(present)} file(s) under {'/'.join(INSTALLED_ASSETS)}/ in total).",
    )


def smoke(wheel: Path, workdir: Path) -> Report:
    """Install `wheel` into a fresh virtualenv under `workdir` and interrogate it."""
    venv = workdir / "venv"
    checks = [_create_environment(venv, wheel, workdir)]
    evidence: dict[str, Any] = {"venv": str(venv), "package": None, "version": None, "assets": []}
    if not checks[0].ok:
        return Report(wheel=str(wheel), ok=False, checks=checks, evidence=evidence)

    # `--help` for both, and for the same reason: it exercises the console script
    # and the module behind it while starting nothing. `muxd` has no `--version`
    # (the daemon takes no such flag); the installed version is read from the
    # metadata by the import probe instead, which is where a user's `pip show`
    # reads it from too.
    checks.append(_check_console_script(venv, workdir, "mux", "--help"))
    checks.append(_check_console_script(venv, workdir, "muxd", "--help"))

    observed, imported = _probe(venv, workdir)
    checks.append(imported)
    if observed is None:
        return Report(wheel=str(wheel), ok=False, checks=checks, evidence=evidence)

    evidence.update(
        {
            "package": observed["package"],
            "prefix": observed["prefix"],
            "version": observed["version"],
            "assets": observed["assets"],
        }
    )
    checks.append(_check_import_isolation(observed, venv))
    checks.append(_check_frontend_installed(observed))
    return Report(
        wheel=str(wheel),
        ok=all(check.ok for check in checks),
        checks=checks,
        evidence=evidence,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path, help="Path to the built .whl to install.")
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Write the full report as JSON to stdout instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    wheel = args.wheel.resolve()
    if not wheel.is_file():
        report = Report(
            wheel=str(wheel),
            ok=False,
            checks=[
                Check(
                    "install",
                    False,
                    f"{wheel} does not exist.",
                    "Build one first: `uv build --wheel`.",
                )
            ],
            evidence={},
        )
    else:
        # Outside the checkout on purpose - a temporary directory under the
        # repository would put `pyproject.toml` on a parent path, and tooling
        # that walks upwards for it behaves differently there.
        with tempfile.TemporaryDirectory(prefix="swe-mux-install-smoke-") as workdir:
            report = smoke(wheel, Path(workdir))

    if args.as_json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))
    else:
        print(render(report, subject="Wheel install smoke"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
