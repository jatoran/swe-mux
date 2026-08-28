"""Generates the sibling pages beside `site/index.html`. Run from anywhere:

    python site/tools/build.py            # write the pages
    python site/tools/build.py --check    # fail if a written page is stale

`index.html` is hand-authored and has no build step; this script does not touch
it. The four pages it writes each have a source that is not prose the site owns:

    changelog/        <- CHANGELOG.md                      (the repository's own)
    acknowledgements/ <- packaging/third_party_licenses.json + THIRD-PARTY-NOTICES.md
                         + site/content/acknowledgements-prose.html (hand-written)
    docs/             <- .docs/ itself, plus the curated map below
    roadmap/          <- site/content/roadmap.html         (hand-written)

Transcribing any of those by hand is the failure this exists to prevent: a copied
dependency table is wrong the first time somebody adds a dependency, and a copied
changelog is wrong the first time somebody releases. The two pages whose argument
*is* the work (roadmap, and the acknowledgements prose) keep their content in
`site/content/` and get only their chrome from here.

**The design system is extracted, not duplicated.** The `<style>` block is read
out of `index.html` at generation time, so there is exactly one copy of the
tokens and exactly one stylesheet for `tools/contrast.py` to audit. Adding a
second copy is how the light theme breaks on one page only.

The output is committed. GitHub Pages deploys `site/` as a directory, so a page
built in CI would mean the deploy needed a build step; regenerating locally and
committing keeps the deploy a file copy. `--check` is what keeps that honest.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent
ROOT = SITE.parent

# Every repository URL on the site is built from this one constant.
REPO = "https://github.com/jatoran/swe-mux"
BLOB = f"{REPO}/blob/master"

# The project's only social account. It is a link out and nothing more: no
# embed, no widget, no script from their host (`README.md` section 6, the
# self-contained rule).
X_URL = "https://x.com/swemux"

# Several root documents still carry the `OWNER` placeholder this site used
# before the account was decided (`CHANGELOG.md`, `README.md`, `SECURITY.md`,
# `pyproject.toml`). Those are not this directory's to edit, and a page that
# lifted their links verbatim would publish dead ones - so any repository URL
# taken out of a source document is normalized on the way in. When those files
# are fixed the substitution simply stops matching; `main` asserts that no
# `OWNER` URL survives into a page either way.
_PLACEHOLDER_REPO = "https://github.com/OWNER/swe-mux"


def repo_url(url: str) -> str:
    """Point a lifted repository URL at the real account."""
    return url.replace(_PLACEHOLDER_REPO, REPO)


# Every `data-todo` value the site is allowed to carry. `tools/check.mjs` fails
# on any other, so an unfilled URL cannot arrive quietly. A placeholder standing
# in for something now known is just a dead link, so a value leaves this list the
# moment its URL is decided.
#
# It is empty, and the guard stays anyway - the same posture as the `/OWNER/`
# assertion in `main`. The last entry was `blog URL`, which left when the blog
# got a decided address (`/blog/`, in PAGES); the page itself is written by
# another branch and is tracked in `check.mjs`'s PENDING_PAGES rather than here,
# because "the URL is not decided" and "the file is not written yet" are
# different states and only the first one is a placeholder.
TODO_VALUES: tuple[str, ...] = ()

# Feature requests are GitHub Discussions in the `Ideas` category, and a
# thumbs-up reaction on one is a vote. `tools/ideas.py` reads them and writes
# `content/ideas.json`; this file is the only thing that renders it. That split
# is the point: the scheduled workflow owns the data and never the markup, so
# changing how the block reads is an ordinary site edit that goes through the
# ordinary gates, and a compromised or merely wrong workflow run cannot write
# HTML onto a published page.
IDEAS = f"{REPO}/discussions/categories/ideas"

# The floor a request clears to be drawn at all. An empty or one-vote "most
# requested" list on a young project reads worse than no list: it publishes that
# nobody is asking for anything. `ideas.py` filters to this number as well, so a
# sub-threshold request never reaches the committed data and cannot churn it,
# and this is the one definition of it.
MIN_VOTES = 3

# How many rows the block draws. The point is a ranked signal rather than an
# inventory, and the category itself is one click away and is the full list.
MAX_IDEAS = 8

# Replaced in `content/roadmap.html`. Below the vote floor the marker's own line
# is dropped rather than left blank, so the page is byte-identical to one that
# never carried the block.
MOST_REQUESTED = "<!--MOST-REQUESTED-->"


# --------------------------------------------------------------------- markdown

_DASHES = str.maketrans({"—": "-", "–": "-", "−": "-"})


def plain(text: str) -> str:
    """Normalize a string lifted from a source document into the site's voice.

    The site never uses an em dash (`README.md` section 5) but the documents this
    reads from are written under no such rule, so anything quoted out of them is
    converted rather than left to fail the gate.
    """
    return " ".join(text.translate(_DASHES).split())


def inline(md: str, base: str | None = None) -> str:
    """Inline Markdown -> HTML. Code spans, links, bold, emphasis. Nothing else.

    Code spans are lifted out first so their contents are never re-read as
    markup: a literal `**` inside a command is a command, not bold.

    `base` is the repository-relative directory the Markdown came from, and it is
    what makes a lifted link survive the move onto a website. A document that
    says `[RELEASING.md](RELEASING.md)` means a file in the repository; copied
    verbatim onto `/changelog/` it means a page that does not exist. With `base`
    set, every link that is not absolute and not a fragment is resolved against
    it and pointed at the repository.
    """
    spans: list[str] = []

    def stash(m: re.Match[str]) -> str:
        spans.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(spans) - 1}\x00"

    def link(m: re.Match[str]) -> str:
        text, href = m.group(1), m.group(2)
        if base is not None and not re.match(r"^([a-z]+:|#|//)", href):
            path = "/".join(p for p in f"{base}/{href}".split("/") if p and p != ".")
            while "/../" in f"/{path}":
                path = re.sub(r"[^/]+/\.\./", "", path, count=1)
            return f'<a href="{BLOB}/{html.escape(path, quote=True)}">{text}</a>'
        return f'<a href="{html.escape(repo_url(href), quote=True)}">{text}</a>'

    out = re.sub(r"`([^`]+)`", stash, plain(md))
    out = html.escape(out, quote=False)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], out)


@dataclass
class Block:
    """One parsed Markdown block: a heading, a bullet list, or a paragraph."""

    kind: str  # "h" | "ul" | "p" | "table"
    level: int = 0
    text: str = ""
    items: list[str] | None = None
    rows: list[list[str]] | None = None


def parse_markdown(md: str) -> list[Block]:
    """A deliberately small Markdown reader: headings, bullets, tables, paragraphs.

    It is strict about what it accepts and silently drops nothing - anything it
    does not recognize ends up in a paragraph, so a source document that grows a
    construct shows up on the page as prose rather than disappearing from it.
    """
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    blocks: list[Block] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if m := re.match(r"^(#{1,6}) (.+)$", line):
            blocks.append(Block("h", len(m.group(1)), m.group(2).strip()))
            i += 1
        elif line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(Block("table", rows=rows))
        elif line.startswith("- "):
            items: list[str] = []
            while i < len(lines) and (lines[i].startswith("- ") or lines[i].startswith("  ")):
                if lines[i].startswith("- "):
                    items.append(lines[i][2:].strip())
                elif items:
                    items[-1] += " " + lines[i].strip()
                i += 1
            blocks.append(Block("ul", items=items))
        else:
            para: list[str] = []
            while i < len(lines) and lines[i].strip() and not re.match(r"^[-#|]", lines[i]):
                para.append(lines[i].strip())
                i += 1
            blocks.append(Block("p", text=" ".join(para)))
    return blocks


def section(md: str, heading: str) -> str:
    """The body of one `## heading` from a Markdown document, without the heading.

    Raises rather than returning empty: every caller is quoting a section it
    believes exists, and a silently empty one would render as a heading with
    nothing under it on a published page.
    """
    m = re.search(
        rf"^#+ {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", md, re.M | re.S
    )
    if not m or not m.group(1).strip():
        raise SystemExit(f"section '{heading}' is missing or empty in its source document")
    return m.group(1)


# ------------------------------------------------------------------------ chrome

# The site's nav, in order, and the only nav there is: the bar's page list, the
# hamburger menu's page list, and `index.html`'s hand-written copy of both are
# all this. Real pages only - the landing page's eight in-page section anchors
# are gone, because an anchor is a position in a document rather than a
# destination and the two do not belong in one list.
#
# `blog` is here before `site/blog/` exists. The page is owned by another branch
# that lands beside this one; registering the route now is what stops the nav
# being edited twice. `tools/check.mjs` carries it in PENDING_PAGES, which is
# where that debt is recorded and where it is removed once the page arrives.
PAGES = [
    ("", "index.html", "swe-mux"),
    ("docs", "docs/index.html", "Docs"),
    ("blog", "blog/index.html", "Blog"),
    ("changelog", "changelog/index.html", "Changelog"),
    ("roadmap", "roadmap/index.html", "Roadmap"),
    ("acknowledgements", "acknowledgements/index.html", "Acknowledgements"),
]

BURGER = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 3.4h13V5h-13Zm0 '
    '3.8h13v1.6h-13Zm0 3.8h13v1.6h-13Z"/></svg>'
)

# The X mark, icon only. Inline like the GitHub mark, because nothing on this
# site loads from a third-party host and a social button is the classic way that
# rule gets broken.
X_MARK = (
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18.244 2.25h3.308l-7.227 8.26 '
    "8.502 11.24h-6.66l-5.214-6.817-5.966 6.817H1.68l7.73-8.835L1.254 2.25h6.826l4.713 "
    '6.231ZM17.083 19.77h1.833L7.084 4.126H5.117Z"/></svg>'
)

GITHUB_MARK = (
    '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 '
    "2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-."
    "23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-"
    ".52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 "
    "0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92."
    "08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-."
    "01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z\"/></svg>"
)
MOON = (
    '<svg class="moon" viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 10.2A6 6 0 0 1 5.8 '
    '2.5a6 6 0 1 0 7.7 7.7Z"/></svg>'
)
SUN = (
    '<svg class="sun" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4.6A3.4 3.4 0 1 0 8 '
    "11.4 3.4 3.4 0 0 0 8 4.6Zm0-4.6h0a.7.7 0 0 1 .7.7v1.3a.7.7 0 0 1-1.4 0V.7A.7.7 0 0 1 8 0Zm0 "
    "13.3a.7.7 0 0 1 .7.7v1.3a.7.7 0 0 1-1.4 0V14a.7.7 0 0 1 .7-.7ZM16 8a.7.7 0 0 1-.7.7H14a.7.7 0 "
    "0 1 0-1.4h1.3A.7.7 0 0 1 16 8ZM2.7 8a.7.7 0 0 1-.7.7H.7a.7.7 0 0 1 0-1.4H2a.7.7 0 0 1 .7.7Zm10"
    ".96-5.66a.7.7 0 0 1 0 1l-.94.94a.7.7 0 1 1-1-1l.94-.94a.7.7 0 0 1 1 0ZM4.28 11.72a.7.7 0 0 1 0"
    " 1l-.94.94a.7.7 0 1 1-1-1l.94-.94a.7.7 0 0 1 1 0Zm9.38 1.94a.7.7 0 0 1-1 0l-.94-.94a.7.7 0 1 1"
    ' 1-1l.94.94a.7.7 0 0 1 0 1ZM4.28 4.28a.7.7 0 0 1-1 0l-.94-.94a.7.7 0 0 1 1-1l.94.94a.7.7 0 0 1'
    ' 0 1Z"/></svg>'
)

# Everything the sub-pages need on top of `index.html`'s stylesheet. Tokens only,
# never a literal colour: a hard-coded rgba survives the theme switch and breaks
# in one direction only (`README.md` section 6).
PAGE_CSS = """
/* ------------------------------------------------------- generated sub-pages */
.page { padding: clamp(34px, 6vw, 64px) 0 0; }
.page h1 { font-size: clamp(21px, 3.6vw, 31px); letter-spacing: -0.02em; }
.page .kick { color: var(--fg-3); font-family: var(--mono); font-size: 12px;
              letter-spacing: 0.16em; text-transform: uppercase; margin-bottom: 16px; }
