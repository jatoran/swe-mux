#!/usr/bin/env python3
"""Cut and encode the recorded loop scenes into the site's committed loop files.

    uv run python trailer/encode_loops.py [loop-name ...]
    uv run python trailer/encode_loops.py --frames [loop-name ...]

Separate from `capture_loops.py` on purpose: the cut points below were chosen by
looking at the footage (frame extractions of each raw take), not derived by the
script that shot it, and re-recording a scene means re-checking its cuts.

Format: muted H.264 MP4 (`yuv420p`, faststart), the modern replacement for the
"gif" the brief asks for - an animated GIF of a dark 1080p UI is an order of
magnitude heavier at worse quality, and `<video autoplay muted loop playsinline>`
is what the page should use. Each output must stay well under a megabyte; the
encode fails loudly if one does not.

**A crop here is a redaction, not a composition choice.** `loop-fleet` films real
claude sessions, and a claude statusline renders the operator's actual account
spend as digits inside a terminal cell grid - which `scan_for_leaks` cannot read,
because it reads the DOM. The `crop` on that entry removes the band those digits
live in, and `--frames` exists so the removal is checked by eye on the *encoded*
file rather than assumed from the geometry. Re-record the scene and you re-check
the crop; the statusline's height is the CLI's to change, not ours.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "loops" / "raw"
OUT = HERE.parent / "site" / "img"
REVIEW = RAW / "review"

MAX_BYTES = 1_000_000


@dataclass(frozen=True)
class Cut:
    """One loop: which spans of which take to keep, and how to frame them.

    `take` is the hero beat this loop is cut from, and that indirection is the
    point rather than an accident of naming: the loops on the page are frames of
    the film (`HERO.md`), so a UI change cannot leave the two disagreeing about
    what the product looks like. Re-record a beat and both re-cut from it.
    """

    take: str
    segments: list[tuple[float, float]]
    speed: float = 1.0
    crf: int = 30
    crop: str | None = None
    scale: str | None = None
    note: str = ""
    redaction: str = field(default="")


# Two crops recur and they are redactions rather than compositions, so the reason
# is written once and referenced rather than repeated in four places.
STATUSLINE_CROP = "1920:1000:0:0"
STATUSLINE_REDACTION = (
    "The bottom 80 rows carry the claude statusline, whose weekly and 5-hour "
    "figures are the operator's real subscription spend, rendered into a terminal "
    "cell grid that `scan_for_leaks` cannot read. The crop keeps the composer and "
    "the deliberation line and drops everything below them. Verify by eye after "
    "any re-record: the statusline's height is the CLI's to change, not ours."
)

CUTS: dict[str, Cut] = {
    # Beat 1. Three real agents brought alive in sequence, in three worktrees,
    # with the first one finishing inside the cut.
    "loop-fleet": Cut(
        take="hero-fleet",
        segments=[(2.0, 21.0)],
        speed=1.2,
        crop=STATUSLINE_CROP,
        scale="1280:-2",
        note="three agents working in worktrees, status moving with nobody typing",
        redaction=STATUSLINE_REDACTION,
    ),
    # Beat 3. Alerts, with the reason and the held-back digest, then a tap
    # through to the session that raised it. No scale and no crop: the take is
    # already the phone's CSS geometry, nothing is ever enlarged, and at 402px
    # the statusline truncates before it reaches the account figures.
    "loop-mobile": Cut(
        take="hero-phone",
        segments=[(1.5, 15.4)],
        note="the phone: one interruption, its reason, and the session behind it",
    ),
    # Beat 4. The Timeline: phase-labelled records, a dead end, a blocked badge,
    # and the budget line above them.
    "loop-evidence": Cut(
        take="hero-evidence",
        segments=[(2.0, 15.4)],
        speed=1.15,
        crop=STATUSLINE_CROP,
        scale="1280:-2",
        note="what the agent actually did, read off the record",
        redaction=STATUSLINE_REDACTION,
    ),
    # Beat 5. Landing strip: gate approved -> queued -> reconciling -> verifying
    # -> landed, with the branch row changing under it.
    "loop-land": Cut(
        take="hero-land",
        segments=[(3.0, 22.0)],
        speed=1.4,
        scale="1280:-2",
        note="one branch through reconcile, verify, fast-forward",
    ),
    # Beat 6. The counter is running -> menu -> "Reload daemon (keep sessions)"
    # -> (downtime cut) -> reconnected, same counter, hundreds of ticks further
    # on. The cut MUST keep legible sequence numbers on both sides: without them
    # the loop is a terminal that scrolled, which is what the earlier `ping` take
    # actually showed.
    "loop-restart": Cut(
        take="hero-reload",
        segments=[(1.0, 12.2), (34.5, 47.5)],
        speed=1.3,
        scale="1280:-2",
        note="sessions outliving the daemon that serves them",
    ),
}


def filters(cut: Cut) -> str:
    parts = [
        f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]"
        for index, (start, end) in enumerate(cut.segments)
    ]
    labels = "".join(f"[v{index}]" for index in range(len(cut.segments)))
    chain = f"{labels}concat=n={len(cut.segments)}:v=1:a=0[cat]"
    steps = []
    if cut.crop:
        steps.append(f"crop={cut.crop}")
    steps.append(f"setpts=PTS/{cut.speed}")
    steps.append("fps=25")
    if cut.scale:
        steps.append(f"scale={cut.scale}:flags=lanczos")
    steps.append("format=yuv420p")
    final = "[cat]" + ",".join(steps) + "[out]"
    return ";".join([*parts, chain, final])


def encode(name: str) -> Path:
    cut = CUTS[name]
    source = RAW / f"{cut.take}.webm"
    if not source.exists():
        raise SystemExit(
            f"{source} does not exist; record it with `capture_hero.py {cut.take}`"
        )
    target = OUT / f"{name}.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(source),
            "-filter_complex", filters(cut),
            "-map", "[out]",
            "-an",
            "-c:v", "libx264", "-preset", "slow", "-crf", str(cut.crf),
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-y", str(target),
        ],
        check=True,
    )
    size = target.stat().st_size
    print(f"{target.name}: {size} bytes - {cut.note}")
    if cut.redaction:
        print(f"  redaction: {cut.redaction}")
    if size > MAX_BYTES:
        raise SystemExit(
            f"{target.name} is {size} bytes, over the {MAX_BYTES} ceiling; raise the CRF "
            "or tighten the cut rather than committing it."
        )
    return target


def dump_frames(name: str) -> None:
    """Even samples from the *encoded* file, for the by-eye review.

    Reviewing the raw take proves nothing about what shipped: the crop that
    removes the account figures is applied here, so this is the artifact that
    has to be looked at.
    """
    target = OUT / f"{name}.mp4"
    if not target.exists():
        raise SystemExit(f"{target} does not exist; encode it first")
    folder = REVIEW / name
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob("*.png"):
        existing.unlink()
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(target),
            "-vf", "fps=1",
            "-y", str(folder / "f%02d.png"),
        ],
        check=True,
    )
    frames = sorted(folder.glob("*.png"))
    print(f"{name}: {len(frames)} review frames under {folder}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="Loop names; default encodes every recorded one.")
    parser.add_argument(
        "--frames",
        action="store_true",
        help="Also dump one frame per second of each encoded loop for the by-eye review.",
    )
    args = parser.parse_args()
    # Keyed on the *take*, not on the loop's own name. Keying on the name silently
    # skipped `loop-evidence`, whose take is `hero-evidence`, and the run still
    # reported success for the four it did encode.
    names = args.names or [
        name for name, cut in CUTS.items() if (RAW / f"{cut.take}.webm").exists()
    ]
    unknown = [name for name in names if name not in CUTS]
    if unknown:
        parser.error(f"unknown loop(s): {', '.join(unknown)}")
    total = 0
    for name in names:
        total += encode(name).stat().st_size
        if args.frames:
            dump_frames(name)
    print(f"\n{len(names)} loop(s), {total} bytes committed to site/img/")
    print("Watch every output before committing. The script cannot tell you what is in it.")


if __name__ == "__main__":
    main()
    sys.exit(0)
