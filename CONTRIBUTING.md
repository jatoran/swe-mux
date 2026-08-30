# Contributing to swe-mux

Thanks for your interest. This file covers the two things a contribution has to
satisfy before it can be merged: the sign-off, and the verification gate.

## Developer Certificate of Origin

swe-mux uses a **DCO sign-off**, not a Contributor License Agreement. There is
no separate document to sign and no account to create. You keep the copyright in
your own work; you certify that you have the right to contribute it.

Add a `Signed-off-by` line to every commit:

```
git commit -s -m "your message"
```

which appends

```
Signed-off-by: Your Name <your.email@example.com>
```

using your `git config user.name` and `user.email`. The name should be a real
name you can be reached at, not a pseudonym, because the line is a statement
about provenance.

By signing off you certify the [Developer Certificate of Origin
1.1](https://developercertificate.org/), reproduced here in full:

```
Developer Certificate of Origin
Version 1.1

Copyright (C) 2004, 2006 The Linux Foundation and its contributors.

Everyone is permitted to copy and distribute verbatim copies of this
license document, but changing it is not allowed.


Developer's Certificate of Origin 1.1

By making a contribution to this project, I certify that:

(a) The contribution was created in whole or in part by me and I
    have the right to submit it under the open source license
    indicated in the file; or

(b) The contribution is based upon previous work that, to the best
    of my knowledge, is covered under an appropriate open source
    license and I have the right under that license to submit that
    work with modifications, whether created in whole or in part
    by me, under the same license (unless I am permitted to submit
    under a different license), and I have the right to submit that
    contribution with modifications.

(c) The contribution was provided directly to me by some other
    person who certified (a), (b) or (c) and I have not modified
    it.

(d) I understand and agree that this project and the contribution
    are public and that a record of the contribution (including all
    personal information I submit with it, including my sign-off) is
    maintained indefinitely and may be redistributed consistent with
    this project or the open source license(s) involved.
```

### Why a DCO and not a CLA

A CLA would let the project relicense your contribution later. A DCO does not,
and that is the intended trade. It keeps the barrier to a first contribution at
one command-line flag, and swe-mux does not need relicensing rights over the
core: anything commercial is separate code, not a re-licensed version of this.

Contributions arrive under Apache-2.0, the license in `LICENSE`, per §5 of that
license.

## Dependencies and licensing

swe-mux distributes a frozen desktop bundle, so a new dependency is a new thing
redistributed to every user. Two rules follow, and both are enforced by a gate
rather than by review:

- **No GPL or AGPL, ever.** Weak copyleft (LGPL) requires an explicit entry in
  `ALLOWLIST` in `packaging/license_audit.py` recording why it may ship and how
  a recipient can replace it. There are two such entries today.
- **No espeak-ng, in any form.** This rejected three otherwise-reasonable
  phonemizer libraries. It is a dependency-review rule, not a preference.
  `.docs/development/ROADMAP.md` Phase 10.5 records the measurements behind it.
- **A transitive dependency nothing imports gets dropped, not documented.** `faster-whisper`
  hard-requires `av>=11`, which is 63 MB of GPL-linked FFmpeg that no swe-mux call site
  reaches. It is removed from the resolution by a `[tool.uv] override-dependencies` entry
  carrying a marker no environment satisfies, and the module-level `import av` it existed for
  is satisfied by `src/swe_mux/av_stub.py`. That stub has exactly one definition, called both
  by the PyInstaller runtime hook and by `voice.py`; adding a second copy is the regression to
  watch for, because the frozen app and a source checkout would then disagree about dictation.
  Note the limit of an override: it governs this project's resolution, not the wheel's
  published `Requires-Dist`.
- **External-only means absent from the artifact.** The `voice-edge` extra is a source-install
  convenience and is not in `DISTRIBUTED_EXTRAS`.
  `packaging/swe_mux.spec` excludes `edge_tts`, and the bundle verifier fails if the LGPL client
  appears under `_internal/`; only swe-mux's Apache bridge ships.
  The managed Settings action installs the client from PyPI into the data directory after
  distribution, where it remains inspectable and replaceable; it does not alter the bundle
  closure.

After changing any dependency, regenerate the notices:

```bash
uv sync --extra desktop --extra voice-local
uv run python packaging/license_audit.py --write
```

and commit the resulting `THIRD-PARTY-NOTICES.md` and
`packaging/third_party_licenses.json`. `tests/test_license_audit.py` runs the
same reconciliation inside the normal test suite - it needs nothing installed -
so a forgotten regeneration fails the gate rather than surfacing in a later
diligence review. `packaging/license_audit.py --check` is the same check as a
standalone command.

The bundle half runs at build time: `verify_bundle_licenses` in
`packaging/build_desktop.py` inspects the built tree for GPL payloads by
artifact name and proves each LGPL package shipped as replaceable source.
Neither half substitutes for the other, because a wheel's declared license is
not a description of its shipped binaries.

## Verification

Development runs on CPython 3.12: the committed `.python-version` pins it, so
`uv sync` selects it for every fresh venv regardless of what newer interpreters
the machine has. The pin exists because `requires-python` deliberately has no
ceiling (that is published metadata, and the packages that cannot build on a
newer interpreter are dev-only), and `tests/test_python_pin.py` keeps the CI
workflows and both mypy configs agreeing with it.

Run the full gate before opening a pull request:

```bash
uv run pytest tests -q -n auto --dist loadgroup -m "not live_agent and not live_subagent and not live_telemetry and not live_quota and not live_automations and not live_mcp and not live_edge_tts and not live_daemon and not live_model_flag"
uv run ruff check src/swe_mux tests packaging
uv run mypy
cd frontend && npx tsc --noEmit && npm test
```

`.worktree-verify` runs exactly this. Read all of its output rather than piping
it through `tail` or `grep`; a trimmed gate has shipped a failing test green
here before.
The script lowers its own process priority to below normal so a running gate
does not starve interactive work on the same machine; set `MUX_KEEP_PRIORITY=1`
to keep normal priority (for timing the gate itself).

The `not live_*` marks deselect tiers that need an authenticated provider CLI, consume quota,
or reach a third-party service, so none of them belongs in a gate. One of them,
`live_daemon`, needs none of that - it starts a real daemon on an OS-allocated port under a
temp data directory and drives a **shell** session through it. It is out of the gate because
it binds ports and spawns processes, not because you cannot run it; CI runs it on Linux and
Windows, and you can run it directly with
`uv run pytest tests/test_live_daemon.py -m live_daemon`.

## Documentation

Documentation lives in `.docs/`. The routing table in `.docs/CLAUDE.md` says
which document owns which subsystem. A change to a subsystem's behaviour updates
its feature document in the same commit.
