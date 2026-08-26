"""Structured subprocess bridge for a user-managed edge-tts installation."""

from __future__ import annotations

import argparse
import asyncio
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), flush=True)


def _error(exc: BaseException) -> dict[str, Any]:
    status = getattr(exc, "status", None)
    return {
        "ok": False,
        "error_type": exc.__class__.__name__,
        "error": (str(exc) or exc.__class__.__name__)[:500],
        "status": int(status) if isinstance(status, int) else None,
    }


async def _run(args: argparse.Namespace) -> int:
    try:
        import edge_tts
    except (ImportError, OSError) as exc:
        _print(_error(exc))
        return 2

    if args.operation == "status":
        _print({"ok": True, "version": version("edge-tts")})
        return 0

    try:
        if args.operation == "voices":
            voices = await edge_tts.list_voices()
            _print({"ok": True, "version": version("edge-tts"), "voices": voices})
            return 0

        text = Path(args.input).read_text(encoding="utf-8")
        destination = Path(args.output)
        communicate = edge_tts.Communicate(
            text,
            args.voice,
            rate=f"{args.rate:+d}%",
            volume=f"{args.volume:+d}%",
            pitch=f"{args.pitch:+d}Hz",
        )
        await communicate.save(str(destination))
        size = destination.stat().st_size
        if size <= 0:
            raise RuntimeError("edge-tts returned no audio")
        _print(
            {
                "ok": True,
                "version": version("edge-tts"),
                "bytes": size,
                "bitrate_bps": 48_000,
                "format": "mp3",
            }
        )
        return 0
    except BaseException as exc:  # edge-tts exposes several network exception families
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        _print(_error(exc))
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("status", "voices", "synthesize"))
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--voice")
    parser.add_argument("--rate", type=int, default=0)
    parser.add_argument("--volume", type=int, default=0)
    parser.add_argument("--pitch", type=int, default=0)
    args = parser.parse_args()
    if args.operation == "synthesize" and not all((args.input, args.output, args.voice)):
        parser.error("synthesize requires --input, --output, and --voice")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
