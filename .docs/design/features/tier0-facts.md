# Tier 0 deterministic facts

## What it is

Durable, no-model capture of deterministic facts about agent sessions (file writes,
commands, tests, git, tools) with pointers back to the immutable event log. The
substrate that provenance, loop detection, and declared-vs-verified read from. Per-project
opt-in and gated (`automation-enablement.md`). Vision: `../../development/CONTROL_PLANE_ROADMAP.md` §5.

## Key concepts

- **Fact**: one deterministic observation — `kind`, `target`, `content_hash`,
  `fingerprint`, bounded `detail`, and a `source_seq` pointer into the event log / raw store.
- **Source pointer**: `source_seq` (event sequence) ties every fact to its origin so it can
  be rehydrated; a summary is never the only copy. It is **best-effort**, not a guarantee:
  the event log is capped by row count while facts are bounded by age, so on a busy fleet an
  older fact's pointer can outlive the event it names. Anything that must survive that
  window reads the fact's own bounded `detail` (or the transcript-anchored `source_ref`),
  which is why `detail` is bounded per value rather than by slicing the serialization.
- **Ownership**: every fact carries the `agent_run_id` and `project_id` it belongs to,
  resolved at capture time. `session_id` alone cannot answer per-run questions — a session
  is resumed, promoted and branched across many runs.
- **Fingerprint**: canonical action signature (`event_type`, `kind`, tool, casefolded
  `target`, exit class, `content_hash`, progress state) for loop detection. Identical
  repeated edits share a fingerprint (loop signal); changed content differs (progress).
  Strips volatile detail. The progress-state component is the failing-test set for test
  results and the working-tree hash for git facts, so "the same action against the same
  unchanged state" is one fingerprint rather than one per exit class.
- **Content hash**: computed at the adapter boundary, never by reading a file back off
  disk — race-free. On the write side it is the exact bytes the agent wrote
  (`tool_call_evidence`); on the read/result side it is the exact bytes the agent saw
  (`tool_result_evidence`, hashed before the payload's `detail` is bounded). The two sides
  hash different representations — a `Read` result is the CLI's rendering of a file, not the
  file — so they are **not** joinable by equality; use `target` plus time order for
  write→read lineage, and hash equality only within one side.
  The one cross-source equality that *is* legitimate is a whole-file write against a
  committed Git blob's bytes (`design/features/git.md`, contributor attribution): both are
  the file, so their SHA-256 digests match.
  It holds only for a write whose hash covers whole-file content: an edit tool hashes the
  replacement fragment, so many writes are matched by path and time instead.
  A codex write is the case that needs the result side: its `apply_patch` call runs through
  the shell/exec tool and classifies as a `command`, so the written path and a hash of the
  applied file contents appear only on the `file_write_result` fact.
  A consumer that reads result facts must therefore treat them as content evidence alone —
  every other harness puts a hash of its result *message* there.
  A Git object id is never comparable with a content hash — it is SHA-1 over a
  `blob <len>\0` header, not a digest of the bytes.
- **Test outcome**: a structured `{framework, passed, failed, errors, skipped,
  failing_tests[]}` parsed from the full tool output at the adapter boundary (pytest,
  jest/vitest, go, cargo, unittest). It is parsed there, not from the fact's bounded
  `detail`, because every runner prints its verdict last.

## Data model

- Table `tier0_facts` on the shared WAL `mux.db`:
  `id, session_id, agent_run_id, project_id, kind, target, content_hash, fingerprint,
  detail_json, source_seq, source_ref, created_at`. Indexed by session/time, kind/time,
  content_hash, (session, fingerprint), and the two shapes the consumers query —
  (agent_run_id, time) and (project_id, time). Command text is never stored beyond bounded
  detail.
- `kind` derives from the event: tool classification (`file_write | file_read | command |
  test | tool`), the matching `_result` variant for the outcome of each
  (`command_result`, `file_write_result`, …), `test_result` for any result carrying a parsed
  test outcome, plus `git` and `compaction`.
- `detail_json` is bounded to 4 KiB by truncating payload **values** and, if still over
  budget, dropping whole keys widest-first (recorded as `_truncated` / `_dropped_keys`);
  structural keys (`tool`, `success`, `exit_code`, `test_outcome`, `scope`, `call_id`)
  are never dropped. The bound is never applied to the serialization itself — that
  produced rows `json.loads` could not read, and the fact was discarded whole.

## Operations

- Consumes normalized events (`tool_use`, `tool_result`, `git_changed`,
  `context_compacted`); everything else is ignored so capture stays cheap.
- Capture runs off the event loop on a single-worker executor behind the shared SQLite
  operation coordinator; failures can never break the event loop or a terminal.
- A capture failure is counted, stamped and logged (first occurrence, then rate-limited),
  never silently swallowed: for a durable-evidence substrate, a silent drop is
  indistinguishable from "nothing happened".
- The adapter emits a normalized `target` + parse-time `content_hash` on `tool_use`
  (`observation.tool_call_evidence`) and correlates the same `target` onto the matching
  `tool_result` by call id; Tier 0 prefers those, falling back to key-scan.
- A codex write is a special case: its path lives in an `*** Add/Update/Delete File:` patch
  header rather than a key, and it applies through an exec wrapper whose `patch_apply_end` result
  carries a different call id than the tool call that held the patch. So the target and content
  are read from that result's `changes` map (`{path: {type, content}}`), which keeps a codex
  file write traceable instead of recording it with no target (the gap the codex live canary
  exposed).
- `git_changed` carries the commit `head` and a `dirty_hash` of the working-tree change set
  (`git_monitor.GitEvidence`), so a fact records which tree it was produced against.
- Gated per session: capture only for sessions whose owning Project opted `tier0` in,
  resolved off the loop with a short TTL cache. The gate resolver returns the session's
  `Tier0Context` (run + project) rather than a bare bool.
- Retention: bounded by age (`prune`), reusing the process-evidence retention window. Run
  hourly by the daemon's supervised retention loop, never on the startup path.

## API surface

- No dedicated route. Capture health (captured/dropped counts, last error) is reported under
  `tier0_capture` in `GET /api/diagnostics/background`.

## Configuration

- Enabled via `automations = { raw_store = true, tier0 = true }` in
  `<project>/.swe-mux/config.toml` (see `automation-enablement.md`).

## Key files

- Store + extraction + gated consumer: `src/swe_mux/tier0_store.py`
- Adapter-boundary evidence: `src/swe_mux/observation.py` (`tool_call_evidence`,
  `tool_result_evidence`, `parse_test_outcome`, `bounded_detail`)
- Git commit/tree evidence: `src/swe_mux/git_monitor.py` (`GitEvidence`, `read_git_reading`)
- Construction, prune, gate resolver, lifecycle: `src/swe_mux/server.py`

## Relates to

- `automation-enablement.md` — the opt-in DAG that gates capture.
- `deterministic-consumers.md` — the model-free detectors that query these facts.
- `../technical/backend/sqlite.md` — shared `mux.db` operation-coordinator rules.
