#!/usr/bin/env python3
"""Render the swe-mux launch trailer and its original synthesized score."""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
BUILD = ROOT / "build"
OUTPUT = ROOT / "output"
STILLS = ROOT / "stills"

DESIGN_W = 1920
DESIGN_H = 1080
DURATION = 48.0

BG = (5, 8, 7)
PANEL = (9, 14, 11)
PANEL_2 = (15, 23, 18)
LINE = (38, 55, 43)
TEXT = (231, 239, 233)
MUTED = (104, 128, 112)
GREEN = (142, 230, 154)
GREEN_HOT = (170, 255, 185)
BLUE = (82, 190, 255)
AMBER = (231, 199, 104)
RED = (240, 113, 120)
PURPLE = (197, 138, 249)

W = DESIGN_W
H = DESIGN_H
SCALE = 1.0


def q(value: float) -> int:
    return max(1, int(round(value * SCALE)))


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def ease_out_cubic(value: float) -> float:
    x = 1.0 - clamp(value)
    return 1.0 - x * x * x


def ease_in_out(value: float) -> float:
    x = clamp(value)
    return 4.0 * x * x * x if x < 0.5 else 1.0 - ((-2.0 * x + 2.0) ** 3) / 2.0


def ease_out_back(value: float) -> float:
    x = clamp(value) - 1.0
    c1 = 1.70158
    c3 = c1 + 1.0
    return 1.0 + c3 * x * x * x + c1 * x * x


def rgba(color: tuple[int, int, int], alpha: int = 255) -> tuple[int, int, int, int]:
    return color[0], color[1], color[2], alpha


def font_path(kind: str) -> str:
    candidates = {
        "display": [
            r"C:\Windows\Fonts\seguisb.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ],
        "body": [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ],
        "mono": [
            r"C:\Windows\Fonts\CascadiaMono.ttf",
            r"C:\Windows\Fonts\consola.ttf",
        ],
    }
    for candidate in candidates[kind]:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"No usable {kind} font found")


@lru_cache(maxsize=128)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(kind), max(6, q(size)))


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, ...],
    outline: tuple[int, ...] | None = None,
    width: int = 1,
) -> None:
    draw.rounded_rectangle(
        tuple(q(v) for v in box),
        radius=q(radius),
        fill=fill,
        outline=outline,
        width=max(1, q(width)),
    )


def line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: tuple[int, ...],
    width: int = 1,
) -> None:
    draw.line([(q(x), q(y)) for x, y in points], fill=fill, width=max(1, q(width)))


def txt(
    draw: ImageDraw.ImageDraw,
    pos: tuple[float, float],
    value: str,
    size: int,
    color: tuple[int, ...] = TEXT,
    kind: str = "body",
    anchor: str | None = None,
    stroke_width: int = 0,
    stroke_fill: tuple[int, ...] | None = None,
) -> None:
    draw.text(
        (q(pos[0]), q(pos[1])),
        value,
        font=font(kind, size),
        fill=color,
        anchor=anchor,
        stroke_width=q(stroke_width) if stroke_width else 0,
        stroke_fill=stroke_fill,
    )


def paste_opacity(
    base: Image.Image, overlay: Image.Image, pos: tuple[int, int], opacity: float
) -> None:
    if opacity <= 0:
        return
    if opacity >= 0.999:
        base.alpha_composite(overlay, pos)
        return
    layer = overlay.copy()
    alpha = layer.getchannel("A").point(lambda value: int(value * opacity))
    layer.putalpha(alpha)
    base.alpha_composite(layer, pos)


@lru_cache(maxsize=16)
def make_background(accent: tuple[int, int, int]) -> Image.Image:
    height, width = H, W
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.zeros((height, width, 3), dtype=np.float32)
    base[:] = np.array(BG, dtype=np.float32)
    base += (yy / max(height - 1, 1))[..., None] * np.array([2.0, 4.0, 3.0])

    for cx, cy, radius, strength, tint in (
        (0.72, 0.08, 0.72, 0.18, accent),
        (0.10, 0.90, 0.82, 0.09, BLUE),
    ):
        dx = (xx / width - cx) / radius
        dy = (yy / height - cy) / radius
        glow = np.exp(-(dx * dx + dy * dy) * 4.2) * strength
        base += glow[..., None] * np.array(tint, dtype=np.float32)

    vignette = 1.0 - 0.46 * np.clip(
        ((xx - width / 2) / (width * 0.74)) ** 2 + ((yy - height / 2) / (height * 0.72)) ** 2,
        0,
        1,
    )
    base *= vignette[..., None]
    rng = np.random.default_rng(7319)
    noise = rng.normal(0, 1.2, (height, width, 1))
    base += noise
    return Image.fromarray(np.uint8(np.clip(base, 0, 255)), "RGB").convert("RGBA")


def draw_ambient(frame: Image.Image, t: float, accent: tuple[int, int, int]) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    grid = 64
    offset_x = (t * 12.0) % grid
    offset_y = (t * 7.0) % grid
    for x in np.arange(-grid + offset_x, DESIGN_W + grid, grid):
        line(draw, [(x, 0), (x, DESIGN_H)], rgba(accent, 11), 1)
    for y in np.arange(-grid + offset_y, DESIGN_H + grid, grid):
        line(draw, [(0, y), (DESIGN_W, y)], rgba(accent, 9), 1)

    for index in range(24):
        seed = index * 19.71
        x = (seed * 71 + t * (22 + index % 5) * 4.0) % 2050 - 60
        y = (seed * 37 + math.sin(t * 0.35 + index) * 55) % 1140 - 30
        length = 10 + (index % 4) * 8
        line(draw, [(x, y), (x + length, y)], rgba(accent, 22 + (index % 3) * 10), 1)


@lru_cache(maxsize=96)
def text_sprite(
    value: str, size: int, color: tuple[int, int, int], kind: str = "display"
) -> Image.Image:
    chosen = font(kind, size)
    probe = Image.new("L", (4, 4))
    d = ImageDraw.Draw(probe)
    bbox = d.textbbox((0, 0), value, font=chosen, stroke_width=q(1))
    pad = q(28)
    width = bbox[2] - bbox[0] + pad * 2
    height = bbox[3] - bbox[1] + pad * 2
    sharp = Image.new("RGBA", (width, height))
    sd = ImageDraw.Draw(sharp)
    sd.text(
        (pad - bbox[0], pad - bbox[1]),
        value,
        font=chosen,
        fill=rgba(color),
        stroke_width=q(1),
        stroke_fill=rgba((0, 0, 0), 145),
    )
    glow_alpha = sharp.getchannel("A").filter(ImageFilter.GaussianBlur(q(11)))
    glow = Image.new("RGBA", sharp.size, rgba(color, 0))
    glow.putalpha(glow_alpha.point(lambda value: int(value * 0.28)))
    glow.alpha_composite(sharp)
    return glow


