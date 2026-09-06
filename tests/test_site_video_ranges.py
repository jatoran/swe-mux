"""Exercise the public video's HTTP range implementation without binding a port."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_video_range_delivery() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the website Worker tests")
    result = subprocess.run(
        [node, "--test", "worker/test/media.test.mjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
