"""Recomputes the WCAG table from the stylesheet. Run from anywhere:

    python site/tools/contrast.py

Every text token, against both backgrounds, in both themes. The table is
computed rather than recorded because a palette edit that quietly drops a token
below AA is invisible in a browser and obvious here.

It covers every page by covering the one stylesheet all of them use:
`tools/build.py` extracts `index.html`'s `<style>` block into each generated
page, and this script verifies that each page carries it byte for byte before
measuring it once. A per-page table would be four identical tables, and the
failure it would catch is exactly the one the identity check catches first.
"""

import re
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent


def lum(hexstr):
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", block))


h = (SITE / "index.html").read_text(encoding="utf-8")

# The one stylesheet, and the proof that it is the one every page carries.
style = re.search(r"<style>\n(.*?)\n</style>", h, re.S)
if not style:
    raise SystemExit("site/index.html has no <style> block")

pages = sorted(p for p in SITE.glob("*/index.html") if p.parent.name not in {"img", "tools"})
print(f"stylesheet  ({len(pages) + 1} pages)")
drifted = []
for page in pages:
    if style.group(1) not in page.read_text(encoding="utf-8"):
        drifted.append(page.parent.name + "/index.html")
    else:
        print(f"  {page.parent.name}/index.html  inherits index.html's tokens")
for name in drifted:
    print(f"  FAIL {name} does not carry index.html's stylesheet; run site/tools/build.py")

dark = tokens(h.split(":root {")[1].split("}")[0])
light = tokens(h.split(':root[data-theme="light"] {')[1].split("}")[0])

for name, t in (("DARK", dark), ("LIGHT", light)):
    print(f"\n{name}  (bg {t['bg']}, panel {t['panel']})")
    for fg in ("fg", "fg-2", "fg-3", "fg-4", "green", "cyan", "orange", "red"):
        on_bg = ratio(t[fg], t["bg"])
        on_panel = ratio(t[fg], t["panel"])
        worst = min(on_bg, on_panel)
        # AA body text needs 4.5, large/decorative 3.0
        mark = "AA " if worst >= 4.5 else ("aa-large" if worst >= 3.0 else "FAIL")
        print(f"  {fg:<7} {t[fg]}  bg {on_bg:5.2f}  panel {on_panel:5.2f}   {mark}")

print()
print("AA needs 4.5:1 for text. --fg-4 sits below it on purpose and is restricted")
print("to borders and inert markers, never body text. See README.md.")

sys.exit(1 if drifted else 0)
