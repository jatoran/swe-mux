#!/usr/bin/env bash
# Run the suite on Linux inside a container, from a Windows host.
#
# A second Linux target beside a WSL checkout, and a much cleaner one: no interop
# PATH, no DrvFs mounts, no Windows-side home. That difference is not cosmetic -
# it caught a test that only passed in WSL because `pwsh.exe` was resolvable
# through interop, which is exactly the kind of accidental pass a single Linux
# target hides. It needs nothing installed but Docker.
#
# It is *not* a non-WSL kernel, though. Docker Desktop runs containers inside the
# WSL2 VM, so `/proc/sys/kernel/osrelease` says `microsoft` and
# `host_platform.running_under_wsl()` reports True in here. That is the honest
# answer about the kernel and it is inert in a container (there are no `/mnt`
# drive mounts and no `.exe` on PATH), but do not read a pass here as proof that
# the port is free of WSL assumptions.
#
# Usage, from the repository root:
#   docker run --rm -v "$PWD:/repo:ro" python:3.12-slim bash /repo/tools/linux_container_verify.sh
#
# The mount is read-only and the tree is copied in, because the suite writes under
# tests/ and the host checkout must not be touched by a verification run.
#
# The container also has its own **PID namespace**, and that has already produced a
# false green here. It starts at pid 1 with a handful of processes, so a small pid a
# test names is absent in here and present on a CI runner, which is a whole VM with
# kernel threads at pids 2..~20. `tests/test_processes_phase4.py` named pid 10, whose
# absence on Windows was the only reason a session fake never reached the process
# walk; the first public Linux CI run failed on it and a plain run of this script
# passed, because in here pid 10 does not exist either. So for anything near process
# discovery, share the host's namespace:
#
#   docker run --rm --pid=host -v "$PWD:/repo:ro" python:3.12-slim bash /repo/tools/linux_container_verify.sh
set -euo pipefail

apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq git >/dev/null 2>&1

# Copy only what the suite needs. Measured on the development host: `dist/` is
# 1.1G of PyInstaller bundle and `.git` another 290M, and dragging both across a
# Windows bind mount took over ten minutes before the first test ran. The excludes
# are what makes this usable rather than an optimization. Host build products are
# also the wrong platform's - a Windows `.venv` under a Linux interpreter fails in
# confusing ways - so they never come along either.
mkdir -p /work
tar -C /repo \
  --exclude=./dist \
  --exclude=./.git \
  --exclude=./.venv \
  --exclude=./node_modules \
  --exclude=./frontend/node_modules \
  --exclude=./.tmp-orca \
  --exclude=./.claude \
  --exclude=./.runtime \
  --exclude=__pycache__ \
  -cf - . | tar -C /work -xf -
cd /work

pip install --quiet uv
# The mount and the container filesystem are different devices, so uv cannot
# hardlink; saying so up front keeps the warning out of the output.
export UV_LINK_MODE=copy
uv sync --quiet

echo "=== platform ==="
uv run python -c "from swe_mux.host_platform import platform_key, running_under_wsl; print('platform:', platform_key(), '| under wsl:', running_under_wsl())"

echo "=== package imports ==="
uv run python -c "import swe_mux.server; print('server import ok')"

echo "=== cli runs ==="
uv run mux --help >/dev/null && echo "mux --help ok"

echo "=== pytest ==="
uv run pytest tests -q -p no:cacheprovider \
  -m "not live_agent and not live_subagent and not live_telemetry and not live_quota and not live_automations and not live_mcp" \
  2>&1 | tail -12
