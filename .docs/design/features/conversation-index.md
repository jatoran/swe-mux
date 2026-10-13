# Conversation index

## What it is

- One incremental reading of each conversation, shared by every surface that reads a conversation for a person: the drawer's Transcript tab, branch points, read-aloud, Copy reply, and History.
- A **reading** (`ConversationSnapshot`) is the conversation's merged reader messages as of one refresh, plus what each surface derives from them: positions by `message_id`, per-message change stamps, the live-branch messages and open tool calls a fork is cut by, and the indexing projection History stores.
- The index keeps each native record reduced to the fields the projections in `transcript_view.py` read, and runs those same projections over them.
  A full read and the index therefore cannot disagree about what a conversation says; `tests/test_conversation_index.py` compares the two for Claude, Codex (rollback, phases, inherited history) and pi.

## Why

- Every reader used to re-decode the whole conversation, up to a 64 MiB tail, on every ask.
  The parse caches in front of that were keyed on the file's watermark, which a working agent changes on every append, so for exactly the sessions people watch they never hit.
- Measured on the development host before the change: a 49 MB Claude transcript cost up to 2.4 s per Transcript tab open under daemon load, two opens in nine hit the route's 5 s timeout and read as "unreadable", and the Resume button waited on a whole-conversation parse.
- Tool inputs were 88-96% of the reader payload's bytes, for a disclosure that starts closed.

## Invariants

- **Decoded once, then extended.**
  The first read decodes the file; a later read decodes only the complete lines appended since.
  An unterminated final line (a writer mid-append) is decoded provisionally, shown, and read again once its newline lands, so it is never committed twice or half.
- **A replaced file is noticed.**
  Before extending, the index compares the first 4 KiB of the file, and the 4 KiB just before where it stopped, with what it read; a mismatch, or a file shorter than the read position, rebuilds from byte zero.
  Neither size nor mtime is evidence of an append: Claude's cancel/revert rewrites a transcript in place, and Windows leaves an open file's mtime frozen.
- **Codex inherited history is pinned.**
  A fork's segments come from `codex_history.segments`; parent segments are read up to their pinned boundary and re-checked by the same two samples, and inherited messages keep their `inherited:` identities.
  Only the fork's own file grows.
- **Records no projection reads are not decoded.**
  A Codex line whose kind is not `response_item`/`event_msg`, or whose payload type is outside the set the projections read, is recognised from its leading bytes (`codex_line_is_skippable`).
  The match is anchored at the record's first byte and steps over top-level scalar members only, so it never reads a nested object's type; anything it cannot read with certainty is decoded.
  On a 297 MB rollout this skips 67% of lines.
- **Tool inputs stay in the file.**
  A kept tool block carries a reference to the record holding it (`segment, start, end, block`) instead of its input.
  An input is read back only when asked for, and only while the block at that reference still carries the call id the reader showed; a rewritten record answers no input rather than another call's.
  The read-back applies the same rule the projection that showed the call applies (`transcript_view.native_tool_input`, `codex_call_input`).
- **Branch marks are recomputed, not extended.**
  One appended record can abandon a whole earlier run (a Claude retry, a Codex rollback) or complete a turn, so every refresh that changed anything re-marks branches and turns over the whole conversation before merging.
- **Identity and position are stable within a generation.**
  `message_id` is the projections' own identity (`offset:N`, `inherited:…`, `record:…`).
  `ordinal` is a message's position in the whole conversation.
  A change no splice can express - a message vanished, or an id moved - bumps `generation` and restamps every message.
- **A generation is never reused.**
  The public `generation` token is the index's epoch (minted per index) plus its counter, so a client holding a reading from before a daemon restart or an eviction is answered with a fresh tail, never told "nothing changed" by a new index whose counters happen to match.

## Change tracking

- `seq` counts changes to what a reader sees; each message is stamped with the `seq` it last changed at.
- A delta (`ConversationSnapshot.delta(since, oldest)`) returns every message from the first one stamped after `since`, at or after the client's oldest message, and names it `replace_from`.
  The client replaces from that message on, or appends when it is the one after its newest; anything else is a gap it cannot see across, and it asks for the tail.
- Pages (`page(limit, before)`) end at the newest message or just before a cursor, with `has_older` saying whether anything precedes them.
- The HTTP contract, including `ETag`/`304` revalidation and the `transcript_appended` cue, is in `design/interfaces.md` (`GET /sessions/{id}/transcript`).

## Sharing and bounds

- **Single-flight.**
  A per-index lock serialises refreshes, so concurrent readers of one conversation share one decode.
  The async entry point (`snapshot_async`) runs refreshes on the registry's own two-thread executor and joins a refresh already in flight, so a first read of a 300 MB rollout never queues behind, or starves, the default pool.