.page .lede { color: var(--fg-2); max-width: 74ch; margin-top: 16px;
              font-size: clamp(15px, 1.7vw, 16.5px); line-height: 1.66; }
.page .lede b { color: var(--fg); }
.page > .wrap > .note { max-width: 74ch; }

.bullets li { display: grid; grid-template-columns: 18px minmax(0, 1fr); gap: 9px;
              padding: 6px 0; color: var(--fg-2); font-size: 14.5px; line-height: 1.58; }
.bullets li::before { content: "\\25B8"; color: var(--green); }
.bullets b { color: var(--fg); }
.prose { color: var(--fg-2); max-width: 74ch; font-size: 14.5px; line-height: 1.62; }
.prose b { color: var(--fg); }

.rel { margin-top: clamp(40px, 6vw, 64px); }

/* The anti-roadmap is drawn as a panel rather than as a fourth run of bullets
   that happens to be last. It is the cheapest instrument this site has for
   answering a request that will never be built with a reason instead of with
   silence, and the roadmap page now invites requests, which makes that job much
   larger than it was. A panel is the whole treatment: no colour of its own, no
   warning register, and the same hairline the rest of the page uses. */
.boundary { border: 1px solid var(--line-2); background: var(--panel);
            padding: 2px clamp(14px, 3vw, 26px) clamp(16px, 2.4vw, 22px); }
