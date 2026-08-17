"""Key the black ground off the swe-mux wordmark, crop it, and emit both theme variants."""
from pathlib import Path

from PIL import Image

SRC = Path(__file__).parent / "wordmark-source.png"
OUT = Path(__file__).parent.parent / "img"

im = Image.open(SRC).convert("RGB")
w, h = im.size
px = im.load()

# Background from the corners, so a slightly-off black still keys cleanly.
corners = [px[x, y] for x, y in
           ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3),
            (w // 2, 2), (w // 2, h - 3))]
bg = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))
# The render carries film-grain style noise: background pixels top out at a
# max-channel of 21 while glyph pixels reach 255. Keying against the sampled
# mean left every noise speck faintly visible and made the bbox the whole frame,
# so the black point sits just above the measured noise ceiling instead.
FLOOR = 22
print("sampled background:", bg, "| black point:", FLOOR)

out = Image.new("RGBA", (w, h))
op = out.load()
for y in range(h):
    for x in range(w):
        r, g, b = px[x, y]
        # Alpha is how far the pixel rises above the ground on its strongest
        # channel. Taking the max rather than luminance keeps the green mark
        # fully opaque instead of fading it for being darker than the white text.
        a = max((r - FLOOR), (g - FLOOR), (b - FLOOR)) / (255 - FLOOR)
        if a <= 0.0:
            continue
        a = min(a, 1.0)
        # Unpremultiply against the ground we removed.
        nr = min(255, max(0, round(bg[0] + (r - bg[0]) / a)))
        ng = min(255, max(0, round(bg[1] + (g - bg[1]) / a)))
        nb = min(255, max(0, round(bg[2] + (b - bg[2]) / a)))
        op[x, y] = (nr, ng, nb, round(a * 255))

box = out.getbbox()
print("content bbox:", box, "of", (w, h))
pad = 6
box = (max(0, box[0] - pad), max(0, box[1] - pad),
       min(w, box[2] + pad), min(h, box[3] + pad))
out = out.crop(box)
print("cropped to:", out.size)

# One master at 3x the largest on-page use; everything else scales down cleanly.
target_w = 640
out = out.resize((target_w, round(out.height * target_w / out.width)), Image.LANCZOS)
OUT.mkdir(exist_ok=True)
out.save(OUT / "logo.png", optimize=True)
print("wrote logo.png", out.size, (OUT / "logo.png").stat().st_size, "bytes")

# Light-theme variant: the near-white glyphs become ink, the green mark darkens
# to the light palette's green. Saturation tells the two apart.
FG_LIGHT = (0x16, 0x19, 0x23)
GREEN_LIGHT = (0x3f, 0x7a, 0x12)
lite = out.copy()
lp = lite.load()
for y in range(lite.height):
    for x in range(lite.width):
        r, g, b, a = lp[x, y]
        if a == 0:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / mx if mx else 0
        base = GREEN_LIGHT if sat > 0.22 else FG_LIGHT
        lp[x, y] = (*base, a)
lite.save(OUT / "logo-light.png", optimize=True)
print("wrote logo-light.png", (OUT / "logo-light.png").stat().st_size, "bytes")
