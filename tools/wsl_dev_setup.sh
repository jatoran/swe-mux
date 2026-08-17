#!/usr/bin/env bash
# Bring the WSL Linux checkout up to date and run a Linux daemon against it.
#
# This is the "I want to drive the Linux build myself" path, not an acceptance
# test: `linux_acceptance.sh` and `linux_agent_acceptance.sh` prove the contracts
# headlessly and exit, whereas this leaves a real daemon running that you open in
# a Windows browser.
#
# Usage (from Windows):
#   wsl -d Ubuntu -- bash /mnt/d/PROJECTS/swe-mux/tools/wsl_dev_setup.sh
# Or from inside the distribution:
#   bash ~/swe-mux-linux/tools/wsl_dev_setup.sh
#
# Options:
#   --port N        daemon port (default 8770)
#   --repo PATH     the Linux checkout (default ~/swe-mux-linux)
#   --data PATH     data directory (default ~/.mux-linux-dev)
#   --no-daemon     do steps 1-4 and stop, without starting anything
#   --rebuild       force `npm ci` + `npm run build` even when they look current
#   --detach        start the daemon in the background instead of the foreground
#   --stash-unmatched
#                   also stash modified files that match neither HEAD nor any
#                   incoming commit. Without it those stop the run, which is the
#                   safe default; with it they go into the same recoverable stash.
#
# There is no Linux desktop app to launch. `pystray` and `pywebview` are declared
# `sys_platform == "win32"` in pyproject, and the tray/WebView shell is Windows-only
# by design - so on Linux swe-mux is a headless daemon plus whatever browser you
# point at it. That is the intended shape, not a gap.
#
# Two things this deliberately does NOT do:
#   * `wsl --shutdown` - it stops every distribution on the machine, which takes
#     the docker-desktop distro and every running container with it.
#   * `tailscale up` - it needs interactive auth, and this script must stay
#     unattended. The daemon runs `--local-only` for the same reason the
#     acceptance scripts do (see the launch step).
set -uo pipefail

PORT=8770
REPO="${MUX_LINUX_REPO:-$HOME/swe-mux-linux}"
DATA="${MUX_LINUX_DATA:-$HOME/.mux-linux-dev}"
RUN_DAEMON=yes
FORCE_REBUILD=no
DETACH=no
STASH_UNMATCHED=no
INVOCATION_ARGS="$*"

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --data) DATA="$2"; shift 2 ;;
    --no-daemon) RUN_DAEMON=no; shift ;;
    --rebuild) FORCE_REBUILD=yes; shift ;;
    --detach) DETACH=yes; shift ;;
    --stash-unmatched) STASH_UNMATCHED=yes; shift ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# Everything this prints also lands in a durable log, because the interesting
# failures here are the ones you only understand in hindsight: which files were
# parked, which commit it fast-forwarded to, whether the bundle was rebuilt or
# skipped. Appended rather than truncated, with a run header, so successive runs
# can be compared; trimmed to the last 2000 lines at startup so it cannot grow
# without bound.
SETUP_LOG="${MUX_LINUX_LOG:-$DATA/wsl_dev_setup.log}"
mkdir -p "$(dirname "$SETUP_LOG")" 2>/dev/null
if [ -f "$SETUP_LOG" ] && [ "$(wc -l < "$SETUP_LOG" 2>/dev/null || echo 0)" -gt 2000 ]; then
  tail -2000 "$SETUP_LOG" > "$SETUP_LOG.trim" && mv "$SETUP_LOG.trim" "$SETUP_LOG"
fi
stamp() { date '+%Y-%m-%dT%H:%M:%S%z'; }
log()  { printf '%s %s\n' "$(stamp)" "$*" >> "$SETUP_LOG"; }

say()  { printf '\n=== %s\n' "$*"; log "STEP  $*"; }
info() { printf '    %s\n' "$*"; log "INFO  $*"; }
fail() { printf '\nSETUP-FAIL: %s\n' "$*" >&2; log "FAIL  $*"; exit 1; }

log "==== run start: $0 $INVOCATION_ARGS (pid $$, host $(uname -sr))"