.boundary .relhead { padding-top: clamp(14px, 2.4vw, 20px); }

/* Vote rows. A count is a number, so it takes the mono face and a fixed column;
   the row itself is a link rather than a description, which is what separates
   this list from the themed ones above it. `overflow-wrap` because a title is
   written by whoever opened the discussion and one unbroken token would push the
   page sideways at 360px. */
.votes li { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 3px 20px;
            padding: 11px 0; border-top: 1px solid var(--line); align-items: baseline; }
.votes li:first-child { border-top: 0; }
.votes .n { font-family: var(--mono); font-size: 12.5px; color: var(--fg-3);
            letter-spacing: 0.04em; white-space: nowrap; }
.votes .n b { color: var(--green); font-weight: 600; }
.votes .t { color: var(--fg-2); font-size: 14.5px; line-height: 1.58;
            overflow-wrap: anywhere; }
@media (max-width: 700px) { .votes li { grid-template-columns: minmax(0, 1fr); } }
.relhead { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
           padding-bottom: 9px; border-bottom: 1px solid var(--line-2); }
.relhead h2 { font-size: clamp(16px, 2.2vw, 20px); }
.relhead .when { color: var(--fg-3); font-family: var(--mono); font-size: 12px;
                 letter-spacing: 0.06em; }
.relhead .fill { flex: 1; }

/* Two label levels inside a release. The Keep a Changelog type carries the brand
   accent; the grouping under it is quieter, so a release reads as structure
   rather than as a run of identical headings. */
.changetype { color: var(--green); font-family: var(--mono); font-size: 11.5px;
              letter-spacing: 0.13em; text-transform: uppercase; margin: 26px 0 2px;
              padding-bottom: 7px; border-bottom: 1px solid var(--line-2); }
.subgroup { color: var(--fg-3); font-family: var(--mono); font-size: 11.5px;
            letter-spacing: 0.09em; margin: 18px 0 3px; }

/* Tables carry the same hairline grid as the panels; no zebra, no shadow. */
table { width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 13.5px; }
table th { text-align: left; font-family: var(--mono); font-weight: 600; font-size: 11px;
           letter-spacing: 0.12em; text-transform: uppercase; color: var(--fg-3);
           padding: 0 14px 8px 0; border-bottom: 1px solid var(--line-2); }
table td { padding: 7px 14px 7px 0; border-bottom: 1px solid var(--line);
           color: var(--fg-2); vertical-align: top; }
table td:first-child { font-family: var(--mono); color: var(--fg); font-size: 12.5px;
                       word-break: break-word; }
table td.v { font-family: var(--mono); font-size: 12.5px; white-space: nowrap; }
.tablewrap { overflow-x: auto; }

/* `.pagenav` used to be defined here, when it was what separated a sub-page's
   bar from the landing page's. Both bars are now the same component, so its
   rules live in `index.html`'s stylesheet with the rest of the chrome and reach
   every page through `index_style()`. Nothing about the bar is defined twice. */

/* `.rel:first-of-type` does not do this: `:first-of-type` counts elements of the
   same tag, and the first `div` in a page is the kick line. */
.lede + .rel, .lede + .toc + .rel { margin-top: clamp(24px, 4vw, 38px); }

.toc { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 18px;
       padding: 13px 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
       font-family: var(--mono); font-size: 12px; }
.docsec { margin-top: clamp(34px, 5vw, 52px); }
.doclist li { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 4px 26px;
              padding: 13px 0; border-top: 1px solid var(--line); }
.doclist li:first-child { border-top: 0; }
.doclist .t { font-family: var(--mono); font-size: 13.5px; font-weight: 600; color: var(--fg);
              letter-spacing: 0.01em; }
.doclist .t > a { color: inherit; }
.doclist .t > a:hover { color: var(--cyan); border-bottom-color: var(--cyan); }
.doclist span.d { color: var(--fg-2); font-size: 14.5px; line-height: 1.58; }
/* Inside `.t`, not a third grid child: as its own cell it lands on a second row
   whose top is set by the tallest summary, which floats it away from the title
   it names. */
.doclist .src { display: block; margin-top: 3px; font-weight: 400; font-size: 11.5px;
                color: var(--fg-3); overflow-wrap: anywhere; }
