"""Resolved-closure license audit and third-party notice generation.

Phase 10.5. Two jobs that read the same data, so they live in one module:

1. **The gate.** Fail when a copyleft distribution enters the distributed
   closure without an explicit allowlist entry. The 2026-08-17 audit's whole
   lesson is that *declared package metadata does not describe shipped
   binaries* - PyAV declares BSD-3-Clause and ships GPL FFmpeg, sherpa-onnx
   declares Apache-2.0 and statically links espeak-ng - so metadata alone is
   never sufficient. This module owns the metadata half; the artifact half
   (`verify_bundle_licenses` in `build_desktop.py`) inspects the built bundle
   for the known payloads by name.
2. **The notices.** Generate `THIRD-PARTY-NOTICES.md` from the lockfiles so it
   cannot drift from what is actually shipped.

Why membership and licenses come from different places
------------------------------------------------------
`uv.lock` is authoritative for *which* packages are in the distributed closure
and is readable with no environment at all. It does not record licenses. The
installed `dist-info` records licenses but is only complete when every
distributed extra is installed, and a bare `uv sync` installs no extras at all,
so `pystray` and the `voice-local` closure behind it are absent from such a venv.

That asymmetry is why the closure walk is defined over `DISTRIBUTED_EXTRAS`
rather than over whatever is installed: the answer must not depend on which
extras the machine running the audit happened to sync.

So the split is:

- ``--write`` needs the full distributed closure installed
  (``uv sync --extra desktop --extra voice-local``). It reads licenses from
  `dist-info`, writes the human-readable `THIRD-PARTY-NOTICES.md`, and writes
  the machine-readable sidecar `packaging/third_party_licenses.json`.
- ``--check`` needs nothing installed. It reconciles the sidecar against
  `uv.lock` plus `frontend/package-lock.json` and fails on a membership
  difference or an unallowlisted copyleft entry.

That way a new dependency entering the closure fails the gate on any machine,
while the license facts themselves are refreshed deliberately by a maintainer
who has everything installed.
"""

from __future__ import annotations

import argparse
import email
import json
import re
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV_LOCK = ROOT / "uv.lock"
NPM_LOCK = ROOT / "frontend" / "package-lock.json"
SIDECAR = Path(__file__).resolve().parent / "third_party_licenses.json"
NOTICES = ROOT / "THIRD-PARTY-NOTICES.md"

# Extras whose packages are redistributed in the desktop bundle. `desktop` ships
# (tray icon + webview) and so does `voice-local` (on-device Kokoro TTS and
# faster-whisper dictation): both are built into the bundle, so both are part of
# the distributed closure whatever a given developer happens to have synced.
# That distinction is the whole point of defining the walk over a declared set of
# extras rather than over the installed environment - `voice-local` carries the
# LGPL `num2words`, and a closure walked over a bare `uv sync` would report a
# clean, copyleft-free bundle that ships it anyway.
#
# `preview-capture` does NOT ship: Playwright is an optional local install the
# user opts into, never bundled, which is the known gap CONTROL_PLANE_ROADMAP.md
# §9 records against Phase 11.
# `voice-edge` does NOT ship either: it is a convenience extra for source users
# and the same LGPL client is external to a frozen install. The spec excludes it
# explicitly and the artifact half rejects it if it appears under `_internal/`.
DISTRIBUTED_EXTRAS = ("desktop", "voice-local")

# The environments swe-mux is distributed for. A dependency counts as shipped if
# it is reachable on ANY of them, because the Linux artifact carries Linux-only
# packages whether or not the audit runs on Windows. `requires-python = ">=3.12"`
# and both sides of the common `python_full_version < '3.13'` markers are covered
# so a package pinned to either branch is still audited.
SUPPORTED_ENVIRONMENTS = tuple(
    {"sys_platform": platform, "os_name": os_name, "platform_system": system,
     "python_full_version": version, "python_version": version.rsplit(".", 1)[0]}
    for platform, os_name, system in (
        ("win32", "nt", "Windows"),
        ("linux", "posix", "Linux"),
        ("darwin", "posix", "Darwin"),
    )
    for version in ("3.12.0", "3.14.0")
)

