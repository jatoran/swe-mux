# Agent skill delivery: how agents learn what swe-mux offers them

## What it is

swe-mux ships one agent skill - `src/swe_mux/assets/skills/swe-mux/SKILL.md` - and the
machinery that puts it where each harness's CLI reads skills.
The skill is the capability-discovery half of the agent surface: MCP tools carry their own
schemas into an MCP-capable session, and the skill is how a session learns the surface exists
at all, what the in-session environment check is, and where the current command contract lives.
`swemux --skill` prints the embedded copy; `swemux install-skill` writes it into skill roots
explicitly; nothing installs it silently.

## Key concepts

- **The skill is embedded, not fetched.** The wheel, the desktop app bundle, and the CLI
  bundle all carry `assets/skills/`, so `swemux --skill` always prints the copy matching the
  running release and guidance cannot drift from the binary.
  There is no registry, no `npx`, and no network anywhere in this feature.
- **The skill refuses to enumerate commands.** Its body teaches exactly three things: the
  `MUX_SESSION_ID` environment check (first, so an agent outside swe-mux stops before touching
  anyone's sessions), "prefer the mux MCP tools when they are visible", and "otherwise
  `swemux --help` is the authority - read ids out of returned JSON".
  A skill that listed subcommands would be stale by the next release; this one cannot be.
  `tests/test_skill_install.py::test_the_shipped_skill_teaches_no_commands` pins the shape:
  no fenced block in the skill invokes `swemux`.
- **The CLI is named but never made the authority path.** The loopback API is unauthenticated
  today (`ROADMAP.md` Phase 23 W1), so the skill explicitly marks session-acting commands
  (send, kill, spawn, reload, update) as operator surface and tells the agent not to run them
  unsolicited.
  The skill may widen only after W1/W2 land an authenticated agent mode.
- **One name everywhere.** Claude keys a skill by its directory name and Codex by its
  frontmatter `name:`, so the directory and the frontmatter agree (`swe-mux`) and the
  invocation is `/swe-mux`, `$swe-mux`, or `/skill:swe-mux` per the descriptor's
  `skill_invocation_prefix`.

## Where it is written, and why those directories

The roots are the ones `agent_skills.py` verified against the real CLIs
(Claude Code 2.1.220, Codex 0.145); the installer targets the same directories the scanner
reads, and `test_the_scanner_finds_what_the_installer_wrote` closes that loop offline.

| Scope | Path | Read by |
|---|---|---|
| project | `<checkout>/.claude/skills/swe-mux/` | claude, omp |
| project | `<checkout>/.agents/skills/swe-mux/` | codex, pi, omp, opencode |
| global | `<claude home>/skills/swe-mux/` | claude, omp |
| global | `<CODEX_HOME>/skills/swe-mux/` | codex |
| global | `~/.agents/skills/swe-mux/` | pi, omp, opencode |

Two facts make this table the whole answer rather than a per-harness matrix:
every non-Claude harness reads the shared project `.agents/skills/` root, and Codex is the
one harness that ignores `~/.agents/skills` at user scope.
None of the non-Claude CLIs accepts a per-session skills directory by flag, env var, or
config key (`codex plugin add` is marketplace-only; redirecting `CODEX_HOME` or
`PI_CODING_AGENT_DIR` would move auth wholesale), so writing into these trees is genuinely
the only route to them - which is why every write is an explicit command.
Claude alone has a per-session route (`--plugin-dir`), which is what the Project-scoped
automatic delivery uses instead of any tree write.

## The boundary: global writes are disclosed, never silent

`~/.claude/skills/` affects every agent that user ever runs, including outside swe-mux.
So `swemux install-skill --global` without `--yes` prints the exact paths it would touch and
stops - a successful preview, exit 0 - and writes only under `--yes`.
Project scope (`--project DIR`, default the current directory) proceeds directly: the files
land visibly in the checkout, show in `git status`, and are the user's to commit or ignore.

Removal (`--remove`) takes back only files whose frontmatter carries the
`managed-by: swe-mux` marker this installer writes.
A user-authored skill sharing the directory name is reported and left in place; declining is
the command working, not failing, and exits 0.
A write or unlink the filesystem refused exits 1 (`EXIT_LOCAL_FAIL`).

Install is write-if-changed for the same reason `adapters/claude.py` writes its MCP config
that way: replacing identical bytes moves mtime, and mtime is what `agent_skills.py` uses to
say a skill appeared after a session started.

## Key files

- `src/swe_mux/assets/skills/swe-mux/SKILL.md` - the skill; frontmatter carries the
  over-firing guard and the `managed-by` marker.
- `src/swe_mux/skill_install.py` - roots, install/remove, the recognition rule.
- `src/swe_mux/cli.py` - `--skill` (top-level flag; a complete invocation with no
  subcommand) and `install-skill`.
- `packaging/swe_mux_cli.spec` - the `datas` entry that carries `assets/skills/` into the
  frozen CLI bundle; `packaging/build_desktop.py::smoke_cli_bundle` executes `--skill`
  against the built tree so a dropped entry fails the build rather than shipping a traceback.
- `tests/test_skill_install.py` - the contracts above.

## Relates to

- `design/features/mux-mcp.md` - the MCP surface the skill points at; MCP is transport, not
  authority, and the skill inherits that rule.
- `development/ROADMAP.md` Phase 23 - W5 (this feature), W1/W2 (the authority work that must
  land before the CLI may become an agent transport).
- `design/features/agent-context.md` / `agent_skills.py` - the discovery scanner whose root
  table this feature writes into.
