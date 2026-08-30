# Backend: voice and the Mux assistant

Index: `../packages.md`.
Design: `../../../design/features/voice.md`, `../../../design/features/assistant.md`.
A quick reference for the whole audio system lives in `../../../CLAUDE.md`.

Each entry lists what the module owns, then **Not:** what it deliberately does not.

## `voice.py`

Completed-reply, manual, and application-text TTS streams with a coherent sentence-first clip and tracked segment-tail tasks.
Each stream owns one immutable `TtsProfile`; provider switches and option edits apply to the next stream, and the profile's `synthesis_key` is part of anchored clip reuse.
Open application-speech streams are `SpeechStream`, one worker per stream so clip indices stay monotonic.
An empty acknowledgement-only open lets later assistant sentences reach the daemon before segment zero finishes synthesis.
`pending_text` is the raw-fragment buffer, `sealed` is the audio-segment FIFO, and only the stream worker calls `application_speech_segments` or the 420-character follow-up batcher.
The opening prefers a natural boundary around 120 characters, has a 200-character hard word-boundary ceiling, and never treats an `assistant_sentence` event as an audio-file boundary.
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

## `av_stub.py`

The single definition of the `av` stub, and the one function that installs it into `sys.modules`.
PyAV is not in swe-mux's resolved closure at all: `faster-whisper` hard-requires it, nothing here reaches it, and it is dropped by a `[tool.uv] override-dependencies` marker no environment satisfies (and by `excludes=["av"]` in the frozen spec) so 63 MB of GPL-linked FFmpeg stays out of both artifacts.
`faster_whisper/audio.py` still runs `import av` at module scope, so `install()` must be called before *any* `faster_whisper` import: the frozen app does it from `packaging/rthook_av_stub.py` before any application import, and a source or wheel install does it from `voice.py` immediately before each of its two imports.
Both entry points call this module rather than carrying their own copy - a second copy drifts, and the drift reads as dictation that works in a checkout and not in the shipped app.
Reaching a real PyAV attribute raises, because that means a code path started needing the removed decoder; module dunders answer as ordinary missing attributes, since `repr()` of a module reads `__file__` and refusing that buried real diagnostics under a RuntimeError from inside the stub.
`install()` is `setdefault`, so a developer environment that still has PyAV installed keeps whatever is already imported.

**Not:** decoding audio, any PyAV shim behaviour, or deciding which decoder `voice.py` uses.

## `tts_profiles.py`

Provider-neutral immutable synthesis profiles for SAPI, Kokoro, and Edge: provider, voice, output format, provider options, duration-rate hint, and a stable hash over everything that can change audio.

**Not:** availability probes, synthesis, catalog state, or mutable configuration.

## `edge_tts_provider.py`

The external-only Edge provider: managed/custom/source interpreter resolution, bounded structured bridge invocation, classified service failures and automatic backoff, MP3 duration from the fixed bitrate, and the atomic last-good service voice catalog under `<data_dir>/voice/providers/edge/voices.json`.
Its managed installer runs `uv venv` and pinned `uv pip install` through `run_bounded`, persists
phases in `<data_dir>/integrations/edge-tts/install.json`, verifies the staging interpreter through
the bridge, and atomically activates `current` without displacing a working environment on failure.
GET/status methods read cached state only; probe, refresh, preview, and synthesis are explicit operations.
Spoken text reaches the bridge through a bounded temporary file and never through argv or logs.

**Not:** redistributing or importing `edge-tts` in the daemon, arbitrary SSML, Microsoft authorization, silent fallback, or Kokoro pronunciation handling.

## `assets/integrations/edge_tts_bridge.py`

Apache-licensed subprocess bridge run by the managed or operator-supplied Python that owns `edge-tts`.
Returns bounded JSON for status and voice discovery and writes the service's MP3 output for synthesis.

**Not:** daemon state, config persistence, retries beyond the upstream client's own protocol handling, or logs containing speech text.

## `voice_audio.py`

WAV concatenation for a completed stream: the audio profile check that decides whether segments *can* be joined, and a chunked copy into a new file.
Refusing (empty input, an unreadable segment, differing channels/sample width/sample rate/compression) is an ordinary outcome reported as `False`, not an error - the caller keeps the segments, which still play in order.

