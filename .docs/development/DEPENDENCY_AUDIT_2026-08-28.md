# Dependency audit, 2026-08-28

An audit of every declared dependency of `swe-mux` 0.1.0, Python and frontend, against the import
closure of the code that actually ships, plus measured install size and install time for the
published artifact.

This is a report.
It changes no dependency, no lockfile, and no source file.

**What was done with it (WP-DEPFIX, 2026-08-28).**
Recorded here rather than by editing the findings, because the measurements below are the evidence
the changes were made against and rewriting them would destroy it.
Read the numbers in § 1-§ 11 as the *before* state.

| Finding | Outcome |
| --- | --- |
| S1, stop shipping the 40 `.gz` sidecars | **Done.** A negated `artifacts` pattern in `pyproject.toml` keeps them out of the wheel and the sdist, and `build_support.precompress_static` regenerates them as the daemon's `static-precompress` startup phase. Measured after: 12.70 -> 8.26 MiB of members (-35.0%), 346 -> 306 entries. `frontend/scripts/compress-static.mjs` is deliberately **kept** as the build-time producer, because a `npm run build` that left the previous build's `index.html.gz` beside fresh content-hashed assets is a blank screen on a daemon nothing in that loop restarts; the two definitions of which files earn a sidecar are compared by a test rather than trusted. |
| S2, declare `cryptography` and `py-vapid` | **Done**, in `dependencies`. No change to the resolved closure - both were already installed transitively - which is the point. |
| S3, declare `numpy` and `ctranslate2` | **Done**, in `voice-local`. Same reasoning, same absence of size cost. |
| S4, make `swe-mux[voice-local]` installable | **Done.** `en-core-web-sm` left published metadata for the unpublished `g2p-model` dependency group, and an installed copy fetches it at first use (`voice_models.SpacyModelStore`). `doctor_local`'s remedy is now derived from how this copy was installed rather than being a fixed `uv sync` command. |
| D1, move `mcp` to an extra | **Refused by the operator.** The MCP surface works out of the box and stays in base dependencies; the 38 MiB and 20 s are accepted. Do not re-propose it. |
| D2, `pillow` out of base | Not done, per the recommendation. |
| D3, how `voice-local` gets its spaCy model | Decided: the first option (runtime acquisition), for the reasons § 10 gives. |
| D4, browser-voice assets to first-use download | Not done, per the recommendation - S1 first, and it is now measured. |
| D5, `http-ece`'s missing wheel | Not done; upstream. |

**One thing this report understated, found while acting on it.**
§ 4 treats the broken declaration as a metadata defect, which it is - but the runtime half is
sharper than "the extra does not install". misaki's `en.G2P.__init__` reads
`if not spacy.util.is_package(name): spacy.cli.download(name)`, and `spacy.cli.download` shells out
to `pip install`. So on any install where the model is absent, the *first spoken sentence* would
have triggered a pip install from inside the synthesis path, on a worker thread - into the venv of
a source checkout, and into nothing at all in a frozen app, which has no pip to reach.
Making the model optional without also refusing that path would have replaced a broken install with
a silent one. `kokoro_tts._ensure_g2p` now raises a typed `KokoroError` before `en.G2P` is
constructed at all.

**And one thing it got right that is worth restating.**
§ 8's note that moving something out of `DISTRIBUTED_EXTRAS` narrows the audited closure applies to
dependency *groups* too, which the walk did not read at all. `g2p-model` is unpublished and
redistributed at the same time - `packaging/swe_mux.spec` collects it into
`_internal/en_core_web_sm/` - so `license_audit.DISTRIBUTED_GROUPS` and
`build_desktop.REQUIRED_BUILD_GROUPS` both name it, and the generated notices and sidecar came back
byte-identical, which is the check that the closure did not move.

## What the numbers say in one paragraph

The 13.3 MB wheel is not a Python dependency problem.
**85% of it is the built frontend bundle and 61% of it is three browser-voice assets**, one of which
is shipped twice.
The Python dependency problem is a different one and it is real: `mcp` is declared in `dependencies`
for a single lazy import that already degrades gracefully when it is absent, and it costs **38 MiB
of installed dependencies and 20 s of a cold `pip install`**.
Separately, `swe-mux[voice-local]` **cannot be installed from PyPI by anyone**, because it declares
`en-core-web-sm`, which does not exist on any index.

## Method, and what each measurement is a measurement of

Every figure has the command that produced it.
Scratch environments were created under a temporary directory; the repository's own `.venv` was read
but never modified.