- **A slow first read answers "indexing", and is not abandoned.**
  A request waits up to 5 s; past that it is answered `reason: "indexing"` with `{read_bytes, total_bytes}`, and the read keeps filling the index so the client's retry finds it ready.
- **Bounded memory.**
  The registry drops the least recently read indexes once their estimated retained bytes pass 192 MiB, or past 48 indexes.
  The estimate is fitted to `tracemalloc` on four real conversations (1,060-1,590 bytes per kept record beyond three times its text) and rounded up.
- **A sweep neither evicts nor pins.**
  History's indexer reads thousands of conversations through `indexed_parse(shared=False)`: it reuses an index a live reader already holds, and otherwise reads through a throwaway one that is never registered.
- History's interactive reads (`shared=True`) keep the indexing projection with its inputs read back until the conversation changes, counted against the budget, and shared read-only between readers.

## Surfaces

| Surface | Reads |
| --- | --- |
| Transcript tab (`GET /sessions/{id}/transcript`) | Tail page, older pages, deltas; inputs from `/transcript/tool-calls` |
| Branch points (`GET /sessions/{id}/branch-points`, `GET /history/{id}/branch-points`) | Cut points and their words from one reading's live messages |
| Copy reply (`GET /sessions/{id}/last-reply`) | A refresh inside the request, between two watermark reads |
| Read-aloud (`voice.py`) | `final_exchange_record`, `message_exchange`: any message resolves, not only the newest window |
| History (`history.read_history_conversation`) | `indexed_parse`: a full parse's exact output, inputs included |

Not served from the index: the MCP and assistant paging reader (`transcript_message_page`), which reads bounded byte windows by cursor and includes system records the index drops, and `transcript_time_summary`, which reads disjoint head and tail slices.

## Measurements

Copies of the four largest transcripts behind the 2026-10-12 report, on the development host, in a quiet process (`tracemalloc` for memory).
"Before" is the previous `transcript_view` on the same files.

| Conversation | Transcript tab, before: every read | After: first read | After: read after an append | After: unchanged |
| --- | --- | --- | --- | --- |
| Codex, 297 MB | 0.23-0.38 s (64 MiB tail only) | 0.36 s (whole file) | 21-29 ms | 0.3 ms |
| Codex, 85 MB | 0.14-0.28 s (64 MiB tail only) | 0.09 s | 7-9 ms | 0.2 ms |
| Claude, 49 MB | 0.11-0.21 s | 0.11-0.15 s | 11-17 ms | 0.3 ms |
| Claude, 55 MB | 0.16-0.25 s | 0.13-0.18 s | 15-22 ms | 0.2 ms |

- Tail payload of 200 messages: 1,662 KB to 229 KB, 360 to 113 KB, 160 to 90 KB, and 205 to 164 KB; a delta after one appended message is 0.2-1.6 KB, and an unchanged reading revalidates as a bodiless `304`.
- Retained memory: 18.1, 1.1, 3.2 and 6.1 MB.
- History's conversation read (identical output, inputs included): first read 1.45 s to 0.58 s on the 297 MB rollout; after an append 1.47 s to 0.18 s there and 0.16-0.22 s to 14-19 ms on the others; an unchanged read stays under 1 ms.

## Failure modes

- A rewrite the samples miss would need to change bytes outside both 4 KiB windows while keeping the file at least as long; the observer's own tail (`observation.JsonlTailer`) and the full rebuild on any detected change bound how long such a reading could persist.
- A Codex envelope that moves the payload's type away from where the skip reads it is decoded rather than skipped: slower, never wrong.
- A conversation evicted between a page and its tool-call request is rebuilt by that request; the reference it holds still verifies against the call id.

## Key files

- `src/swe_mux/conversation_index.py` - the index, readings, registry, tool-input read-back, `indexed_parse`
- `src/swe_mux/transcript_view.py` - the projections, the memo and tool-reference keys they honour
- `src/swe_mux/routes/scan_timeline.py` - `session_transcript`, `session_transcript_tool_calls`, `session_last_reply`
- `src/swe_mux/routes/branch.py` - `_read_branch_points`
- `src/swe_mux/reply_snapshot.py`, `src/swe_mux/history.py` (`read_history_conversation`)
- `src/swe_mux/observation.py` - `AppendNotices`, the `transcript_appended` cue
- `frontend/src/transcriptLoader.ts`, `frontend/src/transcriptView.ts`, `frontend/src/TranscriptTab.tsx`, `frontend/src/TranscriptToolCalls.tsx`
- `tests/test_conversation_index.py`, `tests/test_session_transcript_api.py`, `tests/test_transcript_appended.py`, `frontend/test/transcriptLoader.test.ts`, `frontend/test/renderer/transcript-actions.spec.ts`
