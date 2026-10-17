"""What a tool call targets and what it wrote, from the call's native input.

Pure and standard-library only, because two processes compute it: the daemon, when
a `PreToolUse` hook or a transcript record arrives whole, and the hook client
(`hook_bounds`), which has to compute it over the whole input before cutting an
oversized one down to fit the daemon's body cap - after that, only the client ever
held the bytes the hash is of.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

#: Bound on a normalized tool target. The same bound Tier 0's `target` column
#: applies, kept here so the digest that survives truncation is computed against
#: the exact limit that caused it.
TOOL_TARGET_LIMIT = 512
TOOL_TARGET_FIELDS = (
    "file_path",
    "path",
    "notebook_path",
    "filename",
    "command",
    "cmd",
    "pattern",
    "url",
)
TOOL_CONTENT_FIELDS = ("new_string", "content", "contents", "new_source", "text", "patch")
# The file path in an apply_patch envelope, tolerant of how codex wraps it: the
# patch may arrive as raw text with real newlines, or as a string literal inside a
# JS `exec` call where the newlines are escaped (`\n`) and the marker sits mid-line.
# The terminator therefore accepts an escaped or real newline, a closing quote, or
# end of text.
APPLY_PATCH_FILE_RE = re.compile(
    r"\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*(?:\\n|[\r\n\"]|$)"
)


def apply_patch_target(text: str) -> str | None:
    """The first file path an apply_patch envelope names, if the text is one.

    codex writes through apply_patch, whose tool input carries the file path in an
    ``*** Add/Update/Delete File:`` header rather than a key. Without mining it the
    write records with no target and provenance cannot trace that file across
    sessions — the gap the codex live canary exposed (2026-08-16).
    """
    if "*** " not in text:
        return None
    match = APPLY_PATCH_FILE_RE.search(text)
    if not match:
        return None
    path = match.group(1).strip().strip('"').strip()
    return path[:512] or None


def target_digest(raw: str) -> str | None:
    """A discriminator for a target the stored column cannot hold whole.

    A `target` is bounded to `TOOL_TARGET_LIMIT` characters, and a long shell
    command is exactly the case where the *prefix* is the shared part: three
    iterations of the same heredoc-written probe script agree for 512 characters
    and differ only after it. The truncated prefix therefore collapses distinct
    actions onto one fingerprint (observed live, 2026-08-21: 227 command facts in
    one day sat at exactly the bound). This digest of the untruncated text is
    what keeps them apart, and it is computed here because this is the last place
    the whole string exists — the event payload carries the bounded copy.

    None when nothing was lost, so an untruncated target keeps a fingerprint that
    depends on the target alone.
    """
    if len(raw) <= TOOL_TARGET_LIMIT:
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def tool_call_evidence(tool_input: Any) -> tuple[str | None, str | None, str | None]:
    """Normalized target, parse-time content hash, and full-target digest.

    Runs at the adapter boundary while the native input is still in hand, so the
    hash is of the exact bytes the agent wrote — race-free, unlike reading the
    file back off disk after the event has queued. Native shapes never leave here.
    The third element is the discriminator for a target too long to store whole
    (`target_digest`), and is None whenever the target survived intact.
    """
    if isinstance(tool_input, str):
        raw_text = tool_input
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, ValueError):
            # Not JSON: codex passes an apply_patch envelope as the raw tool input,
            # whose file path lives in a `*** Add/Update/Delete File:` header rather
            # than a key. Hash the exact patch bytes as the write's content.
            patched = apply_patch_target(raw_text)
            content = (
                hashlib.sha256(raw_text.encode("utf-8")).hexdigest() if raw_text else None
            )
            return patched, content, None
    if not isinstance(tool_input, dict):
        return None, None, None
    target: str | None = None
    digest: str | None = None
    for key in TOOL_TARGET_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            raw = value.strip()
            target = raw[:TOOL_TARGET_LIMIT]
            digest = target_digest(raw)
            break
    # A patch may arrive wrapped in a dict (`{"input": "*** Begin Patch..."}`); mine
    # its file path when no explicit target key carried one.
    if target is None:
        for key in ("input", "patch"):
            value = tool_input.get(key)
            if isinstance(value, str):
                patched = apply_patch_target(value)
                if patched:
                    target = patched
                    break
    parts: list[str] = []
    for key in TOOL_CONTENT_FIELDS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                parts.append(edit["new_string"])
    content_hash = (
        hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest() if parts else None
    )
    return target, content_hash, digest