# Copyleft that ships, with the reason it is allowed to. An entry here is a
# deliberate decision, not a suppression: each one still gets a notice section
# and relink instructions in THIRD-PARTY-NOTICES.md, and `verify_bundle_licenses`
# proves the relink condition holds in the built bundle rather than asserting it.
ALLOWLIST: dict[str, str] = {
    "pystray": (
        "LGPL-3.0. The Windows tray icon (`desktop.py`). Weak copyleft: it "
        "reaches swe-mux only through its public API and imposes nothing on "
        "swe-mux's own license. Ships as replaceable source under "
        "`_internal/pystray/` so the LGPL relink condition is satisfied."
    ),
    "num2words": (
        "LGPL-2.1. Required by `misaki.en`, which imports it at module scope "
        "to speak numbers in the Kokoro G2P; there is no misaki English path "
        "without it. Same weak-copyleft reasoning and same relink treatment as "
        "pystray. NOT part of the 2026-08-17 audit baseline - it entered with "
        "the espeak-free TTS replacement, which is why the gate exists."
    ),
}


def owning_extra(name: str, lock_path: Path = UV_LOCK) -> str | None:
    """The distributed extra a package is reached through, if it is optional.

    Only used to tell a reader of the notices how to install a replacement:
    `pystray` arrives with `--extra desktop` and `num2words` with
    `--extra voice-local`, so a single hard-coded `uv sync` line would be wrong
    for both once the voice closure moved behind an extra.
    """
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    root = next(
        (entry for entry in data.get("package", []) if entry["name"] == "swe-mux"),
        None,
    )
    if root is None:
        return None
    for extra in DISTRIBUTED_EXTRAS:
        for dep in root.get("optional-dependencies", {}).get(extra, []):
            if dep["name"] == name:
                return extra
    return None

# Packages whose declared license is known to be false about what their wheel
# actually ships. This is the audit's central finding encoded, and it is the one
# thing metadata can never tell you - each entry is here because someone read
# the binary. Without it the gate reads PyAV as BSD-3-Clause and reports a clean
# closure while 63 MB of GPL FFmpeg sits inside it.
#
# `av` is no longer in the closure at all (Phase 11 dropped it; see `[tool.uv]`
# in pyproject.toml), so its entry is now a **tripwire** rather than a
# description: it costs nothing while the override holds, and the day something
# reintroduces PyAV the gate reads it as GPL-2.0-or-later and fails instead of
# waving through a package that says BSD-3-Clause.
MISDECLARED: dict[str, tuple[str, str]] = {
    "av": (
        "GPL-2.0-or-later",
        "PyAV declares BSD-3-Clause. Its bundled `av.libs` is 63 MB of FFmpeg, "
        "and avcodec's import table links libx264 and libx265 (GPL-2.0-or-later); "
        "the configure string carries `--enable-libx264 --enable-libx265 "
        "--enable-version3` while `av_license()` compiles to the single string "
        "'LGPL version 3 or later'. The linkage governs, not the self-description.",
    ),
}

# In the resolved closure but deliberately NOT in the desktop bundle. Each entry
# records what keeps it out and what remains unresolved, because "absent from the
# bundle" is not the same claim as "absent from the closure": a wheel install
# resolves the full dependency graph and takes these with it.
#
# Empty since Phase 11 dropped `av`, which was the only entry. That is the
# intended steady state - an entry here is a package a *user* installs and we do
# not ship, which is weaker than not depending on it - so the mechanism stays for
# the next one rather than being deleted along with its last occupant.
BUNDLE_EXCLUDED: dict[str, str] = {}

# Licenses that are file-level copyleft. They impose obligations on the *files*
# of that package only, never on swe-mux, so they need license text reproduced
# and nothing else. Recorded separately from `ALLOWLIST` because they are not a
# decision - there is nothing to decide.
FILE_LEVEL_COPYLEFT = {"MPL-2.0", "MPL-1.1", "EPL-2.0", "CDDL-1.0"}