def swoop_text(
    frame: Image.Image,
    lines: list[tuple[str, tuple[int, int, int]]],
    local_t: float,
    x: float,
    y: float,
    sizes: list[int],
    direction: int = 1,
    center: bool = False,
    scene_duration: float = 4.0,
) -> None:
    enter = ease_out_back(local_t / 0.72)
    exit_value = ease_in_out((local_t - (scene_duration - 0.45)) / 0.45)
    motion = direction * 610 * (1.0 - enter) - direction * 300 * exit_value
    opacity = clamp(local_t / 0.22) * (1.0 - clamp((local_t - (scene_duration - 0.25)) / 0.25))
    y_cursor = y
    for index, ((value, color), size) in enumerate(zip(lines, sizes, strict=True)):
        sprite = text_sprite(value, size, color)
        target_x = x + motion
        if center:
            target_x -= sprite.width / SCALE / 2
        target_y = y_cursor + math.sin(local_t * 3.2 + index) * 2
        for trail_index in range(3, 0, -1):
            trail_x = q(target_x - direction * trail_index * 24 * (1.0 - min(enter, 1.0)))
            paste_opacity(frame, sprite, (trail_x, q(target_y)), opacity * (0.07 * trail_index))
        paste_opacity(frame, sprite, (q(target_x), q(target_y)), opacity)
        y_cursor += size * 0.88

    accent_x = x + motion
    if center:
        accent_x -= 160
    draw = ImageDraw.Draw(frame, "RGBA")
    underline_w = 310 * ease_out_cubic((local_t - 0.28) / 0.55) * (1.0 - exit_value)
    line(
        draw,
        [(accent_x, y_cursor + 15), (accent_x + underline_w, y_cursor + 15)],
        rgba(GREEN, int(230 * opacity)),
        5,
    )


def small_label(
    frame: Image.Image,
    value: str,
    x: float,
    y: float,
    color: tuple[int, int, int] = GREEN,
    align: str = "left",
) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    chosen = font("mono", 15)
    bbox = draw.textbbox((0, 0), value, font=chosen)
    width = (bbox[2] - bbox[0]) / SCALE
    if align == "right":
        x -= width + 26
    rounded(draw, (x, y, x + width + 26, y + 34), 2, rgba(PANEL, 220), rgba(color, 120), 1)
    txt(draw, (x + 13, y + 17), value, 15, rgba(color), "mono", anchor="lm")


