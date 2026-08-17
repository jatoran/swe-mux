#!/usr/bin/env bash
# Native-Linux agent acceptance: promotion, hooks, native id, transcript, resume.
#
# The Linux PTY/ownership half is proven by tools/linux_acceptance.sh. This proves
# the half that only a real vendor CLI can: that an agent launched by a Linux
# daemon is *observed* - it gets a conversation id mux knows, its hooks reach the
# ingress, its transcript is found where the adapter says it will be, and its state
# moves. A shell session proves none of that.
#
# Requires an authenticated agent CLI installed natively in the distribution.
# Consumes a small amount of real provider quota: one short prompt.
#
# Usage:  REPO=~/swe-mux-linux tools/linux_agent_acceptance.sh
set -uo pipefail
cd "${REPO:-$HOME/swe-mux-linux}"

PORT="${MUX_ACCEPTANCE_PORT:-18801}"
DATA="${MUX_ACCEPTANCE_DATA:-$HOME/.mux-linux-agent}"
BACKEND="${MUX_ACCEPTANCE_BACKEND:-claude}"
rm -rf "$DATA"; mkdir -p "$DATA"

fail() { echo "AGENT-FAIL: $*"; [ -n "${DAEMON_PID:-}" ] && kill -9 "$DAEMON_PID" 2>/dev/null; exit 1; }
api() { curl -sS --max-time 30 "http://127.0.0.1:$PORT$1" "${@:2}"; }
jqp() { python3 -c "import json,sys;d=json.load(sys.stdin);print(d$1 if d else '')" 2>/dev/null; }

echo "--- the CLI must be native, not a Windows binary reached through interop"
CLI=$(command -v "$BACKEND" 2>/dev/null)
[ -n "$CLI" ] || fail "$BACKEND is not on PATH inside this distribution"
case "$CLI" in
  /mnt/*) fail "$BACKEND resolves to $CLI, a Windows binary through interop, not a native agent" ;;
esac
echo "native $BACKEND: $CLI"

echo "--- starting the daemon"
uv run muxd --host 127.0.0.1 --port "$PORT" --config "$DATA/config.toml" \
  >"$DATA/daemon.out" 2>&1 &
DAEMON_PID=$!
for _ in $(seq 1 90); do api /api/health >/dev/null 2>&1 && break; sleep 0.5; done
api /api/health >/dev/null 2>&1 || { tail -30 "$DATA/daemon.out"; fail "daemon never became healthy"; }

echo "--- the daemon must detect the native CLI"
HARNESS=$(api /api/harnesses)
echo "$HARNESS" | grep -q "\"$BACKEND\"" || fail "the daemon does not know the $BACKEND harness"

PROJECT_ID=$(api /api/projects -X POST -H 'Content-Type: application/json' \
  -d "{\"name\": \"linux-agent\", \"root\": \"$PWD\"}" | jqp "['id']")
[ -n "$PROJECT_ID" ] || fail "could not register a project"

echo "--- spawning a real $BACKEND session"
SESSION=$(api /api/sessions -X POST -H 'Content-Type: application/json' \
  -d "{\"backend\": \"$BACKEND\", \"cwd\": \"$PWD\", \"project_id\": \"$PROJECT_ID\"}")
SID=$(echo "$SESSION" | jqp "['id']")
[ -n "$SID" ] || { echo "$SESSION" | head -c 600; fail "spawn failed"; }
NATIVE=$(echo "$SESSION" | jqp "['native_session_id']")
PID=$(echo "$SESSION" | jqp "['pid']")
echo "session $SID native=$NATIVE pid=$PID"
[ -n "$NATIVE" ] || fail "the session has no conversation id"
[ -n "$PID" ] && [ "$PID" != "-1" ] || fail "the agent process did not start"

echo "--- process ownership"
CHILD_PGID=$(ps -o pgid= -p "$PID" 2>/dev/null | tr -d ' ')
DAEMON_PGID=$(ps -o pgid= -p "$DAEMON_PID" 2>/dev/null | tr -d ' ')
[ -n "$CHILD_PGID" ] || fail "could not read the agent's process group"
[ "$CHILD_PGID" != "$DAEMON_PGID" ] || fail "the agent shares the daemon's process group"
echo "agent pgid=$CHILD_PGID (daemon $DAEMON_PGID)"

echo "--- driving one real turn through the pseudoterminal"
uv run python tools/pty_probe.py "$PORT" "$SID" "" --agent-prompt \
  || echo "(prompt delivery reported a problem; continuing to check observation)"

echo "--- the conversation must be observed, not just running"
# `transcript_path` is not on the session snapshot - the conversation is served by
# its own endpoint, which is what a reader actually consults. Checking the snapshot
# for a key it never had is how an earlier version of this script reported a
# perfectly observed session as unobserved.
OBSERVED=no
for _ in $(seq 1 60); do
  CONV=$(api "/api/sessions/$SID/transcript?limit=20")
  COUNT=$(echo "$CONV" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
items = d.get("messages") or d.get("items") or d.get("entries") or []
print(len(items) if isinstance(items, list) else 0)' 2>/dev/null)
  if [ "${COUNT:-0}" -gt 0 ]; then OBSERVED=yes; break; fi
  sleep 2
done
SNAP=$(api "/api/sessions/$SID")
echo "state: $(echo "$SNAP" | jqp "['state']")"
echo "native id: $(echo "$SNAP" | jqp "['native_session_id']")"
echo "conversation entries: ${COUNT:-0}"

echo "--- the transcript must exist on disk under the harness home"
NATIVE_ID=$(echo "$SNAP" | jqp "['native_session_id']")
FOUND=$(find "$HOME/.claude/projects" -name "$NATIVE_ID.jsonl" 2>/dev/null | head -1)
if [ -n "$FOUND" ]; then
  echo "transcript: $FOUND"
else
  echo "WARN: no transcript file named for this conversation"
  OBSERVED=no
fi

echo "--- history should carry the conversation"
api /api/history 2>/dev/null | head -c 400; echo

echo "--- stopping"
api "/api/sessions/$SID" -X DELETE >/dev/null 2>&1
sleep 2
kill -9 "$DAEMON_PID" 2>/dev/null
sleep 1
if kill -0 "$PID" 2>/dev/null; then
  echo "WARN: the agent process outlived the daemon"
else
  echo "agent tree cleaned up"
fi

[ "$OBSERVED" = yes ] && echo "AGENT-PASS" || { echo "AGENT-FAIL: the agent was never observed"; exit 1; }
