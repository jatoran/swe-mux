#!/usr/bin/env python3
"""Cut the six beats into the hero video.

    uv run python trailer/encode_hero.py [--frames]

`HERO.md` is the brief and the shot list; this is the assembly. Output goes to
`trailer/out/hero.mp4`, which is **gitignored on purpose** - `.git` is already
119 MB, a committed binary is permanent, and this is the asset that gets re-cut
most often. Host it as a GitHub Release asset and point the page at that URL.

No audio track at all, not a silent one: the film is designed for
`<video autoplay muted loop playsinline>` and a muted track is bytes nobody
hears. No captions are burned in either - a beat that needs a caption to land is
a beat that was shot wrong.

The cut points are chosen by looking at the footage, not derived from the
recorder's event JSON. The JSON is the map (`trailer/loops/raw/<beat>.json`,
seconds from recording start); the frames are the territory, because a browser
paints when it paints. Re-record a beat and re-check its cut.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "loops" / "raw"
OUT = HERE / "out"

# 1080p is the delivery size. The phone beat is recorded at the phone's own CSS
# geometry and is letterboxed into it rather than enlarged - upscaling a 402px
# capture to 1080 wide would soften every glyph on the one beat whose text is
# already the smallest.
CANVAS = (1920, 1080)


@dataclass(frozen=True)
class Beat:
    take: str
    start: float
    end: float
    speed: float = 1.0
    crop: str | None = None
    note: str = ""


# Order is the film's order and it is the argument of the piece: setup, absence,
# one interruption, evidence, decision, continuity.
BEATS: list[Beat] = [
    Beat(
        take="hero-fleet",
        start=2.0,
        end=21.0,
        speed=1.3,
        crop="1920:1000:0:0",
        note="1+2. three agents start in worktrees, then nobody is at the desk",
    ),
    Beat(
        take="hero-phone",
        start=1.5,
        end=15.4,
        speed=1.15,
        note="3. one interruption, and the ones held back beside it",
    ),
    Beat(
        take="hero-evidence",
        start=2.0,
        end=15.4,
        speed=1.15,
        crop="1920:1000:0:0",
        note="4. what the agent actually did, read off the record",
    ),
    Beat(
        take="hero-land",
        start=3.0,
        end=22.0,
        speed=1.4,
        note="5. one branch through the gate a human approved",
    ),
    Beat(
        take="hero-reload",
        start=1.0,
        end=12.2,
        speed=1.3,
        note="6a. the counter runs, the menu says 'keep sessions'",
    ),
    Beat(
        take="hero-reload",
        start=34.5,
        end=47.5,
        speed=1.3,
        note="6b. reconnected, same counter, several hundred ticks further on",
    ),
]

# A short cross-dissolve everywhere except into beat 6b, which is a hard cut on
# purpose: that cut is the elision the beat is *about*, and dissolving it would
# suggest continuous footage the film does not have.
DISSOLVE = 0.4
HARD_CUT_BEFORE = {5}


def probe(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def build_filter(beats: list[Beat], inputs: list[str]) -> tuple[str, str, float]:
    """One filter graph: trim, speed, fit each beat to the canvas, then join."""
    width, height = CANVAS
    steps: list[str] = []
    lengths: list[float] = []
    for index, beat in enumerate(beats):
        source = inputs.index(beat.take)
        chain = [f"trim=start={beat.start}:end={beat.end}", "setpts=PTS-STARTPTS"]
        if beat.crop:
            chain.append(f"crop={beat.crop}")
        chain.append(f"setpts=PTS/{beat.speed}")
        chain.append("fps=30")
        # `force_original_aspect_ratio=decrease` plus a pad is what letterboxes
        # the phone beat instead of stretching it, and is a no-op for the beats
        # already at the canvas ratio.
        chain.append(
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos"
        )
        chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#0e1116")
        chain.append("setsar=1")
        steps.append(f"[{source}:v]{','.join(chain)}[b{index}]")
        lengths.append((beat.end - beat.start) / beat.speed)

    current = "b0"
    offset = lengths[0]
    for index in range(1, len(beats)):
        overlap = 0.0 if index in HARD_CUT_BEFORE else DISSOLVE
        label = f"x{index}"
        if overlap:
            steps.append(
                f"[{current}][b{index}]xfade=transition=fade:duration={overlap}"
                f":offset={offset - overlap:.3f}[{label}]"
            )
            offset += lengths[index] - overlap
        else:
            steps.append(f"[{current}][b{index}]concat=n=2:v=1:a=0[{label}]")
            offset += lengths[index]
        current = label
    steps.append(f"[{current}]format=yuv420p[out]")
    return ";".join(steps), "[out]", offset


def encode() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    for beat in BEATS:
        if beat.take not in inputs:
            inputs.append(beat.take)
    missing = [name for name in inputs if not (RAW / f"{name}.webm").exists()]
    if missing:
        raise SystemExit(
            f"no take for {', '.join(missing)}; record with `capture_hero.py {missing[0]}`"
        )
    for beat in BEATS:
        available = probe(RAW / f"{beat.take}.webm")
        if beat.end > available + 0.05:
            raise SystemExit(
                f"{beat.take} is {available:.2f}s but the cut asks for {beat.end:.2f}s; "
                "the take is shorter than the edit believes, so re-check both."
            )
    graph, label, duration = build_filter(BEATS, inputs)
    target = OUT / "hero.mp4"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    for name in inputs:
        command += ["-i", str(RAW / f"{name}.webm")]
    command += [
        "-filter_complex", graph,
        "-map", label,
        "-an",
        "-c:v", "libx264", "-preset", "slow", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-y", str(target),
    ]
    subprocess.run(command, check=True)
    print(f"{target}: {target.stat().st_size} bytes, {duration:.1f}s")
    if not 55.0 <= duration <= 80.0:
        print(
            f"  WARNING: {duration:.1f}s is outside the 60-75s brief. Adjust the cuts in "
            "BEATS rather than shipping it."
        )
    for beat in BEATS:
        print(f"  {(beat.end - beat.start) / beat.speed:5.1f}s  {beat.note}")
    return target


def dump_frames(target: Path) -> None:
    folder = OUT / "review"
    folder.mkdir(parents=True, exist_ok=True)
    for existing in folder.glob("*.png"):
        existing.unlink()
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(target),
            "-vf", "fps=1/2",
            "-y", str(folder / "f%02d.png"),
        ],
        check=True,
    )
    print(f"  {len(sorted(folder.glob('*.png')))} review frames under {folder}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--frames",
        action="store_true",
        help="Also dump one frame every two seconds, for the by-eye review.",
    )
    args = parser.parse_args()
    target = encode()
    if args.frames:
        dump_frames(target)
    print(
        "\nThe hero video is NOT committed. Upload it as a GitHub Release asset and point the "
        "page at that URL (HERO.md, 'Where it lives')."
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
