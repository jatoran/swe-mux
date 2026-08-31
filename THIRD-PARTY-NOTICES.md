# Third-party notices

swe-mux is licensed Apache-2.0 (see `LICENSE`). This file records the
third-party software redistributed with it, and is **generated** by
`packaging/license_audit.py --write` from `uv.lock` and
`frontend/package-lock.json` so it cannot drift from what actually ships.
Do not edit it by hand.

## Copyleft components and how to replace them

**swe-mux redistributes no strong-copyleft (GPL/AGPL) code.** The desktop bundle
contains none, which is checked against the built tree on every build rather than
asserted here, and no GPL package resolves into the dependency closure either.

swe-mux uses weak-copyleft (LGPL) libraries. Weak copyleft imposes nothing on
swe-mux's own license: the obligation is to say the library is there, provide
its license, and let you replace it with your own build.

Some of them are redistributed inside the desktop bundle and some are acquired
from PyPI on an explicit press and never hosted by this project. Each section
below says which, because where the copy lives is exactly what a replacement
instruction has to name.

### num2words 0.5.14 - LGPL

LGPL-2.1. Required by `misaki.en`, which imports it at module scope to speak numbers in the Kokoro G2P; there is no misaki English path without it. Same weak-copyleft reasoning as pystray, and since 2026-08-29 a weaker obligation: swe-mux does not redistribute it at all. The desktop bundle stopped carrying the voice closure (ROADMAP Phase 21 Workstream D) and `swe_mux.voice_runtime` fetches this wheel from PyPI on an explicit press, so the bytes travel from the index to the user. The relink condition still holds for the copy that lands and is asserted on it (`voice_runtime._verify_relinkable`). NOT part of the 2026-08-17 audit baseline - it entered with the espeak-free TTS replacement, which is why the gate exists.

**To replace it:** swe-mux does not redistribute `num2words`. The desktop
bundle does not contain it; the packaged app downloads the pinned wheel
from PyPI on an explicit press and unpacks it as plain, readable Python
source under `<data-dir>/voice-runtime/site/num2words/` - acquired on an explicit press by `swe_mux.voice_runtime`, from the same PyPI wheel this repository pins in `swe_mux/voice_wheels.py`.
Overwrite those files with your own build of the same version and relaunch;
the application imports them from disk at startup.
Running from source (`uv sync --extra voice-local && uv run swemuxd`) replaces it the
usual way, with `pip install num2words==<your build>`.

### pystray 0.19.5 - LGPLv3

LGPL-3.0. The Windows tray icon (`desktop.py`). Weak copyleft: it reaches swe-mux only through its public API and imposes nothing on swe-mux's own license. Ships as replaceable source under `_internal/pystray/` so the LGPL relink condition is satisfied.

**To replace it:** the desktop bundle ships `pystray` as plain, readable
Python source under `swe-mux/_internal/pystray/`, not compiled into the
executable archive. Overwrite those files with your own build of the same
version and relaunch; the application imports them from disk at startup.
Running from source (`uv sync && uv run swemuxd`) replaces it the
usual way, with `pip install pystray==<your build>`.

## Modified redistributions

- **@xterm/xterm and @xterm/addon-webgl** (MIT). Patched at install time by `frontend/scripts/patch-xterm-webgl.mjs` and `frontend/scripts/patch-xterm-requestmode.mjs`. The shipped copies are therefore modified versions, not upstream releases.

## Binary redistributions without an OSI license

- **Intel OpenMP runtime (`libiomp5md.dll`)** (Intel Simplified Software License). Vendored inside the `ctranslate2` wheel and copied into `_internal/ctranslate2/`. Redistributed under Intel's terms, which permit binary redistribution as part of an application.

## Models

Downloaded on demand into the data directory, never bundled. Each is pinned
by immutable revision and verified per-file by SHA-256 before it loads.

