#!/usr/bin/env bash
# Run the suite on Linux inside a container, from a Windows host.
#
# A second Linux target beside a WSL checkout, and deliberately *not* WSL: it also
# confirms the port does not quietly depend on WSL-isms (interop PATH entries,
# DrvFs mounts, a Windows-side home). It is the closest local equivalent of the
# `platform` CI job, and it needs nothing installed but Docker.
#
# Usage, from the repository root:
#   docker run --rm -v "$PWD:/repo:ro" python:3.12-slim bash /repo/tools/linux_container_verify.sh
#
# The mount is read-only and the tree is copied in, because the suite writes under
# tests/ and the host checkout must not be touched by a verification run.
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