**Not:** deciding *when* to join, touching rows, or writing over a source file (a browser mid-download holds one open, and on Windows it cannot be replaced at all).

## `kokoro_tts.py`

The direct-onnxruntime Kokoro-82M engine: espeak-free misaki G2P (`fallback=None`, with a loud refusal if an espeak wrapper is importable), the out-of-vocabulary repair ladder (lexicon, compound splitter, spell-out, with replacements re-verified), phoneme-token chunking, and WAV synthesis with GPU providers when present.

**Not:** model acquisition (`voice_models.py`), any wrapper library (they carry GPL espeak payloads), or network access.

## `voice_models.py`

All three on-demand speech models, under one `not_downloaded`/`downloading`/`ready`/`error` vocabulary, because an operator should learn one shape for every asset swe-mux fetches.

`KokoroModelStore` (TTS) is the pinned, per-file SHA-256-verified download under `<data_dir>/voice-models/kokoro`: a state whose `error` can never load, progress callbacks, and restart-interruption detection.
It hand-rolls the transfer, which is why it can report bytes.

`WhisperModelStore` (STT) wraps `faster_whisper`'s own resolver over the Hugging Face cache.
The cache is authoritative for `ready` - the hub writes atomically, and that resolver already understands every form `stt_whisper_model` accepts (a size alias, a bare repository id, a local directory), so a second state file here would only drift from it.
It reports **no** byte progress: `faster_whisper.download_model` disables the hub's progress hook, so there is nothing to observe, and a proportion derived from an expected total would be an estimate presented as a reading.
`WHISPER_APPROXIMATE_MB` gives the operator the rough cost before they press Download, labelled approximate, and an unlisted name reports no size rather than a guessed one.