- **Import closure.** Every `.py` under `src/swe_mux` (223 files) parsed with `ast`, recording each
  top-level module name imported and whether the import is at module scope or inside a function.
  Submodule names are deliberately *not* treated as first-party: Python 3 has no implicit relative
  imports, so `from mcp import Client` inside `swe_mux/` resolves to the third-party `mcp`
  distribution even though `swe_mux/mcp.py` exists.
  A first pass that got this wrong reported `mcp` as unimported, which is exactly the false
  "safe to remove" a careless audit produces.
- **Dynamic imports.** `importlib` and `__import__` call sites were read individually.
  The only dynamic third-party resolution in the package is `kokoro_tts.ESPEAK_MODULES`, which is a
  `find_spec` *refusal* probe rather than a dependency, and `doctor_local`'s `find_spec` extras
  probes.
  Nothing is imported by a name the AST pass could not see.
- **PyInstaller specs.** `packaging/swe_mux.spec` and `packaging/swe_mux_supervisor.spec` were read
  for `collect_all`, `hiddenimports`, and `excludes`, because a package reached only by the frozen
  build is invisible to a source-tree import scan.
- **Installed size.** Attributed per distribution from each `dist-info` RECORD, so the number is
  what the installer put on disk rather than the compressed wheel size.
  **pip-installed and uv-installed footprints are not comparable**: pip writes `__pycache__` and uv
  does not, which is why the same package measures 19.41 MiB under pip and 14.50 MiB under uv.
  Every size comparison below holds the installer fixed.
- **Install time.** Wall clock around the installer process, cold (empty cache) and warm.
  Measured on the Windows development host over a fast link; the absolute numbers are host-specific
  and the *ratios* are the transferable part.

## 1. Wheel composition: the frontend is the wheel

```
python wheel_composition.py swe_mux-0.1.0-py3-none-any.whl
```

```
wheel: swe_mux-0.1.0-py3-none-any.whl  on-disk 12.71 MiB
 comp MiB   raw MiB  files  area
    10.71     22.88     83  swe_mux/static/assets (built frontend bundle)
     1.80      6.29    223  swe_mux/*.py (Python source)
     0.10      0.11     17  swe_mux/static (other frontend build output)
     0.03      0.08     14  swe_mux/assets (shipped integration assets)
     0.03      0.07      7  swe_mux-0.1.0.dist-info
    12.66     29.43    344  TOTAL
```

The built frontend is **84.6%** of the compressed wheel.
All 223 Python modules together are **14.2%**.

Within the frontend, three browser-voice assets dominate:

```
     2.840 MiB  swe_mux/static/assets/ort-wasm-simd-threaded-BQQNiIPt.wasm.gz
     2.833 MiB  swe_mux/static/assets/ort-wasm-simd-threaded-BQQNiIPt.wasm
     1.855 MiB  swe_mux/static/assets/silero_vad_v5-Dt9-bTDn.onnx
     0.105 MiB  swe_mux/static/assets/ort.bundle.min-ByP5eVXb.js.gz
     0.105 MiB  swe_mux/static/assets/ort.bundle.min-ByP5eVXb.js
     0.010 MiB  swe_mux/static/assets/ort-wasm-simd-threaded-Rz2TxrO3.mjs.gz
     0.009 MiB  swe_mux/static/assets/ort-wasm-simd-threaded-Rz2TxrO3.mjs
browser-voice total: 7.76 MiB of 12.66 MiB = 61.3%
```

The ONNX Runtime WASM binary is 10.7 MiB raw and there is exactly one copy of it, so the two callers
(`sileroVad.ts` and `smartTurn.ts`) already share a single emitted asset.
That deduplication is working and is not where the weight is.

Where the weight is, is that the wheel carries **40 `.gz` sidecars totalling 4.43 MiB compressed,
and every one of them has its plain sibling in the same wheel**:

```
gz members: 40
gz compressed MiB: 4.43
gz with a plain sibling also shipped: 40 (4.43 MiB compressed)
```

That is **35% of the wheel spent re-shipping content the zip container already compresses**.
The sidecars are not decoration: `frontend/scripts/compress-static.mjs` writes them, aiohttp's
`FileResponse._get_file_path_stat_encoding` serves them to any client sending
`Accept-Encoding: gzip`, and `build_support.py` documents that "the daemon serves the `.gz` to any
client sending `Accept-Encoding: gzip` - which is every browser".
Both files must exist, which `tests/test_verify_release_artifact.py` states as
"`.js.gz` is not a `.js`; a tree of only sidecars serves nothing."

## 2. Install time, measured on both installers

Cold means an empty installer cache; the venv is fresh in every case.

| Command | Cold | Warm |
| --- | --- | --- |
| `python -m pip install --no-cache-dir swe-mux` | **40.24 s** | 22.45 s |
| `uv tool install swe-mux` (isolated tool dir and cache) | **5.34 s** | 1.08 s |

