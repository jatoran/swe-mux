#!/usr/bin/env python3
"""Edit the real swe-mux captures into the long feature trailer."""

from __future__ import annotations

import shutil
import subprocess
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import render as original
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
CAPTURES = ROOT / "live-captures"
VIDEO_CAPTURES = CAPTURES / "video"
EXPLORATION = CAPTURES / "exploration"
BUILD = ROOT / "build" / "feature-cut"
OUTPUT = ROOT / "output"
TRASH = ROOT / ".trash"

WIDTH = 1920
HEIGHT = 1080
FPS = 30

GREEN = (185, 255, 96)
BLUE = (92, 205, 255)
AMBER = (255, 194, 77)
PURPLE = (207, 135, 255)
TEXT = (242, 246, 244)
MUTED = (150, 164, 155)
BG = (5, 8, 10)


@dataclass(frozen=True)
class Segment:
    slug: str
    source: str
    start: float
    duration: float
    title: str
    subtitle: str
    accent: tuple[int, int, int] = GREEN
    mobile: bool = False


SEGMENTS = [
    Segment(
        "workspace",
        "01_workspace_status",
        2.9,
        8.8,
        "EVERY PROJECT. LIVE.",
        "SESSIONS // STATUS // QUOTAS // RESOURCES",
    ),
    Segment(
        "split",
        "02_split_panes",
        2.9,
        12.8,
        "SPLIT THE WORK.",
        "REAL AGENTS. SIDE BY SIDE.",
        BLUE,
    ),
    Segment(
        "drawers",
        "03_drawers_notes",
        2.9,
        14.2,
        "YOUR CONTROL PLANE.",
        "NOTES // CONTEXT // GIT // PROCESSES // TRANSCRIPT",
    ),
    Segment(
        "queue",
        "04_prompt_queue",
        2.9,
        14.7,
        "STAGE THE NEXT MOVE.",
        "PROMPTS // QUEUE // MAILBOX // AUTO-DELIVERY",
        AMBER,
    ),
    Segment(
        "automation",
        "05_automation",
        2.7,
        4.3,
        "AUTOMATIONS THAT WATCH.",
        "TITLE // SUMMARIZE // TRIAGE // REVIEW",
        PURPLE,
    ),
    Segment(
        "customization",
        "07_customization",
        2.8,
        11.8,
        "MAKE IT YOURS.",
        "COMMAND RAIL // VOICE // THEME // REMOTE",
        PURPLE,
    ),
    Segment(
        "voice",
        "08_voice_speech",
        2.8,
        12.8,
        "TALK. LISTEN. KEEP MOVING.",
        "READ ALOUD // HANDS-FREE // SPOKEN SUMMARIES",
        BLUE,
    ),
    Segment(
        "fleet",
        "09_process_fleet",
        2.8,
        6.0,
        "SEE WHAT IS REALLY RUNNING.",
        "PIDS // CPU // MEMORY // PORTS // PREVIEWS",
        GREEN,
    ),
    Segment(
        "palette",
        "10_command_palette",
        2.8,
        9.7,
        "ONE COMMAND SURFACE.",
        "BROADCAST // VOICE // SPLIT // RESUME",
        PURPLE,
    ),
    Segment(
        "mobile",
        "11_mobile",
        2.8,
        13.8,
        "THE WHOLE WORKFLOW. IN YOUR HAND.",
        "PROJECTS // AGENTS // NOTES // QUEUE // TOUCH RAIL",
        GREEN,
        True,
    ),
]


MONTAGE = [
    ("files", "FILES. OPEN IN A PANE."),
    ("clipboard", "CLIPBOARD. ACROSS DEVICES."),
    ("commands", "SKILLS. KEYS. COMMANDS."),
    ("transcript", "THE FULL TRANSCRIPT."),
    ("alerts", "ATTENTION WITHOUT NOISE."),
    ("menu", "EVERY SURFACE. ONE MENU."),
]