@media (max-width: 700px) { .doclist li { grid-template-columns: minmax(0, 1fr); } }
"""

THEME_INIT = """(function () {
  var t;
  try { t = localStorage.getItem('swemux-theme'); } catch (e) {}
  if (!t) t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  document.currentScript.previousElementSibling.setAttribute('content', t);
})();"""

THEME_TOGGLE = """(function () {
  var btn = document.getElementById('themebtn');
  function apply(t) {
    document.documentElement.setAttribute('data-theme', t);
    document.querySelector('meta[name=color-scheme]').setAttribute('content', t);
  }
  btn.addEventListener('click', function () {
    var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    apply(next);
    try { localStorage.setItem('swemux-theme', next); } catch (e) {}
  });
})();"""

FAVICON = (
    "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'>"
    "<path d='M2.6 2.3 L13.4 6.1 L13.4 9.9 L2.6 13.7 Z' fill='%238fdb6f'/></svg>"
)


def index_style() -> str:
    """The single `<style>` block out of `index.html`.

    Everything on the site renders from these bytes. Copying them into a template
    here would create a second design system that drifts one page at a time, and
    `tools/contrast.py` would go on auditing only the first.
    """
    text = (SITE / "index.html").read_text(encoding="utf-8")
    m = re.search(r"<style>\n(.*?)\n</style>", text, re.S)
    if not m:
        raise SystemExit("site/index.html has no <style> block to inherit from")
    return m.group(1)


def index_block(name: str) -> str:
    """A marked run of inline script out of `index.html`.

    Same rule as `index_style`, applied to behaviour rather than to the
    stylesheet: the header is the one component that must be identical on every
    page, and a second copy of its script here is a copy that drifts. The
    landing page is the one that has to carry the source anyway, because it is
    hand-authored and has no build step, so it is the source for both.
    """
    text = (SITE / "index.html").read_text(encoding="utf-8")
    m = re.search(rf"/\* >>> {name}:.*?\*/\n(.*?)\n/\* <<< {name} \*/", text, re.S)
    if not m:
        raise SystemExit(
            f"site/index.html has no '>>> {name}' ... '<<< {name}' block to inherit from"
        )
    return m.group(1)


def shell(slug: str, title: str, description: str, body: str) -> str:
    """The chrome every generated page shares: head, top bar, footer, scripts."""
    up = "../"  # every generated page lives one directory below the deploy root
    items = [(up, "home", False)] + [
        (f"{up}{s}/", label.lower(), s == slug) for s, _, label in PAGES if s
    ]

    def links(indent: str) -> str:
        return "\n".join(
            f'{indent}<a href="{href}"'
            f'{" aria-current=\"page\"" if current else ""}>{text}</a>'
            for href, text, current in items
        )

    nav = links("      ")
    # The menu carries every page the bar carries, plus the two links the bar
    # keeps at narrow widths, so nothing is reachable only by remembering that
    # it is there. `install` is the one fragment link in the whole chrome and is
    # a call to action rather than a nav entry; `tools/check.mjs` asserts that it
    # is the only one, because the section anchors this nav used to hold are
    # exactly what must not come back.
    menu = links("        ") + (
        f'\n        <a class="sep" href="{up}#install">install</a>'
        f'\n        <a href="{REPO}">github</a>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}" />
<meta name="color-scheme" content="dark" />
<script>
/* Runs before first paint so the chosen scheme never flashes. */
{THEME_INIT}
</script>
<link rel="icon" href="{FAVICON}" />
<style>
{index_style()}
{PAGE_CSS.strip()}
</style>
</head>
<body>

<div class="bar">
  <div class="wrap">
    <a class="brand" href="{up}" aria-label="swe-mux, home">
      <img class="dark" src="{up}img/logo.png" width="640" height="73" alt="" />
      <img class="lite" src="{up}img/logo-light.png" width="640" height="73" alt="" />
    </a>
    <nav class="secnav pagenav" aria-label="Pages">
{nav}
    </nav>
    <div class="util">
      <a class="cta" href="{up}#install">install</a>
      <a href="{REPO}" aria-label="Source on GitHub" title="Source on GitHub">
        {GITHUB_MARK}
        <span class="lbl">github</span>
      </a>
      <button type="button" id="themebtn" aria-label="Toggle colour scheme" title="Toggle colour scheme">
        {MOON}
        {SUN}
      </button>
      <button type="button" id="menubtn" class="burger" aria-label="Menu" aria-expanded="false" aria-controls="menu" title="Menu">
        {BURGER}
      </button>
    </div>
  </div>
  <div class="menu" id="menu" hidden>
    <div class="wrap">
      <nav aria-label="Menu">
{menu}
      </nav>
    </div>
  </div>
</div>

<main>
{body}
</main>

<footer>
  <div class="wrap">
    <div class="cols">
      <div>
        <img class="brandmark dark" src="{up}img/logo.png" width="640" height="73" alt="swe-mux" />
        <img class="brandmark lite" src="{up}img/logo-light.png" width="640" height="73" alt="swe-mux" />
        <div>Agentic development environment and agent control plane.</div>
        <div>Windows-first. Local-only. No account.</div>
      </div>
      <div>
        <h4>pages</h4>
        <div><a href="{up}">home</a> &middot; <a href="{up}docs/">docs</a>
        &middot; <a href="{up}blog/">blog</a></div>
        <div><a href="{up}changelog/">changelog</a> &middot; <a href="{up}roadmap/">roadmap</a>
        &middot; <a href="{up}acknowledgements/">acknowledgements</a></div>
      </div>
      <div>
        <h4>source</h4>
        <div><a href="{REPO}">github.com/jatoran/swe-mux</a></div>
        <div><a href="{IDEAS}">request a feature</a> &middot;
        <a href="{REPO}/issues">report a bug</a></div>
        <div><code>.docs/design/00_OVERVIEW.md</code></div>
      </div>
      <div>
        <h4>legal</h4>
        <div><a href="{BLOB}/LICENSE">Apache-2.0</a></div>
        <div><a href="{BLOB}/THIRD-PARTY-NOTICES.md">Third-party notices</a></div>
        <div><a href="{up}privacy/">Privacy</a> &middot; <a href="{up}terms/">Terms</a></div>
        <div class="social">
          <a href="{X_URL}" rel="me" aria-label="swe-mux on X" title="swe-mux on X">
            {X_MARK}
          </a>
        </div>
      </div>
    </div>
    <p class="colophon"><span class="h">#</span> This page is generated by
    <code>site/tools/build.py</code>. Edit its source, not the HTML.</p>
    <p class="colophon"><span class="h">#</span> swe-mux is not affiliated with, endorsed
    by, or sponsored by Anthropic, OpenAI, or any other vendor whose CLI it launches.
    Product names and trademarks belong to their respective owners. You run those tools
    under your own account and your own agreement with each vendor.</p>
  </div>
</footer>

<script>
{THEME_TOGGLE}

/* ------------------------------------------------------------------- menu */
{index_block("menu")}
</script>

</body>
</html>
"""


# --------------------------------------------------------------------- changelog