`uv` is 7.5x faster cold and 21x faster warm on the same dependency set, because it resolves and
downloads in parallel and links rather than copying.
Anyone whose first impression of install time matters should be sent to `uv` or `pipx`, which the
README already does.

Two things dominate pip's cold time, and they are separable:

```
### how long the http-ece sdist build costs pip on its own
httpece rc=0 seconds=11.551
```

`http-ece` publishes **no wheel**, only an sdist, so every pip install of `swe-mux` builds it,
including the setuptools bootstrap.
That is 11.55 s, **29% of the cold pip install**, for an 8.8 kB source archive that produces a
4,867-byte wheel.
It arrives through `pywebpush`.
`uv` builds it too (`Building http-ece==1.2.1` in its log) and absorbs it into 5.3 s total.

The other is `mcp`, quantified in the next section.

## 3. `mcp` in `dependencies` is the single largest Python cost

`mcp>=2.0.0` is declared in base `dependencies`.
The whole of its use in the codebase is three lazy imports in one function:

```
## mcp  (3 sites, scopes=['lazy'])
  mcp_tools.py:683  [lazy]  mcp
  mcp_tools.py:684  [lazy]  mcp.client.stdio
  mcp_tools.py:685  [lazy]  mcp.client.streamable_http
```

That function, `mcp_tools.claude_probe`, **already handles its own absence**:

```python
    try:
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.streamable_http import streamable_http_client
    except ImportError as exc:  # pragma: no cover - dependency is declared
        return _probe_failure("claude", server, f"the MCP client is unavailable: {exc}")
```

`swe-mux` implements the MCP *server* side itself in `swe_mux/mcp.py` and does not use this package
for it.
The dependency exists only so that the Agent Environment drawer's per-server "Fetch tools" button
can dial a Claude-configured server, which `.docs/CLAUDE.md` already documents as reached only by an
explicit press.

### What it costs

Same nine other base dependencies in both arms, same installer, cold in both cases.

```
### pip cold: base WITH mcp
pipcold-withmcp rc=0 seconds=43.799
### pip cold: base WITHOUT mcp
pipcold-nomcp rc=0 seconds=23.375
```

**-20.42 s, a 47% cut to a cold `pip install`.**

On disk, comparing the two pip-installed environments (both include pip itself at 5.52 MiB):

| Environment | site-packages on disk | distributions |
| --- | --- | --- |
| `pip install swe-mux` (base, as published) | **133.39 MiB** | 52 |
| the same base dependencies without `mcp` | **59.33 MiB** | 29 |

Excluding `swe-mux` itself (36.59 MiB) and pip, third-party dependency weight goes from 89.24 MiB to
50.79 MiB.
**`mcp` and its subtree are 38.45 MiB and 23 distributions.**

The subtree, from the measured table:

```
    19.41    1067  pywin32  312
     5.40      13  pydantic_core  2.46.5
     3.61     217  pydantic  2.13.5
     2.47     254  mcp  2.1.1
     1.01      82  jsonschema  4.26.0
     0.94      68  httpx2  2.12.0
     0.91      40  click  8.5.0
     0.69      92  uvicorn  0.52.4
     0.62      22  mcp-types  2.1.1
     0.62      68  httpcore2  2.12.0
     0.59      76  starlette  1.6.0
     0.56      11  rpds-py  2026.6.3
     0.40      78  opentelemetry-api  1.44.0
     ... plus PyJWT, referencing, python-multipart, h11, truststore,
        typing-inspection, sse-starlette, annotated-types,
        jsonschema-specifications
```

`pywin32` alone is 19.41 MiB and reaches this project **only** through `mcp`:

```
Name: pywin32
Requires:
Required-by: mcp
```

Its marker is `pywin32>=311; sys_platform == 'win32'`, so this particular 19.4 MiB lands on Windows
only, which is the proving platform and therefore the platform whose first impression matters most.

A full ASGI web stack (`starlette`, `uvicorn`, `sse-starlette`, `python-multipart`,
`opentelemetry-api`) is installed into every base install of a product that serves HTTP with
`aiohttp` and never touches any of it.

### Why this does not show up under uv

```
### cold: base WITH mcp        5.759 s
### cold: base WITHOUT mcp     5.580 s
```

Under `uv` the difference is 0.18 s, because parallel resolution and downloading hides it.
Disk cost is unchanged.
This is why the recommendation is about **size and the pip path**, not about uv's wall clock.

## 4. `swe-mux[voice-local]` cannot be installed from PyPI

