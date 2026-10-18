"""The two hashes a tracked process is identified by.

Standard library only, so the worker process (`worker.py`) can identify the
processes it samples without importing `operational_telemetry`, which brings the
telemetry stores with it. `operational_telemetry` re-exports both names, which is
where every existing caller reads them.
"""

from __future__ import annotations

import hashlib


def process_identity(session_id: str, pid: int, creation_time: float) -> str:
    """A process as one session's: stable across samples, distinct across pid reuse."""
    material = f"{session_id}\0{pid}\0{creation_time:.6f}".encode()
    return hashlib.sha256(material).hexdigest()


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()


__all__ = ["command_hash", "process_identity"]