def build_changelog() -> str:
    md = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    links = dict(re.findall(r"^\[([^\]]+)\]:\s*(\S+)\s*$", md, re.M))
    body_md = re.sub(r"^\[[^\]]+\]:\s*\S+\s*$", "", md, flags=re.M)
    blocks = parse_markdown(body_md)

    first = _first_release(blocks)
    preamble = [b for b in blocks[1:first] if b.kind == "p"]
    out: list[str] = []
    out.append('<section class="page">\n  <div class="wrap">')
    out.append('    <div class="kick">what changed, and when</div>')
    out.append("    <h1>Changelog</h1>")
    out.append('    <div class="lede">')
    for b in preamble:
        out.append(f'      <p>{inline(b.text, base="")}</p>')
    out.append("    </div>")

    i = first
    while i < len(blocks):
        b = blocks[i]
        if b.kind == "h" and b.level == 2:
            name, when = _release_heading(b.text)
            href = links.get(name)
            shown = (
                f'<a href="{html.escape(repo_url(href))}">{html.escape(name)}</a>'
                if href
                else html.escape(name)
            )
            out.append('    <div class="rel">')
            out.append('      <div class="relhead">')
            out.append(f"        <h2>{shown}</h2>")
            if when:
                out.append(f'        <span class="when">{html.escape(when, quote=False)}</span>')
            out.append('        <span class="fill"></span>')
            out.append("      </div>")
            i += 1
            body, i = _release_body(blocks, i)
            out.extend(body)
            out.append("    </div>")
        else:
            i += 1
    out.append("  </div>\n</section>")
    return "\n".join(out)


def _first_release(blocks: list[Block]) -> int:
    for n, b in enumerate(blocks):
        if b.kind == "h" and b.level == 2:
            return n
    raise SystemExit("CHANGELOG.md has no release headings")


def _release_heading(text: str) -> tuple[str, str]:
    m = re.match(r"^\[([^\]]+)\]\s*(?:-\s*(.+))?$", plain(text))
    if not m:
        return plain(text), ""
    return m.group(1), (m.group(2) or "").strip()


def _release_body(blocks: list[Block], i: int) -> tuple[list[str], int]:
    out: list[str] = []
    empty = True
    while i < len(blocks) and not (blocks[i].kind == "h" and blocks[i].level == 2):
        b = blocks[i]
        if b.kind == "h":
            # `###` is the Keep a Changelog type (Added, Fixed, ...) and `####` is
            # this project's grouping inside it. They are drawn differently on
            # purpose: rendered identically, a release reads as a flat run of
            # labels with no way to see where "Added" ends.
            cls = "changetype" if b.level <= 3 else "subgroup"
            out.append(f'      <div class="{cls}">{inline(b.text, base="")}</div>')
            empty = False
        elif b.kind == "p":
            out.append(f'      <p class="prose">{inline(b.text, base="")}</p>')
            empty = False
        elif b.kind == "ul" and b.items:
            out.append('      <ul class="bullets">')
            for item in b.items:
                out.append(f'        <li><span>{inline(item, base="")}</span></li>')
            out.append("      </ul>")
            empty = False
        i += 1
    if empty:
        # Keep a Changelog keeps an `Unreleased` heading standing even when it is
        # empty. A heading with nothing under it reads as a rendering bug, so the
        # emptiness is stated instead of shown.
        out.append('      <p class="prose">Nothing yet.</p>')
    return out, i


# ---------------------------------------------------------------------- the docs

# One entry per document, and every document under `.docs/design/features/` is
# either here or in OMITTED with a reason. `build` fails on a document that is in
# neither, so a new feature doc is a decision about whether it is public rather
# than something that silently never appears.
DOC_SECTIONS: list[tuple[str, str, list[str]]] = [
    (
        "overview",
        "Overview",
        ["architecture.md", "data-model.md", "interfaces.md"],
    ),
    (
        "sessions",
        "Sessions and terminals",
        [
            "features/sessions.md",
            "features/session-recovery.md",
            "features/terminal-input.md",
            "features/launch-profiles.md",
            "features/backends.md",
            "features/status-detection.md",
            "features/delivery-readiness.md",
            "features/approvals.md",
        ],
    ),
    (
        "workbench",
        "Projects and the workbench",
        [
            "features/projects.md",
            "features/project-resources.md",
            "features/project-actions.md",
            "features/project-card.md",
            "features/workspace-layout.md",
            "features/ui.md",
            "features/prompt-library.md",
            "features/processes-and-previews.md",
            "features/git.md",
            "features/agent-context.md",
            "features/agent-environment.md",
            "features/configurator.md",
        ],
    ),
    (
        "history",
        "History and transcripts",
        ["features/history.md", "features/transcript-branches.md"],
    ),
    (
        "control-plane",
        "The control plane",
        [
            "features/tier0-facts.md",
            "features/deterministic-consumers.md",
            "features/code-graph.md",
            "features/attention-ranking.md",
            "features/fleet-intelligence.md",
            "features/scan-timeline.md",
            "features/operational-telemetry.md",
            "features/automation.md",
            "features/automation-enablement.md",
        ],
    ),
    (
        "queues",
        "Queues, messaging, and landing",
        [
            "features/prompt-queue.md",
            "features/auto-delivery.md",
            "features/agent-messaging.md",
            "features/land-queue.md",
            "features/scheduled-runs.md",
            "features/mux-mcp.md",
        ],
    ),
    (
        "voice",
        "Voice, assistant, and alerts",
        [
            "features/voice.md",
            "features/assistant.md",
            "features/notifications.md",
            "features/device-presence.md",
        ],
    ),
    (
        "accounts",
        "Accounts, usage, and budgets",
        ["features/provider-accounts.md", "features/usage.md", "features/budgets.md"],
    ),
    (
        "running",
        "Running it",
        ["features/remote-access.md", "features/desktop-shell.md"],
    ),
    (
        "reference",
        "Technical reference",
        [
            "../technical/00_INDEX.md",
            "../technical/backend/packages.md",
            "../technical/backend/sqlite.md",
            "../technical/frontend/packages.md",
            "../technical/frontend/workspace-state.md",
        ],
    ),
]

OMITTED = {
    "features/meta-hooks.md": "a compatibility engine for a retired hook contract, not a feature",
    "features/observations.md": "storage compatibility for a user surface that was retired",
    "features/ghost-windows.md": "an internal remediation sweep with no user-facing control",
    "features/setting-links.md": "the internal mechanism behind a switched-off surface",
}


def doc_anchor(repo_rel: str) -> str:
    """The stable fragment a document is reachable at: `/docs/#<anchor>`.

    Derived from the path rather than the title, because a title is edited and a
    path is moved deliberately, and because two documents genuinely share a
    filename (`backend/packages.md`, `frontend/packages.md`) - a stem-only anchor
    silently emits the same `id` twice and the second one is unreachable.
    `.docs/design/features/` collapses away, since it is where most of these live
    and the shorter fragment is the one an in-app link will carry.
    """
    rel = repo_rel.removeprefix(".docs/").removesuffix(".md")
    rel = rel.removeprefix("design/features/").removeprefix("design/")
    rel = re.sub(r"(^|/)\d+_", r"\1", rel)
    return re.sub(r"[^a-z0-9]+", "-", rel.lower()).strip("-")


