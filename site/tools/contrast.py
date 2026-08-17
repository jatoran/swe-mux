import re
from pathlib import Path

h = (Path(__file__).parent.parent / "index.html").read_text(encoding="utf-8")


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
