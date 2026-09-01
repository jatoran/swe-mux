"""Draw the screenshot placeholders that stand in for the real captures. Run from anywhere:

    python site/tools/placeholders.py

Every file it writes into `img/` is a placeholder, not content. They exist because
the captures that used to sit in those filenames were real screenshots of a live
machine - project names, an operator name, account spend percentages, absolute
paths, transcript prose - and this directory is a public deploy root, so anything
in it is served and scraped. Blurring or cropping a capture leaves the capture in
the repository's history of that file; replacing it does not.

Three rules the drawing follows, each of which is the reason for a design choice
that would otherwise look arbitrary.

**One raster, both themes.** These slots are for real screenshots later, and a
real screenshot is one file, so a placeholder that shipped as a dark/light pair
(the way the wordmark does) would bake a naming convention into the markup that
dies the day the first real capture lands. Instead the ground is left fully
transparent and only the marks are drawn: `figure.shot` supplies
`background: var(--panel-2)`, so the placeholder takes the page's own panel
colour in whichever theme is showing, and swapping in a real capture is a pure
file replacement with no markup change.

**Every mark sits at one luminance, and it is computed rather than picked.**
No single colour clears WCAG AA against both `--panel-2` values; the two grounds
are 15.11:1 apart. `TARGET_L` is the luminance whose contrast against the two is
equal, which is the best a single raster can do, and it lands just under 3.9:1 -
above the 3.0 large-text floor in both themes rather than comfortable in one and
unreadable in the other. Each mark is a palette token rescaled to that luminance
in linear light, so it keeps the token's hue and loses only its brightness.
Hierarchy is carried by size and alpha instead, because lowering alpha moves a
mark toward the ground in *both* themes while a second colour would not.

**The palette is read out of the stylesheet, never transcribed.** Same rule
`tools/contrast.py` and `tools/build.py` already follow: there is one copy of the
tokens and it is `index.html`'s.

The script fails rather than writing if any opaque mark drops below 3.0:1 against
either ground, so a palette edit cannot quietly make these illegible.
"""

import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SITE = Path(__file__).resolve().parent.parent
IMG = SITE / "img"

# Every screenshot slot the site owns, with the pixel dimensions measured off the
# captures these placeholders replaced - matching them exactly is what keeps the
# page's layout, and `tools/check.mjs`'s overflow assertions, unchanged.
#
# `label` is what the raster says. `brief` is what the real capture has to
# contain, kept here rather than in prose so the specification and the thing
# standing in for it cannot drift apart. Section 8 of `README.md` holds the
# rules those briefs have to satisfy (crop to the feature, never the window;
# the crop must contain the claim; an empty panel is not a screenshot).
SLOTS = [
    ("desktop-alerts.webp", 2100, 1275, "Attention inbox",
     "The ranked inbox cropped to the panel, showing the interrupt budget line and at "
     "least one suppressed item with its reason. Not an empty inbox."),
    ("desktop-git.webp", 2100, 1275, "Git map",
     "The Git drawer's map cropped to the rows: branches with ahead/behind counts and "
     "the commit provenance column."),
    ("desktop-insight.webp", 2100, 1275, "Behaviour timeline",
     "The Insight tab's timeline cropped to the records, with scan budget visible and "
     "actual scan records present."),
    ("desktop-notes.webp", 2100, 1275, "Note editor",
     "The note editor body only, cropped out of the drawer: rendered headings, nested "
     "lists, and a checkbox row."),
    ("mobile-nav.webp", 1206, 2622, "Mobile navigation",
     "The navigation overlay: two projects expanded with session rows, status dots, "
     "elapsed times, and model names."),
    ("mobile-notes.webp", 1206, 2622, "Mobile notes",
     "The Markdown editor on a phone with rendered structure, proving the editor is not "
     "a desktop-only surface."),
    ("mobile-alerts.webp", 1206, 2622, "Mobile alerts",
     "The attention inbox on a phone with ranked items present and the budget line "
     "visible."),
]

TAG = "placeholder"
SUB = "screenshot pending"


# --------------------------------------------------------------------- colour
# WCAG relative luminance and contrast: the published formulae, not a choice this
# file gets to make. `tools/contrast.py` is the audit that owns the palette; this
# is the same arithmetic applied to colours it does not see, because they are
# drawn into a raster rather than declared in the stylesheet.
def _to_linear(c):
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _from_linear(c):
    c = min(1.0, max(0.0, c))
    return c * 12.92 if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def linear(hexstr):
    return tuple(_to_linear(int(hexstr[i:i + 2], 16) / 255) for i in (1, 3, 5))