def doc_label(repo_rel: str) -> str:
    """The path a docs entry shows, shortened by the two prefixes every row shares.

    The link carries the full path; this is what a reader scans, and forty rows
    all beginning `.docs/design/features/` spend a third of the column saying the
    same thing and then break mid-filename because nothing is left. The two
    prefixes dropped are stated on the page.
    """
    return repo_rel.removeprefix(".docs/").removeprefix("design/features/")


def _lead_paragraph(text: str) -> str:
    """The prose paragraph sitting directly under a document's H1, if it has one.

    Walked rather than matched with a regex. The obvious `^# .+\\n+(.+?)` under
    `re.DOTALL` does not do this: the first `.+` swallows the whole file and
    backtracks into some heading in the middle of it, which produced summaries
    reading "## Failure modes" before anybody looked at the output.
    """
    lines = text.split("\n")
    if not lines or not lines[0].startswith("# "):
        return ""
    i = 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines) or lines[i][:1] in {"#", "-", "|", ">"}:
        return ""
    para: list[str] = []
    while i < len(lines) and lines[i].strip() and lines[i][:1] not in {"#", "|"}:
        para.append(lines[i].strip())
        i += 1
    return plain(" ".join(para))


def _doc_summary(text: str, path: str, title: str) -> str:
    """One line describing a document, taken from the document. May be empty.

    Preference order, and the reason for it. A doc's own `## What it is` opening
    sentence is written by whoever owns the doc and moves when the doc moves, so
    it goes first; a lead paragraph sitting directly under the H1 is the same
    thing by another convention and goes second. `00_OVERVIEW.md`'s map label is
    third because it is a *map* label: written to sit beside a path, it is often
    just the title again, and rendering "Architecture" under a heading that reads
    "Architecture" is worse than rendering nothing.

    Which is what a document with none of the three gets. The alternative is
    writing a summary here, and a hand-written line beside forty generated ones
    is the one that goes stale without anybody noticing.
    """
    if m := re.search(r"^## What it is\s*\n+(.+?)(?=\n\n|\n#)", text, re.M | re.S):
        lead = plain(m.group(1)).lstrip("- ")
        return re.split(r"(?<=[.])\s", lead)[0]
    if lead := _lead_paragraph(text):
        return lead
    overview = (ROOT / ".docs/design/00_OVERVIEW.md").read_text(encoding="utf-8")
    for label, mapped in re.findall(r"^- (.+?): `([^`]+)`$", overview, re.M):
        if mapped == path and plain(label).lower() != plain(title).lower():
            return plain(label) + "."
    return ""


def build_docs() -> str:
    base = ROOT / ".docs/design"
    listed = [p for _, _, paths in DOC_SECTIONS for p in paths]
    if len(listed) != len(set(listed)):
        raise SystemExit("a document is listed in two docs sections")

    on_disk = {f"features/{p.name}" for p in (base / "features").glob("*.md")}
    unclassified = sorted(on_disk - set(listed) - set(OMITTED))
    if unclassified:
        raise SystemExit(
            "these feature docs are neither published nor omitted, so the docs page "
            "would silently not mention them: " + ", ".join(unclassified)
        )
    stale = sorted(set(OMITTED) - on_disk)
    if stale:
        raise SystemExit("OMITTED names documents that no longer exist: " + ", ".join(stale))

    out: list[str] = []
    out.append('<section class="page">\n  <div class="wrap">')
    out.append('    <div class="kick">how it works, in its own words</div>')
    out.append("    <h1>Documentation</h1>")
    out.append(
        '    <div class="lede"><p>swe-mux is documented in the repository, beside the code, '
        "and this index is generated from those documents rather than rewritten beside them. "
        "Each entry links to the document itself; the summary is the document's own opening "
        "line. <b>These are design documents.</b> They state what a subsystem is, the "
        "invariants it holds, and the decisions it has already made, which is what you want "
        "when a behaviour surprises you and not what you want on your first ten minutes - "
        "for that, the in-app tour and <code>mux doctor</code> come first.</p>"
        "<p>Paths under each title are relative to <code>.docs/</code>, and a bare filename "
        "means <code>.docs/design/features/</code>. The link is the whole path.</p></div>"
    )

    out.append('    <nav class="toc" aria-label="Documentation sections">')
    for slug, title, _ in DOC_SECTIONS:
        out.append(f'      <a href="#sec-{slug}">{html.escape(title.lower(), quote=False)}</a>')
    out.append("    </nav>")

    seen: set[str] = set()
    for slug, title, paths in DOC_SECTIONS:
        out.append(f'    <div class="docsec" id="sec-{slug}">')
        out.append(
            f'      <div class="head"><span class="n">#</span>'
            f'<h2>{html.escape(title, quote=False)}</h2><span class="fill"></span></div>'
        )
        out.append('      <ul class="doclist">')
        for rel in paths:
            src = (base / rel).resolve()
            text = src.read_text(encoding="utf-8")
            h1 = re.search(r"^# (.+)$", text, re.M)
            if not h1:
                raise SystemExit(f"{rel} has no H1 for the docs page to title it with")
            repo_rel = src.relative_to(ROOT).as_posix()
            anchor = doc_anchor(repo_rel)
            if anchor in seen or anchor == f"sec-{slug}":
                raise SystemExit(f"docs anchor '{anchor}' is not unique; see doc_anchor()")
            seen.add(anchor)
            title_html = inline(h1.group(1))
            summary = _doc_summary(text, rel, h1.group(1))
            src_dir = repo_rel.rsplit("/", 1)[0]
            out.append(f'        <li id="{anchor}">')
            out.append(
                f'          <div class="t"><a href="{BLOB}/{repo_rel}">'
                f"{title_html}</a>"
                f'<span class="src">{html.escape(doc_label(repo_rel), quote=False)}</span></div>'
            )
            # Always emitted, even empty: `.doclist li` is a two-column grid and a
            # missing cell moves the next entry's title into the summary column.
            out.append(f'          <span class="d">{inline(summary, base=src_dir)}</span>')
            out.append("        </li>")
        out.append("      </ul>")
        out.append("    </div>")

    left_out = "; ".join(
        f"<code>{html.escape(p, quote=False)}</code>, {html.escape(plain(reason), quote=False)}"
        for p, reason in OMITTED.items()
    )
    out.append(
        '    <p class="note">Not everything under <code>.docs/</code> is listed here. '
        f"Left out, each with its reason: {left_out}. "
        "Development notes, audits, and the internal plan are unlisted too; the "
        '<a href="../roadmap/">roadmap page</a> is the public projection of the last of '
        "those.</p>"
    )
    out.append("  </div>\n</section>")
    return "\n".join(out)