_STRONG = re.compile(r"\bA?GPL|GENERAL PUBLIC LICENSE", re.IGNORECASE)
_WEAK = re.compile(r"\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC", re.IGNORECASE)
_FILE_LEVEL = re.compile(r"\bMPL\b|MOZILLA PUBLIC|\bEPL\b|ECLIPSE PUBLIC|\bCDDL\b", re.IGNORECASE)

# Models are downloaded on demand and never bundled, but they are redistributed
# in the sense that swe-mux tells the user to fetch them and then ships their
# output. Listed in the notices for completeness; they are not packages and no
# lockfile knows about them.
MODELS = (
    ("Kokoro-82M (ONNX int8 weights + voices)", "Apache-2.0", "hexgrad/Kokoro-82M"),
    ("Whisper (faster-whisper CTranslate2 conversions)", "MIT", "openai/whisper"),
    ("Silero VAD", "MIT", "snakers4/silero-vad"),
    ("en_core_web_sm (spaCy English model)", "MIT", "explosion/spacy-models"),
)

# Redistributed with modifications, which the license requires stating.
MODIFIED_VENDORED = (
    (
        "@xterm/xterm and @xterm/addon-webgl",
        "MIT",
        "Patched at install time by `frontend/scripts/patch-xterm-webgl.mjs` and "
        "`frontend/scripts/patch-xterm-requestmode.mjs`. The shipped copies are "
        "therefore modified versions, not upstream releases.",
    ),
)

# Ships inside the ctranslate2 wheel under Intel's own redistribution terms
# rather than an OSI license, so no lockfile license field describes it.
BINARY_REDISTRIBUTIONS = (
    (
        "Intel OpenMP runtime (`libiomp5md.dll`)",
        "Intel Simplified Software License",
        "Vendored inside the `ctranslate2` wheel and copied into "
        "`_internal/ctranslate2/`. Redistributed under Intel's terms, which "
        "permit binary redistribution as part of an application.",
    ),
)


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    license: str
    ecosystem: str  # "python" | "npm"

    @property
    def category(self) -> str:
        return classify(self.license)


def classify(license_text: str) -> str:
    """Bucket a license string. Order matters: LGPL contains "GPL"."""
    text = (license_text or "").strip()
    if not text:
        return "unknown"
    if _WEAK.search(text):
        return "weak-copyleft"
    if _STRONG.search(text):
        return "strong-copyleft"
    if _FILE_LEVEL.search(text):
        return "file-level-copyleft"
    return "permissive"


def _reachable(marker: str | None) -> bool:
    """True when `marker` can hold on any platform swe-mux is distributed for.

    A license audit has to cover every target, not the machine it runs on: a
    dependency that only installs on Linux still ships in the Linux artifact.
    So a marker is satisfied if ANY supported environment satisfies it, which
    also drops the genuinely unreachable ones - `httpx2` carries a Pyodide-only
    `httpx2-jsfetch` under `sys_platform == 'emscripten'` that no swe-mux
    artifact can ever contain.
    """
    if not marker:
        return True
    try:
        from packaging.markers import Marker
    except ModuleNotFoundError:  # pragma: no cover - packaging is ubiquitous
        raise SystemExit(
            "packaging is required to evaluate dependency markers. "
            "Run this under `uv run`."
        ) from None
    parsed = Marker(marker)
    return any(parsed.evaluate(environment) for environment in SUPPORTED_ENVIRONMENTS)


