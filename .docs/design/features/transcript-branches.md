# Transcript branches

## What it is

- A Claude transcript is an append-only **DAG**, not a list of turns.
  `parentUuid` names the record a record answers.
  A retry, a `/rewind`, or a resend after a failed request appends a **new sibling** under the same parent; the previous attempt stays in the file forever.
- The **live branch** is the ancestry of the newest record.
  The file is append-only, so the last record is by construction on the branch still being written, and the ancestors of that record are exactly the nodes whose subtree contains it.
- An **abandoned** record is one the live branch does not reach.
  It was never sent to the provider and the CLI stops showing it the moment the conversation branches away.
- Every reader in mux read these files in file order until this was built, so a prompt resent eight times through an outage was eight prompts to the Transcript tab, to history search, and to every consumer of the indexing parse.
  Measured across 60 recent transcripts of one machine (2026-08-18): 37 held off-branch records; 236 records and 36 conversational messages in total.

## Invariants

- **The last record in the read window is the leaf.**
  Not `leafUuid`, which appears only in `last-prompt` checkpoints, points at the record before it, and is written on abandoned branches too.
- **Ancestry alone is not the live set, and treating it as such is the worse bug.**
  Three shapes hang off an ancestor rather than continuing the chain, and all three are live conversation:
  - A **parallel tool batch** writes one assistant record per call, and parents each `tool_result` to the record whose call it answers, so every result but the last is a sibling.
    A naive parent-chain walk drops the first result of every batch.
  - **Subagent** (`isSidechain`) turns hang off their spawning record.
  - **Attachment** records continue whichever record they were written for.
  Results are re-admitted by matching their `tool_use_id` against the live branch's own calls, so an abandoned branch's results can never be adopted by a live parent.
- **A transcript with no record linkage is read exactly as before.**
  No `uuid` on any record is a declared "cannot answer", not "nothing branched": every record stays live.
  This is what keeps older transcripts and the other dialects unchanged.
- **The two projections differ on purpose.**
  - The **indexing projection** (`transcript_view.parse_transcript`) drops abandoned records.
    Everything downstream of it - history indexing and search, automation rules, the scan timeline, fleet intelligence, the MCP transcript surface - treats what it gets as things that were said.
  - The **reader projection** (`transcript_view.conversation_view`) keeps them and marks them `abandoned`.
    A person looking at their own conversation is entitled to see that a branch happened; silently deleting the seven identical prompts an outage produced leaves them unable to tell a retry storm from a reader that is mangling the file.
- **Tool calls never cross a branch boundary.**
  An abandoned turn's calls belong to the branch that made them and are discarded rather than credited to whichever live message follows.
- **A branch boundary is never a streaming split.**
  Two attempts at one turn stay two messages; merging them produces one message that says both things with no seam to see.
- **An abandoned call is not an open call.**
  `_claude_open_tool_calls` ignores `tool_use` blocks on abandoned records while still reading their results.
  An outage or interrupt landing between a call and its result otherwise leaves that id open for the rest of the file, marks every later cut point illegal, and retires branching for the life of the conversation - silently, because an illegal cut point renders as an ordinary unavailable one.
- **Abandoned messages are not offered as fork points.**
  Cutting at one would write a loadable fork, but the picker names a moment in the conversation, and a retried session would name the same moment eight identical times.
  The CLI's own resume is the way back to an abandoned branch.

## Bounded reads

The branch test runs against whatever window a reader was handed, and degrades in the safe direction.

- A **whole file** or a **trailing slice** contains the real leaf, so the classification is exact.
- A **head slice** (the paging reader, `transcript_message_page`) resolves every branch that closes inside the window and does not notice one that does not.
  Failing to mark is a record shown that could have been folded; the opposite would be conversation hidden from its reader.
- The one case that misclassifies is a conversation resumed back **into** a previously abandoned branch, which appends past that branch and makes an earlier sibling live again.
  Reaching it requires `claude --resume` on the older branch rather than any in-session rewind.

`transcript_time_summary` reads disjoint head and tail slices with no connecting linkage and therefore does not classify at all.
It is unaffected: first and last conversational timestamps are the same whichever branch carried them.

## Surfaces

| Surface | Abandoned turns |
| --- | --- |
| Drawer Transcript tab (`GET /sessions/{id}/transcript`) | Kept, marked `abandoned`, folded per contiguous run; `abandoned_messages` counts them |
| History transcript (`GET /history/{id}/transcript`) | Absent; `abandoned_messages` reports how many |
| History search / FTS index | Absent |
| MCP `read_transcript` | Absent; `abandoned_messages` counts them per page |
| Branch points (`GET /sessions/{id}/branch-points`) | Not offered |
| Diagnostic bundle transcript slices | Absent; `abandoned_messages` reports how many |

The drawer folds each contiguous run behind one row, collapsed, and does not remember the fold between mounts: an abandoned branch is something to check once, not a preference.
Under a search the fold is dropped and abandoned messages stand on their own, because the reader asked for every match and a fold nothing reveals them through would hide one.
Copy-all and select-all take the live messages only: the fold exists so the reader knows the retry happened, not so it can be pasted into a bug report as though it were said.

## Migration

`TRANSCRIPT_PARSER_VERSION` is `4`.
The version is part of the transcript index watermark, so every indexed conversation reparses once on its next touch and the duplicate rows an outage produced leave `history_messages` then.
Nothing rewrites a native transcript at any point.

## Key files

- `src/swe_mux/transcript_view.py` - `_claude_live_uuids`, `_mark_abandoned_records`, and the two projections
- `src/swe_mux/server.py` - the session, history, and diagnostic-bundle payloads
- `src/swe_mux/mcp.py` - `read_transcript`
- `frontend/src/transcriptView.ts` - `groupTranscriptMessages`, `transcriptLiveMessages`
- `frontend/src/TranscriptTab.tsx` - the fold
- `tests/test_transcript_branches.py`
