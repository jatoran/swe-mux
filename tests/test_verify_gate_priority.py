"""The verification gate and the renderer suite yield the CPU to the live fleet.

`.worktree-verify` saturates every core by design (`-n auto` pytest, tsc, the
node suites), and several worktrees legitimately run it at once - measured
2026-08-30, three concurrent gates plus a Playwright renderer suite held all 32
logical CPUs at ~85% and delayed typed input across every live session. Both
entry points therefore drop themselves to below-normal priority before spawning
anything, so every child inherits it and the interactive fleet is scheduled
first.

The lowering is deliberately best-effort - a gate that cannot lower itself must
still verify - and that is exactly why it needs tests: a quoting typo in the
PowerShell one-liner or a renamed constant would degrade to "priority:
unchanged" forever, and nothing else in the gate would notice. The contract
half pins the blocks in place and keeps the two opt-outs the same spelling; the
behavioral half executes the *shipped bytes* against a scratch child and
asserts the OS honored them, so the tested command and the running command
cannot drift apart.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The one opt-out both entry points honor. A second spelling would mean an
#: operator who set one still had the other stealing the machine.
OPT_OUT = "MUX_KEEP_PRIORITY"


def _gate_text() -> str:
    return (REPO_ROOT / ".worktree-verify").read_text(encoding="utf-8")


def _renderer_config_text() -> str:
    return (REPO_ROOT / "frontend" / "playwright.renderer.config.ts").read_text(
        encoding="utf-8"
    )


def test_the_gate_lowers_its_priority_before_its_first_expensive_step() -> None:
    """The block has to run before `step "pytest"`: lowering after the workers
    have spawned lowers nothing, because Windows only passes the class down at
    child creation."""
    text = _gate_text()
    for needle in (OPT_OUT, "BelowNormal", "renice"):
        assert needle in text, f".worktree-verify lost its {needle!r} half"
    assert "ps -o nice=" in text, "the POSIX branch no longer verifies its result"
    assert text.index(OPT_OUT) < text.index('step "pytest"'), (
        "the priority block must precede the first spawned step"
    )


def test_the_renderer_suite_lowers_its_priority_with_the_same_opt_out() -> None:
    """The renderer suite is the other core-saturating entry point agents run
    directly (`npx playwright test --config playwright.renderer.config.ts`), so
    the lowering lives in the config - the one file every invocation loads -
    rather than in an npm script a direct invocation would bypass."""
    text = _renderer_config_text()
    assert "PRIORITY_BELOW_NORMAL" in text, (
        "playwright.renderer.config.ts no longer lowers its priority"
    )
    assert OPT_OUT in text, "the renderer suite lost the opt-out"
    assert text.index("PRIORITY_BELOW_NORMAL") < text.index("defineConfig({"), (
        "the priority block must run at config load, before anything is spawned"
    )


def _scratch_child() -> subprocess.Popen[bytes]:
    # No pipes on purpose: a piped transport left open when the test ends is the
    # finalizer failure mode CLAUDE.md § Verification documents.
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


@pytest.mark.skipif(sys.platform != "win32", reason="exercises the Windows branch")
def test_the_gates_own_powershell_command_actually_lowers_a_process() -> None:
    """Runs the exact command shipped in `.worktree-verify` (extracted, not
    retyped) against a scratch child. The gate swallows this command's failure
    by design, so a broken edit to it is invisible everywhere but here."""
    import psutil

    match = re.search(
        r'powershell\.exe -NoProfile -Command "(.+?)" >', _gate_text()
    )
    assert match, ".worktree-verify no longer carries the PowerShell lowering command"
    child = _scratch_child()
    try:
        command = match.group(1).replace("$winpid", str(child.pid))
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=True,
            timeout=60,
        )
        assert psutil.Process(child.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
    finally:
        child.kill()
        child.wait(timeout=30)


@pytest.mark.skipif(sys.platform == "win32", reason="exercises the POSIX branch")
def test_the_gates_own_renice_invocation_actually_lowers_a_process() -> None:
    """Same shipped-bytes rule for the POSIX branch: the niceness value is read
    out of the script so a changed number stays tested."""
    match = re.search(r"renice (\d+) -p", _gate_text())
    assert match, ".worktree-verify no longer carries the renice lowering command"
    niceness = int(match.group(1))
    if os.getpriority(os.PRIO_PROCESS, 0) > niceness:
        pytest.skip(
            "this process already runs below the gate's target niceness, so the"
            " lowering cannot be demonstrated from here"
        )
    child = _scratch_child()
    try:
        subprocess.run(
            ["renice", "-n", str(niceness), "-p", str(child.pid)],
            check=True,
            timeout=60,
        )
        observed = os.getpriority(os.PRIO_PROCESS, child.pid)
        if sys.platform == "darwin" and observed != niceness:
            pytest.skip(
                "the macOS hosted runner accepts renice but leaves the priority"
                " unchanged; the gate verifies that postcondition and reports it"
            )
        assert observed == niceness
    finally:
        child.kill()
        child.wait(timeout=30)