def python_closure(lock_path: Path = UV_LOCK) -> dict[str, str]:
    """Walk `uv.lock` from swe-mux's runtime deps to the distributed closure.

    Dev groups are excluded deliberately: `pyinstaller` is GPL-2.0-with-exception
    and is a build tool that is never distributed, so including it would make the
    gate cry wolf on the one copyleft package that genuinely cannot matter.
    """
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = {entry["name"]: entry for entry in data.get("package", [])}
    root = packages.get("swe-mux")
    if root is None:
        raise SystemExit(f"{lock_path} has no swe-mux package entry; cannot resolve the closure.")

    def edges(entry: dict) -> list[str]:
        return [
            dep["name"]
            for dep in entry.get("dependencies", [])
            if _reachable(dep.get("marker"))
        ]

    seeds = edges(root)
    optional = root.get("optional-dependencies", {})
    for extra in DISTRIBUTED_EXTRAS:
        seeds += [
            dep["name"]
            for dep in optional.get(extra, [])
            if _reachable(dep.get("marker"))
        ]

    seen: dict[str, str] = {}
    queue = list(seeds)
    while queue:
        name = queue.pop()
        if name in seen:
            continue
        entry = packages.get(name)
        if entry is None:
            raise SystemExit(f"{lock_path} references {name!r} with no package entry.")
        seen[name] = entry["version"]
        queue += edges(entry)
        # A transitive package's own extras are only pulled when something asked
        # for them; uv records that by listing the extra's members directly in
        # the requiring package's `dependencies`, so there is nothing to expand.
    return dict(sorted(seen.items()))


def npm_closure(lock_path: Path = NPM_LOCK) -> dict[str, tuple[str, str]]:
    """Non-dev packages from `package-lock.json`, which records licenses inline."""
    data = json.loads(lock_path.read_text(encoding="utf-8"))
    result: dict[str, tuple[str, str]] = {}
    for path, entry in data.get("packages", {}).items():
        if not path or entry.get("dev") or entry.get("devOptional"):
            continue
        name = entry.get("name") or path.split("node_modules/", 1)[-1]
        version = entry.get("version", "")
        if not version:
            continue
        license_text = entry.get("license") or ""
        if isinstance(license_text, dict):
            license_text = license_text.get("type", "")
        result[name] = (version, str(license_text))
    return dict(sorted(result.items()))


def _site_packages() -> Path:
    for candidate in (
        ROOT / ".venv" / "Lib" / "site-packages",
        ROOT / ".venv" / "lib" / f"python3.{sys.version_info.minor}" / "site-packages",
    ):
        if candidate.is_dir():
            return candidate
    return Path(next(iter(p for p in sys.path if p.endswith("site-packages")), "."))


def installed_licenses(names: Iterable[str]) -> dict[str, str]:
    """Read licenses out of installed `dist-info`, keyed by normalized name."""
    site = _site_packages()
    wanted = {_normalize(name) for name in names}
    found: dict[str, str] = {}
    for dist_info in site.glob("*.dist-info"):
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            continue
        message = email.message_from_string(
            metadata.read_text(encoding="utf-8", errors="replace")
        )
        name = _normalize(message.get("Name") or "")
        if name not in wanted:
            continue
        found[name] = _license_of(message, dist_info)
    return found


# Enough of each license's distinctive wording to identify it from the text
# itself. Ordered most-specific first, which is load-bearing: the LGPL text also
# contains "GNU GENERAL PUBLIC LICENSE" by reference, so the Lesser check has to
# run before the plain GPL one or every LGPL package reads as strong copyleft.
_TEXT_SIGNATURES = (
    ("LGPL-2.1", "gnu lesser general public license version 2.1"),
    ("LGPL-3.0", "gnu lesser general public license version 3"),
    ("LGPL", "gnu lesser general public license"),
    ("LGPL", "gnu library general public license"),
    ("AGPL-3.0", "gnu affero general public license"),
    ("GPL", "gnu general public license"),
    ("MPL-2.0", "mozilla public license version 2.0"),
    ("Apache-2.0", "apache license version 2.0"),
    ("Apache-2.0", "www.apache.org/licenses/license-2.0"),
    ("MIT", "permission is hereby granted, free of charge"),
    ("ISC", "permission to use, copy, modify, and/or distribute this software"),
    ("BSD", "redistribution and use in source and binary forms"),
)