def lum(hexstr):
    r, g, b = linear(hexstr)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    hi, lo = max(lum(a), lum(b)), min(lum(a), lum(b))
    return (hi + 0.05) / (lo + 0.05)


def rescale(hexstr, target):
    """The token's hue at `target` luminance: scale it in linear light, then clamp.

    Clamping can pull the result off the target, so callers measure the colour
    that came back rather than assuming the one they asked for.
    """
    r, g, b = linear(hexstr)
    have = 0.2126 * r + 0.7152 * g + 0.0722 * b
    k = target / have if have > 0 else 0.0
    r8, g8, b8 = (round(255 * _from_linear(c * k)) for c in (r, g, b))
    return f"#{r8:02x}{g8:02x}{b8:02x}"


def rgb(hexstr, alpha=1.0):
    return tuple(int(hexstr[i:i + 2], 16) for i in (1, 3, 5)) + (round(255 * alpha),)


# ---------------------------------------------------------------------- fonts
# Pillow's bundled face, which is present wherever Pillow is and therefore gives
# the same output on any host that runs this. A host font would make the committed
# rasters depend on which machine last ran the script, and the site loads no font
# from a network by rule (README.md section 6), which applies to its tooling too.
def font(size):
    return ImageFont.load_default(size=size)


def text_width(draw, s, f, tracking=0.0):
    if not tracking:
        return draw.textlength(s, font=f)
    return sum(draw.textlength(ch, font=f) for ch in s) + tracking * max(0, len(s) - 1)


def tracked(draw, xy, s, f, fill, tracking, anchor_left=True):
    """`letter-spacing` has no Pillow equivalent, so the tag is set glyph by glyph."""
    x, y = xy
    if not anchor_left:
        x -= text_width(draw, s, f, tracking) / 2
    for ch in s:
        draw.text((x, y), ch, font=f, fill=fill, anchor="lm")
        x += draw.textlength(ch, font=f) + tracking


def wrapped(draw, s, f, maxw):
    lines, line = [], ""
    for word in s.split():
        trial = (line + " " + word).strip()
        if line and draw.textlength(trial, font=f) > maxw:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


# ----------------------------------------------------------------- the palette
html = (SITE / "index.html").read_text(encoding="utf-8")


def tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


dark = tokens(html.split(":root {")[1].split("}")[0])
light = tokens(html.split(':root[data-theme="light"] {')[1].split("}")[0])
GROUNDS = (dark["panel-2"], light["panel-2"])

# The luminance whose contrast against both grounds is equal, and therefore the
# highest worst-case contrast a single raster can reach across the two themes.
# Solving (L+.05)/(Ld+.05) = (Ll+.05)/(L+.05) gives the geometric mean.
TARGET_L = ((lum(GROUNDS[0]) + 0.05) * (lum(GROUNDS[1]) + 0.05)) ** 0.5 - 0.05

INK = rescale(dark["fg"], TARGET_L)        # the neutral: label, frame, hatch
ACCENT = rescale(dark["green"], TARGET_L)  # the brand accent: tag and rule

print(f"grounds     dark {GROUNDS[0]}  light {GROUNDS[1]}")
print(f"target lum  {TARGET_L:.4f}")
failures = 0
for name, token, value in (("ink", "--fg", INK), ("accent", "--green", ACCENT)):
    d, li = ratio(value, GROUNDS[0]), ratio(value, GROUNDS[1])
    worst = min(d, li)
    mark = "ok" if worst >= 3.0 else "FAIL"
    if worst < 3.0:
        failures += 1
    print(f"  {name:<7} {token:<8} {value}  on dark {d:5.2f}  on light {li:5.2f}   {mark}")
if failures:
    print("\nA mark below 3.0:1 is illegible in one of the two themes. Fix the palette")
    print("or the derivation; do not lower the floor.")
    sys.exit(1)