`SpacyModelStore` (the Kokoro G2P's spaCy model) is the newest of the three and is here for a **packaging** reason rather than a size one.
`en-core-web-sm` is published as a GitHub release asset and exists on no index, so declaring it in the `voice-local` extra put a bare unresolvable `Requires-Dist` in the wheel and made `pip install "swe-mux[voice-local]"` fail outright for every downstream user of 0.1.0 (`development/DEPENDENCY_AUDIT_2026-08-28.md` § 4).
It is declared in the unpublished `g2p-model` dependency group and acquired here: pinned URL, SHA-256 verified in memory before anything touches disk, unpacked whole into `<data_dir>/voice-models/spacy/site` and swapped into place.
`activate()` puts that directory on `sys.path` rather than writing into `site-packages`, because spaCy resolves a bare model name through `importlib.metadata.distribution` and not through importability - so the `.dist-info` has to come along, and a package directory alone would import and still not load.
It short-circuits when the environment already resolves the distribution, which is why a source checkout and the frozen bundle are untouched by any of this.
`_source()` is derived from whether that entry is on `sys.path`, never remembered: `installed` and `downloaded` are one working state reached two ways, and a flag set at one moment reported the wrong one the first time it was written.

The refusal it depends on lives in `kokoro_tts._ensure_g2p`: misaki's `G2P.__init__` reads `if not spacy.util.is_package(name): spacy.cli.download(name)`, which shells out to `pip install` from inside the synthesis path - into the venv of a source checkout, and into nothing at all in a frozen app.
That is what makes an absent model a reported state rather than an unrequested install.

The rule all three stores enforce: **a download happens only from an explicit act.**
`VoiceService._require_whisper_weights` refuses transcription rather than letting `WhisperModel(name)` fetch the weights inside the decode path, and `_ensure_whisper_model` skips an absent *routing* model instead of constructing it, because construction is the download.

The fourth first-use asset, the browser-side Silero VAD, is deliberately not here and does not download: its WASM runtime and ONNX model are emitted into the frontend bundle by Vite and served same-origin by this daemon.

**Not:** bundling models, loading them (`kokoro_tts.py`), any unpinned Kokoro revision, downloading anything a human did not ask for, or acquiring the libraries those models load into (`voice_runtime.py`).

## `voice_runtime.py` (and `wheel_closure.py`, the mechanism it now shares)

The speech **libraries**, as a first-use asset, under the same four-state vocabulary as the three model stores.
ROADMAP Phase 21 Workstream D.
The desktop bundle carried 277.1 MiB of spaCy, thinc, blis, CTranslate2, onnxruntime, tokenizers, numpy, misaki and num2words for two features that both ship switched off, and now carries none of it.

Since ROADMAP Phase 24 the store's mechanism - state file, streaming verification, staged unpack and swap, `sys.path` activation - lives in `wheel_closure.py` as `WheelClosureStore`, parameterized by a `ClosureSpec`, because the desktop shell closure (`desktop_runtime.py`) needed the identical path and a second copy of it would be a second thing to audit.
`voice_runtime.py` keeps everything voice-specific: the two capability module sets, `closure_importable(capability)`, the num2words relink declaration, and the spec.
`wheel_closure._extract_sdist` is the one Phase 24 addition to the mechanism: a spec may pin an sdist (the desktop closure's `proxy-tools` publishes no wheel), under the non-negotiable **extract-never-build** rule - nothing from the archive is executed, only the already-importable package source is copied out, and an sdist whose package would need building is refused loudly (`tests/test_wheel_closure.py`).

`VoiceRuntimeStore` fetches the pinned wheels for **this interpreter**, verifies each against its size and SHA-256 while streaming, unpacks them into `<data_dir>/voice-runtime/site` beside the live tree and swaps, then puts that directory on `sys.path`.
The mechanism is `SpacyModelStore`'s, widened from one wheel to a closure, and it borrows that store's two hard-won properties verbatim.
`activate()` short-circuits when the environment already resolves the closure, so a source checkout with `--extra voice-local` is untouched and cannot be perturbed.
`_source()` is derived from whether the entry is on `sys.path` rather than remembered, because a flag set at one moment reports the wrong one.

Three refusals distinguish it from an installer, and it must never become one.
It **resolves nothing**: the pin table is a fixed list `uv` produced, with no solver, no index query and no "latest" anything.
A closure that can change without a commit is a closure nobody audited.
It **verifies before it promotes**: the tree is built in `site.staging` and swapped, and a wheel that fails its hash is deleted rather than retried into service.
And it **refuses a partial closure**: `wheels_for_this_interpreter` raises naming the distributions it could not cover.
A closure missing one native package fails at import time, much later, with an error naming the wrong thing.

`_verify_relinkable` is where the LGPL obligation lives now.
swe-mux does not distribute `num2words` any more: the wheel goes from PyPI to the user, and what this project ships is a URL and a hash.
`build_desktop.verify_bundle_licenses` therefore cannot assert anything about it.
This asserts the same property on the tree that lands, readable `.py` source a recipient can replace, which is what `THIRD-PARTY-NOTICES.md` promises.

`_extract_wheel` promotes a wheel's `.data/purelib` and `.data/platlib` into the site root and drops `scripts`, `headers` and `data`.
Only the first two belong on `sys.path`, and `scripts` would leave console-script launchers pointing at an interpreter that need not exist.
The merge is per-file rather than per-directory, because `Path.replace` fails on an existing directory and two wheels legitimately contribute to one namespace (`google/protobuf`).

**Not:** dependency resolution, installing into any environment's `site-packages`, acquiring the model *weights* (`voice_models.py`), or fetching anything without an explicit press.

## `voice_wheels.py`

**Generated. Never hand-edited.**
`uv run python packaging/generate_voice_pins.py --write` produces it from `uv.lock`; `tests/test_voice_wheels.py` regenerates it and fails on any difference.

It holds every wheel the lockfile records for every distribution reachable *only* through the voice extras.
That is a set difference over the lockfile's own graph, so which packages are acquired is a graph question rather than a judgement.
`wheels_for_this_interpreter` picks one wheel per distribution using `packaging.tags.sys_tags()`, the same ordering pip and uv use.
The interesting cases are exactly the ones a hand-rolled `(platform, machine, version)` key gets wrong: `cp39-abi3` wheels that load on 3.12, `py2.py3-none-any`, macOS deployment targets.
`CLOSURE_DIGEST` moves only when the pins move, which is what lets a state file say which closure it built without re-hashing 315 MB.

**Not:** a place to add a package by hand, a resolver, or a description of anything but this repository's own resolution.

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