def run(command: list[str]) -> None:
    printable = " ".join(command[:7])
    print(printable, flush=True)
    subprocess.run(command, check=True)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nk=1:nw=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def archive_existing(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    target = TRASH / f"feature-cut-{stamp}" / path.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(target))


def prepare() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    archive_existing(BUILD, stamp)
    BUILD.mkdir(parents=True, exist_ok=False)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in (
        "swe-mux-feature-trailer-1080p.mp4",
        "swe-mux-feature-trailer-score.wav",
        "swe-mux-feature-trailer-preview.mp4",
        "swe-mux-feature-trailer-contact-sheet.jpg",
    ):
        archive_existing(OUTPUT / name, stamp)


def latest_capture(stem: str) -> Path:
    matches = sorted(
        VIDEO_CAPTURES.glob(f"*/{stem}.webm"),
        key=lambda path: path.stat().st_mtime,
    )
    if not matches:
        raise FileNotFoundError(f"No live capture found for {stem}")
    return matches[-1]


def font_path(bold: bool = False) -> str:
    candidates = (
        [r"C:\Windows\Fonts\CascadiaCode-Bold.ttf", r"C:\Windows\Fonts\consolab.ttf"]
        if bold
        else [r"C:\Windows\Fonts\CascadiaMono.ttf", r"C:\Windows\Fonts\consola.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError("No monospace font is available")


@lru_cache(maxsize=16)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(bold), size)


def make_caption(segment: Segment) -> Path:
    width = 930 if not segment.mobile else 870
    height = 170
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((18, 18, width - 2, height - 2), 12, fill=(0, 0, 0, 180))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    image.alpha_composite(shadow)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (8, 8, width - 10, height - 10),
        8,
        fill=(7, 11, 13, 232),
        outline=(*segment.accent, 175),
        width=2,
    )
    draw.rectangle((8, 8, 20, height - 10), fill=(*segment.accent, 255))
    draw.text((52, 40), segment.title, font=font(42, True), fill=TEXT)
    draw.text((54, 105), segment.subtitle, font=font(18), fill=(*segment.accent, 255))
    path = BUILD / f"caption-{segment.slug}.png"
    image.save(path)
    return path


def make_title_card(path: Path, eyebrow: str, title: str, subtitle: str, accent) -> None:
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    base = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    base[:] = np.array(BG)
    glow = np.exp(-(((xx / WIDTH - 0.76) / 0.48) ** 2 + ((yy / HEIGHT - 0.18) / 0.52) ** 2) * 4.2)
    base += glow[..., None] * np.array(accent, dtype=np.float32) * 0.18
    rng = np.random.default_rng(9183)
    base += rng.normal(0, 1.5, (HEIGHT, WIDTH, 1))
    image = Image.fromarray(np.uint8(np.clip(base, 0, 255)), "RGB").convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    for x in range(0, WIDTH, 64):
        draw.line((x, 0, x, HEIGHT), fill=(*accent, 12), width=1)
    for y in range(0, HEIGHT, 64):
        draw.line((0, y, WIDTH, y), fill=(*accent, 10), width=1)
    draw.rectangle((134, 258, 148, 802), fill=(*accent, 255))
    draw.text((196, 286), eyebrow, font=font(22, True), fill=(*accent, 255))
    draw.text((190, 373), title, font=font(82, True), fill=TEXT)
    draw.text((194, 514), subtitle, font=font(27), fill=MUTED)
    draw.text((194, 742), "swe_mux", font=font(28, True), fill=(*accent, 255))
    draw.text((352, 747), "// MULTIPLEX THE WORK", font=font(18), fill=(110, 127, 117, 255))
    image.convert("RGB").save(path, quality=96)


def image_clip(source: Path, target: Path, duration: float, zoom: bool = True) -> None:
    if zoom:
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,"
            "zoompan=z='min(zoom+0.00035,1.045)':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={int(duration * FPS)}:s=1920x1080:fps={FPS},format=yuv420p"
        )
    else:
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p"
        )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(source),
            "-vf",
            vf,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-y",
            str(target),
        ]
    )


