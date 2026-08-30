"""Assert the pinned voice closure resolves on every interpreter swe-mux may run on.

`requires-python = ">=3.12"` invites `uv tool install swe-mux` onto whatever the
newest CPython on the machine is, while the voice closure is generated from a lock
resolved against 3.12 and is only as new as its slowest native dependency. Those two
facts drifted apart silently, and the first person to run into it was a tester on a
clean Windows 11 laptop where uv had picked 3.14: local voice setup dead-ended at
"the pinned voice closure has no wheel this interpreter can load for: spacy", with no
hint that the cause was the *interpreter version* rather than a broken package.

CI could not have caught it, because every leg runs `uv python install 3.12` and asks
the question on the one interpreter that was always going to say yes.

This script asks it on the others. It runs the real selector
(`voice_wheels.wheels_for_this_interpreter`) inside a target interpreter, which is
the only way to get an honest answer: the selection is made against that
interpreter's own `packaging.tags`, and no amount of inspecting filenames from 3.12
reproduces what 3.14 will accept.

Usage:
    uv run python packaging/check_voice_closure_interpreters.py 3.13 3.14

Exits non-zero naming the interpreter and the distributions it has no wheel for.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Run inside the *target* interpreter. It imports only `voice_wheels` and
#: `packaging`, neither of which needs swe-mux installed, so the check costs one
#: interpreter download rather than a whole environment.
_PROBE = """
import json, sys
sys.path.insert(0, {src!r})
try:
    from swe_mux.voice_wheels import DISTRIBUTIONS, wheels_for_this_interpreter
except Exception as exc:
    print(json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}}))
    raise SystemExit(0)
try:
    selected = wheels_for_this_interpreter()
except LookupError as exc:
    print(json.dumps({{"ok": False, "error": str(exc)}}))
    raise SystemExit(0)
print(json.dumps({{
    "ok": True,
    "version": "%d.%d" % sys.version_info[:2],
    "selected": len(selected),
    "distributions": len(DISTRIBUTIONS),
}}))
"""


def check(version: str) -> tuple[bool, str]:
    """Resolve the closure inside CPython *version*. Returns (ok, message)."""
    source = str(ROOT / "src")
    try:
        completed = subprocess.run(
            ["uv", "run", "--python", version, "--no-project", "--with", "packaging",
             "python", "-c", _PROBE.format(src=source)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            cwd=ROOT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run CPython {version}: {exc}"
    if completed.returncode != 0:
        return False, f"CPython {version} probe failed: {completed.stderr.strip()[:500]}"
    line = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
    try:
        payload = json.loads(line)
    except ValueError:
        return False, f"CPython {version} probe returned no verdict: {completed.stdout[:300]}"
    if not payload.get("ok"):
        return False, f"CPython {version}: {payload.get('error')}"
    return True, (
        f"CPython {payload['version']}: {payload['selected']}/{payload['distributions']} "
        "distributions resolved"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "versions",
        nargs="*",
        default=["3.12", "3.13", "3.14"],
        help="CPython versions to resolve the closure against (default: 3.12 3.13 3.14)",
    )
    args = parser.parse_args(argv)
    failures = 0
    for version in args.versions:
        ok, message = check(version)
        print(("ok   " if ok else "FAIL ") + message)
        failures += 0 if ok else 1
    if failures:
        print(
            f"\n{failures} interpreter(s) cannot load the pinned voice closure. Either "
            "regenerate the pins against a resolution that covers them "
            "(`uv lock --upgrade-package <name>` then "
            "`uv run python packaging/generate_voice_pins.py --write`), or narrow what "
            "swe-mux claims to support.",
            file=sys.stderr,
        )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
