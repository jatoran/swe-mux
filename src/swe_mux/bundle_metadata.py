"""What a built desktop bundle says about itself, and why anyone asks.

One small JSON file written into the root of `dist/swe-mux` at build time
(`packaging/build_desktop.py`) and read back out of a *release archive* by the
updater (`update_install.py`) before anything is staged. It exists for exactly
one question that cannot be answered any other way:

**Does installing this release require a new PTY supervisor?**

That question is not cosmetic. The supervisor owns every live pseudoterminal and
is the reason an app swap preserves sessions at all; updating it reaps the whole
fleet, which `CLAUDE.md` treats as a deliberate out-of-band act. So the updater
has to know the answer *before* it swaps, and the only honest source is the
incoming bundle itself. Reading it from the archive is the cheapest correct
answer: the alternative is executing a freshly downloaded binary to ask it, which
is a strictly worse thing to do with a file that arrived over the network.

`supervisor_protocol` is the discriminator rather than a source hash, and that is
a measured choice, not a preference. `build_desktop.supervisor_source_hash()`
mixes in the *build machine's* installed `pywinpty`/`psutil`/`pyinstaller`
versions, so two machines building identical source produce different hashes -
comparing them across a release would refuse every update forever. The protocol
number is the thing the daemon and the supervisor actually negotiate on
(`supervisor.py`'s `hello` refuses a mismatch outright), it is compiled into both
halves, and it is what `CLAUDE.md` names as the change that forces a reap.

`schema` is honoured before any field is read, for the same reason
`update_check.parse_manifest` does it: a build installed today may be reading a
bundle produced by a build years newer, and an unrecognized document must answer
"cannot tell" instead of guessing. "Cannot tell" is a refusal here, because the
property being protected is the operator's live fleet.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The file's name inside the bundle root (`dist/swe-mux/bundle.json`) and, with
#: the archive's top-level directory in front of it, inside a release archive.
BUNDLE_METADATA_NAME = "bundle.json"

#: This document's schema, independent of the update manifest's.
BUNDLE_METADATA_SCHEMA = 1

#: Reasons a read produced no metadata. A closed set, so a caller branches on the
#: word rather than on a message.
BUNDLE_METADATA_OK = "ok"
BUNDLE_METADATA_MISSING = "missing"
BUNDLE_METADATA_MALFORMED = "malformed"
BUNDLE_METADATA_UNSUPPORTED_SCHEMA = "unsupported_schema"


@dataclass(frozen=True, slots=True)
class BundleMetadata:
    """What a bundle claims about itself, reduced to what a decision needs."""

    version: str
    supervisor_protocol: int
    platform: str
    built: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": BUNDLE_METADATA_SCHEMA,
            "version": self.version,
            "supervisor_protocol": self.supervisor_protocol,
            "platform": self.platform,
            "built": self.built,
        }


def bundle_metadata(
    *,
    version: str,
    supervisor_protocol: int,
    platform: str,
    built: str | None = None,
) -> BundleMetadata:
    """Describe a bundle being built now.

    `built` is a UTC timestamp for a human reading the file; nothing decides on
    it, which is why it is allowed to default rather than being demanded.
    """
    stamp = built or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BundleMetadata(
        version=str(version),
        supervisor_protocol=int(supervisor_protocol),
        platform=str(platform),
        built=stamp,
    )


def parse_bundle_metadata(payload: object) -> tuple[BundleMetadata | None, str]:
    """`(metadata, reason)` for a decoded `bundle.json`.

    The schema is checked before any field is read, and a `supervisor_protocol`
    that is not an integer is `malformed` rather than defaulted: a default here
    would be an assumption about whether the operator's sessions survive.
    """
    if not isinstance(payload, dict):
        return None, BUNDLE_METADATA_MALFORMED
    if payload.get("schema") != BUNDLE_METADATA_SCHEMA:
        return None, BUNDLE_METADATA_UNSUPPORTED_SCHEMA
    protocol = payload.get("supervisor_protocol")
    if isinstance(protocol, bool) or not isinstance(protocol, int):
        return None, BUNDLE_METADATA_MALFORMED
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return None, BUNDLE_METADATA_MALFORMED
    platform = payload.get("platform")
    built = payload.get("built")
    return (
        BundleMetadata(
            version=version.strip(),
            supervisor_protocol=protocol,
            platform=platform.strip() if isinstance(platform, str) else "",
            built=built.strip() if isinstance(built, str) else "",
        ),
        BUNDLE_METADATA_OK,
    )


def read_bundle_metadata(bundle_root: Path) -> tuple[BundleMetadata | None, str]:
    """Read `bundle.json` out of an extracted bundle directory."""
    path = Path(bundle_root) / BUNDLE_METADATA_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, BUNDLE_METADATA_MISSING
    except (OSError, ValueError):
        return None, BUNDLE_METADATA_MALFORMED
    return parse_bundle_metadata(payload)


def write_bundle_metadata(bundle_root: Path, metadata: BundleMetadata) -> Path:
    """Write `bundle.json` into a built bundle root, returning its path."""
    path = Path(bundle_root) / BUNDLE_METADATA_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metadata.as_dict(), indent=2) + "\n", encoding="utf-8")
    return path
