# Backend: voice and the Mux assistant

Index: `../packages.md`.
Design: `../../../design/features/voice.md`, `../../../design/features/assistant.md`.
A quick reference for the whole audio system lives in `../../../CLAUDE.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `voice.py`

Completed-reply, manual, and application-text TTS streams with a coherent sentence-first clip and tracked segment-tail tasks.
Open application-speech streams are `SpeechStream`, one worker per stream so clip indices stay monotonic, with a tighter opening clip because that clip *is* time-to-first-sound.
Also one-shot summary and verbatim overrides, bounded Whisper STT with GPU-to-CPU fallback, temporary audio lifecycle, compatibility voice-submit idempotency, and one-use approval challenges bound to the current screen fingerprint.

A clip is a *stream*, not a row.
Rows are per segment because segments are how synthesis buys time-to-first-sound; `stream_id` plus `segment_index` is what puts them back together, and every read that a person sees goes through `clip_groups`/`group_snapshot` (one entry per reply, segments in spoken order, text and duration and bytes summed, one rolled-up status).
Eviction and deletion are per stream for the same reason, since half a reply is not a thing to keep.

Every clip is anchored to the assistant message it renders (`message_anchor`, `source_ts`), captured from the same `transcript_view` reduction the reader tab shows, and `VoiceStore` is what those two columns buy: arrival-ordered listing and a `(run, anchor, kind)` lookup that answers a repeat request from the store instead of a second summary call.
That lookup returns a complete *stream* (`anchored_group`): answering with the newest ready row handed back a segmented reply's last segment, so replaying a message spoke only its ending.
`generate(message_id=...)` speaks a *named* reply rather than the newest one, which is what the Transcript tab's per-message playback is; a message that is not an assistant reply in the readable window is a `VoiceError`, never a silent fall back.
A clip's row is inserted `synthesizing` before the engine runs and updated by whichever path leaves synthesis, so a backlog is visible while it is being made and no path can leave a row claiming work that stopped.

STT and TTS subprocesses and local models stay off the event loop.
Incoming WAV duration, encoding, and bytes are validated before transcription; Whisper decodes validated PCM from memory.
The optional legacy SAPI recognizer deletes its bounded temporary WAV and text files after the request, and sweeps stale files left by an abandoned recognizer.

Failures are typed `VoiceError` and never touch the PTY, history, or transcripts.

**Not:** browser microphone permission, mounted-composer state, PTY ownership, or approval-state classification.

## `voice_audio.py`

WAV concatenation for a completed stream: the audio profile check that decides whether segments *can* be joined, and a chunked copy into a new file.
Refusing (empty input, an unreadable segment, differing channels/sample width/sample rate/compression) is an ordinary outcome reported as `False`, not an error - the caller keeps the segments, which still play in order.

**Not:** deciding *when* to join, touching rows, or writing over a source file (a browser mid-download holds one open, and on Windows it cannot be replaced at all).

## `kokoro_tts.py`

The direct-onnxruntime Kokoro-82M engine: espeak-free misaki G2P (`fallback=None`, with a loud refusal if an espeak wrapper is importable), the out-of-vocabulary repair ladder (lexicon, compound splitter, spell-out, with replacements re-verified), phoneme-token chunking, and WAV synthesis with GPU providers when present.

**Not:** model acquisition (`voice_models.py`), any wrapper library (they carry GPL espeak payloads), or network access.

## `voice_models.py`

The pinned, per-file SHA-256-verified Kokoro model download under `<data_dir>/voice-models/kokoro`: explicit `not_downloaded`/`downloading`/`ready`/`error` state whose error can never load, progress callbacks, and restart-interruption detection.

**Not:** bundling models, loading them (`kokoro_tts.py`), or any unpinned revision.

## `assistant.py`

The Mux assistant: daemon-owned dialogs, messages, and actions in SQLite, plus the bounded tool-calling turn loop.

The rule the design enforces: the model proposes names, deterministic code resolves and executes through existing paths, and the consequential-action confirmation floor is not configurable.

- Sentence-granular streaming (`_SentenceStreamer`), so a turn is one speech stream spoken as the model writes it.
- A per-round budget line the model plans against, plus an announced - never silent - exhaustion.
- A one-deep merge queue for utterances that arrive mid-turn, because a refusal has nowhere to go and simply loses what the operator said.
- Speech suppression only when a lone confirmation card is the turn's whole outcome.
- The per-dialog action ledger and identical-proposal guard.
  A confirmation is never a turn, so nothing in the message log records that the operator already said yes; without the guard a confirmed write is proposed, and written, twice.
- The announcement-restarted cancel window.
  A card is announced **once per card**, never per event, and its window moves **once**: extending re-emits the card and a device announces a card when it sees one, so a second extension closes that into a loop that talks over the operator with no way to stop it.
- Name-to-entity resolution with candidate answers.
- The per-class trust policy: read, navigation, reversible, consequential.
- Name-only project creation preflight: the folder leaf from `leaf_names.suggest_folder_name`, and the parent from `new_project_parent` only.
- UI-command dispatch to the originating device with bounded acknowledgement.
- The Project Action pair - list with per-file approval state, and run one approved action on the consequential floor, refusing an unapproved one by naming its file - plus the bounded post-run outcome watch that reports one terse notification, success or an issue flag, and never reads a step's output back.
- `builtin:assistant` ledger spend and daily-budget refusal.

**Not:** executing anything outside injected daemon operations, approving a Project Action or running an unapproved one, quoting task output in an outcome report, emitting identifiers from the model, PTY writes, or the reflex voice path.