# uv installs to ~/.local/bin, which a login shell adds to PATH but a plain
# `bash script.sh` (how this is invoked through wsl.exe) does not. Without this
# the script reports uv missing on a host where uv is installed and working.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# nvm is a shell *function* sourced from a profile, so a non-interactive
# `bash script.sh` - which is how wsl.exe invokes this - never sees it and silently
# gets whatever /usr/bin/node happens to be. That is not a cosmetic difference here:
# the distro node is 18, and the frontend's own postbuild step
# (frontend/scripts/compress-static.mjs) calls `import.meta.dirname`, which exists
# only from node 20.11. On 18 it is `undefined`, `path.resolve(undefined)` throws,
# and the failure surfaces *after* a successful vite build - which reads as a
# broken bundle rather than a wrong interpreter.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
if [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh" >/dev/null 2>&1
  CURRENT_MAJOR=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
  if [ -z "${CURRENT_MAJOR:-}" ] || [ "$CURRENT_MAJOR" -lt 20 ] 2>/dev/null; then
    WANTED=$(nvm version 20 2>/dev/null)
    if [ -n "$WANTED" ] && [ "$WANTED" != "N/A" ]; then
      nvm use 20 >/dev/null 2>&1 && NVM_SWITCHED="$WANTED"
    fi
  fi
fi

# ---------------------------------------------------------------- preflight ---
say "preflight"

case "$REPO" in
  /mnt/*)
    fail "REPO points at $REPO, which is the Windows checkout over DrvFs.
    Run against a native Linux clone instead: the daemon would otherwise write its
    data through the 9p mount, and node_modules/.venv on DrvFs are slow enough to
    look like hangs."
    ;;
esac

[ -d "$REPO/.git" ] || fail "no git checkout at $REPO (set --repo)"
cd "$REPO" || fail "cannot enter $REPO"

MISSING=
for t in git curl python3 node npm diff; do
  command -v "$t" >/dev/null 2>&1 || MISSING="$MISSING $t"
done
command -v uv >/dev/null 2>&1 || MISSING="$MISSING uv"
[ -z "$MISSING" ] || fail "missing on PATH:$MISSING
    uv:   curl -LsSf https://astral.sh/uv/install.sh | sh
    node: sudo apt install nodejs npm   (or nvm, see the node note below)"

info "repo   $REPO"
info "data   $DATA"
info "port   $PORT"
info "git    $(git --version | awk '{print $3}')"
info "python $(python3 --version 2>&1 | awk '{print $2}')"
info "uv     $(uv --version 2>/dev/null | awk '{print $2}')"

NODE_VER=$(node --version | sed 's/^v//')
NODE_MAJOR=${NODE_VER%%.*}
info "node   $NODE_VER${NVM_SWITCHED:+  (nvm switched from the distro node)}"
info "npm    $(npm --version)"
# A hard requirement, not a warning. Node 20 is what CI pins, what
# @continuity-editor/editor declares in `engines`, and what `import.meta.dirname`
# in the postbuild script needs. Failing here costs seconds; failing at postbuild
# costs a full npm ci plus a vite build first, and looks like a bundling bug.
if [ "$NODE_MAJOR" -lt 20 ]; then
  fail "node $NODE_VER is too old - the frontend build requires node 20+.
    nvm is $( [ -s "$NVM_DIR/nvm.sh" ] && echo "installed but has no v20" || echo "not installed" ).
    Fix with:  nvm install 20   (then re-run; this script picks it up automatically)
    The specific breakage: frontend/scripts/compress-static.mjs uses
    import.meta.dirname, added in node 20.11 and undefined before it."
fi

# ------------------------------------------------- reconcile the working tree ---
# A checkout that has been used to stage Phase 10 files by hand cannot fast-forward:
# the pull would have to overwrite tracked files that are locally modified and
# create untracked files that already exist. Both are refused, correctly.
#
# The rule here is that nothing moves unless it can be *proved* to carry no content
# of its own - only CRLF where the committed blob has LF, or an untracked file that
# already matches the commit about to create it. Anything with a real change stops
# the script with a list and changes nothing. What does move is copied to a
# timestamped backup under .trash/ and stashed rather than deleted, so every step
# is recoverable even if the proof is somehow wrong.
say "reconciling the working tree"

git fetch origin >/dev/null 2>&1 || fail "git fetch from origin failed"
UPSTREAM=origin/master
git rev-parse --verify --quiet "$UPSTREAM" >/dev/null || fail "no $UPSTREAM to pull"

BEHIND=$(git rev-list --count "HEAD..$UPSTREAM" 2>/dev/null || echo 0)
AHEAD=$(git rev-list --count "$UPSTREAM..HEAD" 2>/dev/null || echo 0)
info "local $(git rev-parse --short HEAD) is $BEHIND behind / $AHEAD ahead of $UPSTREAM"
[ "$AHEAD" = "0" ] || fail "this checkout has $AHEAD commit(s) that $UPSTREAM does not.
    Refusing to touch it - reconcile by hand so nothing is lost."

BACKUP="$REPO/.trash/wsl-dev-setup-$(date +%Y%m%d-%H%M%S)"

# Equal after deleting every CR? Then the only difference is line endings.
same_modulo_cr() {  # <path> <git-ref>
  local path="$1" ref="$2"
  git show "$ref:$path" >/dev/null 2>&1 || return 1
  diff -q <(git show "$ref:$path" | tr -d '\r') <(tr -d '\r' < "$path") >/dev/null 2>&1
}

# A file can be "modified" for three quite different reasons, and only the third is
# real work:
#   1. it differs from HEAD only by CR bytes            -> line-ending noise
#   2. it equals some commit in the range about to land -> a stale hand-copy of
#      work that has since been committed, which is how this checkout was used to
#      test Phase 10 before Phase 10 existed as commits
#   3. neither                                          -> stop, it is unique
# Checking only (1) misreads every file in (2) as precious and refuses to run;
# checking against the upstream *tip* alone misses the ones copied from a commit
# partway through the range, which is most of them.
INCOMING_COMMITS=$(git rev-list --reverse "$(git merge-base HEAD "$UPSTREAM")..$UPSTREAM" 2>/dev/null)

matches_any_landed() {  # <path> -> 0 if it equals the blob at some incoming commit
  local path="$1" c
  for c in $INCOMING_COMMITS; do
    if same_modulo_cr "$path" "$c"; then
      LANDED_AT="$c"
      return 0
    fi
  done
  return 1
}

REAL_CHANGES=
CRLF_ONLY=
STALE_COPIES=
while IFS= read -r path; do
  [ -n "$path" ] || continue
  [ -f "$path" ] || continue
  if same_modulo_cr "$path" HEAD; then
    CRLF_ONLY="$CRLF_ONLY$path"$'\n'
  elif matches_any_landed "$path"; then
    STALE_COPIES="$STALE_COPIES$path"$'\n'
    log "STALE $path == $LANDED_AT"
  else
    REAL_CHANGES="$REAL_CHANGES$path"$'\n'
  fi
done < <(git diff --name-only)

# Untracked files that the incoming commits also add - these are what actually
# blocks a fast-forward. Same three-way split as the tracked files, and for the
# same reason: a file copied in from partway through the range matches no *tip*,
# so comparing against `$UPSTREAM` alone leaves behind exactly the files that
# stop the merge.
UNTRACKED_STALE=
UNTRACKED_UNMATCHED=
while IFS= read -r path; do
  [ -n "$path" ] || continue
  [ -d "$path" ] && continue
  [ -f "$path" ] || continue
  # Only files the merge is actually going to create can block it.
  git cat-file -e "$UPSTREAM:$path" 2>/dev/null || continue
  if same_modulo_cr "$path" "$UPSTREAM" || matches_any_landed "$path"; then
    UNTRACKED_STALE="$UNTRACKED_STALE$path"$'\n'
  else
    UNTRACKED_UNMATCHED="$UNTRACKED_UNMATCHED$path"$'\n'
  fi
done < <(git ls-files --others --exclude-standard)

N_CRLF=$(printf '%s' "$CRLF_ONLY" | grep -c . || true)
N_REAL=$(printf '%s' "$REAL_CHANGES" | grep -c . || true)
N_COPY=$(printf '%s' "$STALE_COPIES" | grep -c . || true)
N_STALE=$(printf '%s' "$UNTRACKED_STALE" | grep -c . || true)
N_UNTRACKED_NEW=$(printf '%s' "$UNTRACKED_UNMATCHED" | grep -c . || true)

info "$N_CRLF modified file(s) differ only by CRLF line endings"
info "$N_COPY modified file(s) are stale copies of commits that already landed"
info "$N_STALE untracked file(s) match a commit that is landing"
info "$N_REAL modified file(s) match nothing landed and may be real work"
info "$N_UNTRACKED_NEW untracked file(s) block the merge and match nothing landed"

# An untracked file the merge wants to create, whose content matches nothing in
# the incoming range, is the one case that could be unique work sitting where a
# new file is about to appear. It gets the same opt-in as its tracked equivalent -
# and moving it aside is already non-destructive, so the gate is about the user
# knowing, not about recoverability.
if [ "$N_UNTRACKED_NEW" -gt 0 ]; then
  printf '\n    Untracked, in the way of the merge, matching nothing landed:\n'
  printf '%s' "$UNTRACKED_UNMATCHED" | sed 's/^/      /'
  if [ "$STASH_UNMATCHED" = yes ]; then
    info "--stash-unmatched given: moving them aside into the backup"
    UNTRACKED_STALE="$UNTRACKED_STALE$UNTRACKED_UNMATCHED"
    N_STALE=$((N_STALE + N_UNTRACKED_NEW))
  else
    printf '    Re-run with --stash-unmatched to move them into %s\n' ".trash/"
    fail "$N_UNTRACKED_NEW untracked file(s) block the merge; nothing was changed."
  fi
fi

if [ "$N_REAL" -gt 0 ]; then
  printf '\n    These match neither HEAD nor any incoming commit:\n'
  printf '%s' "$REAL_CHANGES" | sed 's/^/      /'
  if [ "$STASH_UNMATCHED" = yes ]; then
    info "--stash-unmatched given: they go into the same recoverable stash"
    CRLF_ONLY="$CRLF_ONLY$REAL_CHANGES"
    N_CRLF=$((N_CRLF + N_REAL))
  else
    printf '\n    Inspect them with (inside %s):\n' "$REPO"
    printf '      git diff -- <path>\n'
    printf '    If they are not work you want, re-run with --stash-unmatched and\n'
    printf '    they will be stashed alongside the rest rather than lost.\n'
    fail "$N_REAL file(s) could not be proved disposable; nothing was changed."
  fi
fi

# Stale copies ride along with the CRLF set: both are parked the same way, and
# both are recoverable from the same stash.
if [ "$N_COPY" -gt 0 ]; then
  CRLF_ONLY="$CRLF_ONLY$STALE_COPIES"
  N_CRLF=$((N_CRLF + N_COPY))
fi

if [ "$N_CRLF" -gt 0 ] || [ "$N_STALE" -gt 0 ]; then
  mkdir -p "$BACKUP" || fail "cannot create $BACKUP"
  info "backing up to $BACKUP"

  if [ "$N_CRLF" -gt 0 ]; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      mkdir -p "$BACKUP/$(dirname "$path")"
      cp -p "$path" "$BACKUP/$path"
    done <<< "$CRLF_ONLY"
    # Stashed, not discarded. `git checkout --` would be the obvious way to drop a
    # difference that is provably only CR bytes, but the shared git policy forbids
    # discarding working-tree changes and the proof does not earn an exception -
    # a stash is equally effective at unblocking the merge and stays recoverable
    # through git itself, on top of the file copies above.
    STASH_MSG="wsl_dev_setup: $N_CRLF disposable file(s) parked $(stamp)"
    # `--pathspec-file-nul`, not `-z`: `-z` is not a `git stash push` option and the
    # command fails outright. Stderr goes to the log rather than /dev/null, because
    # a swallowed git error here reads as "the stash mysteriously failed".
    if printf '%s' "$CRLF_ONLY" | tr '\n' '\0' \
        | git stash push --pathspec-from-file=- --pathspec-file-nul -m "$STASH_MSG" \
          >>"$SETUP_LOG" 2>&1; then
      info "stashed $N_CRLF CRLF-only file(s) as $(git stash list | head -1 | cut -d: -f1)"
      info "  recover with: git stash pop   (you almost certainly do not want to)"
      info "  drop it with: git stash drop  (yours to run, not this script's)"
    else
      printf '\n    git said:\n'; tail -5 "$SETUP_LOG" | sed 's/^/      /'
      fail "could not stash the disposable files; nothing was changed (see $SETUP_LOG)"
    fi
  fi

  if [ "$N_STALE" -gt 0 ]; then
    while IFS= read -r path; do
      [ -n "$path" ] || continue
      mkdir -p "$BACKUP/$(dirname "$path")"
      mv "$path" "$BACKUP/$path"
    done <<< "$UNTRACKED_STALE"
    info "moved $N_STALE hand-staged file(s) aside so the pull can create them"
  fi
fi

# A literal `D:/` directory shows up when a Windows path reaches a Linux API during
# testing. It is never real, but it is also not this script's to delete, so it is
# parked with everything else.
if [ -d "$REPO/D:" ]; then
  mkdir -p "$BACKUP"
  mv "$REPO/D:" "$BACKUP/D-colon-junk"
  info "parked the stray 'D:' directory (a Windows path that reached a Linux API)"
fi

# ---------------------------------------------------------------- 2. git pull ---
say "pulling"
if [ "$BEHIND" = "0" ]; then
  info "already at $UPSTREAM ($(git rev-parse --short HEAD))"
else
  git merge --ff-only "$UPSTREAM" || fail "fast-forward to $UPSTREAM failed"
  info "now at $(git rev-parse --short HEAD)"
fi

# ---------------------------------------------------------------- 3. uv sync ---
say "syncing python dependencies"
uv sync || fail "uv sync failed"
info "venv python $(uv run python --version 2>&1 | awk '{print $2}')"

# --------------------------------------------------------------- 4. frontend ---
# The hashed assets/ and index.html under src/swe_mux/static are gitignored build
# output. A fresh clone serves no UI at all until this runs, which presents as a
# daemon that is healthy on /api/health and blank in the browser.
say "building the frontend"
cd "$REPO/frontend" || fail "no frontend directory"

NEED_INSTALL=no
[ -d node_modules ] || NEED_INSTALL=yes
[ package-lock.json -nt node_modules ] && NEED_INSTALL=yes
[ "$FORCE_REBUILD" = yes ] && NEED_INSTALL=yes
if [ "$NEED_INSTALL" = yes ]; then
  info "npm ci (this is the slow step on a first run)"
  npm ci || fail "npm ci failed"
else
  info "node_modules is current, skipping npm ci"
fi

INDEX="$REPO/src/swe_mux/static/index.html"
NEED_BUILD=no
[ -f "$INDEX" ] || NEED_BUILD=yes
[ "$FORCE_REBUILD" = yes ] && NEED_BUILD=yes
# Any source newer than the built index means the bundle is stale.
if [ "$NEED_BUILD" = no ] && [ -n "$(find src index.html vite.config.ts package.json -newer "$INDEX" -print -quit 2>/dev/null)" ]; then
  NEED_BUILD=yes
fi
if [ "$NEED_BUILD" = yes ]; then
  info "npm run build"
  npm run build || fail "npm run build failed"
else
  info "bundle is newer than every frontend source, skipping build"
fi
[ -f "$INDEX" ] || fail "the build produced no $INDEX"
info "bundle $(ls -1 "$REPO/src/swe_mux/static/assets"/index-*.js 2>/dev/null | head -1 | xargs -r basename)"

cd "$REPO" || fail "cannot return to $REPO"

if [ "$RUN_DAEMON" = no ]; then
  say "done (--no-daemon)"
  info "start it yourself with:"
  info "  cd $REPO && uv run muxd --host 127.0.0.1 --port $PORT --config $DATA/config.toml --local-only"
  exit 0
fi

# ------------------------------------------------------------- 5. the daemon ---
say "starting the daemon"
mkdir -p "$DATA"

# `--local-only` is not optional and not a preference. Without it the daemon runs
# the startup mobile-voice setup, which retargets the single Tailscale Serve 443
# route at *this* port. A throwaway Linux daemon would then own the address the
# phone uses, and when it exits that address answers nothing - while the real
# Windows daemon keeps working on loopback and never notices it was displaced.
# That exact mistake broke phone access during Phase 10 testing.
#
# `--config` chooses the data directory (Config takes data_dir from the config
# file's parent), so this cannot disturb a real fleet in ~/.mux.
info "http://localhost:$PORT/  (WSL2 forwards Windows localhost into the distro)"
info "data dir $DATA"
info "log      $DATA/daemon.out"

if [ "$DETACH" = yes ]; then
  nohup uv run muxd --host 127.0.0.1 --port "$PORT" \
    --config "$DATA/config.toml" --local-only >"$DATA/daemon.out" 2>&1 &
  DAEMON_PID=$!
  for _ in $(seq 1 90); do
    curl -sS --max-time 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  if curl -sS --max-time 5 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    say "SETUP-PASS"
    info "daemon healthy on pid $DAEMON_PID"
    info "open http://localhost:$PORT/ from Windows"
    info "stop it with: kill $DAEMON_PID"
  else
    tail -30 "$DATA/daemon.out"
    fail "daemon never became healthy"
  fi
else
  info "foreground - Ctrl+C stops it"
  say "SETUP-PASS (handing off to the daemon)"
  exec uv run muxd --host 127.0.0.1 --port "$PORT" \
    --config "$DATA/config.toml" --local-only
fi