def render_live_segment(segment: Segment, index: int) -> Path:
    source = latest_capture(segment.source)
    caption = make_caption(segment)
    target = BUILD / f"{index:02d}-{segment.slug}.mp4"
    fade_out = max(0.0, segment.duration - 0.65)
    caption_filter = (
        f"[1:v]format=rgba,fade=t=in:st=0:d=0.24:alpha=1,"
        f"fade=t=out:st={fade_out:.3f}:d=0.42:alpha=1[cap];"
    )
    x = "if(lt(t\\,0.60)\\,-w+(90+w)*t/0.60\\,90)"
    if segment.mobile:
        video_filter = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,gblur=sigma=38,eq=brightness=-0.45:saturation=0.7[bg];"
            "[0:v]scale=430:932:force_original_aspect_ratio=decrease,"
            "pad=454:956:12:12:color=0x070b0d[phone];"
            "[bg][phone]overlay=x=1260:y=62[base];"
        )
        y = 735
    else:
        video_filter = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x05080a[base];"
        )
        y = 845
    complex_filter = (
        video_filter
        + caption_filter
        + f"[base][cap]overlay=x='{x}':y={y}:shortest=1,format=yuv420p[out]"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{segment.start:.3f}",
            "-i",
            str(source),
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(caption),
            "-filter_complex",
            complex_filter,
            "-map",
            "[out]",
            "-t",
            f"{segment.duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-y",
            str(target),
        ]
    )
    return target


def montage_clip(start_index: int) -> list[Path]:
    paths: list[Path] = []
    for offset, (name, label) in enumerate(MONTAGE):
        source = EXPLORATION / f"{name}.png"
        segment = Segment(
            f"montage-{name}",
            "",
            0,
            1.25,
            label,
            "REAL UI // LIVE PROJECT DATA",
            (GREEN, BLUE, PURPLE, AMBER)[offset % 4],
        )
        caption = make_caption(segment)
        target = BUILD / f"{start_index + offset:02d}-montage-{name}.mp4"
        x = "if(lt(t\\,0.32)\\,-w+(90+w)*t/0.32\\,90)"
        filter_complex = (
            "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080[base];"
            "[1:v]format=rgba,fade=t=in:st=0:d=0.12:alpha=1,"
            "fade=t=out:st=0.96:d=0.22:alpha=1[cap];"
            f"[base][cap]overlay=x='{x}':y=845:shortest=1,format=yuv420p[out]"
        )
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-framerate",
                str(FPS),
                "-i",
                str(source),
                "-loop",
                "1",
                "-framerate",
                str(FPS),
                "-i",
                str(caption),
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                "-t",
                "1.25",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "17",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(FPS),
                "-y",
                str(target),
            ]
        )
        paths.append(target)
    return paths


def usage_clip(index: int) -> Path:
    segment = Segment(
        "usage",
        "",
        0,
        5.2,
        "KNOW THE BURN.",
        "COST // QUOTA // TOOLS // CONTEXT // COMPACTION",
        AMBER,
    )
    source = EXPLORATION / "usage.png"
    caption = make_caption(segment)
    target = BUILD / f"{index:02d}-usage.mp4"
    x = "if(lt(t\\,0.60)\\,-w+(90+w)*t/0.60\\,90)"
    filter_complex = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,zoompan=z='min(zoom+0.00030,1.035)':"
        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        "d=156:s=1920x1080:fps=30[base];"
        "[1:v]format=rgba,fade=t=in:st=0:d=0.24:alpha=1,"
        "fade=t=out:st=4.55:d=0.42:alpha=1[cap];"
        f"[base][cap]overlay=x='{x}':y=845:shortest=1,format=yuv420p[out]"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(source),
            "-loop",
            "1",
            "-framerate",
            str(FPS),
            "-i",
            str(caption),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-t",
            "5.2",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "17",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-y",
            str(target),
        ]
    )
    return target