| Model | License | Upstream |
|---|---|---|
| Kokoro-82M (ONNX int8 weights + voices) | Apache-2.0 | hexgrad/Kokoro-82M |
| Whisper (faster-whisper CTranslate2 conversions) | MIT | openai/whisper |
| Silero VAD | MIT | snakers4/silero-vad |
| en_core_web_sm (spaCy English model) | MIT | explosion/spacy-models |

## Python packages (106)

| Package | Version | License |
|---|---|---|
| addict | 2.4.0 | UNKNOWN |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiohttp | 3.14.1 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache 2.0 |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.14.1 | MIT |
| attrs | 26.1.0 | MIT |
| blis | 1.3.3 | BSD |
| bottle | 0.13.4 | MIT |
| catalogue | 2.0.10 | MIT |
| certifi | 2026.6.17 | MPL-2.0 |
| cffi | 2.1.0 | MIT-0 |
| charset-normalizer | 3.4.9 | MIT |
| click | 8.4.2 | BSD-3-Clause |
| cloudpathlib | 0.24.0 | MIT License |
| clr-loader | 0.3.1 | MIT |
| colorama | 0.4.6 | BSD License |
| confection | 1.3.3 | MIT |
| cryptography | 49.0.0 | Apache-2.0 OR BSD-3-Clause |
| ctranslate2 | 4.8.1 | MIT |
| cymem | 2.0.13 | MIT |
| docopt | 0.6.2 | MIT |
| en-core-web-sm | 3.8.0 | MIT |
| faster-whisper | 1.2.1 | MIT |
| filelock | 3.31.1 | MIT |
| flatbuffers | 25.12.19 | Apache 2.0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.6.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.5.2 | Apache-2.0 |
| http-ece | 1.2.1 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.12.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx2 | 2.12.0 | BSD-3-Clause |
| huggingface-hub | 1.24.0 | Apache-2.0 |
| idna | 3.18 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD License |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| markdown-it-py | 4.2.0 | MIT License |
| markupsafe | 3.0.3 | BSD-3-Clause |
| mcp | 2.0.0 | MIT |
| mcp-types | 2.0.0 | MIT |
| mdurl | 0.1.2 | MIT License |
| misaki | 0.9.4 | Apache License |
| multidict | 6.7.1 | Apache License 2.0 |
| murmurhash | 1.0.15 | MIT |
| num2words | 0.5.14 | LGPL |
| numpy | 2.5.1 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| onnxruntime | 1.27.0 | MIT License |
| opentelemetry-api | 1.44.0 | Apache-2.0 |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pillow | 12.3.0 | MIT-CMU |
| preshed | 3.0.13 | MIT |
| propcache | 0.5.2 | Apache-2.0 |
| protobuf | 7.35.1 | 3-Clause BSD License |
| proxy-tools | 0.1.0 | MIT |
| psutil | 7.2.2 | BSD-3-Clause |
| py-vapid | 1.9.4 | MPL-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic-core | 2.46.4 | MIT |
| pygments | 2.20.0 | BSD-2-Clause |
| pyjwt | 2.13.0 | MIT |
| pystray | 0.19.5 | LGPLv3 |
| python-multipart | 0.0.32 | Apache-2.0 |
| pythonnet | 3.1.0 | MIT |
| pywebpush | 2.3.0 | MPL-2.0 |
| pywebview | 6.2.1 | BSD 3-Clause License |
| pywin32 | 312 | PSF |
| pywinpty | 3.0.5 | MIT License |
| pyyaml | 6.0.3 | MIT |
| referencing | 0.37.0 | MIT |
| regex | 2026.7.19 | Apache-2.0 AND CNRI-Python |
| requests | 2.34.2 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| rpds-py | 2026.6.3 | MIT |
| setuptools | 83.0.0 | MIT |
| shellingham | 1.5.4 | ISC License |
| six | 1.17.0 | MIT |
| smart-open | 8.0.1 | MIT License |
| spacy | 3.8.16 | MIT |
| spacy-legacy | 3.0.12 | MIT |
| spacy-loggers | 1.0.5 | MIT |
| srsly | 2.5.3 | MIT |
| sse-starlette | 3.4.8 | BSD-3-Clause |
| starlette | 1.6.0 | BSD-3-Clause |
| thinc | 8.3.13 | MIT |
| tokenizers | 0.23.1 | Apache Software License |
| tqdm | 4.69.0 | MPL-2.0 AND MIT |
| tree-sitter | 0.26.0 | MIT License |
| tree-sitter-language-pack | 1.14.3 | MIT |
| truststore | 0.10.4 | MIT |
| typer | 0.27.1 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.3 | Apache-2.0 |
| urllib3 | 2.7.0 | MIT |
| uvicorn | 0.52.4 | BSD-3-Clause |
| wasabi | 1.1.3 | MIT |
| watchfiles | 1.2.0 | MIT |
| weasel | 1.0.0 | MIT |
| wrapt | 2.3.0 | BSD-2-Clause |
| yarl | 1.24.2 | Apache-2.0 |