This is a correctness defect in published metadata, and it is **not** the recorded `av` limitation in
`RELEASE_MANUAL_TASKS.md` § 9.
It is a different, larger one that makes that limitation unreachable.

The published wheel declares:

```
Provides-Extra: voice-local
Requires-Dist: en-core-web-sm; extra == 'voice-local'
```

`en-core-web-sm` is not on PyPI, or on any index:

```
python -m pip index versions en-core-web-sm
ERROR: No matching distribution found for en-core-web-sm
```

Both installers therefore refuse the extra outright.

```
uv pip install "swe-mux[voice-local]"
  × No solution found when resolving dependencies:
  ╰─▶ Because there are no versions of en-core-web-sm and
      swe-mux[voice-local]==0.1.0 depends on en-core-web-sm, we can conclude
      that swe-mux[voice-local]==0.1.0 cannot be used.
```

```
python -m pip install "swe-mux[voice-local]"
ERROR: Could not find a version that satisfies the requirement en-core-web-sm; extra == "voice-local"
ERROR: No matching distribution found for en-core-web-sm; extra == "voice-local"
PIP_VOICE_LOCAL_RC=1
```

The cause is the same shape as the recorded `av` gap and worth stating in those terms.
`[tool.uv.sources]` resolves `en-core-web-sm` from a GitHub release URL, and like the `av` override
it is **a property of this project's resolution, not of the published metadata**.
`uv.lock` carries the URL and the hash; `Requires-Dist` carries only a bare name with nowhere to
resolve it from.
A PEP 508 direct-URL requirement is not a way out, because PyPI rejects distributions whose
`Requires-Dist` contains one.

Three things follow, and each is independently worth fixing:

- Every user-facing instruction that names the extra is currently unrunnable for an installed copy.
  `mux doctor`'s remedy string for this row is `uv sync --extra voice-local`
  (`doctor_local._EXTRAS`), which is a source-checkout command and cannot be run by someone who
  installed from PyPI at all.
- `RELEASE_MANUAL_TASKS.md` § 9 and `ROADMAP.md` state that "a downstream
  `pip install swe-mux[voice-local]` still resolves `faster-whisper`'s own declared `av>=11`".
  Measurement refutes that: the command resolves nothing, so no downstream user has ever pulled the
  GPL-linked `av` payload through this extra.
  The recorded limitation is real in principle and currently unreachable in practice, and the
  ordering matters, because fixing the `en-core-web-sm` defect is what *activates* the `av` one.
- CI cannot see this. `.github/workflows/ci.yml` exercises the extra with `uv sync --extra
  voice-local`, which goes through `uv.lock` and `[tool.uv.sources]`; nothing anywhere installs any
  extra from the index.
  `packaging/install_smoke.py` installs the base wheel only.

## 5. Declared dependencies that no code path imports

Every base dependency is imported by first-party code except one, and that one is correct:

| Declared | Imported by `src/swe_mux`? | Verdict |
| --- | --- | --- |
| `aiohttp` | yes, 50 module-scope sites | correct |
| `mcp` | yes, 3 lazy sites in one function | correct, but misplaced (§3) |
| `pillow` | yes, 2 lazy sites | correct, but see §7 |
| `psutil` | yes, 19 sites | correct |
| `pywebpush` | yes, module scope in `push.py` | correct |
| `pywinpty` (`winpty`) | yes, module scope in `pty_backend_windows.py` | correct |
| `tree-sitter` | yes, 3 lazy sites | correct |
| `tree-sitter-language-pack` | yes, 3 lazy sites | correct |
| `tzdata` | **no import anywhere** | correct anyway, see below |
| `watchfiles` | yes, module scope in `project_watcher.py` | correct |

`tzdata` is a pure data distribution with no importable entry point.
It is reached by the standard library: `schedules.py` calls `ZoneInfo(timezone)`, and Windows ships
no IANA database, which is why the marker is `sys_platform == 'win32'`.
`packaging/swe_mux.spec` collects it explicitly for the same reason.
An import scan will always report it as unused and it must never be removed on that evidence.

In the extras, three names are declared and not imported by first-party code, and all three are
correct:

- `spacy` is loaded by `misaki.en`, which does `import spacy` at module scope and
  `spacy.load("en_core_web_sm")`.
- `en-core-web-sm` is the model that `spacy.load` resolves, named nowhere in `src/swe_mux`.
- `num2words` is imported at module scope by `misaki.en`, and is declared directly rather than left
  transitive on purpose: it is LGPL-2.1, and `packaging/swe_mux.spec`'s `collect_all("num2words")`
  is what satisfies the relink condition by shipping it as readable source under
  `_internal/num2words/`.
  `packaging/license_audit.py` allowlists it with that reasoning.

