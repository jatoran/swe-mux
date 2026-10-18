"""A stand-in daemon that hosts one worker, prints its pids, and waits to be killed.

`tests/test_worker_host.py` kills this process outright - no teardown, no
shutdown request - to show the worker dies with its daemon. Run with the data
directory as the only argument.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from swe_mux.worker_host import WorkerHost


@dataclass
class _Config:
    data_dir: Path
    worker_process_enabled: bool = True
    log_level: str = "INFO"
    gil_switch_interval_ms: float = 5.0


async def _main(data_dir: Path) -> None:
    host = WorkerHost(_Config(data_dir))
    await host.start()
    ready = await host.wait_ready(60.0)
    report = {"ready": ready, "pid": host.pid, "launcher_pid": host.launcher_pid}
    print(json.dumps(report), flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main(Path(sys.argv[1])))
