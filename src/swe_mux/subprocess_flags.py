from __future__ import annotations

import os
import subprocess


def background_creation_flags() -> int:
    """Keep daemon-owned console programs invisible in a windowed Windows build."""
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