def synthesize_score(path: Path, duration: float, sample_rate: int = 48_000) -> None:
    rng = np.random.default_rng(731942)
    mix = np.zeros((int(duration * sample_rate), 2), dtype=np.float64)
    chord_progression = [
        (146.83, 174.61, 220.00),
        (116.54, 146.83, 174.61),
        (130.81, 164.81, 196.00),
        (110.00, 130.81, 164.81),
    ]
    bass_pattern = [73.42, 73.42, 73.42, 55.00, 58.27, 58.27, 65.41, 55.00]
    arp_sets = [
        [293.66, 440.00, 523.25, 698.46],
        [233.08, 349.23, 440.00, 587.33],
        [261.63, 392.00, 523.25, 659.25],
        [220.00, 329.63, 440.00, 523.25],
    ]

    for frequency, pan in ((73.42, -0.35), (110.0, 0.35), (146.83, 0.0)):
        original.add_tone(mix, sample_rate, 0.0, 7.0, frequency, 0.07, "saw", pan, 1.2, 1.3, 0.004)

    breakdowns = [(38.0, 43.0), (73.0, 78.0), (duration - 8.0, duration - 5.0)]

    def breakdown(at: float) -> bool:
        return any(start <= at < end for start, end in breakdowns)

    for bar_index, bar_start in enumerate(np.arange(3.0, duration - 3.0, 2.0)):
        chord = chord_progression[bar_index % len(chord_progression)]
        amplitude = 0.054 if not breakdown(bar_start) else 0.027
        for note_index, frequency in enumerate(chord):
            original.add_tone(
                mix,
                sample_rate,
                bar_start,
                2.12,
                frequency,
                amplitude,
                "saw",
                (note_index - 1) * 0.35,
                0.30,
                0.42,
                0.003,
            )

    for beat_index, beat_time in enumerate(np.arange(3.0, duration - 5.0, 0.5)):
        bass = bass_pattern[beat_index % len(bass_pattern)]
        original.add_tone(
            mix,
            sample_rate,
            beat_time,
            0.40,
            bass,
            0.22 if not breakdown(beat_time) else 0.10,
            "square",
            0.0,
            0.008,
            0.09,
        )
        if breakdown(beat_time):
            if beat_index % 4 == 0:
                original.add_kick(mix, sample_rate, beat_time, 0.66)
        else:
            original.add_kick(mix, sample_rate, beat_time, 0.88)
            if beat_index % 2 == 1:
                original.add_noise_hit(mix, sample_rate, beat_time, 0.24, 0.14, 0.0, rng)

    for step_index, step_time in enumerate(np.arange(7.0, duration - 7.0, 0.25)):
        bar_index = int((step_time - 3.0) / 2.0) % len(arp_sets)
        frequency = arp_sets[bar_index][step_index % 4]
        original.add_tone(
            mix,
            sample_rate,
            step_time,
            0.19,
            frequency,
            0.063 if not breakdown(step_time) else 0.025,
            "glass",
            -0.55 if step_index % 2 == 0 else 0.55,
            0.004,
            0.07,
        )
        if not breakdown(step_time):
            original.add_noise_hit(
                mix,
                sample_rate,
                step_time,
                0.07,
                0.028 if step_index % 2 == 0 else 0.018,
                -0.38 if step_index % 2 == 0 else 0.38,
                rng,
                True,
            )

    for transition in np.arange(3.0, duration - 4.0, 4.0):
        original.add_riser(mix, sample_rate, transition, rng)
        original.add_noise_hit(mix, sample_rate, transition, 0.55, 0.22, 0.0, rng)
        original.add_tone(mix, sample_rate, transition, 0.9, 55.0, 0.22, "sine", 0.0, 0.002, 0.42)

    finale = duration - 5.0
    for frequency, pan in ((146.83, -0.4), (220.0, 0.0), (293.66, 0.4), (349.23, 0.1)):
        original.add_tone(
            mix,
            sample_rate,
            finale,
            5.0,
            frequency,
            0.12,
            "saw",
            pan,
            0.12,
            1.2,
            0.003,
        )
    original.add_kick(mix, sample_rate, finale, 1.05)
    original.add_tone(mix, sample_rate, finale, 2.4, 36.71, 0.36, "sine", 0.0, 0.004, 0.9)

    duck = np.ones(len(mix), dtype=np.float64)
    for kick_time in np.arange(3.0, finale + 0.1, 0.5):
        start = int(kick_time * sample_rate)
        length = min(len(mix) - start, int(0.17 * sample_rate))
        if length > 0:
            duck[start : start + length] *= 0.72 + 0.28 * (1 - np.exp(-np.linspace(0, 5, length)))
    mix *= duck[:, None]
    fade_in = int(0.5 * sample_rate)
    fade_out = int(1.3 * sample_rate)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    mix = np.tanh(mix * 1.38)
    peak = np.max(np.abs(mix))
    if peak:
        mix *= 0.84 / peak
    pcm = np.int16(np.clip(mix, -1, 1) * 32767)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def concat(paths: list[Path], target: Path) -> None:
    list_path = BUILD / "concat.txt"
    list_path.write_text("".join(f"file '{path.as_posix()}'\n" for path in paths), encoding="utf-8")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-y",
            str(target),
        ]
    )