`edge-tts` is never imported by the installed package at all.
Its only import site is `src/swe_mux/assets/integrations/edge_tts_bridge.py`, which is package
*data* executed as a script under a separate operator-supplied or managed interpreter, and
`packaging/swe_mux.spec` excludes the package from the bundle even when the build environment has
it.
The extra is what its comment says it is, source-install convenience, and it is not dead.

## 6. Undeclared direct imports

Four packages are imported directly by `src/swe_mux` and declared nowhere.
All four currently arrive transitively, so nothing is broken today; each is a latent break the day
an upstream drops the edge.

| Module | Imported at | Arrives via | Where it belongs |
| --- | --- | --- | --- |
| `cryptography` | `push.py:52`, **module scope** | `pywebpush` -> `py-vapid` -> `cryptography` | base |
| `py_vapid` | `push.py:53`, **module scope** | `pywebpush` | base |
| `numpy` | `kokoro_tts.py:360,612`, `voice.py:3231`, lazy | `onnxruntime`, `spacy` | `voice-local` |
| `ctranslate2` | `voice.py:3347`, lazy | `faster-whisper` | `voice-local` |

The two module-scope ones are the sharper case: `push.py` is imported by the daemon at startup, so a
`pywebpush` release that stopped requiring `cryptography` would turn into an `ImportError` at
daemon start rather than a degraded feature.

```
Name: cryptography
Requires: cffi
Required-by: http_ece, py-vapid, pywebpush
```

Declaring them costs nothing in size, since they are already installed.
It costs one line each and converts an invisible assumption into a checkable claim, which is the
same standard `pyproject.toml`'s own header sets for every other entry.

## 7. `pillow` in base

`pillow` is 15.40 MiB installed and 7.2 MB of wheel, the second-largest single item in a base
install after `pywin32`.
Both its import sites are lazy, and they are not the same kind of consumer:

- `project_files.py:258` wraps it in `_pillow()`, which catches `ImportError`, logs
  "Pillow is not installed; project image presentation is unavailable", and returns `None`.
  This path is genuinely optional.
- `desktop.py:175`, inside `create_tray_image`, is a bare `from PIL import Image, ImageDraw` with no
  guard.
  Its only caller is `desktop.py:700`, constructing `pystray.Icon(...)`, which is unreachable unless
  the `desktop` extra is installed.

So Pillow's unguarded consumer is already gated behind an extra, and its base-level consumer already
degrades.
`pyproject.toml`'s comment for it argues correctly that Pillow is not a Windows-only feature; that
argument is about the *marker*, and does not by itself establish that it belongs in `dependencies`
rather than in `desktop` plus an image extra.
This one is a decision, not a defect: see §10.

## 8. Transitive surprises, and the license gate

Nothing GPL or AGPL is in the base closure.
The gate's sidecar (`packaging/third_party_licenses.json`, 202 entries) already tracks every
transitive package the base install pulls, including the whole `mcp` subtree (`pywin32`,
`cryptography`, `pydantic`, `starlette`, `uvicorn`, `jsonschema`, `opentelemetry-api`, `httpx2`,
`httpcore2`, `truststore`, `python-multipart`, `sse-starlette`, `mcp-types`).
The audit found no package in the closure that the gate does not know about.

Two observations rather than findings:

- The `av` override works as documented.
  `av` is absent from the repository's synced environment, and `faster-whisper` resolves without it.
- Moving `mcp` to an extra would move 23 distributions out of `DISTRIBUTED_EXTRAS`' reach unless the
  new extra is added to that tuple.
  Whether it should be depends on whether the desktop bundle keeps collecting `mcp`, which it
  currently does (`packaging/swe_mux.spec`).
  This is a coupling to handle deliberately, not a reason not to move it.

## 9. Frontend

Every one of the 33 `dependencies` and 4 `devDependencies` in `frontend/package.json` has a real
source reference; there are no unused entries and nothing is in the wrong section.

The two that looked suspicious were both cleared by reading the call sites:

- `onnxruntime-web` appears in `scripts/smart-turn-bench.mts`, which is a dev bench, but it is also
  imported by `src/sileroVad.ts:49` and `src/smartTurn.ts:70`, and both reach its WASM and `.mjs`
  through explicit `../node_modules/onnxruntime-web/dist/...?url` imports because the package's
  exports map does not expose them.
  It is a runtime dependency.
  Note that `@ricky0123/vad-web` carries its *own* nested `onnxruntime-web` (visible in
  `package-lock.json` as `@ricky0123/vad-web/node_modules/onnxruntime-web`); only one runtime is
  emitted into the built bundle, so this is not currently costing anything, but it is the thing to
  re-check on any `@ricky0123/vad-web` upgrade.
