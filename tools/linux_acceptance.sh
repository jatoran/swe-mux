#!/usr/bin/env bash
# Headless-Linux acceptance for Phase 10: the parts a unit test cannot reach.
#
# Runs a real daemon on an isolated port and data dir, registers a real Project,
# spawns a real shell on a real POSIX pseudoterminal, drives input and output
# through the same WebSocket a browser uses, and then SIGKILLs the daemon to
# prove the guardian reaps the session tree.
#
# The unit suite covers the guardian mechanism directly (tests/test_platform_seams.py).
# What only this can prove is the whole stack agreeing: daemon startup, Project
# registration, profile resolution to a POSIX shell, pseudoterminal allocation,
# ownership assignment, the transport, and cleanup - in one process, in that order.
#
# Usage:  REPO=~/swe-mux tools/linux_acceptance.sh
# Prints SMOKE-PASS on success and exits non-zero with SMOKE-FAIL otherwise.
#
# It never touches ~/.mux: the daemon is pointed at its own data directory, so
# running this cannot disturb a real fleet on the same machine.
set -uo pipefail
cd "${REPO:-$HOME/swe-mux-linux}"

PORT="${MUX_ACCEPTANCE_PORT:-18799}"
DATA="${MUX_ACCEPTANCE_DATA:-$HOME/.mux-linux-acceptance}"
rm -rf "$DATA"
mkdir -p "$DATA"

fail() { echo "SMOKE-FAIL: $*"; exit 1; }
api() { curl -sS --max-time 15 "http://127.0.0.1:$PORT$1" "${@:2}"; }

echo "--- starting daemon"
# `--config` is how the data dir is chosen: Config takes data_dir from the config
# file's parent, so an isolated dir keeps this off the real ~/.mux.
uv run muxd --host 127.0.0.1 --port "$PORT" --config "$DATA/config.toml" \
  >"$DATA/daemon.out" 2>&1 &
DAEMON_PID=$!

for _ in $(seq 1 60); do
  if api /api/health >/dev/null 2>&1; then break; fi
  sleep 0.5
done
api /api/health >/dev/null 2>&1 || { tail -30 "$DATA/daemon.out"; fail "daemon never became healthy"; }
echo "daemon healthy (pid $DAEMON_PID)"
api /api/health | head -c 400; echo

echo "--- registering a project"
PROJ_DIR="$PWD"
PROJECT=$(api /api/projects -X POST -H 'Content-Type: application/json' \
  -d "{\"name\": \"linux-smoke\", \"root\": \"$PROJ_DIR\"}")
echo "$PROJECT" | head -c 300; echo
PROJECT_ID=$(echo "$PROJECT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null)
[ -n "$PROJECT_ID" ] || fail "no project id"

echo "--- spawning a shell session"
SESSION=$(api /api/sessions -X POST -H 'Content-Type: application/json' \
  -d "{\"backend\": \"shell\", \"cwd\": \"$PROJ_DIR\", \"project_id\": \"$PROJECT_ID\"}")
echo "$SESSION" | head -c 400; echo
SID=$(echo "$SESSION" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))' 2>/dev/null)
[ -n "$SID" ] || fail "no session id"
PID=$(echo "$SESSION" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("pid",""))' 2>/dev/null)
echo "session $SID pid $PID"

sleep 2

echo "--- process group + guardian"
[ -n "$PID" ] && [ "$PID" != "-1" ] || fail "session has no pid"
CHILD_PGID=$(ps -o pgid= -p "$PID" | tr -d ' ')
DAEMON_PGID=$(ps -o pgid= -p "$DAEMON_PID" | tr -d ' ')
echo "child pgid=$CHILD_PGID daemon pgid=$DAEMON_PGID"
[ "$CHILD_PGID" != "$DAEMON_PGID" ] || fail "child shares the daemon's process group (setsid did not happen)"
GUARDIANS=$(pgrep -f 'swe_mux.posix_guardian' | wc -l)
echo "guardian processes: $GUARDIANS"
[ "$GUARDIANS" -ge 1 ] || fail "no guardian process was started"
# One guardian for the whole reaper tree. Two would mean the nested per-session
# reaper started its own instead of registering with the daemon-wide one, which
# costs a process per session for no extra protection.
[ "$GUARDIANS" -eq 1 ] || fail "expected exactly one guardian, found $GUARDIANS"

echo "--- driving the pty over its WebSocket (the path a browser uses)"
uv run python tools/pty_probe.py "$PORT" "$SID" mux-linux-smoke-marker \
  || fail "the pseudoterminal did not echo and run the command"

echo "--- killing the daemon with SIGKILL (guardian must reap the tree)"
# Every muxd process, not just the shell's job. `uv run muxd` is a launcher whose
# child is the real daemon: killing only the launcher leaves the daemon holding
# the guardian's pipe, and the guardian would then be correct to do nothing -
# which reads as a failure of the guardian rather than of the test.
pgrep -af muxd || true
pkill -9 -f muxd || true
kill -9 "$DAEMON_PID" 2>/dev/null
sleep 1
REAPED=no
for _ in $(seq 1 30); do
  if ! kill -0 "$PID" 2>/dev/null; then REAPED=yes; break; fi
  sleep 0.5
done
[ "$REAPED" = yes ] || { ps -o pid,pgid,cmd -p "$PID"; fail "session tree survived an unclean daemon death"; }
echo "session tree reaped after unclean daemon death"

sleep 1
LEFTOVER=$(pgrep -f 'swe_mux.posix_guardian' | wc -l)
[ "$LEFTOVER" = "0" ] || { pgrep -af 'swe_mux.posix_guardian'; fail "guardian did not exit after doing its job"; }
echo "guardian exited cleanly"

echo "SMOKE-PASS"