def _license_of(message: email.message.Message, dist_info: Path) -> str:
    expression = (message.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = [
        item.split("::")[-1].strip()
        for item in message.get_all("Classifier") or []
        if item.startswith("License ::")
    ]
    declared = (message.get("License") or "").strip().splitlines()
    first = declared[0].strip() if declared else ""
    # A `License:` field holding the whole license text is common and useless
    # here; prefer the classifier when the field is clearly prose.
    if first and len(first) <= 60:
        return first
    if classifiers:
        return "; ".join(classifiers)
    # PEP 639 packages increasingly declare nothing and ship only the file. Read
    # the actual text, which is better evidence than any declaration anyway.
    sniffed = _sniff_license_file(message, dist_info)
    return sniffed or first[:60]


def _sniff_license_file(message: email.message.Message, dist_info: Path) -> str:
    names = [item.strip() for item in message.get_all("License-File") or []]
    candidates: list[Path] = []
    for name in names:
        candidates += [dist_info / "licenses" / name, dist_info / name]
    candidates += sorted(dist_info.glob("licenses/*")) + sorted(dist_info.glob("LICENSE*"))
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")[:4000]
        # Licenses are centred, indented, and wrapped differently by every
        # project that vendors them, so match on collapsed whitespace.
        head = re.sub(r"\s+", " ", raw).strip().lower()
        for spdx, signature in _TEXT_SIGNATURES:
            if signature in head:
                return spdx
    return ""


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def collect() -> list[Package]:
    """The full distributed closure with licenses. Requires a complete install."""
    python = python_closure()
    licenses = installed_licenses(python)
    missing = sorted(name for name in python if _normalize(name) not in licenses)
    if missing:
        extras = " ".join(f"--extra {extra}" for extra in DISTRIBUTED_EXTRAS)
        raise SystemExit(
            "Cannot read licenses for "
            + ", ".join(missing)
            + f".\nRun `uv sync {extras}` first: --write needs the whole distributed "
            "closure installed, and a bare `uv sync` installs no extras."
        )
    result = [
        Package(
            name,
            version,
            MISDECLARED[name][0] if name in MISDECLARED else licenses[_normalize(name)],
            "python",
        )
        for name, version in python.items()
    ]
    result += [
        Package(name, version, license_text, "npm")
        for name, (version, license_text) in npm_closure().items()
    ]
    return sorted(result, key=lambda item: (item.ecosystem, item.name.lower()))


def violations(packages: Iterable[Package]) -> list[str]:
    """Copyleft in the closure with no allowlist entry, plus unknown licenses."""
    problems: list[str] = []
    for package in packages:
        category = package.category
        if category in {"permissive", "file-level-copyleft"}:
            continue
        if category == "unknown":
            problems.append(
                f"{package.name} {package.version} ({package.ecosystem}) declares no "
                "license. An unknown license is not a permissive one - resolve it or "
                "allowlist it with a reason."
            )
            continue
        if package.name in BUNDLE_EXCLUDED:
            # Copyleft that is in the closure but kept out of the bundle. Not a
            # violation, and deliberately not silent either: it is named in the
            # notices under its own heading with what remains unresolved.
            continue
        if package.name not in ALLOWLIST:
            problems.append(
                f"{package.name} {package.version} ({package.ecosystem}) is "
                f"{category} ({package.license}) and is neither allowlisted nor "
                "excluded from the bundle. Either remove it, add an ALLOWLIST entry "
                "in packaging/license_audit.py recording why it may ship and how the "
                "relink condition is met, or add a BUNDLE_EXCLUDED entry recording "
                "what keeps it out of the bundle."
            )
    return problems


def write_sidecar(packages: list[Package]) -> None:
    SIDECAR.write_text(
        json.dumps(
            {
                "comment": (
                    "Generated by packaging/license_audit.py --write. Do not edit by "
                    "hand. Regenerate after any dependency change."
                ),
                "packages": [asdict(package) for package in packages],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_sidecar() -> list[Package]:
    if not SIDECAR.is_file():
        raise SystemExit(
            f"{SIDECAR} is missing. Run `uv run python packaging/license_audit.py --write` "
            "with the full closure installed."
        )
    data = json.loads(SIDECAR.read_text(encoding="utf-8"))
    return [Package(**entry) for entry in data["packages"]]


def membership_drift(recorded: list[Package]) -> list[str]:
    """Reconcile the sidecar against the lockfiles, with no environment needed."""
    problems: list[str] = []
    for ecosystem, actual in (
        ("python", python_closure()),
        ("npm", {name: version for name, (version, _) in npm_closure().items()}),
    ):
        known = {
            package.name: package.version
            for package in recorded
            if package.ecosystem == ecosystem
        }
        for name, version in actual.items():
            if name not in known:
                problems.append(
                    f"{name} {version} ({ecosystem}) is in the lockfile but not in "
                    "the recorded notices."
                )
            elif known[name] != version:
                problems.append(
                    f"{name} moved {known[name]} -> {version} ({ecosystem}) since the "
                    "notices were generated."
                )
        for name in known:
            if name not in actual:
                problems.append(
                    f"{name} ({ecosystem}) is in the recorded notices but no longer in "
                    "the lockfile."
                )
    if problems:
        extras = " ".join(f"--extra {extra}" for extra in DISTRIBUTED_EXTRAS)
        problems.append(
            f"Regenerate with `uv sync {extras}` then "
            "`uv run python packaging/license_audit.py --write`."
        )
    return problems


def _source_replacement_lines(name: str) -> list[str]:
    """How a source install substitutes its own build of an allowlisted package."""
    extra = owning_extra(name)
    install = f"uv sync --extra {extra}" if extra else "uv sync"
    return [
        f"Running from source (`{install} && uv run muxd`) replaces it the",
        f"usual way, with `pip install {name}==<your build>`.",
    ]


def render_notices(packages: list[Package]) -> str:
    lines = [
        "# Third-party notices",
        "",
        "swe-mux is licensed Apache-2.0 (see `LICENSE`). This file records the",
        "third-party software redistributed with it, and is **generated** by",
        "`packaging/license_audit.py --write` from `uv.lock` and",
        "`frontend/package-lock.json` so it cannot drift from what actually ships.",
        "Do not edit it by hand.",
        "",
        "## Copyleft components and how to replace them",
        "",
        "**swe-mux redistributes no strong-copyleft (GPL/AGPL) code.** The desktop bundle",
        "contains none, which is checked against the built tree on every build rather than",
        "asserted here, and no GPL package resolves into the dependency closure either.",
        "",
        "swe-mux does ship two weak-copyleft (LGPL) libraries. Weak copyleft imposes",
        "nothing on swe-mux's own license: the obligation is to say the library is there,",
        "provide its license, and let you replace it with your own build.",
        "",
    ]
    for name, reason in sorted(ALLOWLIST.items()):
        package = next((item for item in packages if item.name == name), None)
        version = package.version if package else "(not in the current closure)"
        license_text = package.license if package else "?"
        lines += [
            f"### {name} {version} - {license_text}",
            "",
            reason,
            "",
            f"**To replace it:** the desktop bundle ships `{name}` as plain, readable",
            f"Python source under `swe-mux/_internal/{name}/`, not compiled into the",
            "executable archive. Overwrite those files with your own build of the same",
            "version and relaunch; the application imports them from disk at startup.",
            *_source_replacement_lines(name),
            "",
        ]
    # Rendered only when there is something to render. A standing heading over an
    # empty list would read as a claim that such packages exist, which is the
    # opposite of what an empty `BUNDLE_EXCLUDED` means.
    if BUNDLE_EXCLUDED:
        lines += [
            "## In the dependency closure but not redistributed",
            "",
            "These resolve as dependencies and are installed by `pip install swe-mux`, but",
            "swe-mux redistributes none of them: the desktop bundle excludes each one, and a",
            "wheel install fetches them from PyPI rather than from us. They are listed anyway,",
            "because an audit that reads only what is shipped and calls the closure clean is",
            "the exact mistake this file exists to prevent.",
            "",
        ]
        for name, reason in sorted(BUNDLE_EXCLUDED.items()):
            package = next((item for item in packages if item.name == name), None)
            version = package.version if package else "(not in the current closure)"
            true_license = MISDECLARED.get(name, (package.license if package else "?", ""))[0]
            declared = MISDECLARED.get(name, ("", ""))[1]
            lines += [f"### {name} {version} - {true_license}", ""]
            if declared:
                lines += [f"*Declared license is misleading.* {declared}", ""]
            lines += [reason, ""]

    lines += [
        "## Modified redistributions",
        "",
    ]
    for name, license_text, note in MODIFIED_VENDORED:
        lines += [f"- **{name}** ({license_text}). {note}", ""]

    lines += ["## Binary redistributions without an OSI license", ""]
    for name, license_text, note in BINARY_REDISTRIBUTIONS:
        lines += [f"- **{name}** ({license_text}). {note}", ""]

    lines += [
        "## Models",
        "",
        "Downloaded on demand into the data directory, never bundled. Each is pinned",
        "by immutable revision and verified per-file by SHA-256 before it loads.",
        "",
        "| Model | License | Upstream |",
        "|---|---|---|",
    ]
    for name, license_text, upstream in MODELS:
        lines.append(f"| {name} | {license_text} | {upstream} |")

    for ecosystem, heading in (("python", "Python packages"), ("npm", "Frontend packages")):
        members = [item for item in packages if item.ecosystem == ecosystem]
        lines += [
            "",
            f"## {heading} ({len(members)})",
            "",
            "| Package | Version | License |",
            "|---|---|---|",
        ]
        for package in members:
            lines.append(f"| {package.name} | {package.version} | {package.license} |")

    lines += [
        "",
        "## Notices required by Apache-2.0 dependencies",
        "",
        "Apache-2.0 §4(d) requires reproducing any NOTICE file carried by an",
        "Apache-2.0 dependency. The Apache-2.0 packages above that ship one do so",
        "inside their own distribution, which is redistributed intact in the bundle",
        "and in the wheel; their NOTICE files are preserved there rather than being",
        "copied into this file, where they would drift.",
        "",
        "## Full license texts",
        "",
        "Every package listed above redistributes its own license text inside its",
        "own distribution (`*.dist-info/` for Python, `node_modules/<pkg>/` for the",
        "frontend), which the bundle and the wheel preserve. The canonical texts for",
        "the licenses named here are also available at:",
        "",
        "- Apache-2.0: <https://www.apache.org/licenses/LICENSE-2.0>",
        "- MIT: <https://opensource.org/license/mit>",
        "- BSD-3-Clause: <https://opensource.org/license/bsd-3-clause>",
        "- MPL-2.0: <https://mozilla.org/MPL/2.0/>",
        "- LGPL-3.0: <https://www.gnu.org/licenses/lgpl-3.0.html>",
        "- LGPL-2.1: <https://www.gnu.org/licenses/old-licenses/lgpl-2.1.html>",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check",
        action="store_true",
        help="Fail on closure drift or unallowlisted copyleft. Needs no install.",
    )
    group.add_argument(
        "--write",
        action="store_true",
        help=(
            "Regenerate THIRD-PARTY-NOTICES.md and the sidecar. Needs every "
            "distributed extra installed: "
            + " ".join(f"--extra {extra}" for extra in DISTRIBUTED_EXTRAS)
            + "."
        ),
    )
    args = parser.parse_args(argv)

    if args.write:
        packages = collect()
        problems = violations(packages)
        if problems:
            print("Refusing to write notices for a closure that fails the gate:")
            for problem in problems:
                print(f"  - {problem}")
            return 1
        write_sidecar(packages)
        NOTICES.write_text(render_notices(packages), encoding="utf-8", newline="\n")
        print(f"Wrote {NOTICES} and {SIDECAR} ({len(packages)} packages).")
        return 0

    recorded = read_sidecar()
    problems = membership_drift(recorded) + violations(recorded)
    if not NOTICES.is_file():
        problems.append(f"{NOTICES} is missing.")
    elif NOTICES.read_text(encoding="utf-8") != render_notices(recorded):
        problems.append(
            f"{NOTICES} does not match the recorded closure. Regenerate it with "
            "`uv run python packaging/license_audit.py --write`."
        )
    if problems:
        print("License audit failed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"License audit clean ({len(recorded)} packages in the distributed closure).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