- `typescript` matches inside `src/codeLanguage.ts` only as the string `typescript:` in a
  CodeMirror language option.
  It is correctly a `devDependency` regardless.

The 15 `@codemirror/lang-*` packages are each reached by a dynamic `import()` in
`src/codeLanguage.ts` and are named in `vite.config.ts`'s chunking, so each becomes its own lazy
chunk rather than entry-bundle weight.
Measured in the wheel, all of the CodeMirror language chunks together are under 60 kB compressed.
They are not a size problem and consolidating them would not be worth the loss of laziness.

What inflates the built bundle is not a package.json entry at all.
It is the two model assets in §1, plus the `.gz` duplication.

## 10. Findings, ranked

### Safe

**S1. Stop shipping the 40 `.gz` sidecars in the wheel and regenerate them on first run.**
`-4.43 MiB of 12.66 MiB, a 35% wheel reduction.`
The sidecars exist to let aiohttp serve precompressed bytes; they do not need to be *built* by the
frontend toolchain to exist on the serving machine.
Regenerating all 40 from the shipped sources costs, measured:

```
files with a shipped .gz sidecar: 40
input  16.22 MiB
output 4.41 MiB
gzip level 9, single thread: 0.93 s
largest member alone (ort-wasm-simd-threaded.wasm): 0.69 s
same member at level 6: 0.43 s
```

0.93 s, once, on first daemon start, in exchange for a third of the wheel.
The regeneration must be idempotent and content-addressed the way `build_support.publish_frontend`
already is, because a stale `.gz` outliving its source is a blank screen rather than a slow page,
which `tests/test_desktop.py::test_frontend_publish_drops_every_precompressed_variant` exists to
prevent.
Do **not** simply drop the sidecars without regenerating them: aiohttp does no on-the-fly
compression for `add_static`, so the browser would fetch the 10.7 MiB WASM uncompressed, and the
mobile-over-Tailscale case would get materially worse.

**S2. Declare `cryptography` and `py-vapid` in `dependencies`.**
Zero size cost; both are already installed.
They are module-scope imports in `push.py`, which the daemon imports at startup, so the failure mode
of leaving them undeclared is a daemon that will not start.

**S3. Declare `numpy` and `ctranslate2` in `voice-local`.**
Same reasoning, lower urgency because both call sites are lazy and inside features that already
report typed diagnostics.

**S4. Fix `swe-mux[voice-local]` so it can resolve from an index.**
It cannot today (§4), so this is a repair rather than an optimisation, and there is no trade-off in
*doing* it.
The mechanism is a decision (D3).

### Needs a decision

**D1. Move `mcp` from `dependencies` to a new extra.**
`-38.45 MiB installed, -20.4 s on a cold pip install, -23 distributions.`

*For:* the code already degrades correctly without it, its single consumer is one explicitly pressed
button in one drawer, and it is 43% of the base install's dependency weight.
A base install would drop from 133.39 MiB to 59.33 MiB and from 52 distributions to 29.

*Against:* the drawer button silently becomes a typed "the MCP client is unavailable" for anyone who
installed without the extra, and the Agent Environment doc's contract that an empty catalog must say
*which kind* of empty it is now has one more kind to distinguish.
`packaging/swe_mux.spec` must keep collecting `mcp`, and `license_audit.DISTRIBUTED_EXTRAS` must
gain the new extra so the desktop bundle's closure is unchanged.

*Recommendation:* move it.
Name the extra for the capability rather than the package, and have `doctor_local._EXTRAS` gain a
row so the absence is reported rather than discovered.
This is the largest available Python-side win and the code was already written to survive it.

**D2. Decide whether `pillow` stays in base.**
`-15.40 MiB installed if it moves.`

*For moving:* its unguarded consumer is the tray icon, which needs the `desktop` extra anyway, and
its base consumer already degrades with a log line.

*Against:* Project image presentation silently becomes unavailable for the default install, which is
a visible product regression for a feature that is not obviously optional, and `pyproject.toml`
already records a deliberate decision to make Pillow available on every platform.

*Recommendation:* leave it in base for now and revisit only if D1 lands and 15 MiB is still the
next-largest item worth arguing about.
The size is real but the capability loss is user-visible in a way the `mcp` probe's is not.

**D3. How `voice-local` gets its spaCy model.**
Three options, and the extra is broken until one is chosen.

- Drop `en-core-web-sm` from the extra and resolve the model at runtime, downloading it on first use
  the way the Whisper weights already are, with the existing typed-diagnostic pattern for absence.
  This is the only option that leaves a working `pip install swe-mux[voice-local]`.
