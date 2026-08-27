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


# A selector whose height scales in one rule and is pinned to bare px in a later
# one renders at the pinned value: the cascade is last-wins at equal specificity,
# and a `@media(max-width:760px)` block sits after the base rule it overrides. So
# a new mobile override silently un-scales that element on the one device the
# scale mostly exists for. Each entry below is a deliberate pin, with its reason.
INTENTIONAL_PINS = {
    (".context-menu", "width"): "mobile: width:min(300px,100dvw - 8px), viewport-bound",
    (".terminal-menu", "width"): "mobile: width:min(300px,100dvw - 8px), viewport-bound",
    (".settings-panel", "height"): "height:min(760px,...) is a panel maximum, viewport-bound",
    (".mobile-nav-toggle", "height"): "44px touch target, already comfortable at any scale",
    # The rail chip's height is no longer one of these. It reads `var(--rail-chip-h)` in
    # a single rule, and the density variable group resolves that to
    # `calc(27px*var(--ui-scale))` on the desktop and to the 44px touch target on the
    # phone - so the mobile target is stated once where the desktop value is stated,
    # rather than as a later override that has to be excused here.
    (".terminal-action-rail button", "min-width"): "96px touch target",
    (".sidebar-footer .notes-shelf-trigger", "height"): "44px touch target",
}


def test_no_unintended_rule_pins_a_height_the_base_scales() -> None:
    css = (SRC / "style.css").read_text(encoding="utf-8")
    # Blank comments, keeping newlines so the reported lines match the file.
    css = re.sub(
        r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), css, flags=re.DOTALL
    )
    props = ("height", "grid-template-rows", "width", "min-width", "line-height")
    declaration = re.compile(r"(?<![-\w])(" + "|".join(props) + r")\s*:\s*([^;]+)")

    scaled: set[tuple[str, str]] = set()
    pinned: set[tuple[str, str]] = set()
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if rule.group(1).lstrip().startswith("@"):
            continue
        for found in declaration.finditer(rule.group(2)):
            value = found.group(2)
            sizes = [float(size) for size in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)px", value)]
            # Below ~16px is a dot, rule, divider or progress bar, not type.
            if not sizes or max(sizes) < 16:
                continue
            for selector in rule.group(1).split(","):
                key = (" ".join(selector.split()), found.group(1))
                if not key[0]:
                    continue
                (scaled if "var(--ui-scale)" in value else pinned).add(key)

    unexplained = (scaled & pinned) - set(INTENTIONAL_PINS)
    assert not unexplained, (
        "these selectors scale in one rule and are pinned to bare px in another, so the "
        "pin wins and they stop following --ui-scale: "
        + ", ".join(f"{selector} {prop}" for selector, prop in sorted(unexplained))
        + " — either scale the pinned declaration or add it to INTENTIONAL_PINS with a reason"
    )
    # Keep the allowlist honest: an entry that no longer conflicts is stale.
    stale = set(INTENTIONAL_PINS) - (scaled & pinned)
    assert not stale, f"INTENTIONAL_PINS entries no longer conflict: {sorted(stale)}"


def test_portalled_overlays_are_normalized_like_the_rest_of_the_chrome() -> None:
    """A popover rendered into `document.body` still has to follow the scale.

    The normalizer is scoped to `.app-shell *`, and the account/resource popovers
    escape it deliberately (`createPortal`) so a narrow or collapsed sidebar cannot
    clip them. Nothing then overrode their own px font sizes, so they rendered at a
    fixed size at every setting — measured: every labelled row stayed at 7.5-9px while
    the chrome beside it went 11px → 15.4px. That is the surface where the quota
    numbers are actually read, so it is the worst one to leave behind.
    """
    css = (SRC / "style.css").read_text(encoding="utf-8")
    accounts = (SRC / "ProviderAccounts.tsx").read_text(encoding="utf-8")
    resources = (SRC / "ResourceUsage.tsx").read_text(encoding="utf-8")

    assert ".ui-portal,\n.ui-portal * {" in css
    assert ".ui-portal *::before,.ui-portal *::after {" in css
    # Every root handed to createPortal carries the class, or it silently opts out.
    for name, source in (("ProviderAccounts", accounts), ("ResourceUsage", resources)):
        for portal in re.finditer(r"createPortal\((\w+),\s*document\.body\)", source):
            root = re.search(
                rf"const {portal.group(1)}\s*=[^\n]*?class=\"([^\"]*)\"", source
            )
            assert root, f"{name}: cannot find the root element for {portal.group(1)}"
            assert "ui-portal" in root.group(1).split(), (
                f"{name} portals {portal.group(1)} to the body without `ui-portal`, "
                "so it will not follow the UI scale"
            )


def test_the_terminal_font_follows_the_chrome_scale() -> None:
    """xterm owns its font, so CSS cannot reach it and the scale is passed as a number.

    Pinned because the wiring has three halves that are individually inert: the prop
    has to reach the pane, the memo has to let the change through, and the apply has to
    happen outside the construction effect (listing it there would dispose the terminal
    and replay the whole buffer to change a font size).
    """
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    pane = (SRC / "TerminalPane.tsx").read_text(encoding="utf-8")

    assert "uiScale={uiScale}" in app
    assert "previewUiScaleConfig(config)" in app
    assert "watchUiScaleProfile(scale=>" in app
    assert "activeUiScale={uiScale} onUiScalePreview={previewUiScaleConfig}" in app

    assert "const baseFont = scaledFontSize(BASE_FONT_SIZE, uiScale)" in pane
    assert "useEffect(() => { applyBaseFontRef.current() }, [uiScale])" in pane
    # The memo lives in `terminalPaneMemo.ts` now, so its rules can be exercised as a
    # function; the "let the change through" half of this wiring is asserted there.
    memo = (SRC / "terminalPaneMemo.ts").read_text(encoding="utf-8")
    assert "a.uiScale === b.uiScale" in memo
    # A scale change must not be in the terminal's construction deps.
    lifetime = re.search(r"\n  \}, \[session\.id, keybindings,[^\]]*\]\)", pane)
    assert lifetime, "the terminal lifetime effect's dep list has moved"
    assert "uiScale" not in lifetime.group(0)
    # Nothing may read the unscaled constant once the terminal exists; every site that
    # used to has to go through the ref, or a pane silently keeps the 100% font.
    body = pane.split("function TerminalPaneImpl", 1)[1]
    assert "BASE_FONT_SIZE" not in body.replace(
        "const baseFont = scaledFontSize(BASE_FONT_SIZE, uiScale)", ""
    )


def test_scale_inputs_are_captured_before_browser_zoom_and_terminal_input() -> None:
    app = (SRC / "App.tsx").read_text(encoding="utf-8")

    assert "window.addEventListener('keydown',onKey,true)" in app
    assert "window.addEventListener('wheel',onWheel,{capture:true,passive:false})" in app
    assert "event.stopImmediatePropagation()" in app
    assert "showInteractionHud(`UI scale ${Math.round(next*100)}%${limit}`)" in app