# ---------------------------------------------------------- acknowledgements

ECOSYSTEM_LABEL = {"python": "Python", "npm": "Frontend"}


def build_acknowledgements() -> str:
    notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text(encoding="utf-8")
    sidecar = json.loads((ROOT / "packaging/third_party_licenses.json").read_text(encoding="utf-8"))
    packages = sidecar["packages"]
    prose = (SITE / "content/acknowledgements-prose.html").read_text(encoding="utf-8")

    out: list[str] = []
    out.append('<section class="page">\n  <div class="wrap">')
    out.append('    <div class="kick">what this is built on</div>')
    out.append("    <h1>Acknowledgements</h1>")
    out.append(
        '    <div class="lede"><p>swe-mux runs coding agents somebody else wrote, in terminals '
        "somebody else's emulator draws, over a network somebody else's mesh built. "
        "<b>The inventory below is generated from the same files that produce "
        "<code>THIRD-PARTY-NOTICES.md</code></b>, so it cannot drift from what actually ships; "
        "the prose above it is hand-written and names only projects whose influence is visible "
        "in the repository.</p></div>"
    )

    out.append(prose.strip())

    # ------------------------------------------------ curated sections, extracted
    out.append('    <div class="rel">')
    out.append('      <div class="relhead"><h2>What ships alongside it</h2>'
               '<span class="fill"></span></div>')
    out.append(
        '      <p class="prose">Three facts about the shipped artifact that a license list '
        "alone does not carry. Each is lifted from "
        f'<a href="{BLOB}/THIRD-PARTY-NOTICES.md">'
        "<code>THIRD-PARTY-NOTICES.md</code></a>, which is itself generated from the resolved "
        "dependency closure and checked against the built desktop bundle.</p>"
    )
    for heading in (
        "Modified redistributions",
        "Binary redistributions without an OSI license",
    ):
        out.append(f'      <div class="grouplabel">{html.escape(heading, quote=False)}</div>')
        for block in parse_markdown(section(notices, heading)):
            if block.kind == "ul" and block.items:
                out.append('      <ul class="bullets">')
                for item in block.items:
                    out.append(f'        <li><span>{inline(item, base="")}</span></li>')
                out.append("      </ul>")

    out.append('      <div class="grouplabel">Weak copyleft, and how to replace it</div>')
    out.append('      <ul class="bullets">')
    copyleft = section(notices, "Copyleft components and how to replace them")
    # Each library is a `### <name> <version> - <license>` heading followed by one
    # paragraph of why it is here. Split on the headings rather than matching
    # across them: an unanchored `.+?` under `re.S` runs to the end of the file,
    # which is exactly what it did the first time this was written.
    for chunk in re.split(r"^### ", copyleft, flags=re.M)[1:]:
        head, _, rest = chunk.partition("\n")
        name, _, spdx = head.partition(" - ")
        first = next((p for p in rest.split("\n\n") if p.strip()), "")
        out.append(
            f'        <li><span><b>{inline(name.strip())}</b> '
            f"({inline(spdx.strip())}). {inline(plain(first), base='')}</span></li>"
        )
    out.append("      </ul>")
    out.append(
        '      <p class="note">Both ship as plain, replaceable source inside the desktop '
        "bundle rather than compiled into its archive, which is the LGPL relink condition. "
        "No GPL or AGPL code is redistributed, and none resolves into the dependency "
        "closure.</p>"
    )
    out.append("    </div>")

    # ------------------------------------------------------------------- models
    out.append('    <div class="rel">')
    out.append('      <div class="relhead"><h2>Models</h2><span class="fill"></span></div>')
    out.append(
        '      <p class="prose">Downloaded on demand into the data directory and never '
        "bundled. Each is pinned by immutable revision and verified per file by SHA-256 "
        "before it loads.</p>"
    )
    for block in parse_markdown(section(notices, "Models")):
        if block.kind == "table" and block.rows:
            out.extend(_table(block.rows))
    out.append("    </div>")

    # -------------------------------------------------------- the full inventory
    out.append('    <div class="rel">')
    out.append('      <div class="relhead"><h2>The dependency closure</h2>'
               '<span class="fill"></span></div>')
    out.append(
        '      <p class="prose">Every package swe-mux resolves and redistributes, with the '
        "license each one declares. Generated from "
        "<code>packaging/third_party_licenses.json</code>, which "
        "<code>packaging/license_audit.py</code> writes from the two lockfiles and the test "
        "suite reconciles against them. The declared license is what the package says about "
        "itself; where that has been measured to disagree with what the package actually "
        "ships, the notices file records the disagreement rather than this table.</p>"
    )
    for ecosystem in ("python", "npm"):
        rows = sorted(
            (p for p in packages if p["ecosystem"] == ecosystem),
            key=lambda p: p["name"].lower(),
        )
        out.append(
            f'      <div class="grouplabel">{ECOSYSTEM_LABEL[ecosystem]} '
            f"<span>{len(rows)} packages</span></div>"
        )
        out.extend(
            _table(
                [["Package", "Version", "License"]]
                + [[p["name"], p["version"], p["license"]] for p in rows],
                numeric={1},
            )
        )
    out.append("    </div>")
    out.append("  </div>\n</section>")
    return "\n".join(out)


def _table(rows: list[list[str]], numeric: set[int] | None = None) -> list[str]:
    numeric = numeric or set()
    out = ['      <div class="tablewrap">', "      <table>"]
    head, *body = rows
    cells = "".join(f"<th>{inline(c)}</th>" for c in head)
    out.append(f"        <thead><tr>{cells}</tr></thead>")
    out.append("        <tbody>")
    for row in body:
        cells = "".join(
            f'<td class="v">{inline(c)}</td>' if n in numeric else f"<td>{inline(c)}</td>"
            for n, c in enumerate(row)
        )
        out.append(f"        <tr>{cells}</tr>")
    out.append("        </tbody>")
    out.append("      </table>")
    out.append("      </div>")
    return out