- Keep the declaration and document the extra as source-install only, updating
  `doctor_local._EXTRAS`, `README.md`, and `OPERATOR_LIFECYCLE.md` to stop offering a command that
  cannot work.
- Vendor the model. Rejected on size: it would add tens of MB to an artifact this audit is trying to
  shrink.

*Recommendation:* the first.
It matches how every other first-use asset in this product already behaves, it makes the published
extra true, and it is the prerequisite for the `av` gap in § 9 of `RELEASE_MANUAL_TASKS.md` being a
live concern rather than a theoretical one.

**D4. Whether the browser-voice assets ship in the wheel.**
`-4.69 MiB of non-sidecar wheel content, 37%, if they move to first-use download.`
`silero_vad_v5.onnx` (1.855 MiB) and the ONNX Runtime WASM (2.833 MiB) are lazy in the browser
already and are only needed by hands-free capture.

*For:* combined with S1 this would take the wheel from 12.66 MiB to roughly 5 MiB.

*Against:* they are shipped locally on purpose rather than fetched from a CDN, and a first-use
download reintroduces a network dependency into a feature that currently works offline, on a
product whose whole posture is local.

*Recommendation:* do not do this until S1 has been done and measured.
S1 alone gets a third of the wheel with no behavioural change; this one trades a real property for
the next third and should be argued on its own merits, not bundled.

**D5. `pywebpush` and its sdist-only `http-ece`.**
`-11.55 s on a cold pip install`, which is 29% of it.
`http-ece` publishes no wheel, so every pip install compiles it.

*For acting:* it is the single largest time cost in the pip path, and pip is the slow path that
shapes first impressions.

*Against:* the only fixes are upstream (ask `http-ece` to publish a wheel) or structural (make web
push an extra, which turns notifications off by default).
Neither is a local change.

*Recommendation:* open an upstream issue asking `http-ece` to publish a wheel, and in the meantime
keep steering the README's install instructions at `uv` and `pipx`, which absorb the build into
5.3 s of total install.
Do not make web push an extra for this.

### Leave alone

Each of these was suspected and cleared, with the reason, so the next audit does not re-run it.

- **`tzdata`.** No import anywhere in the tree, and correct.
  `schedules.py` reaches it through `zoneinfo.ZoneInfo`, Windows has no system IANA database, and
  the marker is already `sys_platform == 'win32'`.
  `packaging/swe_mux.spec` collects it explicitly because the source graph cannot see it.
- **`spacy`, `en-core-web-sm`, `num2words`.** Not imported by first-party code, all three required.
  `misaki/en.py:10` does `import spacy` and `misaki/en.py:503` does `spacy.load(name)` where
  `name` is `en_core_web_{'trf' if trf else 'sm'}`.
  `num2words` is additionally an LGPL compliance obligation discharged by the spec's `collect_all`.
  (The *declaration* of `en-core-web-sm` is broken, D3, but the dependency is real.)
- **`edge-tts`.** Never imported by the installed package.
  Its only import site is package data run under a separate interpreter, and the bundle excludes it.
  The extra is correctly labelled source-install convenience.
- **The `dev` and `package` dependency groups.** PEP 735 groups are never published; the wheel's
  `METADATA` carries `Provides-Extra` for the four extras and no group content at all.
  `pyinstaller` cannot reach a user.
- **`pytest-asyncio`.** Looked redundant beside plain pytest; it is not.
  `pyproject.toml` sets `asyncio_mode = "auto"`, which is this plugin's setting.
- **`pytest-xdist`.** Load-bearing for the gate's `--dist loadgroup`, which is the only mode that
  honours the `xdist_group` marks pinning the real-console tests.
- **`aiohttp`.** 50 module-scope import sites across every route module. Not going anywhere.
- **`psutil`.** 19 sites, and additionally collected by `swe_mux_supervisor.spec`, so it is in the
  supervisor's frozen closure as well as the daemon's.
- **`pywinpty`.** Module scope in `pty_backend_windows.py`, correctly marked `sys_platform ==
  'win32'`, and the POSIX side uses the standard library `pty` with no third-party dependency.
  `doctor_local._pty_check` exists specifically because this is the one compiled dependency in the
  runtime closure.
- **`tree-sitter` and `tree-sitter-language-pack`.** Lazy, 3 sites each, and the language pack ships
  the compiled grammars the frozen app needs; the spec collects both.
- **`playwright`.** Correctly in the `preview-capture` extra.
  Measured at **103.60 MiB** installed for the Python package alone, before any browser download,
  which is a good argument for it staying exactly where it is.
