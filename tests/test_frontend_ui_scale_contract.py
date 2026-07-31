"""Chrome scale spans three files that can only drift silently.

`config.py` decides which steps are accepted, `uiScale.ts` decides which are
offered and which are honoured once received, and `style.css` is the only thing
that renders any of it. A step added to one and not the others produces either a
select entry the daemon rejects on save, or a stored value the browser quietly
falls back from — neither of which surfaces as a failure anywhere else.
"""

from __future__ import annotations

import re
from pathlib import Path

from swe_mux.config import UI_SCALES, Config

SRC = Path(__file__).parents[1] / "frontend" / "src"


def test_browser_offers_exactly_the_steps_the_daemon_accepts() -> None:
    source = (SRC / "uiScale.ts").read_text(encoding="utf-8")
    steps = re.search(r"UI_SCALE_STEPS\s*=\s*\[([^\]]+)\]", source)
    assert steps, "uiScale.ts must export a literal UI_SCALE_STEPS array"
    assert {float(value) for value in steps.group(1).split(",")} == UI_SCALES


def test_both_device_classes_default_to_todays_size() -> None:
    """The feature must be inert until someone changes it."""
    config = Config()
    assert config.ui_scale_desktop == 1.0
    assert config.ui_scale_mobile == 1.0
    assert 1.0 in UI_SCALES
    source = (SRC / "uiScale.ts").read_text(encoding="utf-8")
    assert re.search(r"DEFAULT_UI_SCALE\s*:\s*UiScale\s*=\s*1\.0", source)


def test_the_stylesheet_derives_chrome_type_from_the_scale() -> None:
    css = (SRC / "style.css").read_text(encoding="utf-8")
    # `--ui-scale:1` in :root is what makes an unloaded config render at the
    # historical size, and is the value uiScale.ts releases the property back to.
    assert "--ui-scale:1;" in css
    assert "--ui-font-size:calc(11px*var(--ui-scale))" in css


def test_geometry_that_holds_chrome_text_scales_with_it() -> None:
    """Type scaling alone clips; the boxes holding it have to move too.

    Not a count of every scaled declaration — that list is meant to grow. This
    pins the surfaces where a fixed height and a growing font visibly collide
    first, so a rewrite of one of them cannot quietly drop the scale.
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    scaled = set(re.findall(r"([-\w]+):calc\([\d.]+px\*var\(--ui-scale\)\)", css))
    for prop in ("height", "min-height", "grid-template-rows"):
        assert prop in scaled, f"no {prop} scales with --ui-scale"

    # Every selector whose rule scales something. A rule is `<selectors>{<body>}`,
    # and the same selector is re-declared down the cascade, so this is a
    # membership test rather than a count.
    scaling_selectors = " ".join(
        rule.group(1)
        for rule in re.finditer(r"([^{}]*)\{([^}]*)\}", css)
        if "var(--ui-scale)" in rule.group(2)
    )
    for selector in (
        ".session-row",  # two lines in one fixed box: the tightest fit in the app
        ".project-row",
        ".sidebar-heading",
        ".pane-bar",  # per-pane status bar
        ".stack-tabs button",  # tab titles, which the scale exists to enlarge
        ".workspace",  # the app-identity row above the sidebar
    ):
        assert selector in scaling_selectors, f"{selector} does not follow --ui-scale"