# ------------------------------------------------------------------------ roadmap


def read_ideas() -> list[dict[str, object]]:
    """The vote-ranked requests `tools/ideas.py` last wrote. May be empty.

    A missing file is not an error: Discussions may not be enabled on the
    repository yet, and a fresh clone has no reason to carry a fetched artifact.
    A malformed one is, because the alternative is a section that quietly stops
    being drawn, which looks exactly like nobody asking for anything.

    The vote floor is applied here as well as in `ideas.py`. Reading a number out
    of a file and rendering it is not the same as deciding it: this is the copy
    that governs the page, so a hand-edited or stale `ideas.json` cannot put a
    one-vote request in front of a reader.
    """
    path = SITE / "content/ideas.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not isinstance(items, list):
        raise SystemExit("content/ideas.json: 'items' is not a list")
    for item in items:
        if not isinstance(item, dict) or not {"title", "url", "votes"} <= set(item):
            raise SystemExit(f"content/ideas.json: an item is missing a field: {item!r}")
    ranked = sorted(
        (i for i in items if int(i["votes"]) >= MIN_VOTES),
        key=lambda i: (-int(i["votes"]), str(i["title"]).lower()),
    )
    return ranked[:MAX_IDEAS]


def build_most_requested() -> str:
    """The vote-ranked block, or an empty string when nothing clears the floor.

    Titles are written by whoever opened the discussion, so they go through
    `plain()` before `html.escape()`: the site's no-em-dash rule is asserted over
    the finished page, and a request titled with one would otherwise fail a build
    nobody in this repository triggered.
    """
    items = read_ideas()
    if not items:
        return ""
    rows = "\n".join(
        '        <li><span class="n"><b>{votes}</b> {noun}</span>'
        '<span class="t"><a href="{url}">{title}</a></span></li>'.format(
            votes=int(i["votes"]),
            noun="vote" if int(i["votes"]) == 1 else "votes",
            url=html.escape(str(i["url"]), quote=True),
            title=html.escape(plain(str(i["title"])), quote=False),
        )
        for i in items
    )
    return f"""<div class="rel" id="most-requested">
      <div class="relhead"><h2>Most requested</h2><span class="fill"></span></div>
      <p class="prose">The open <a href="{IDEAS}">Ideas</a> discussions carrying the most
      thumbs-up, read from the GitHub API once a day. <b>A vote is a signal, not a
      commitment.</b> Nothing is scheduled by appearing here, and a request can sit at the
      top of this list and still be one of the things
      <a href="#not-planned">deliberately not on the roadmap</a>.</p>
      <ul class="votes">
{rows}
      </ul>
      <p class="note">Ranked by reaction count and nothing else: not curated, not
      reordered, and nothing left off it. Below {MIN_VOTES} votes a request is not drawn
      here at all, which is why this list is short rather than empty.</p>
    </div>"""


def build_roadmap() -> str:
    text = (SITE / "content/roadmap.html").read_text(encoding="utf-8").strip()
    # Exactly one, not at least one: the substitution below is a plain replace,
    # so a second copy - in that file's own explanatory header comment, most
    # obviously - would draw the block twice, once of it inside a comment where
    # nobody would ever see it was wrong.
    found = text.count(MOST_REQUESTED)
    if found != 1:
        raise SystemExit(
            f"content/roadmap.html carries the {MOST_REQUESTED} marker {found} times; "
            "it needs exactly one, marking where the vote-ranked block goes"
        )
    block = build_most_requested()
    if not block:
        # The blank line after it goes too: leaving one behind would put two
        # blank lines where every other section boundary has one, and the diff
        # between a page with the block and a page without it would carry a
        # whitespace change nobody wrote.
        return re.sub(rf"^[ \t]*{re.escape(MOST_REQUESTED)}\n\n?", "", text, flags=re.M)
    return text.replace(MOST_REQUESTED, block)


# --------------------------------------------------------------------------- main

BUILDERS = {
    "docs": (
        build_docs,
        "Docs · swe-mux",
        "Index of swe-mux's design documentation: sessions and terminals, the workbench, "
        "the control plane, queues and landing, voice, and the technical reference.",
    ),
    "changelog": (
        build_changelog,
        "Changelog · swe-mux",
        "Every released change to swe-mux, in Keep a Changelog format, generated from the "
        "repository's own CHANGELOG.md.",
    ),
    "roadmap": (
        build_roadmap,
        "Roadmap · swe-mux",
        "What swe-mux is building next, what it deliberately will not build, and how to ask "
        "for something. No dates.",
    ),
    "acknowledgements": (
        build_acknowledgements,
        "Acknowledgements · swe-mux",
        "The projects swe-mux is built on, and the full dependency closure it redistributes, "
        "generated from the same files that produce THIRD-PARTY-NOTICES.md.",
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if any page on disk is stale")
    args = ap.parse_args()

    stale: list[str] = []
    for slug, (builder, title, description) in BUILDERS.items():
        page = shell(slug, title, description, builder())
        if "—" in page or "–" in page:
            raise SystemExit(f"{slug}: an em dash reached the output; see README.md section 5")
        for value in set(re.findall(r'data-todo="([^"]*)"', page)) - set(TODO_VALUES):
            raise SystemExit(
                f'{slug}: data-todo="{value}" is not in TODO_VALUES. Every unfilled URL on '
                "the site is a documented placeholder (README.md section 11)."
            )
        if "/OWNER/" in page:
            raise SystemExit(
                f"{slug}: a github.com/OWNER/ URL reached the output. The account is "
                "decided; see repo_url() for the source documents that still say OWNER."
            )
        target = SITE / slug / "index.html"
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == page:
            print(f"  {slug}/index.html  up to date")
            continue
        if args.check:
            stale.append(f"{slug}/index.html")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" so the LF this builds stays LF: Python's text mode would
        # translate it to CRLF on Windows and every regenerate would read as a
        # whole-file diff.
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(page)
        print(f"  {slug}/index.html  written ({len(page.splitlines())} lines)")

    if stale:
        print("\nSTALE: " + ", ".join(stale))
        print("Their sources changed. Run: python site/tools/build.py")
        return 1
    print("\nall pages current" if args.check else "\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