- **`pystray` and `pywebview`.** Correctly in `desktop`, correctly `sys_platform == 'win32'`, and
  their consumers in `desktop.py` are lazy.
  The extra adds about 5.5 MiB (`pythonnet` 3.21 MiB, `pywebview` 1.54 MiB, `bottle` 0.39 MiB,
  `clr_loader` 0.16 MiB, `pystray` 0.14 MiB, `six` 0.04 MiB, `proxy_tools` 0.01 MiB).
- **The 15 `@codemirror/lang-*` frontend packages.** Each behind a dynamic `import()` and its own
  Vite chunk; all of them together are under 60 kB compressed in the wheel.
- **`onnxruntime-web` as a frontend `dependency`.** Reached by `src/sileroVad.ts` and
  `src/smartTurn.ts` at runtime, not only by the bench script.
- **The single emitted ONNX Runtime WASM.** Two callers, one asset; deduplication already works.

## 11. Achievable win

Wheel size, from a measured 12.66 MiB of members:

| Change | Wheel after | Cut |
| --- | --- | --- |
| S1, regenerate `.gz` on first run | 8.23 MiB | **-35%** |
| S1 + D4, also move the browser-voice models to first use | ~4.9 MiB | **-61%** |

Installed size and install time for a default `pip install swe-mux`:

| Change | site-packages | cold pip |
| --- | --- | --- |
| today | 133.39 MiB | 40.24 s |
| D1, `mcp` to an extra | **59.33 MiB** | **~23.4 s** |
| D1 + D2, also Pillow out of base | ~44 MiB | ~22 s |
| D1 + D5 upstream `http-ece` wheel | ~59 MiB | ~12 s |

The honest summary is that **the wheel and the install are two different problems with two different
fixes**, and neither one helps the other.
S1 fixes a third of the download.
D1 fixes 55% of the disk footprint and half the cold pip time.

## 12. The one change to make first

**S1: stop shipping the `.gz` sidecars and regenerate them on first daemon start.**

It is the largest single win available (35% of the wheel), it is the only large win with no
behavioural trade-off to argue about, the runtime property it must preserve is already proven by an
existing test, and the cost is one measured 0.93 s on first run.
D1 is the larger *total* win and the one to do second, but it changes what an installed copy can do
and therefore needs the extra named, `DISTRIBUTED_EXTRAS` updated, and a `doctor` row added before
it is safe.

## Appendix: reproduction

Every command was run against `swe-mux` 0.1.0 from PyPI, into scratch environments under a temporary
directory. `<scratch>` below is that directory.

```bash
# Wheel composition
python -m pip download --no-deps -d <scratch>/wheelhouse swe-mux
python wheel_composition.py <scratch>/wheelhouse/swe_mux-0.1.0-py3-none-any.whl

# Cold pip install, timed
uv venv --seed --python 3.12 <scratch>/v-pip-cold
<scratch>/v-pip-cold/Scripts/python.exe -m pip install --no-cache-dir --progress-bar off swe-mux

# Cold uv tool install, timed, isolated tool dir and cache
UV_TOOL_DIR=<scratch>/uvtool/tools UV_TOOL_BIN_DIR=<scratch>/uvtool/bin \
  UV_CACHE_DIR=<scratch>/uvcache uv tool install --python 3.12 swe-mux

# The mcp delta, same nine other base dependencies in both arms
python -m pip install --no-cache-dir aiohttp>=3.11 pillow>=11.0 psutil>=6.1 pywebpush>=2.0 \
  pywinpty>=3.0.2 tree-sitter>=0.23 tree-sitter-language-pack>=0.7 tzdata>=2024.1 \
  watchfiles>=1.0 mcp>=2.0.0
python -m pip install --no-cache-dir aiohttp>=3.11 pillow>=11.0 psutil>=6.1 pywebpush>=2.0 \
  pywinpty>=3.0.2 tree-sitter>=0.23 tree-sitter-language-pack>=0.7 tzdata>=2024.1 watchfiles>=1.0

# Per-distribution installed size, attributed from each dist-info RECORD
python dist_sizes.py <venv>/Lib/site-packages

# The voice-local resolution failure, on both installers
uv pip install --python <venv>/Scripts/python.exe "swe-mux[voice-local]"
python -m pip install --no-cache-dir "swe-mux[voice-local]"
python -m pip index versions en-core-web-sm

# The http-ece sdist build in isolation
python -m pip install --no-cache-dir http-ece

# Import closure of the shipped package
python import_closure.py src/swe_mux closure.json
python report_sites.py closure.json
```

The measurement scripts (`wheel_composition.py`, `dist_sizes.py`, `import_closure.py`,
`report_sites.py`, `frontend_usage.py`, `gzip_cost.py`) were written for this audit and are not part
of the repository.
Each is short enough to rewrite from its description here, and the descriptions are in § Method
above.
