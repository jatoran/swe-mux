from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .project_files import revision, safe_note_filename


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def note_body(text: str) -> str:
    if not text.startswith("---\nswe_mux_note = 1\n"):
        return text
    boundary = text.find("\n---\n", 4)
    return text[boundary + 5 :] if boundary >= 0 else text


def space_note_path(data_dir: Path, space_id: str) -> Path:
    return data_dir / "notes" / "spaces" / f"{safe_note_filename(space_id)}.md"


def read_space_note(data_dir: Path, space_id: str, label: str | None) -> dict[str, Any]:
    path = space_note_path(data_dir, space_id)
    label = label or space_id
    use_stored_label = label == space_id
    owner = {"id": "swe-mux", "label": label, "root": str(data_dir)}
    if not path.exists():
        return {
            "project": owner,
            "storage": "app-data",
            "owner_label": label,
            "kind": "spaces",
            "id": space_id,
            "path": str(path),
            "exists": False,
            "revision": "missing",
            "markdown": "",
            "status": "missing",
        }
    try:
        data = path.read_bytes()
        text = data.decode("utf-8")
        if use_stored_label and (match := re.search(r"^label = (.+)$", text, re.MULTILINE)):
            try:
                stored_label = json.loads(match.group(1))
                if isinstance(stored_label, str) and stored_label:
                    label = stored_label
                    owner["label"] = label
            except json.JSONDecodeError:
                pass
        return {
            "project": owner,
            "storage": "app-data",
            "owner_label": label,
            "kind": "spaces",
            "id": space_id,
            "path": str(path),
            "exists": True,
            "revision": revision(data),
            "markdown": note_body(text),
            "status": "ready" if os.access(path, os.W_OK) else "read-only",
        }
    except (OSError, UnicodeDecodeError) as exc:
        return {
            "project": owner,
            "storage": "app-data",
            "owner_label": label,
            "kind": "spaces",
            "id": space_id,
            "path": str(path),
            "exists": True,
            "revision": "unreadable",
            "markdown": "",
            "status": "malformed",
            "error": str(exc),
        }


def write_space_note(
    data_dir: Path,
    space_id: str,
    label: str,
    markdown: str,
    expected_revision: str,
) -> dict[str, Any]:
    if len(markdown.encode("utf-8")) > 1024 * 1024:
        raise ValueError("note exceeds the 1 MiB limit")
    current = read_space_note(data_dir, space_id, label)
    if current["revision"] != expected_revision:
        raise ValueError("space note changed externally; reload before saving")
    header = (
        "---\nswe_mux_note = 1\nkind = \"spaces\"\n"
        f"id = {json.dumps(space_id)}\nlabel = {json.dumps(label)}\nstorage = \"app-data\"\n---\n"
    )
    _atomic_write(space_note_path(data_dir, space_id), (header + markdown).encode("utf-8"))
    return read_space_note(data_dir, space_id, label)


def list_space_notes(data_dir: Path, labels: dict[str, str]) -> list[dict[str, Any]]:
    root = data_dir / "notes" / "spaces"
    if not root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        identity = path.stem
        label = labels.get(identity, identity)
        for key, target in (("id", "identity"), ("label", "label")):
            match = re.search(rf"^{key} = (.+)$", text, re.MULTILINE)
            if not match:
                continue
            try:
                value = json.loads(match.group(1))
                if isinstance(value, str):
                    if target == "identity":
                        identity = value
                    else:
                        label = value
            except json.JSONDecodeError:
                pass
        results.append(
            {
                "id": identity,
                "label": labels.get(identity, label),
                "active": identity in labels,
                "path": str(path),
                "revision": revision(path.read_bytes()),
            }
        )
    return results