def place_card(
    frame: Image.Image,
    card: Image.Image,
    cx: float,
    cy: float,
    scale: float = 1.0,
    angle: float = 0.0,
    opacity: float = 1.0,
) -> None:
    if scale <= 0 or opacity <= 0:
        return
    transformed = card
    if abs(scale - 1.0) > 0.002:
        transformed = transformed.resize(
            (max(1, int(card.width * scale)), max(1, int(card.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if abs(angle) > 0.03:
        transformed = transformed.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    x = q(cx) - transformed.width // 2
    y = q(cy) - transformed.height // 2
    paste_opacity(frame, transformed, (x, y), opacity)


def ui_text(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    value: str,
    size: int = 13,
    color=TEXT,
    mono=False,
    anchor=None,
) -> None:
    txt(draw, (x, y), value, size, rgba(color), "mono" if mono else "body", anchor=anchor)


def draw_status_dot(
    draw: ImageDraw.ImageDraw, x: float, y: float, color: tuple[int, int, int], pulse: float = 0.0
) -> None:
    if pulse > 0:
        radius = 7 + 5 * pulse
        draw.ellipse(
            (q(x - radius), q(y - radius), q(x + radius), q(y + radius)),
            fill=rgba(color, int(45 * (1 - pulse))),
        )
    draw.rectangle((q(x - 3), q(y - 3), q(x + 3), q(y + 3)), fill=rgba(color))


def draw_terminal(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    title: str,
    backend: str,
    lines_data: list[tuple[str, tuple[int, int, int]]],
    phase: float,
    focused: bool = False,
) -> None:
    x0, y0, x1, y1 = box
    rounded(
        draw, (x0, y0, x1, y1), 1, rgba((5, 9, 7), 255), rgba(GREEN if focused else LINE, 185), 1
    )
    draw.rectangle((q(x0), q(y0), q(x1), q(y0 + 42)), fill=rgba((10, 17, 12), 255))
    line(draw, [(x0, y0 + 42), (x1, y0 + 42)], rgba(LINE, 180), 1)
    ui_text(draw, x0 + 16, y0 + 21, "$", 12, GREEN, True, "lm")
    ui_text(draw, x0 + 34, y0 + 21, title, 12, TEXT, True, "lm")
    ui_text(draw, x1 - 14, y0 + 21, backend.upper(), 9, MUTED, True, "rm")
    max_lines = max(1, int(phase * (len(lines_data) + 1)))
    y = y0 + 72
    for index, (value, color) in enumerate(lines_data[:max_lines]):
        prefix = "›" if index == 0 else " "
        ui_text(draw, x0 + 18, y, prefix, 12, GREEN if index == 0 else MUTED, True)
        ui_text(draw, x0 + 39, y, value, 12, color, True)
        y += 27
    cursor_alpha = 255 if int(phase * 12) % 2 == 0 else 60
    draw.rectangle(
        (q(x0 + 38), q(min(y, y1 - 30)), q(x0 + 48), q(min(y + 17, y1 - 13))),
        fill=rgba(GREEN, cursor_alpha),
    )


def draw_sidebar(
    draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, phase: float
) -> None:
    draw.rectangle((q(x0), q(y0), q(x1), q(y1)), fill=rgba((7, 12, 9), 255))
    line(draw, [(x1, y0), (x1, y1)], rgba(LINE, 200), 1)
    ui_text(draw, x0 + 18, y0 + 28, "WORKSPACES", 10, MUTED, True)
    projects = [
        ("ORBITAL", "refactor auth", "CODEX", GREEN),
        ("NOVA", "ship release", "CLAUDE", BLUE),
        ("ATLAS", "tests + docs", "POWERSHELL", AMBER),
    ]
    y = y0 + 54
    for index, (project, session, backend, color) in enumerate(projects):
        active = index == 0
        if active:
            draw.rectangle(
                (q(x0 + 7), q(y - 7), q(x1 - 7), q(y + 73)),
                fill=rgba((14, 29, 19), 255),
                outline=rgba(GREEN, 60),
            )
            draw.rectangle((q(x0 + 7), q(y - 7), q(x0 + 10), q(y + 73)), fill=rgba(GREEN))
        ui_text(draw, x0 + 20, y + 5, "▾" if active else "›", 11, MUTED, True)
        ui_text(draw, x0 + 39, y + 5, project, 12, TEXT, True)
        draw_status_dot(draw, x0 + 29, y + 39, color, (math.sin(phase * 8 + index) + 1) * 0.15)
        ui_text(draw, x0 + 44, y + 36, session, 11, TEXT)
        ui_text(draw, x0 + 44, y + 57, backend, 9, MUTED, True)
        y += 96
    ui_text(draw, x0 + 18, y1 - 26, "● DAEMON ONLINE", 9, GREEN, True)


def draw_topbar(draw: ImageDraw.ImageDraw, w: float, title: str) -> None:
    draw.rectangle((0, 0, q(w), q(52)), fill=rgba((7, 11, 9), 255))
    line(draw, [(0, 52), (w, 52)], rgba(LINE, 210), 1)
    rounded(draw, (14, 13, 40, 39), 4, rgba(GREEN), None)
    ui_text(draw, 27, 26, ">_", 11, (7, 14, 9), True, "mm")
    ui_text(draw, 52, 25, "swe-mux", 15, TEXT, False, "lm")
    ui_text(draw, 155, 26, "ORBITAL", 10, GREEN, True, "lm")
    ui_text(draw, 240, 26, title.upper(), 9, MUTED, True, "lm")
    ui_text(draw, w - 22, 26, "LOCAL  ●", 9, GREEN, True, "rm")


def make_app_window(
    variant: str, phase: float, width: int = 1500, height: int = 760
) -> Image.Image:
    pw, ph = q(width), q(height)
    card = Image.new("RGBA", (pw + q(34), ph + q(38)), (0, 0, 0, 0))
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow, "RGBA")
    sd.rounded_rectangle((q(26), q(30), pw + q(26), ph + q(30)), radius=q(9), fill=(0, 0, 0, 170))
    card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(q(10))))

    ui = Image.new("RGBA", (pw, ph), rgba(PANEL))
    draw = ImageDraw.Draw(ui, "RGBA")
    draw.rounded_rectangle(
        (0, 0, pw - 1, ph - 1), radius=q(7), fill=rgba(PANEL), outline=rgba(LINE), width=q(1)
    )
    draw_topbar(draw, width, variant)

    sidebar_w = 240
    draw_sidebar(draw, 0, 52, sidebar_w, height, phase)
    main_x = sidebar_w
    main_y = 52
    main_w = width - main_x
    main_h = height - main_y

    terminal_lines = [
        ("codex: executing release plan", BLUE),
        ("indexed 184 project files", MUTED),
        ("✓ backend checks passed", GREEN),
        ("✓ frontend checks passed", GREEN),
        ("building desktop bundle...", AMBER),
        ("ready for review", GREEN_HOT),
    ]

    if variant == "persistence":
        draw_terminal(
            draw,
            (main_x + 10, main_y + 10, width - 10, height - 10),
            "release conductor",
            "codex",
            terminal_lines,
            phase,
            True,
        )
        toast_p = ease_out_back((phase - 0.38) / 0.18)
        if toast_p > 0:
            toast_y = 98 - 86 * min(toast_p, 1)
            rounded(
                draw,
                (width - 430, toast_y, width - 22, toast_y + 54),
                2,
                rgba((11, 23, 15), 245),
                rgba(GREEN, 170),
                1,
            )
            ui_text(draw, width - 410, toast_y + 18, "DAEMON RELOADED", 10, GREEN, True)
            ui_text(draw, width - 410, toast_y + 38, "3 sessions preserved", 11, TEXT)

    elif variant in {"agents", "split"}:
        gap = 8
        split_p = 1.0 if variant == "agents" else ease_out_cubic((phase - 0.16) / 0.45)
        usable = main_w - 20
        left_width = usable * (1.0 - 0.62 * split_p)
        left_box = (main_x + 10, main_y + 10, main_x + 10 + left_width, height - 10)
        draw_terminal(draw, left_box, "release conductor", "codex", terminal_lines, phase, True)
        if split_p > 0.03:
            rx0 = left_box[2] + gap
            right_lines = [
                ("claude: auditing auth boundary", PURPLE),
                ("reading session lifecycle", MUTED),
                ("✓ race condition isolated", GREEN),
                ("drafting patch", AMBER),
            ]
            draw_terminal(
                draw,
                (rx0, main_y + 10, width - 10, main_y + main_h * 0.54),
                "auth audit",
                "claude",
                right_lines,
                phase,
                False,
            )
            shell_lines = [
                ("npm test", BLUE),
                ("996 passed in 58.42s", GREEN),
                ("git status --short", MUTED),
                ("working tree clean", GREEN_HOT),
            ]
            draw_terminal(
                draw,
                (rx0, main_y + main_h * 0.54 + gap, width - 10, height - 10),
                "verification",
                "pwsh",
                shell_lines,
                phase,
                False,
            )

    elif variant == "preview":
        split = main_x + main_w * 0.43
        draw_terminal(
            draw,
            (main_x + 10, main_y + 10, split - 5, height - 10),
            "dev server",
            "codex",
            terminal_lines[:5],
            phase,
            True,
        )
        x0, y0, x1, y1 = split + 5, main_y + 10, width - 10, height - 10
        rounded(draw, (x0, y0, x1, y1), 1, rgba((8, 13, 11)), rgba(LINE), 1)
        draw.rectangle((q(x0), q(y0), q(x1), q(y0 + 42)), fill=rgba((11, 18, 14)))
        ui_text(draw, x0 + 16, y0 + 21, "◉ PREVIEW  /preview/orbital", 10, GREEN, True, "lm")
        rounded(draw, (x0 + 22, y0 + 72, x1 - 22, y0 + 142), 3, rgba((15, 26, 20)), rgba(LINE), 1)
        ui_text(draw, x0 + 42, y0 + 98, "DEPLOY VELOCITY", 10, MUTED, True)
        ui_text(draw, x0 + 42, y0 + 127, "24.8x", 25, GREEN_HOT, True)
        graph_x0, graph_y0 = x0 + 26, y0 + 188
        graph_x1, graph_y1 = x1 - 26, y1 - 34
        for grid_y in range(5):
            gy = graph_y0 + (graph_y1 - graph_y0) * grid_y / 4
            line(draw, [(graph_x0, gy), (graph_x1, gy)], rgba(LINE, 100), 1)
        points: list[tuple[float, float]] = []
        for index in range(18):
            xp = graph_x0 + (graph_x1 - graph_x0) * index / 17
            yp = graph_y1 - (graph_y1 - graph_y0) * (
                0.18 + index / 24 + 0.10 * math.sin(index * 1.3 + phase * 8)
            )
            points.append((xp, yp))
        line(draw, points, rgba(GREEN, 235), 4)
        for xp, yp in points[::3]:
            draw.ellipse((q(xp - 4), q(yp - 4), q(xp + 4), q(yp + 4)), fill=rgba(GREEN_HOT))

    elif variant == "fleet":
        x0, y0 = main_x + 18, main_y + 18
        ui_text(draw, x0, y0 + 10, "AGENT FLEET", 18, TEXT, True)
        ui_text(draw, width - 26, y0 + 10, "6 ACTIVE  /  2 READY", 10, GREEN, True, "rm")
        rows = [
            ("release conductor", "CODEX", "WORKING", GREEN, 0.76),
            ("auth audit", "CLAUDE", "READY", BLUE, 0.48),
            ("mobile regression", "CODEX", "RUNNING TESTS", AMBER, 0.63),
            ("docs sweep", "CLAUDE", "WAITING", PURPLE, 0.34),
        ]
        y = y0 + 62
        for index, (name, backend, state, color, quota) in enumerate(rows):
            reveal = ease_out_cubic((phase - index * 0.07) / 0.34)
            x_shift = 70 * (1 - reveal)
            rounded(
                draw,
                (x0 + x_shift, y, width - 24 + x_shift, y + 92),
                2,
                rgba((10, 18, 13)),
                rgba(LINE),
                1,
            )
            draw_status_dot(
                draw, x0 + 24 + x_shift, y + 29, color, (math.sin(phase * 10 + index) + 1) * 0.12
            )
            ui_text(draw, x0 + 43 + x_shift, y + 24, name, 13, TEXT)
            ui_text(draw, x0 + 43 + x_shift, y + 52, backend, 9, MUTED, True)
            ui_text(draw, width - 40 + x_shift, y + 25, state, 9, color, True, "rm")
            bar_x0, bar_x1 = x0 + 310 + x_shift, width - 40 + x_shift
            draw.rectangle((q(bar_x0), q(y + 61), q(bar_x1), q(y + 67)), fill=rgba((31, 43, 35)))
            draw.rectangle(
                (q(bar_x0), q(y + 61), q(bar_x0 + (bar_x1 - bar_x0) * quota * reveal), q(y + 67)),
                fill=rgba(color),
            )
            y += 106

    elif variant == "queue":
        drawer_w = 460
        draw_terminal(
            draw,
            (main_x + 10, main_y + 10, width - drawer_w - 10, height - 10),
            "release conductor",
            "codex",
            terminal_lines,
            phase,
            True,
        )
        dx0 = width - drawer_w
        draw.rectangle((q(dx0), q(main_y), q(width), q(height)), fill=rgba((9, 15, 11)))
        line(draw, [(dx0, main_y), (dx0, height)], rgba(GREEN, 120), 1)
        ui_text(draw, dx0 + 18, main_y + 28, "QUEUE  /  RELEASE CONDUCTOR", 10, GREEN, True)
        cards = [
            ("NEXT", "Run the packaging verification.", GREEN),
            ("ARMED", "Summarize the release risk.", BLUE),
            ("DRAFT", "Prepare the final handoff.", MUTED),
        ]
        y = main_y + 62
        for index, (state, body, color) in enumerate(cards):
            reveal = ease_out_back((phase - 0.10 - index * 0.10) / 0.30)
            if reveal <= 0:
                continue
            x_shift = 95 * (1 - min(reveal, 1))
            rounded(
                draw,
                (dx0 + 16 + x_shift, y, width - 16 + x_shift, y + 112),
                2,
                rgba((13, 23, 17)),
                rgba(color, 150),
                1,
            )
            ui_text(draw, dx0 + 31 + x_shift, y + 25, state, 9, color, True)
            ui_text(draw, dx0 + 31 + x_shift, y + 54, body, 11, TEXT)
            ui_text(
                draw, dx0 + 31 + x_shift, y + 84, "✓ TARGET SAFE  ✓ REVISION MATCH", 8, MUTED, True
            )
            y += 126
        rounded(draw, (dx0 + 16, height - 76, width - 16, height - 20), 2, rgba(GREEN), None)
        ui_text(draw, (dx0 + width) / 2, height - 48, "SEND NEXT", 11, (7, 13, 8), True, "mm")

    elif variant == "voice":
        draw_terminal(
            draw,
            (main_x + 10, main_y + 10, width - 10, height - 10),
            "release conductor",
            "codex",
            terminal_lines[:4],
            phase,
            True,
        )
        overlay_y = height - 176
        rounded(
            draw,
            (main_x + 34, overlay_y, width - 34, height - 34),
            3,
            rgba((10, 20, 14), 248),
            rgba(GREEN, 155),
            1,
        )
        draw.ellipse(
            (q(main_x + 60), q(overlay_y + 30), q(main_x + 124), q(overlay_y + 94)),
            fill=rgba(RED, 220),
        )
        ui_text(draw, main_x + 92, overlay_y + 62, "●", 18, (8, 12, 9), True, "mm")
        ui_text(draw, main_x + 148, overlay_y + 46, "LISTENING", 10, GREEN, True)
        ui_text(draw, main_x + 148, overlay_y + 76, '"Mux, send"', 18, TEXT)
        wave_x0 = main_x + 500
        wave_x1 = width - 62
        mid = overlay_y + 70
        samples = 46
        points = []
        for index in range(samples):
            xp = wave_x0 + (wave_x1 - wave_x0) * index / (samples - 1)
            amp = 12 + 30 * abs(math.sin(index * 0.72 + phase * 19))
            yp = mid + math.sin(index * 1.45 + phase * 28) * amp
            points.append((xp, yp))
        line(draw, points, rgba(BLUE, 230), 3)

    elif variant == "history":
        list_w = 390
        lx0 = main_x
        draw.rectangle((q(lx0), q(main_y), q(lx0 + list_w), q(height)), fill=rgba((8, 13, 10)))
        line(draw, [(lx0 + list_w, main_y), (lx0 + list_w, height)], rgba(LINE), 1)
        rounded(
            draw,
            (lx0 + 16, main_y + 18, lx0 + list_w - 16, main_y + 58),
            2,
            rgba((5, 10, 7)),
            rgba(GREEN, 100),
            1,
        )
        ui_text(draw, lx0 + 32, main_y + 38, "search: release", 10, MUTED, True, "lm")
        entries = [
            ("Release conductor", "12 messages  /  today", True),
            ("Auth boundary audit", "28 messages  /  yesterday", False),
            ("Mobile input regression", "41 messages  /  jul 31", False),
            ("Preview proxy fix", "19 messages  /  jul 30", False),
        ]
        y = main_y + 82
        for title, meta, active in entries:
            rounded(
                draw,
                (lx0 + 12, y, lx0 + list_w - 12, y + 76),
                2,
                rgba((19, 31, 22)) if active else rgba((8, 13, 10)),
                rgba(GREEN, 100) if active else rgba(LINE, 90),
                1,
            )
            ui_text(draw, lx0 + 28, y + 26, title, 12, TEXT)
            ui_text(draw, lx0 + 28, y + 52, meta, 9, MUTED, True)
            y += 84
        tx0 = lx0 + list_w
        ui_text(draw, tx0 + 30, main_y + 38, "RELEASE CONDUCTOR", 14, TEXT, True)
        messages = [
            ("YOU", "Finish the release path and verify every gate.", GREEN),
            ("CODEX", "Backend and frontend checks are green. Packaging now.", BLUE),
            ("YOU", "Capture the remaining risk and prepare the handoff.", GREEN),
        ]
        y = main_y + 86
        for role, body, color in messages:
            rounded(
                draw, (tx0 + 28, y, width - 28, y + 116), 3, rgba((12, 19, 15)), rgba(color, 90), 1
            )
            ui_text(draw, tx0 + 47, y + 27, role, 9, color, True)
            ui_text(draw, tx0 + 47, y + 61, body, 12, TEXT)
            y += 134

    card.alpha_composite(ui, (q(8), q(6)))
    return card


def draw_transition(frame: Image.Image, t: float, accent: tuple[int, int, int]) -> None:
    beat = t % 4.0
    if beat < 0.18 and t > 0.2:
        flash = int(95 * (1.0 - beat / 0.18))
        ImageDraw.Draw(frame, "RGBA").rectangle((0, 0, W, H), fill=rgba(accent, flash))
    if beat < 0.46 and t > 0.2:
        p = ease_out_cubic(beat / 0.46)
        draw = ImageDraw.Draw(frame, "RGBA")
        x = -420 + p * 2500
        draw.polygon(
            [(q(x), 0), (q(x + 200), 0), (q(x - 340), H), (q(x - 540), H)],
            fill=rgba(accent, int(110 * (1 - p))),
        )


def draw_intro(frame: Image.Image, local_t: float) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    p = ease_out_back(local_t / 0.8)
    beam = ease_out_cubic(local_t / 1.2)
    center_x = 960
    draw.rectangle(
        (q(center_x - 470 * beam), q(531), q(center_x + 470 * beam), q(536)), fill=rgba(GREEN, 130)
    )
    icon = Image.open(ASSETS / "swe-mux-icon.png").convert("RGBA")
    icon = icon.resize((q(140), q(140)), Image.Resampling.LANCZOS)
    place_card(frame, icon, center_x, 228, 0.72 + 0.28 * min(p, 1), 0, clamp(local_t / 0.28))
    swoop_text(
        frame,
        [("YOUR AGENTS", TEXT), ("DON'T CLOCK OUT.", GREEN_HOT)],
        local_t,
        center_x,
        345,
        [88, 106],
        1,
        True,
    )
    if local_t > 1.15:
        small_label(frame, "WINDOWS-NATIVE  //  LOCAL-FIRST", 960, 676, GREEN, "right")


def scene_app(
    frame: Image.Image,
    local_t: float,
    variant: str,
    headline: list[tuple[str, tuple[int, int, int]]],
    sizes: list[int],
    direction: int = 1,
) -> None:
    phase = clamp(local_t / 3.3)
    card = make_app_window(variant, phase)
    enter = ease_out_back((local_t - 0.18) / 0.70)
    exit_p = ease_in_out((local_t - 3.55) / 0.45)
    card_scale = (0.87 + 0.13 * min(enter, 1.0)) * (1.0 + 0.08 * exit_p)
    card_y = 666 + 80 * (1 - min(enter, 1.0)) + 30 * exit_p
    place_card(
        frame, card, 1010, card_y, card_scale, -1.0 * direction * (1 - min(enter, 1.0)), 1 - exit_p
    )
    swoop_text(frame, headline, local_t, 126, 72, sizes, direction, False)


def draw_remote(frame: Image.Image, local_t: float) -> None:
    phase = clamp(local_t / 3.2)
    app = make_app_window("agents", phase, 1240, 650)
    enter = ease_out_back((local_t - 0.12) / 0.68)
    exit_p = ease_in_out((local_t - 3.55) / 0.45)
    place_card(
        frame,
        app,
        760 - 120 * (1 - min(enter, 1)),
        670,
        0.90 * (1 - 0.12 * exit_p),
        -1.5,
        1 - exit_p,
    )

    phone_w, phone_h = q(330), q(650)
    phone = Image.new("RGBA", (phone_w, phone_h), (0, 0, 0, 0))
    pd = ImageDraw.Draw(phone, "RGBA")
    pd.rounded_rectangle(
        (0, 0, phone_w - 1, phone_h - 1),
        radius=q(44),
        fill=rgba((4, 7, 6)),
        outline=rgba((81, 107, 89)),
        width=q(3),
    )
    pd.rounded_rectangle(
        (q(11), q(11), phone_w - q(11), phone_h - q(11)),
        radius=q(34),
        fill=rgba((8, 13, 10)),
        outline=rgba(LINE),
        width=q(1),
    )
    pd.rounded_rectangle((q(114), q(14), q(216), q(29)), radius=q(8), fill=rgba((1, 3, 2)))
    ui_text(pd, 24, 60, "swe-mux", 14, TEXT)
    ui_text(pd, 305, 61, "●", 10, GREEN, True, "rm")
    ui_text(pd, 24, 100, "ORBITAL", 10, GREEN, True)
    rows = [
        ("release conductor", "WORKING", GREEN),
        ("auth audit", "READY", BLUE),
        ("verification", "PASS", AMBER),
    ]
    y = 132
    for name, state, color in rows:
        rounded(pd, (18, y, 312, y + 78), 3, rgba((13, 22, 16)), rgba(LINE), 1)
        draw_status_dot(pd, 38, y + 26, color)
        ui_text(pd, 54, y + 24, name, 11, TEXT)
        ui_text(pd, 54, y + 51, state, 8, color, True)
        y += 90
    rounded(pd, (18, 432, 312, 576), 3, rgba((5, 10, 7)), rgba(GREEN, 120), 1)
    ui_text(pd, 34, 462, "$ codex", 10, BLUE, True)
    ui_text(pd, 34, 493, "building release...", 10, TEXT, True)
    ui_text(pd, 34, 525, "✓ sessions live", 10, GREEN, True)
    ui_text(pd, 165, 614, "TAILNET CONNECTED", 8, GREEN, True, "mm")

    phone_x = 1510 + 180 * (1 - min(enter, 1))
    place_card(frame, phone, phone_x, 670, 1.0, 2.2, 1 - exit_p)
    swoop_text(frame, [("DESKTOP", TEXT), ("TO POCKET.", BLUE)], local_t, 128, 78, [88, 104], 1)
    small_label(frame, "SAME LIVE SESSIONS", 1600, 936, BLUE, "right")


def draw_montage(frame: Image.Image, local_t: float) -> None:
    words = [
        ("LONG-LIVED.", GREEN_HOT),
        ("REMOTE.", BLUE),
        ("CONTROLLED.", TEXT),
    ]
    segment = min(2, int(local_t / (4.0 / 3.0)))
    segment_t = local_t - segment * (4.0 / 3.0)
    value, color = words[segment]
    sprite = text_sprite(value, 150, color)
    p = ease_out_back(segment_t / 0.34)
    exit_p = ease_in_out((segment_t - 1.03) / 0.25)
    scale = 0.72 + 0.28 * min(p, 1.0) + 0.32 * exit_p
    place_card(frame, sprite, 960, 540, scale, -3.5 * (1 - min(p, 1.0)), 1 - exit_p)
    draw = ImageDraw.Draw(frame, "RGBA")
    for index in range(11):
        width = 300 + index * 37
        y = 230 + index * 61
        x = -400 + ((local_t * 760 + index * 210) % 2700)
        line(draw, [(x, y), (x + width, y)], rgba(color, 45), 3 if index % 3 == 0 else 1)


def draw_end(frame: Image.Image, local_t: float) -> None:
    draw = ImageDraw.Draw(frame, "RGBA")
    p = ease_out_back(local_t / 0.75)
    icon = Image.open(ASSETS / "swe-mux-icon.png").convert("RGBA")
    icon = icon.resize((q(180), q(180)), Image.Resampling.LANCZOS)
    place_card(frame, icon, 960, 300, 0.75 + 0.25 * min(p, 1), 0, clamp(local_t / 0.25))
    logo = text_sprite("swe-mux", 128, TEXT)
    place_card(frame, logo, 960, 520, 0.88 + 0.12 * min(p, 1), 0, clamp((local_t - 0.12) / 0.35))
    tag = text_sprite("KEEP EVERY AGENT IN MOTION.", 42, GREEN_HOT, "mono")
    place_card(frame, tag, 960, 678, 1.0, 0, clamp((local_t - 0.55) / 0.45))
    underline = ease_out_cubic((local_t - 0.75) / 0.8)
    line(draw, [(730, 744), (730 + 460 * underline, 744)], rgba(BLUE, 210), 4)
    if local_t > 1.3:
        small_label(frame, "CLAUDE CODE  /  CODEX  /  POWERSHELL", 960, 830, MUTED, "right")


def render_frame(t: float) -> Image.Image:
    scene = min(11, int(t // 4.0))
    local_t = t - scene * 4.0
    accents = [GREEN, GREEN, BLUE, GREEN, BLUE, BLUE, GREEN, AMBER, BLUE, PURPLE, GREEN, GREEN]
    accent = accents[scene]
    frame = make_background(accent).copy()
    draw_ambient(frame, t, accent)

    if scene == 0:
        draw_intro(frame, local_t)
    elif scene == 1:
        scene_app(
            frame,
            local_t,
            "persistence",
            [("RELOAD.", TEXT), ("THEY KEEP RUNNING.", GREEN_HOT)],
            [80, 92],
            1,
        )
        small_label(frame, "SESSION-PRESERVING", 1770, 118, GREEN, "right")
    elif scene == 2:
        scene_app(
            frame, local_t, "agents", [("ONE WINDOW.", TEXT), ("EVERY AGENT.", BLUE)], [86, 102], -1
        )
        small_label(frame, "CLAUDE  /  CODEX  /  SHELL", 1780, 120, BLUE, "right")
    elif scene == 3:
        scene_app(
            frame, local_t, "split", [("SPLIT.", TEXT), ("STACK. SHIP.", GREEN_HOT)], [92, 105], 1
        )
        small_label(frame, "PROJECT-OWNED WORKSPACES", 1775, 118, GREEN, "right")
    elif scene == 4:
        scene_app(
            frame, local_t, "preview", [("YOUR DEV LOOP.", TEXT), ("LIVE.", BLUE)], [82, 112], -1
        )
        small_label(frame, "LOOPBACK PREVIEWS  /  HMR", 1780, 118, BLUE, "right")
    elif scene == 5:
        draw_remote(frame, local_t)
    elif scene == 6:
        scene_app(
            frame, local_t, "fleet", [("THE FLEET.", TEXT), ("IN SIGHT.", GREEN_HOT)], [92, 110], 1
        )
        small_label(frame, "STATE  /  QUOTA  /  PROCESS", 1775, 118, GREEN, "right")
    elif scene == 7:
        scene_app(
            frame, local_t, "queue", [("QUEUE", TEXT), ("THE NEXT MOVE.", AMBER)], [96, 102], -1
        )
        small_label(frame, "DURABLE  /  ORDERED  /  AUDITED", 1780, 118, AMBER, "right")
    elif scene == 8:
        scene_app(
            frame, local_t, "voice", [("TALK.", TEXT), ("LISTEN. MOVE.", BLUE)], [102, 102], 1
        )
        small_label(frame, "HANDS-FREE CONVERSATION", 1780, 118, BLUE, "right")
    elif scene == 9:
        scene_app(
            frame,
            local_t,
            "history",
            [("HISTORY", TEXT), ("WITHOUT THE HUNT.", PURPLE)],
            [88, 90],
            -1,
        )
        small_label(frame, "SEARCH  /  RESUME  /  REVIEW", 1780, 118, PURPLE, "right")
    elif scene == 10:
        draw_montage(frame, local_t)
    else:
        draw_end(frame, local_t)

    draw_transition(frame, t, accent)
    return frame.convert("RGB")


def envelope(length: int, attack: float, release: float, sample_rate: int) -> np.ndarray:
    env = np.ones(length, dtype=np.float64)
    attack_n = min(length, max(1, int(attack * sample_rate)))
    release_n = min(length, max(1, int(release * sample_rate)))
    env[:attack_n] *= np.linspace(0.0, 1.0, attack_n, endpoint=False)
    env[-release_n:] *= np.linspace(1.0, 0.0, release_n)
    return env


def add_tone(
    mix: np.ndarray,
    sample_rate: int,
    start: float,
    duration: float,
    frequency: float,
    amplitude: float,
    wave_kind: str = "sine",
    pan: float = 0.0,
    attack: float = 0.01,
    release: float = 0.08,
    detune: float = 0.0,
) -> None:
    begin = max(0, int(start * sample_rate))
    end = min(len(mix), begin + int(duration * sample_rate))
    length = end - begin
    if length <= 0:
        return
    tt = np.arange(length, dtype=np.float64) / sample_rate
    phase = 2.0 * math.pi * frequency * tt
    if wave_kind == "saw":
        signal = sum(np.sin(phase * harmonic) / harmonic for harmonic in range(1, 8)) * (
            2.0 / math.pi
        )
    elif wave_kind == "square":
        signal = sum(np.sin(phase * harmonic) / harmonic for harmonic in range(1, 10, 2)) * (
            4.0 / math.pi
        )
    elif wave_kind == "glass":
        signal = np.sin(phase) + 0.42 * np.sin(phase * 2.01) + 0.18 * np.sin(phase * 3.99)
    else:
        signal = np.sin(phase)
    if detune:
        signal += 0.38 * np.sin(2.0 * math.pi * frequency * (1.0 + detune) * tt)
    signal *= envelope(length, attack, release, sample_rate) * amplitude
    left = math.sqrt((1.0 - pan) / 2.0)
    right = math.sqrt((1.0 + pan) / 2.0)
    mix[begin:end, 0] += signal * left
    mix[begin:end, 1] += signal * right


def add_kick(mix: np.ndarray, sample_rate: int, start: float, amplitude: float = 0.85) -> None:
    duration = 0.34
    begin = int(start * sample_rate)
    end = min(len(mix), begin + int(duration * sample_rate))
    if end <= begin:
        return
    tt = np.arange(end - begin, dtype=np.float64) / sample_rate
    phase = 2 * math.pi * (44 * tt + (116 - 44) * (1 - np.exp(-tt * 32)) / 32)
    body = np.sin(phase) * np.exp(-tt * 12.5)
    click = np.sin(2 * math.pi * 2800 * tt) * np.exp(-tt * 95)
    signal = amplitude * (body + 0.12 * click)
    mix[begin:end, 0] += signal * 0.72
    mix[begin:end, 1] += signal * 0.72


def add_noise_hit(
    mix: np.ndarray,
    sample_rate: int,
    start: float,
    duration: float,
    amplitude: float,
    pan: float,
    rng: np.random.Generator,
    bright: bool = False,
) -> None:
    begin = int(start * sample_rate)
    end = min(len(mix), begin + int(duration * sample_rate))
    if end <= begin:
        return
    length = end - begin
    raw = rng.normal(0, 1, length)
    if bright:
        signal = raw - np.concatenate(([0.0], raw[:-1])) * 0.78
    else:
        kernel = np.ones(7) / 7
        signal = np.convolve(raw, kernel, mode="same")
    tt = np.arange(length) / sample_rate
    signal *= np.exp(-tt * (18 if bright else 10)) * amplitude
    left = math.sqrt((1.0 - pan) / 2.0)
    right = math.sqrt((1.0 + pan) / 2.0)
    mix[begin:end, 0] += signal * left
    mix[begin:end, 1] += signal * right


def add_riser(mix: np.ndarray, sample_rate: int, end_time: float, rng: np.random.Generator) -> None:
    duration = 0.78
    begin = max(0, int((end_time - duration) * sample_rate))
    end = min(len(mix), int(end_time * sample_rate))
    length = end - begin
    if length <= 0:
        return
    tt = np.linspace(0, 1, length)
    raw = rng.normal(0, 1, length)
    high = raw - np.concatenate(([0.0], raw[:-1])) * (0.25 + 0.70 * tt)
    tone = np.sin(2 * math.pi * (180 * tt + 960 * tt * tt))
    env = tt * tt
    signal = (0.085 * high + 0.05 * tone) * env
    pan = np.sin(tt * math.pi * 3) * 0.55
    mix[begin:end, 0] += signal * np.sqrt((1 - pan) / 2)
    mix[begin:end, 1] += signal * np.sqrt((1 + pan) / 2)


def synthesize_music(path: Path, sample_rate: int = 48_000) -> None:
    rng = np.random.default_rng(42024)
    mix = np.zeros((int(DURATION * sample_rate), 2), dtype=np.float64)

    # Slow opening drone.
    for frequency, pan in ((73.42, -0.35), (110.0, 0.35), (146.83, 0.0)):
        add_tone(mix, sample_rate, 0.0, 8.0, frequency, 0.075, "saw", pan, 1.3, 1.5, 0.004)

    chord_progression = [
        (146.83, 174.61, 220.00),
        (116.54, 146.83, 174.61),
        (130.81, 164.81, 196.00),
        (110.00, 130.81, 164.81),
    ]
    for bar_start in np.arange(4.0, 48.0, 2.0):
        chord = chord_progression[int((bar_start - 4.0) / 2.0) % len(chord_progression)]
        fullness = 0.040 if bar_start < 12 else 0.060
        if 32 <= bar_start < 36:
            fullness = 0.030
        for note_index, frequency in enumerate(chord):
            add_tone(
                mix,
                sample_rate,
                bar_start,
                2.12,
                frequency,
                fullness,
                "saw",
                (note_index - 1) * 0.35,
                0.32,
                0.42,
                0.003,
            )

    bass_pattern = [73.42, 73.42, 73.42, 55.00, 58.27, 58.27, 65.41, 55.00]
    for beat_index, beat_time in enumerate(np.arange(4.0, 44.0, 0.5)):
        frequency = bass_pattern[beat_index % len(bass_pattern)]
        amplitude = 0.18 if beat_time < 12 else 0.23
        if 32 <= beat_time < 36:
            amplitude *= 0.55
        add_tone(
            mix, sample_rate, beat_time, 0.40, frequency, amplitude, "square", 0.0, 0.008, 0.09
        )

    arp_sets = [
        [293.66, 440.00, 523.25, 698.46],
        [233.08, 349.23, 440.00, 587.33],
        [261.63, 392.00, 523.25, 659.25],
        [220.00, 329.63, 440.00, 523.25],
    ]
    for step_index, step_time in enumerate(np.arange(8.0, 44.0, 0.25)):
        bar_index = int((step_time - 4.0) / 2.0) % len(arp_sets)
        frequency = arp_sets[bar_index][step_index % 4]
        amplitude = 0.050 if step_time < 16 else 0.075
        if 32 <= step_time < 36:
            amplitude = 0.035
        add_tone(
            mix,
            sample_rate,
            step_time,
            0.19,
            frequency,
            amplitude,
            "glass",
            (-0.55 if step_index % 2 == 0 else 0.55),
            0.004,
            0.07,
        )

    for beat_index, beat_time in enumerate(np.arange(4.0, 44.0, 0.5)):
        if 32 <= beat_time < 34:
            if beat_index % 4 == 0:
                add_kick(mix, sample_rate, beat_time, 0.70)
        else:
            add_kick(mix, sample_rate, beat_time, 0.78 if beat_time < 16 else 0.92)
        if beat_index % 2 == 1 and not (32 <= beat_time < 34):
            add_noise_hit(mix, sample_rate, beat_time, 0.24, 0.15, 0.0, rng, False)

    for hat_index, hat_time in enumerate(np.arange(4.0, 44.0, 0.25)):
        if 32 <= hat_time < 34:
            continue
        amp = 0.035 if hat_index % 2 == 0 else 0.022
        add_noise_hit(
            mix, sample_rate, hat_time, 0.08, amp, -0.35 if hat_index % 2 == 0 else 0.35, rng, True
        )

    for transition in np.arange(4.0, 45.0, 4.0):
        add_riser(mix, sample_rate, transition, rng)
        add_noise_hit(mix, sample_rate, transition, 0.55, 0.24, 0.0, rng, False)
        add_tone(mix, sample_rate, transition, 0.95, 55.0, 0.25, "sine", 0.0, 0.002, 0.45)

    # Final logo chord and sub impact.
    for frequency, pan in ((146.83, -0.35), (220.0, 0.0), (293.66, 0.35), (349.23, 0.1)):
        add_tone(mix, sample_rate, 44.0, 4.0, frequency, 0.11, "saw", pan, 0.12, 1.0, 0.003)
    add_kick(mix, sample_rate, 44.0, 1.05)
    add_tone(mix, sample_rate, 44.0, 2.2, 36.71, 0.34, "sine", 0.0, 0.004, 0.85)

    # Sidechain-style ducking at kick positions.
    duck = np.ones(len(mix), dtype=np.float64)
    for kick_time in np.arange(4.0, 44.5, 0.5):
        start = int(kick_time * sample_rate)
        length = min(len(mix) - start, int(0.17 * sample_rate))
        if length <= 0:
            continue
        curve = 0.72 + 0.28 * (1 - np.exp(-np.linspace(0, 5, length)))
        duck[start : start + length] *= curve
    mix *= duck[:, None]

    fade_in = min(len(mix), int(0.55 * sample_rate))
    fade_out = min(len(mix), int(1.0 * sample_rate))
    mix[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    mix = np.tanh(mix * 1.35)
    peak = np.max(np.abs(mix))
    if peak > 0:
        mix *= 0.84 / peak
    pcm = np.int16(np.clip(mix, -1, 1) * 32767)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def render_stills() -> None:
    STILLS.mkdir(parents=True, exist_ok=True)
    key_times = [2.2, 5.9, 9.8, 13.8, 17.8, 21.8, 25.8, 29.8, 33.8, 37.8, 41.4, 46.2]
    still_images: list[Image.Image] = []
    for index, timestamp in enumerate(key_times, 1):
        frame = render_frame(timestamp)
        path = STILLS / f"{index:02d}-{timestamp:04.1f}s.png"
        frame.save(path, optimize=True)
        still_images.append(frame.resize((q(480), q(270)), Image.Resampling.LANCZOS))

    sheet = Image.new("RGB", (q(1920), q(810)), (3, 5, 4))
    for index, still in enumerate(still_images):
        x = (index % 4) * q(480)
        y = (index // 4) * q(270)
        sheet.paste(still, (x, y))
    sheet.save(OUTPUT / "contact-sheet.jpg", quality=93, subsampling=0)


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {result.returncode}")


def render_video(fps: int, preview: bool) -> Path:
    visual_path = BUILD / ("visual-preview.mp4" if preview else "visual-1080p.mp4")
    total_frames = int(DURATION * fps)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast" if not preview else "veryfast",
        "-crf",
        "16" if not preview else "19",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(visual_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, cwd=ROOT)
    assert process.stdin is not None
    try:
        for frame_index in range(total_frames):
            timestamp = frame_index / fps
            process.stdin.write(render_frame(timestamp).tobytes())
            if frame_index % max(1, fps * 2) == 0:
                print(f"rendered {timestamp:05.1f}s / {DURATION:.1f}s", flush=True)
    finally:
        process.stdin.close()
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg video encoder failed with exit code {return_code}")
    return visual_path


def mux(video_path: Path, music_path: Path, preview: bool) -> Path:
    final_path = OUTPUT / (
        "swe-mux-trailer-preview.mp4" if preview else "swe-mux-trailer-1080p.mp4"
    )
    run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(music_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(final_path),
        ]
    )
    return final_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Render 960x540 at 24 fps")
    parser.add_argument(
        "--stills-only", action="store_true", help="Render storyboard stills and contact sheet only"
    )
    parser.add_argument(
        "--skip-stills", action="store_true", help="Skip still and contact-sheet generation"
    )
    return parser.parse_args()


def main() -> int:
    global W, H, SCALE
    args = parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must be available on PATH", file=sys.stderr)
        return 2

    if args.preview:
        W, H = 960, 540
        SCALE = 0.5
        fps = 24
    else:
        W, H = DESIGN_W, DESIGN_H
        SCALE = 1.0
        fps = 30

    BUILD.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    STILLS.mkdir(parents=True, exist_ok=True)

    music_path = OUTPUT / "swe-mux-original-score.wav"
    print("synthesizing original score", flush=True)
    synthesize_music(music_path)
    if not args.skip_stills:
        print("rendering storyboard stills", flush=True)
        render_stills()
    if args.stills_only:
        print(OUTPUT / "contact-sheet.jpg")
        return 0

    video_path = render_video(fps, args.preview)
    final_path = mux(video_path, music_path, args.preview)
    print(final_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