def make_contact_sheet(video: Path, target: Path, duration: float) -> None:
    frames = BUILD / "contact-frames"
    frames.mkdir(parents=True, exist_ok=True)
    images: list[Image.Image] = []
    for index, timestamp in enumerate(np.linspace(2.0, duration - 2.0, 12)):
        frame = frames / f"{index:02d}.jpg"
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=480:270",
                "-q:v",
                "2",
                "-y",
                str(frame),
            ]
        )
        with Image.open(frame) as opened:
            images.append(opened.convert("RGB"))
    sheet = Image.new("RGB", (1920, 810), (2, 4, 5))
    for index, image in enumerate(images):
        sheet.paste(image, ((index % 4) * 480, (index // 4) * 270))
    sheet.save(target, quality=92)


def main() -> None:
    prepare()
    intro_image = BUILD / "intro.jpg"
    outro_image = BUILD / "outro.jpg"
    make_title_card(
        intro_image,
        "THE AGENT WORKSPACE",
        "DO MORE.\nLOSE NOTHING.",
        "Real sessions. Real projects. One control plane.",
        GREEN,
    )
    make_title_card(
        outro_image,
        "SWE-MUX",
        "KEEP EVERY\nTHREAD ALIVE.",
        "Desktop // Mobile // Remote // One live workspace.",
        BLUE,
    )
    intro = BUILD / "00-intro.mp4"
    image_clip(intro_image, intro, 4.2)

    clips: list[Path] = [intro]
    for index, segment in enumerate(SEGMENTS[:5], 1):
        clips.append(render_live_segment(segment, index))
    clips.extend(montage_clip(len(clips)))
    clips.append(usage_clip(len(clips)))
    for segment in SEGMENTS[5:]:
        clips.append(render_live_segment(segment, len(clips)))

    outro = BUILD / f"{len(clips):02d}-outro.mp4"
    image_clip(outro_image, outro, 5.6)
    clips.append(outro)

    silent = BUILD / "feature-cut-silent.mp4"
    concat(clips, silent)
    duration = probe_duration(silent)
    score = OUTPUT / "swe-mux-feature-trailer-score.wav"
    synthesize_score(score, duration)
    final = OUTPUT / "swe-mux-feature-trailer-1080p.mp4"
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(silent),
            "-i",
            str(score),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-shortest",
            "-movflags",
            "+faststart",
            "-y",
            str(final),
        ]
    )
    preview = OUTPUT / "swe-mux-feature-trailer-preview.mp4"
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(final),
            "-vf",
            "scale=960:540",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "26",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-y",
            str(preview),
        ]
    )
    make_contact_sheet(
        final,
        OUTPUT / "swe-mux-feature-trailer-contact-sheet.jpg",
        duration,
    )
    print(f"final={final}", flush=True)
    print(f"duration={duration:.3f}", flush=True)


if __name__ == "__main__":
    main()