## Frontend packages (96)

| Package | Version | License |
|---|---|---|
| @codemirror/autocomplete | 6.20.3 | MIT |
| @codemirror/commands | 6.11.0 | MIT |
| @codemirror/lang-cpp | 6.0.3 | MIT |
| @codemirror/lang-css | 6.3.1 | MIT |
| @codemirror/lang-go | 6.0.1 | MIT |
| @codemirror/lang-html | 6.4.12 | MIT |
| @codemirror/lang-java | 6.0.2 | MIT |
| @codemirror/lang-javascript | 6.2.5 | MIT |
| @codemirror/lang-json | 6.0.2 | MIT |
| @codemirror/lang-markdown | 6.5.2 | MIT |
| @codemirror/lang-php | 6.0.2 | MIT |
| @codemirror/lang-python | 6.2.1 | MIT |
| @codemirror/lang-rust | 6.0.2 | MIT |
| @codemirror/lang-sql | 6.10.0 | MIT |
| @codemirror/lang-xml | 6.1.0 | MIT |
| @codemirror/lang-yaml | 6.1.3 | MIT |
| @codemirror/language | 6.12.4 | MIT |
| @codemirror/legacy-modes | 6.5.3 | MIT |
| @codemirror/lint | 6.9.7 | MIT |
| @codemirror/search | 6.7.1 | MIT |
| @codemirror/state | 6.7.1 | MIT |
| @codemirror/view | 6.43.9 | MIT |
| @continuity-editor/editor | 0.2.40 | MIT |
| @lezer/common | 1.5.2 | MIT |
| @lezer/cpp | 1.1.6 | MIT |
| @lezer/css | 1.3.6 | MIT |
| @lezer/go | 1.0.1 | MIT |
| @lezer/highlight | 1.2.3 | MIT |
| @lezer/html | 1.3.13 | MIT |
| @lezer/java | 1.1.3 | MIT |
| @lezer/javascript | 1.5.4 | MIT |
| @lezer/json | 1.0.3 | MIT |
| @lezer/lr | 1.4.10 | MIT |
| @lezer/markdown | 1.7.2 | MIT |
| @lezer/php | 1.0.5 | MIT |
| @lezer/python | 1.1.19 | MIT |
| @lezer/rust | 1.0.2 | MIT |
| @lezer/xml | 1.0.6 | MIT |
| @lezer/yaml | 1.0.4 | MIT |
| @marijn/find-cluster-break | 1.0.3 | MIT |
| @protobufjs/aspromise | 1.1.2 | BSD-3-Clause |
| @protobufjs/base64 | 1.1.2 | BSD-3-Clause |
| @protobufjs/codegen | 2.0.5 | BSD-3-Clause |
| @protobufjs/eventemitter | 1.1.1 | BSD-3-Clause |
| @protobufjs/fetch | 1.1.1 | BSD-3-Clause |
| @protobufjs/float | 1.0.2 | BSD-3-Clause |
| @protobufjs/inquire | 1.1.2 | BSD-3-Clause |
| @protobufjs/path | 1.1.2 | BSD-3-Clause |
| @protobufjs/pool | 1.1.0 | BSD-3-Clause |
| @protobufjs/utf8 | 1.1.2 | BSD-3-Clause |
| @ricky0123/vad-web | 0.0.24 | ISC |
| @ricky0123/vad-web/node_modules/long | 4.0.0 | Apache-2.0 |
| @ricky0123/vad-web/node_modules/onnxruntime-common | 1.14.0 | MIT |
| @ricky0123/vad-web/node_modules/onnxruntime-web | 1.14.0 | MIT |
| @types/long | 4.0.2 | MIT |
| @types/node | 26.2.0 | MIT |
| @xterm/addon-clipboard | 0.2.0 | MIT |
| @xterm/addon-fit | 0.11.0 | MIT |
| @xterm/addon-search | 0.16.0 | MIT |
| @xterm/addon-unicode11 | 0.9.0 | MIT |
| @xterm/addon-web-links | 0.12.0 | MIT |
| @xterm/addon-webgl | 0.19.0 | MIT |
| @xterm/xterm | 6.0.0 | MIT |
| classnames | 2.5.1 | MIT |
| crelt | 1.0.7 | MIT |
| diff-match-patch | 1.0.5 | Apache-2.0 |
| events | 3.3.0 | MIT |
| flatbuffers | 1.12.0 | SEE LICENSE IN LICENSE.txt |
| gitdiff-parser | 0.3.1 | MIT |
| graphology | 0.26.0 | MIT |
| graphology-layout-forceatlas2 | 0.10.1 | MIT |
| graphology-types | 0.24.8 | MIT |
| graphology-utils | 2.5.2 | MIT |
| guid-typescript | 1.0.9 | ISC |
| js-base64 | 3.8.1 | BSD-3-Clause |
| js-tokens | 4.0.0 | MIT |
| lodash | 4.18.1 | MIT |
| long | 5.3.2 | Apache-2.0 |
| loose-envify | 1.4.0 | MIT |
| onnx-proto | 4.0.4 | MIT |
| onnx-proto/node_modules/long | 4.0.0 | Apache-2.0 |
| onnx-proto/node_modules/protobufjs | 6.11.6 | BSD-3-Clause |
| onnxruntime-common | 1.20.1 | MIT |
| onnxruntime-web | 1.20.1 | MIT |
| platform | 1.3.6 | MIT |
| preact | 10.29.7 | MIT |
| protobufjs | 7.6.5 | BSD-3-Clause |
| qrcode-generator | 1.5.2 | MIT |
| react | 19.2.8 | MIT |
| react-diff-view | 3.3.3 | MIT |
| shallow-equal | 3.1.0 | MIT |
| sigma | 3.0.3 | MIT |
| style-mod | 4.1.3 | MIT |
| undici-types | 8.3.0 | MIT |
| w3c-keyname | 2.2.8 | MIT |
| warning | 4.0.3 | MIT |

## Notices required by Apache-2.0 dependencies

Apache-2.0 §4(d) requires reproducing any NOTICE file carried by an
Apache-2.0 dependency. The Apache-2.0 packages above that ship one do so
inside their own distribution, which is redistributed intact in the bundle
and in the wheel; their NOTICE files are preserved there rather than being
copied into this file, where they would drift.

## Full license texts

Every package listed above redistributes its own license text inside its
own distribution (`*.dist-info/` for Python, `node_modules/<pkg>/` for the
frontend), which the bundle and the wheel preserve. The canonical texts for
the licenses named here are also available at:

- Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>
- MIT: <https://opensource.org/license/mit>
- BSD-3-Clause: <https://opensource.org/license/bsd-3-clause>
- MPL-2.0: <https://mozilla.org/MPL/2.0/>
- LGPL-3.0: <https://www.gnu.org/licenses/lgpl-3.0.html>
- LGPL-2.1: <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html>