# ---------------------------------------------------------------------- draw
def dashed_rect(draw, box, fill, width, dash, gap):
    x0, y0, x1, y1 = box
    for x in range(int(x0), int(x1), dash + gap):
        xe = min(x + dash, x1)
        draw.rectangle([x, y0, xe, y0 + width - 1], fill=fill)
        draw.rectangle([x, y1 - width + 1, xe, y1], fill=fill)
    for y in range(int(y0), int(y1), dash + gap):
        ye = min(y + dash, y1)
        draw.rectangle([x0, y, x0 + width - 1, ye], fill=fill)
        draw.rectangle([x1 - width + 1, y, x1, ye], fill=fill)


def render(w, h, label, filename):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")
    u = min(w, h) / 100.0  # one unit: the short side is the same on both aspect ratios

    # Hatch, at the stylesheet's own angle. The `.crop` and `.vis` placeholders on
    # the page are filled with `repeating-linear-gradient(135deg, ...)`; this is
    # that fill, drawn, so a raster placeholder and a CSS one read as one family.
    hatch_w = max(1, round(0.15 * u))
    step = round(1.55 * u)
    for x in range(-h, w + h, step):
        d.line([(x, h), (x + h, 0)], fill=rgb(INK, 0.10), width=hatch_w)

    # Dashed frame, echoing `.crop`'s `1px dashed var(--line-2)`.
    inset = round(2.2 * u)
    dashed_rect(d, (inset, inset, w - inset - 1, h - inset - 1),
                rgb(INK, 0.42), max(1, round(0.16 * u)), round(0.9 * u), round(0.7 * u))

    pad = round(4.6 * u)
    f_tag = font(round(2.15 * u))
    f_label = font(round(6.0 * u))
    f_sub = font(round(3.4 * u))
    f_foot = font(round(2.15 * u))
    track = 0.12 * f_tag.size  # the stylesheet's `letter-spacing: 0.12em` on `.tag`

    # Tag, top left, over the hairline the `.tag` class carries as a bottom border.
    tag_y = pad + f_tag.size * 0.5
    tracked(d, (pad, tag_y), TAG.upper(), f_tag, rgb(ACCENT), track)
    rule_w = text_width(d, TAG.upper(), f_tag, track)
    d.rectangle([pad, tag_y + f_tag.size * 0.85,
                 pad + rule_w, tag_y + f_tag.size * 0.85 + max(1, round(0.13 * u))],
                fill=rgb(ACCENT, 0.55))

    # Label and its subtitle, optically centred.
    cx, cy = w / 2, h / 2
    lines = wrapped(d, label, f_label, w - 2 * pad)
    lh = f_label.size * 1.22
    top = cy - (len(lines) * lh + f_label.size * 0.9 + f_sub.size) / 2
    for i, line in enumerate(lines):
        d.text((cx, top + i * lh + lh / 2), line, font=f_label, fill=rgb(INK), anchor="mm")
    rule_y = top + len(lines) * lh + f_label.size * 0.30
    d.rectangle([cx - 5 * u, rule_y, cx + 5 * u, rule_y + max(1, round(0.14 * u))],
                fill=rgb(ACCENT, 0.70))
    d.text((cx, rule_y + f_label.size * 0.55 + f_sub.size * 0.5), SUB,
           font=f_sub, fill=rgb(INK, 0.74), anchor="mm")

    # Provenance, bottom left: what this file is and what regenerates it.
    foot_y = h - pad - f_foot.size * 0.5
    d.text((pad, foot_y - f_foot.size * 1.45), f"img/{filename}",
           font=f_foot, fill=rgb(INK, 0.62), anchor="lm")
    d.text((pad, foot_y), "site/tools/placeholders.py",
           font=f_foot, fill=rgb(INK, 0.45), anchor="lm")
    return im


IMG.mkdir(exist_ok=True)
print()
for filename, w, h, label, _brief in SLOTS:
    path = IMG / filename
    if path.exists():
        with Image.open(path) as existing:
            if existing.size != (w, h):
                print(f"  note {filename} on disk is {existing.size}, "
                      f"table says {(w, h)}; writing the table's size")
    render(w, h, label, filename).save(path, "WEBP", lossless=True, quality=100, method=6)
    print(f"  wrote {filename:24} {w:5} x {h:<5} {path.stat().st_size:>7} bytes   {label}")

print(f"\n{len(SLOTS)} placeholders. Each one replaces a real capture and must itself be")
print("replaced before launch, by a shot taken in an environment with no personal or")
print("third-party data in it. README.md section 8 holds the briefs and the rules.")
