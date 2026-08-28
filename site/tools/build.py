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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import docs_content  # noqa: E402  (the documentation prose; see the docs section)

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

# The same device on two more hand-authored pages: the prose is the file, the
# generated part is one marker inside it. `content/compare.html` owns the framing
# and the caveats while `content/compare.json` owns every cell; `content/blog.html`
# owns the index copy while `content/blog/` owns the posts.
COMPARISON_TABLE = "<!--COMPARISON-TABLE-->"
POSTS = "<!--POSTS-->"

# The agent-onboarding line, and the file it points at. Kept here as one constant
# because it is quoted on `/docs/` and the guide it names is a *served* path
# (`site/` is the deploy root), so the two go stale together or not at all.
AGENT_GUIDE_PATH = "agent-guide.md"
AGENT_GUIDE_URL = f"https://swemux.dev/{AGENT_GUIDE_PATH}"
AGENT_PROMPT = (
    "Help me understand and set up swe-mux. "
    f"Read {AGENT_GUIDE_URL} first, then walk me through it step by step."
)


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
.doclist span.d { color: var(--fg-2); font-size: 14.5px; line-height: 1.58; max-width: 72ch; }
/* Inside `.t`, not a third grid child: as its own cell it lands on a second row
   whose top is set by the tallest summary, which floats it away from the title
   it names. */
.doclist .src { display: block; margin-top: 3px; font-weight: 400; font-size: 11.5px;
                color: var(--fg-3); overflow-wrap: anywhere; }
@media (max-width: 700px) { .doclist li { grid-template-columns: minmax(0, 1fr); } }
/* The same "where this lives" line under a feature-guide row, where the label
   column is the feature's name rather than its path. */
.flat .src { display: block; margin-top: 4px; font-size: 11.5px; color: var(--fg-3); }
.flat .src a { color: var(--fg-3); }
/* `.flat b` is the *label* column: monospace, and carrying the green marker. A
   `<b>` inside the description column inherited both, so an emphasised clause in
   the middle of a sentence rendered as monospace with a stray bullet in front of
   it. The landing page never hit this because its descriptions carry no bold;
   the feature guide's do, and emphasis inside prose is the reason it is prose. */
.flat span b { font-family: var(--sans); font-size: inherit; letter-spacing: normal; }
.flat span b::before { content: none; }

/* ------------------------------------------------------------- the changelog

   A release entry is compact by default and complete on demand. The full 0.1.0
   entry is sixty bullets, which nobody scans; the group labels are what a person
   actually wants off a changelog ("did anything change in Git?"), so those are
   drawn flat and the bullets live behind one disclosure per release.

   `<details>` rather than a script, because these pages carry no JavaScript of
   their own beyond the theme toggle, and a disclosure that needs one would be a
   feature that is missing with JS off rather than merely unstyled. */
.relgroups { margin-top: 13px; color: var(--fg-2); font-size: 14px; line-height: 1.66; }
.relgroups .k { color: var(--fg); font-family: var(--mono); font-size: 11.5px;
                letter-spacing: 0.11em; text-transform: uppercase; }
.relgroups .sep { color: var(--fg-3); padding: 0 2px; }
.relhead .relnotes { font-family: var(--mono); font-size: 12px; letter-spacing: 0.05em; }
.relmore { margin-top: 16px; border-top: 1px solid var(--line); }
.relmore > summary { list-style: none; cursor: pointer; padding: 11px 0 0;
                     font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.11em;
                     text-transform: uppercase; color: var(--fg-3); }
.relmore > summary::-webkit-details-marker { display: none; }
.relmore > summary::before { content: "\\25B8"; color: var(--green); display: inline-block;
                             width: 15px; transition: transform 0.12s linear; }
.relmore[open] > summary::before { transform: rotate(90deg); }
.relmore > summary:hover { color: var(--fg); }
.relmore .changetype:first-of-type { margin-top: 18px; }

/* ------------------------------------------------------------ the comparison

   Two renderings of the same data, and the split is deliberate. The matrix is
   markers only, because seven columns of prose at 360px is seven columns of one
   word per line; the sentences and their sources live below it, one block per
   row, in a grid that collapses to a single column like every other list here.

   `min-width` on the matrix is load-bearing and not a guess. `table` here is
   `width: 100%`, which means a narrow viewport does not overflow the wrapper: it
   *squeezes*, and at 390px the label column reached one character per line and
   the thirteen rows became six thousand pixels tall. A minimum width makes it
   overflow instead, which `.tablewrap`'s `overflow-x: auto` then scrolls. The
   value is the row's own content: 190px of label plus seven marker columns. */
.cmp { min-width: 640px; }
.cmp th:first-child, .cmp td:first-child { min-width: 190px; }
.cmp td:first-child { font-family: inherit; font-size: 13.5px; color: var(--fg-2); }
.cmp td:first-child a { color: var(--fg); }
.cmp th.tool, .cmp td.c { text-align: center; }
.cmp th.tool.self, .cmp td.c.self { background: var(--panel); }
.cmp td.c { font-family: var(--mono); font-size: 12.5px; white-space: nowrap;
            padding-left: 4px; padding-right: 4px; }
.v-yes { color: var(--green); }
.v-partial { color: var(--orange); }
.v-no, .v-unclear { color: var(--fg-3); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 22px; margin-top: 14px;
          font-family: var(--mono); font-size: 12px; color: var(--fg-3); }
.legend b { font-weight: 400; }
.loseflag { font-family: var(--mono); font-size: 11px; letter-spacing: 0.09em;
            text-transform: uppercase; color: var(--orange); }
.cmprow li { display: grid; grid-template-columns: 30px 132px minmax(0, 1fr); gap: 4px 16px;
             padding: 12px 0; border-top: 1px solid var(--line); }
.cmprow li:first-child { border-top: 0; }
.cmprow .m { font-family: var(--mono); font-size: 12.5px; }
.cmprow .who { font-family: var(--mono); font-size: 13.5px; font-weight: 600; color: var(--fg); }
.cmprow li.self .who { color: var(--green); }
.cmprow .d { color: var(--fg-2); font-size: 14.5px; line-height: 1.58; }
.cmprow .cite { display: block; margin-top: 4px; font-size: 11.5px; color: var(--fg-3); }
.cmprow .cite a { color: var(--fg-3); }
@media (max-width: 700px) {
  .cmprow li { grid-template-columns: 30px minmax(0, 1fr); }
  .cmprow .d { grid-column: 2 / -1; }
}

/* -------------------------------------------------- the agent-onboarding block

   Prominent by position rather than by colour: it sits directly under the lede,
   above the section nav, and takes the panel treatment the roadmap's boundary
   panel already uses. It is the first thing on the page because the reader most
   likely to act on it is the one who has not decided to read anything yet. */
.agentbox { border: 1px solid var(--line-2); background: var(--panel);
            padding: clamp(15px, 2.6vw, 22px); margin-top: clamp(20px, 3vw, 28px); }
.agentbox h2 { font-size: clamp(15px, 2vw, 17px); }
.agentbox .prose { margin-top: 8px; }
.agentbox .code { margin-top: 13px; }
.agentbox .code pre { white-space: pre-wrap; overflow-wrap: anywhere; color: var(--fg); }

/* -------------------------------------------------------------- quick starts */
.steps { counter-reset: step; margin-top: 12px; }
.steps li { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 9px;
            padding: 7px 0; color: var(--fg-2); font-size: 14.5px; line-height: 1.58;
            counter-increment: step; }
.steps li::before { content: counter(step) "."; font-family: var(--mono);
                    font-size: 12.5px; color: var(--green); }
.steps b { color: var(--fg); }
.proof { margin-top: 12px; color: var(--fg-2); font-size: 14px; line-height: 1.58; }
.proof b { color: var(--fg); font-family: var(--mono); font-size: 11.5px;
           letter-spacing: 0.11em; text-transform: uppercase; }

/* ------------------------------------------------------------------ the blog

   Posts render inline on the index, each under its own `id`, which is the same
   fragment contract `/docs/#<slug>` carries. A post per page would mean pages
   `tools/check.mjs` never discovers, since it walks one directory level. */
.post { margin-top: clamp(34px, 5vw, 48px); }
.post .meta { color: var(--fg-3); font-family: var(--mono); font-size: 12px;
              letter-spacing: 0.06em; margin-top: 4px; }
.post .prose, .post .bullets, .post table { max-width: 74ch; }
.post h3 { margin-top: 24px; font-size: clamp(15px, 2vw, 17px); }

/* -------------------------------------------------------- the docs browser

   A persistent sidebar, one URL per page, search, and prev/next. Two columns
   above 900px and one below it, and the sidebar is ONE list in the markup
   rather than a desktop copy and a mobile copy - a second copy is how a page
   ends up in one nav and not the other.

   `minmax(0, 1fr)` on the content track is load-bearing: a `<pre>` inside a
   grid item has a min-content width equal to its longest line, so without it
   an install command pushes the whole page sideways and `tools/check.mjs`
   fails at 360. */
.docsgrid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 26px 0; }
@media (min-width: 900px) {
  .docsgrid { grid-template-columns: 224px minmax(0, 1fr); gap: 0 clamp(30px, 4vw, 58px);
              align-items: start; }
  /* 45px clears the 44px sticky bar with the hairline under it. */
  .dsaside { position: sticky; top: 45px; max-height: calc(100vh - 60px);
             overflow-y: auto; overscroll-behavior: contain;
             padding: clamp(30px, 5vw, 54px) 0 20px; }
  .dsbody { padding-top: clamp(30px, 5vw, 54px); }
}
.page.docs { padding-top: 0; }
@media (max-width: 899px) { .dsaside { padding-top: clamp(28px, 6vw, 40px); } }
@media (max-width: 899px) { .dsbody { padding-top: 4px; } }

.dsvh { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0;
        overflow: hidden; clip-path: inset(50%); white-space: nowrap; }

/* Search. The control is `position: relative` because the result list hangs
   under it, and the list is in flow on the aside rather than absolutely
   positioned so it cannot overlap the nav it sits above at any width. */
.dsearch { position: relative; }
.dsearch input {
  width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
  font-family: var(--mono); color: var(--fg); background: var(--panel-2);
  border: 1px solid var(--line-2); border-radius: 0; padding: 8px 30px 8px 11px;
  appearance: none;
}
.dsearch input::placeholder { color: var(--fg-3); }
/* 16px on narrow screens, because iOS Safari zooms the whole page into any
   focused input whose font is smaller than that. */
@media (max-width: 899px) { .dsearch input { font-size: 16px; } }
.dsearch input:focus-visible { outline: 2px solid var(--green); outline-offset: 1px; }
/* The `/` hint, drawn as a key. `--fg-3` rather than `--fg-4`, because `--fg-4`
   does not clear AA and is restricted to borders and inert markers. */
.dskey { position: absolute; right: 9px; top: 8px; pointer-events: none;
         font-family: var(--mono); font-size: 11px; color: var(--fg-3);
         border: 1px solid var(--line-2); padding: 0 5px; line-height: 1.45; }
.dsearch input:focus + .dskey { display: none; }
.dsr { margin-top: 8px; border: 1px solid var(--line-2); background: var(--panel); }
.dsr[hidden] { display: none; }
.dsr a { display: block; border: 0; padding: 9px 11px; border-top: 1px solid var(--line); }
.dsr a:first-child { border-top: 0; }
.dsr a:hover, .dsr a:focus-visible { background: var(--panel-2); outline: 0; }
.dsr a:focus-visible { box-shadow: inset 2px 0 0 var(--green); }
.dsr .t { display: block; font-family: var(--mono); font-size: 12.5px; color: var(--fg);
          font-weight: 600; overflow-wrap: anywhere; }
.dsr a:hover .t, .dsr a:focus-visible .t { color: var(--cyan); }
.dsr .d { display: block; margin-top: 3px; font-size: 12.5px; line-height: 1.5;
          color: var(--fg-2); overflow-wrap: anywhere; }
.dsnone { margin: 0; padding: 10px 11px; font-size: 12.5px; color: var(--fg-2); }

/* The sidebar. A disclosure below 900px, always open above it. */
.dsnavbtn { display: block; width: 100%; margin-top: 12px; text-align: left; cursor: pointer;
            font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.12em;
            text-transform: uppercase; color: var(--fg-2); background: var(--panel);
            border: 1px solid var(--line-2); padding: 9px 12px; }
.dsnavbtn::before { content: "\\25B8"; color: var(--green); display: inline-block;
                    width: 15px; transition: transform 0.12s linear; }
.dsnavbtn[aria-expanded="true"]::before { transform: rotate(90deg); }
.dsnavbtn:hover { color: var(--fg); }
.dsnavbtn:focus-visible { outline: 2px solid var(--green); outline-offset: 1px; }
@media (min-width: 900px) { .dsnavbtn { display: none; } }
.dsnav { margin-top: 14px; display: flex; flex-direction: column; }
.dsnav[hidden] { display: none; }
.dsnav a { border: 0; color: var(--fg-2); font-size: 13.5px; line-height: 1.4;
           padding: 6px 0 6px 11px; border-left: 1px solid var(--line);
           overflow-wrap: anywhere; }
.dsnav a:hover { color: var(--fg); border-left-color: var(--line-2); }
.dsnav a:focus-visible { outline: 2px solid var(--green); outline-offset: -2px; }
.dsnav a[aria-current="page"] { color: var(--green); border-left-color: var(--green); }
.dsgroup { margin: 18px 0 5px; font-family: var(--mono); font-size: 11px;
           letter-spacing: 0.13em; text-transform: uppercase; color: var(--fg-3); }
.dsnav > a:first-child + .dsgroup { margin-top: 14px; }

/* Page body. The first section gets no top margin, because the heading list
   above it already separates it from the lede. */
.dsbody .docsec:first-of-type { margin-top: clamp(26px, 4vw, 36px); }
.dsbody .prose, .dsbody .bullets, .dsbody .steps, .dsbody .flat, .dsbody .lede,
.dsbody .note, .dsbody .proof { max-width: 76ch; }
.dsbody .prose + .prose, .dsbody .prose + .note { margin-top: 12px; }
.dsbody .code, .dsbody .tablewrap { margin-top: 14px; }
.dsbody .head + .prose, .dsbody .head + .bullets, .dsbody .head + .flat,
.dsbody .head + .steps { margin-top: 12px; }
.dsbody .flat li { grid-template-columns: 180px minmax(0, 1fr); }
@media (max-width: 760px) { .dsbody .flat li { grid-template-columns: minmax(0, 1fr); } }
.dsbody .docsec .kbd, .dsbody kbd {
  font-family: var(--mono); font-size: 0.86em; color: var(--fg);
  border: 1px solid var(--line-2); background: var(--panel-2); padding: 1px 5px;
  white-space: nowrap;
}

/* Previous and next. A grid rather than `space-between`, so a page with only a
   next link keeps it on the right where every other page draws it. */
.dsstep { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px;
          margin-top: clamp(40px, 6vw, 62px); padding-top: 20px;
          border-top: 1px solid var(--line-2); }
.dsstep a { border: 0; display: block; padding: 12px 14px; border: 1px solid var(--line);
            background: var(--panel); }
.dsstep a:hover { border-color: var(--line-2); }
.dsstep a:focus-visible { outline: 2px solid var(--green); outline-offset: 1px; }
.dsstep a.next { text-align: right; }
.dsstep .l { display: block; font-family: var(--mono); font-size: 11px;
             letter-spacing: 0.12em; text-transform: uppercase; color: var(--fg-3); }
.dsstep .t { display: block; margin-top: 4px; font-size: 14px; color: var(--fg);
             overflow-wrap: anywhere; }
.dsstep a:hover .t { color: var(--cyan); }
@media (max-width: 560px) {
  .dsstep { grid-template-columns: minmax(0, 1fr); }
  .dsstep a.next { text-align: left; }
  .dsstep .gap { display: none; }
}
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


def page_up(path: str) -> str:
    """The relative prefix reaching the deploy root from a generated page.

    A page's registry key is its URL path (`docs`, `docs/install`), so its depth
    is a property of the key rather than something a caller passes and can get
    wrong. `/docs/install/` is two directories down and needs `../../`.
    """
    return "../" * (path.count("/") + 1)


def shell(path: str, title: str, description: str, body: str, scripts: str = "") -> str:
    """The chrome every generated page shares: head, top bar, footer, scripts.

    `path` is the page's URL path below the deploy root, which is also its
    `BUILDERS` key. Only its first segment reaches the nav: a documentation
    sub-page marks `docs` as the current nav entry, because the bar names real
    destinations and the docs browser has a navigation surface of its own.

    `scripts` is page-specific inline JavaScript, appended after the two blocks
    every page carries. The documentation pages are the only user of it.
    """
    up = page_up(path)
    nav_slug = path.split("/")[0]
    # Prefixed rather than joined with a newline in the template: a page with no
    # extra script would otherwise gain a blank line inside `<script>`, which is
    # a whitespace diff on eight pages that have nothing to do with this.
    extra = f"\n{scripts}" if scripts else ""
    items = [(up, "home", False)] + [
        (f"{up}{s}/", label.lower(), s == nav_slug) for s, _, label in PAGES if s
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

<a class="skip" href="#main">skip to content</a>

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

<main id="main">
{body}
</main>

<footer>
  <div class="wrap">
    <div class="fbrand">
      <img class="brandmark dark" src="{up}img/logo.png" width="640" height="73" alt="swe-mux" />
      <img class="brandmark lite" src="{up}img/logo-light.png" width="640" height="73" alt="swe-mux" />
      <p>Agentic development environment and agent control plane.<br />
      Windows-first. Local-only. No account.</p>
    </div>
    <div class="fcols">
      <div class="fcol">
        <h4>pages</h4>
        <ul>
          <li><a href="{up}">home</a></li>
          <li><a href="{up}docs/">docs</a></li>
          <li><a href="{up}blog/">blog</a></li>
          <li><a href="{up}changelog/">changelog</a></li>
          <li><a href="{up}roadmap/">roadmap</a></li>
          <li><a href="{up}acknowledgements/">acknowledgements</a></li>
          <li><a href="{up}compare/">compare</a></li>
        </ul>
      </div>
      <div class="fcol">
        <h4>source</h4>
        <ul>
          <li><a href="{REPO}">github.com/jatoran/swe-mux</a></li>
          <li><a href="{IDEAS}">request a feature</a></li>
          <li><a href="{REPO}/issues">report a bug</a></li>
        </ul>
      </div>
      <div class="fcol">
        <h4>legal</h4>
        <ul>
          <li><a href="{BLOB}/LICENSE">Apache-2.0</a></li>
          <li><a href="{BLOB}/THIRD-PARTY-NOTICES.md">Third-party notices</a></li>
          <li><a href="{up}privacy/">Privacy</a></li>
          <li><a href="{up}terms/">Terms</a></li>
        </ul>
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
{index_block("menu")}{extra}
</script>

</body>
</html>
"""


# --------------------------------------------------------------------- changelog


def build_changelog(up: str) -> str:
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
            href = repo_url(links[name]) if name in links else ""
            shown = (
                f'<a href="{html.escape(href, quote=True)}">{html.escape(name)}</a>'
                if href
                else html.escape(name)
            )
            out.append('    <div class="rel">')
            out.append('      <div class="relhead">')
            out.append(f"        <h2>{shown}</h2>")
            if when:
                out.append(f'        <span class="when">{html.escape(when, quote=False)}</span>')
            out.append('        <span class="fill"></span>')
            # Only a link that is actually a release gets called one. `[Unreleased]`
            # resolves to a `compare/` URL under Keep a Changelog's own convention,
            # and labelling that "release notes" would send a reader to a diff.
            if "/releases/tag/" in href:
                out.append(
                    f'        <a class="relnotes" href="{html.escape(href, quote=True)}">'
                    "release notes</a>"
                )
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
    """One release, drawn compact: its own prose, a group index, then a disclosure.

    The shape this replaced rendered every heading and every bullet flat, which
    for the first release meant sixty bullets under ten sub-headings in a wall
    nobody reads to the end of. Nothing is dropped; it is reordered around what a
    person actually comes to a changelog for.

    The prose a release writes about itself stays at the top, because it is short
    and it is the only part written to be read as prose. Under it goes an index of
    *where* the release landed - the Keep a Changelog type, then this project's own
    groups inside it, with a count - which answers "did anything change in Git" in
    one glance. The bullets themselves go behind one `<details>` per release,
    rendered exactly as before.
    """
    lede: list[str] = []
    detail: list[str] = []
    # `groups` is ordered by first appearance and maps a change type to the
    # sub-headings under it; `counts` is per type, because a bullet's home is the
    # type rather than the group and a release may have bullets under neither.
    groups: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    current = ""
    seen_heading = False

    while i < len(blocks) and not (blocks[i].kind == "h" and blocks[i].level == 2):
        b = blocks[i]
        if b.kind == "h":
            # `###` is the Keep a Changelog type (Added, Fixed, ...) and `####` is
            # this project's grouping inside it. They are drawn differently on
            # purpose: rendered identically, a release reads as a flat run of
            # labels with no way to see where "Added" ends.
            label = plain(b.text)
            if b.level <= 3:
                current = label
                groups.setdefault(current, [])
                counts.setdefault(current, 0)
            elif current:
                groups[current].append(label)
            cls = "changetype" if b.level <= 3 else "subgroup"
            detail.append(f'        <div class="{cls}">{inline(b.text, base="")}</div>')
            seen_heading = True
        elif b.kind == "p":
            target = detail if seen_heading else lede
            indent = "        " if seen_heading else "      "
            target.append(f'{indent}<p class="prose">{inline(b.text, base="")}</p>')
        elif b.kind == "ul" and b.items:
            if current:
                counts[current] = counts.get(current, 0) + len(b.items)
            detail.append('        <ul class="bullets">')
            for item in b.items:
                detail.append(f'          <li><span>{inline(item, base="")}</span></li>')
            detail.append("        </ul>")
            seen_heading = seen_heading or bool(current)
        i += 1

    if not lede and not detail:
        # Keep a Changelog keeps an `Unreleased` heading standing even when it is
        # empty. A heading with nothing under it reads as a rendering bug, so the
        # emptiness is stated instead of shown.
        return ['      <p class="prose">Nothing yet.</p>'], i

    out = list(lede)
    out.extend(_release_index(groups, counts))
    if detail:
        total = sum(counts.values())
        noun = "entry" if total == 1 else "entries"
        label = f"all {total} {noun}" if total else "the full entry"
        out.append('      <details class="relmore">')
        out.append(f"        <summary>{html.escape(label, quote=False)}</summary>")
        out.extend(detail)
        out.append("      </details>")
    return out, i


def _release_index(groups: dict[str, list[str]], counts: dict[str, int]) -> list[str]:
    """The scannable line: each change type, its groups, and how many entries.

    Empty when a release has no `###` headings at all, which is the shape a small
    prose-only entry takes; drawing an empty index there would be a stray rule.
    """
    if not groups:
        return []
    sep = ' <span class="sep">&middot;</span> '
    out = ['      <div class="relgroups">']
    for kind, names in groups.items():
        n = counts.get(kind, 0)
        parts = []
        if n:
            parts.append(f'<span class="sep">{n} {"entry" if n == 1 else "entries"}</span>')
        parts.extend(html.escape(name, quote=False) for name in names)
        head = f'<span class="k">{html.escape(kind, quote=False)}</span>'
        out.append(f"        <div>{head}{(' ' + sep.join(parts)) if parts else ''}</div>")
    out.append("      </div>")
    return out


# ---------------------------------------------------------------------- the docs
#
# `/docs/` is a documentation browser: a persistent sidebar, one URL per page,
# in-page search, and prev/next. It replaced a single anchored page that was
# mostly a link farm - 54 of its 61 outbound links pointed at `.docs/**.md` blobs
# on GitHub, which is to say a reader who wanted to know how a feature worked was
# bounced off the site into raw Markdown written for maintainers.
#
# Two decisions follow from that and both are asserted rather than remembered
# (`tools/check.mjs`, the `docs browser` section):
#
# - **No documentation page links to a `.docs/**.md` blob.** The design documents
#   are internal-voiced: they name phases and incident dates and state invariants
#   that read as commitments on a public page. The answer is to write the pages,
#   which `tools/docs_content.py` does.
# - **A stub page is worse than the link it replaced**, because it costs a click
#   and answers nothing. So the tree is short and each page is written to be worth
#   arriving at; topics that could not be written well from the repository are
#   left out rather than stubbed.
#
# The old `/docs/#<slug>` fragment contract is gone with the index that carried
# it. It was documented as an interface because the in-app help modals linked
# into it; they do not, and never did - the only swemux.dev URL the product uses
# is `/version.json` (`update_check.py`). `site/README.md` section 10 records the
# correction.

DOCS_ROOT = "docs"

# Ids the docs chrome owns. A heading that slugified onto one of these would make
# the chrome's own element unreachable by fragment, so `build_docs_page` refuses
# rather than emitting a page with two elements answering to one id.
DOCS_RESERVED_IDS = {
    "menu",
    "menubtn",
    "themebtn",
    "dsq",
    "dsr",
    "dsstatus",
    "dsnav",
    "dsnavbtn",
    "dsempty",
}

_TAGS = re.compile(r"<[^>]+>")

# The tags an authored fragment may open, and the entities it may name. Content
# in `docs_content.py` is *trusted* inline HTML, which is what lets a command
# reference put `<code>` in a table cell - and which means a literal `<` or `&`
# is emitted raw and misread by the browser rather than escaped. Nothing else on
# this site has that hazard, because everything else is either lifted through
# `inline()` or is hand-written HTML somebody is looking at.
#
# So it is checked rather than remembered. The allowlist is small on purpose: a
# fragment wanting a tag outside it is a fragment that should be a block.
_ALLOWED_TAGS = re.compile(r"</?(?:b|em|code|kbd|a|span)(?:\s[^<>]*)?/?>")
_BARE_AMP = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|middot|nbsp|hellip|rsquo|#\d+|#x[0-9a-fA-F]+);)")


def check_fragment(where: str, fragment: str) -> None:
    """Refuse an authored fragment that a browser would read as broken markup."""
    if _BARE_AMP.search(fragment):
        raise SystemExit(
            f"{where}: a bare '&' reached authored content. Write '&amp;'. "
            f"In: {fragment[:120]!r}"
        )
    for match in re.finditer(r"<", fragment):
        if not _ALLOWED_TAGS.match(fragment, match.start()):
            raise SystemExit(
                f"{where}: '<' at offset {match.start()} does not open an allowed inline tag "
                f"({_ALLOWED_TAGS.pattern}). Write '&lt;', or make it a block. "
                f"In: {fragment[max(0, match.start() - 40):match.start() + 60]!r}"
            )


def heading_id(text: str) -> str:
    """The fragment a `h2` block is reachable at, derived from its own words."""
    return re.sub(r"[^a-z0-9]+", "-", plain(text).lower()).strip("-")


def strip_markup(fragment: str) -> str:
    """The readable text inside an authored inline-HTML fragment.

    Used for the search index and for a result's snippet, so what a reader
    searches is what a reader sees. Entities are resolved because `&amp;` is not
    a word anybody types into a search box.
    """
    return " ".join(html.unescape(_TAGS.sub(" ", fragment)).split())


def _doc_blocks_html(page: docs_content.Page, out: list[str]) -> None:
    """Render one page's blocks. A `h2` opens a section; everything else fills it.

    Sections exist so a heading can carry an `id` and a hairline rule without
    every page having to declare its own structure, and so the in-page heading
    list is derived from the same blocks the prose is.
    """
    open_section = False

    def close() -> None:
        nonlocal open_section
        if open_section:
            out.append("      </div>")
            open_section = False

    check_fragment(f"docs page '{page.slug}' lede", page.lede)
    for kind, value in page.blocks:
        if kind in {"p", "note", "proof"}:
            check_fragment(f"docs page '{page.slug}'", str(value))
        elif kind in {"ul", "steps"}:
            for item in value:  # type: ignore[union-attr]
                check_fragment(f"docs page '{page.slug}'", item)
        elif kind == "flat":
            for _, text in value:  # type: ignore[misc]
                check_fragment(f"docs page '{page.slug}'", text)
        elif kind == "table":
            headers, rows = value  # type: ignore[misc]
            for cell in [*headers, *(c for row in rows for c in row)]:
                check_fragment(f"docs page '{page.slug}' table", cell)

    for kind, value in page.blocks:
        if kind == "h2":
            close()
            text = str(value)
            out.append(f'      <div class="docsec" id="{heading_id(text)}">')
            out.append(
                '        <div class="head"><span class="n">#</span>'
                f'<h2>{html.escape(text, quote=False)}</h2><span class="fill"></span></div>'
            )
            open_section = True
            continue
        if not open_section:
            out.append('      <div class="docsec">')
            open_section = True
        if kind == "p":
            out.append(f'        <p class="prose">{value}</p>')
        elif kind == "note":
            out.append(f'        <p class="note">{value}</p>')
        elif kind == "ul":
            out.append('        <ul class="bullets">')
            for item in value:  # type: ignore[union-attr]
                out.append(f"          <li><span>{item}</span></li>")
            out.append("        </ul>")
        elif kind == "steps":
            out.append('        <ol class="steps">')
            for item in value:  # type: ignore[union-attr]
                out.append(f"          <li><span>{item}</span></li>")
            out.append("        </ol>")
        elif kind == "flat":
            out.append('        <ul class="flat">')
            for label, text in value:  # type: ignore[misc]
                out.append(
                    f"          <li><b>{html.escape(label, quote=False)}</b>"
                    f"<span>{text}</span></li>"
                )
            out.append("        </ul>")
        elif kind == "code":
            body = html.escape(str(value), quote=False)
            out.append(f'        <div class="code"><pre>{body}</pre></div>')
        elif kind == "proof":
            out.append(f'        <p class="proof"><b>It worked when</b> {value}</p>')
        elif kind == "table":
            # Cells carry inline HTML like every other block, rather than being
            # escaped as plain text. Escaping them was the first shape and it was
            # wrong in exactly the place a table is most useful: a command
            # reference wants `<code>--export</code>` in a cell, and escaping
            # published the tag itself.
            headers, rows = value  # type: ignore[misc]
            out.append('        <div class="tablewrap"><table>')
            head = "".join(f"<th>{h}</th>" for h in headers)
            out.append(f"          <thead><tr>{head}</tr></thead>")
            out.append("          <tbody>")
            for row in rows:
                cells = "".join(f"<td>{c}</td>" for c in row)
                out.append(f"            <tr>{cells}</tr>")
            out.append("          </tbody>")
            out.append("        </table></div>")
        else:
            raise SystemExit(f"docs: unknown block kind {kind!r} on page '{page.slug}'")
    close()


def doc_headings(page: docs_content.Page) -> list[tuple[str, str]]:
    """`(text, id)` for every `h2` on a page, in order."""
    return [(str(v), heading_id(str(v))) for k, v in page.blocks if k == "h2"]


def doc_search_text(page: docs_content.Page) -> str:
    """Everything on a page a search should be able to find, as plain text.

    Derived from the same blocks that render, which is the whole reason the
    content is declarative: a page written as markup would need its search text
    written a second time, and the copy is what drifts.
    """
    parts = [page.title, strip_markup(page.lede)]
    for kind, value in page.blocks:
        if kind in {"h2", "code"}:
            parts.append(strip_markup(str(value)))
        elif kind in {"p", "note", "proof"}:
            parts.append(strip_markup(str(value)))
        elif kind in {"ul", "steps"}:
            parts.extend(strip_markup(item) for item in value)  # type: ignore[union-attr]
        elif kind == "flat":
            # The colon matters: a label and its description run together read as
            # one broken sentence in a search snippet ("A run note Something
            # worth recording"), which is the only place this text is ever seen.
            parts.extend(f"{a}: {strip_markup(b)}" for a, b in value)  # type: ignore[misc]
        elif kind == "table":
            headers, rows = value  # type: ignore[misc]
            parts.extend(strip_markup(h) for h in headers)
            parts.extend(strip_markup(c) for row in rows for c in row)
    return " ".join(" ".join(parts).split())


# ------------------------------------------------------------------ docs chrome


def _docs_search(base: str) -> list[str]:
    """The search control. Static markup; the script behind it does the rest.

    `base` is the path back to `/docs/` from this page, and it is stamped on the
    control rather than computed in the script: the index holds URLs relative to
    `/docs/`, and the page that resolves them is the one that knows how deep it
    is.
    """
    return [
        f'      <div class="dsearch" data-base="{base}">',
        '        <label class="dsvh" for="dsq">Search the documentation</label>',
        '        <input type="search" id="dsq" placeholder="Search the docs"'
        ' autocomplete="off" spellcheck="false" aria-controls="dsr"'
        ' aria-expanded="false" aria-describedby="dskey" />',
        '        <div class="dskey" id="dskey" aria-hidden="true">/</div>',
        '        <p class="dsvh" id="dsstatus" role="status" aria-live="polite"></p>',
        '        <div class="dsr" id="dsr" hidden></div>',
        "      </div>",
    ]


def _docs_sidebar(base: str, current: str | None) -> list[str]:
    """The persistent section-and-page navigation, identical on every docs page.

    It is one list rather than a desktop copy and a mobile copy. Below 900px a
    disclosure button collapses it, and the button is wired by the inline script
    directly beneath it rather than by the deferred one at the end of the body:
    that runs before the nav is painted, so a narrow viewport does not flash a
    full-height list. With scripting off the nav is simply open, which is long
    rather than broken - the failure mode that matters is a nav that is *only*
    reachable through JavaScript, and this is not that.
    """
    out = [
        '      <button type="button" id="dsnavbtn" class="dsnavbtn" hidden'
        ' aria-expanded="true" aria-controls="dsnav">All documentation</button>',
        '      <nav class="dsnav" id="dsnav" aria-label="Documentation">',
        # `base or "./"` because an empty `href` is a link to the current URL
        # including its query and fragment, which is not the same thing as a
        # link to this directory and is not what `/docs/` means here.
        f'        <a href="{base or "./"}"'
        f'{" aria-current=\"page\"" if current is None else ""}>Overview</a>',
    ]
    for section in docs_content.SECTIONS:
        out.append(
            f'        <div class="dsgroup">{html.escape(section.title, quote=False)}</div>'
        )
        for page in section.pages:
            mark = ' aria-current="page"' if page.slug == current else ""
            out.append(
                f'        <a href="{base}{page.slug}/"{mark}>'
                f"{html.escape(page.title, quote=False)}</a>"
            )
    out.append("      </nav>")
    out.append("      <script>" + DOCS_NAV_INIT + "</script>")
    return out


def _docs_prevnext(base: str, index: int) -> list[str]:
    """Previous and next, over the sidebar's own flat order.

    Derived from that order rather than declared, so a page inserted into a
    section cannot end up unreachable by walking the chain.
    """
    pages = docs_content.pages()
    out = ['      <nav class="dsstep" aria-label="Page navigation">']
    if index > 0:
        prev = pages[index - 1]
        out.append(
            f'        <a class="prev" rel="prev" href="{base}{prev.slug}/">'
            f'<span class="l">Previous</span>'
            f"<span class=\"t\">{html.escape(prev.title, quote=False)}</span></a>"
        )
    else:
        out.append('        <span class="gap"></span>')
    if index < len(pages) - 1:
        nxt = pages[index + 1]
        out.append(
            f'        <a class="next" rel="next" href="{base}{nxt.slug}/">'
            f'<span class="l">Next</span>'
            f"<span class=\"t\">{html.escape(nxt.title, quote=False)}</span></a>"
        )
    out.append("      </nav>")
    return out


def _agent_block(up: str) -> list[str]:
    """The copy-paste line that hands setup to an agent, and the guide behind it.

    The guide is served from the deploy root as a plain Markdown file, which is
    what makes it fetchable by an agent without a parser.
    """
    return [
        '      <div class="agentbox">',
        '        <div class="relhead"><h2>Have an agent set it up</h2>'
        '<span class="fill"></span></div>',
        '        <p class="prose">Paste this to Claude Code, Codex, or any agent that can '
        "fetch a URL. It reads a guide written for that job: install, first run, the "
        "concepts worth explaining, and what leaves the machine.</p>",
        '        <div class="code"><pre>'
        f"{html.escape(AGENT_PROMPT, quote=False)}</pre></div>",
        f'        <p class="note">The guide is <a href="{up}{AGENT_GUIDE_PATH}">'
        f"<code>{html.escape(AGENT_GUIDE_URL, quote=False)}</code></a>, and it is plain "
        "Markdown rather than a page, so an agent gets the text and not a layout. "
        "Reading it costs nothing and it will tell your agent to ask before it installs "
        "anything.</p>",
        "      </div>",
    ]


def _docs_frame(base: str, current: str | None, article: list[str]) -> str:
    """Sidebar plus article, the shape every page under `/docs/` shares."""
    out = ['<section class="page docs">\n  <div class="wrap">']
    out.append('    <div class="docsgrid">')
    out.append('    <div class="dsaside">')
    out.extend(_docs_search(base))
    out.extend(_docs_sidebar(base, current))
    out.append("    </div>")
    out.append('    <article class="dsbody">')
    out.extend(article)
    out.append("    </article>")
    out.append("    </div>")
    out.append("  </div>\n</section>")
    return "\n".join(out)


def build_docs(up: str) -> str:
    """`/docs/` itself: what is here, and a map of the tree.

    A map rather than a table of contents. The sidebar is already the table of
    contents and is on every page, so repeating it here as a bare list would
    spend the one page a reader lands on saying nothing they cannot already see.
    Each section gets its own summary and each page its own line.
    """
    base = ""
    body: list[str] = []
    body.append('      <div class="kick">install it, use it, then read why</div>')
    body.append("      <h1>Documentation</h1>")
    body.append(
        '      <div class="lede"><p>Written for somebody <b>using</b> swe-mux. Start at '
        "<a href=\"install/\">Install</a> and read forward, or search - every page is its "
        "own URL and the sidebar is on all of them.</p></div>"
    )
    body.extend(_agent_block(up))

    for section, blurb in DOCS_SECTION_BLURBS:
        check_fragment(f"docs index blurb for '{section}'", blurb)
        pages = next(s.pages for s in docs_content.SECTIONS if s.title == section)
        body.append('      <div class="docsec">')
        body.append(
            f'        <div class="head"><span class="n">#</span>'
            f'<h2>{html.escape(section, quote=False)}</h2><span class="fill"></span></div>'
        )
        body.append(f'        <p class="prose">{blurb}</p>')
        body.append('        <ul class="flat">')
        for page in pages:
            body.append(
                f'          <li><b><a href="{base}{page.slug}/">'
                f"{html.escape(page.title, quote=False)}</a></b>"
                f"<span>{html.escape(page.description, quote=False)}</span></li>"
            )
        body.append("        </ul>")
        body.append("      </div>")

    body.append(
        '      <p class="note">Changing swe-mux rather than using it? '
        '<a href="contributing/">Developing swe-mux</a> is the maintainer page, and it is '
        "the only one here that sends you into the repository.</p>"
    )
    return _docs_frame(base, None, body)


# One line per sidebar section, saying what the section is for. Written here
# rather than on `Section` because it is copy for one page - the index - and the
# sidebar deliberately shows the titles alone.
DOCS_SECTION_BLURBS: list[tuple[str, str]] = [
    (
        "Getting started",
        "Installing it, getting a first agent session running, and reaching it from a "
        "phone. About half an hour end to end, and none of it needs a checkout.",
    ),
    (
        "Concepts",
        "The four ideas the rest of the application is built on. Worth reading once, in "
        "order, because every later page assumes them.",
    ),
    (
        "Working in it",
        "What each surface does and where its controls are, one page per subsystem.",
    ),
    (
        "Reference",
        "Every setting, every command, every default chord, and every file swe-mux writes.",
    ),
    (
        "Help",
        "What actually goes wrong, and what to do about it.",
    ),
]


def build_docs_page(page: docs_content.Page, index: int) -> str:
    """One `/docs/<slug>/` page: lede, heading list, blocks, prev and next."""
    base = "../"
    headings = doc_headings(page)
    seen = set()
    for text, ident in headings:
        if ident in DOCS_RESERVED_IDS:
            raise SystemExit(
                f"docs page '{page.slug}': heading '{text}' slugifies onto '{ident}', which "
                "the docs chrome already owns; rename the heading"
            )
        if ident in seen:
            raise SystemExit(
                f"docs page '{page.slug}': two headings slugify onto '{ident}', so the "
                "second is unreachable by fragment"
            )
        seen.add(ident)

    body: list[str] = []
    body.append(
        f'      <div class="kick">{html.escape(_section_of(page).lower(), quote=False)}</div>'
    )
    body.append(f"      <h1>{html.escape(page.title, quote=False)}</h1>")
    body.append(f'      <div class="lede"><p>{page.lede}</p></div>')
    if len(headings) > 2:
        body.append('      <nav class="toc" aria-label="On this page">')
        for text, ident in headings:
            body.append(f'        <a href="#{ident}">{html.escape(text.lower(), quote=False)}</a>')
        body.append("      </nav>")
    _doc_blocks_html(page, body)
    body.extend(_docs_prevnext(base, index))
    return _docs_frame(base, page.slug, body)


def _section_of(page: docs_content.Page) -> str:
    for section in docs_content.SECTIONS:
        if page in section.pages:
            return section.title
    raise SystemExit(f"docs page '{page.slug}' belongs to no section")


# ----------------------------------------------------------------- the search index
#
# A prebuilt index plus a small amount of vanilla JavaScript, because the site is
# static files on GitHub Pages with no build step and no server, and a search that
# needed either would be a search that does not work here.
#
# It is a **script** rather than a JSON file, and that is not a style choice. The
# gate opens these pages over `file://`, where `fetch()` of a sibling file is a
# cross-origin request and is refused; a classic `<script src>` is not. So the
# same artifact that works on the deployed site also works in `tools/check.mjs`
# and for anyone who opens the tree locally.
#
# It is loaded lazily, on the first interaction with the search box, so a reader
# who never searches never pays for it.


def build_search_index() -> str:
    """`docs/search-index.js`: one row per page, as a global a classic script sets."""
    rows = []
    for page in docs_content.pages():
        rows.append(
            {
                "u": f"{page.slug}/",
                "t": page.title,
                "s": strip_markup(page.description),
                "h": [[text, ident] for text, ident in doc_headings(page)],
                "b": doc_search_text(page),
            }
        )
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return (
        "/* Generated by site/tools/build.py from site/tools/docs_content.py.\n"
        "   Do not edit: run the build. A classic script rather than JSON so it\n"
        "   loads over file:// as well as over https://. */\n"
        f"window.__MUXDOCS = {payload};\n"
    )


# The disclosure that collapses the sidebar below 900px. Inlined directly under
# the nav rather than deferred to the end of the body, so it runs before the nav
# is painted and a narrow viewport does not flash a full-height list.
DOCS_NAV_INIT = """(function () {
  var btn = document.getElementById('dsnavbtn');
  var nav = document.getElementById('dsnav');
  if (!btn || !nav) return;
  btn.hidden = false;
  function set(open) {
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    nav.hidden = !open;
  }
  var narrow = window.matchMedia('(max-width: 899px)');
  set(!narrow.matches);
  btn.addEventListener('click', function () {
    set(btn.getAttribute('aria-expanded') !== 'true');
  });
  narrow.addEventListener('change', function (e) { set(!e.matches); });
})();"""

# Search. Vanilla, no framework, no bundler, and no request until somebody uses
# it. Ranking is deliberately crude and deliberately explainable: a title hit
# beats a heading hit beats a body hit, every query word has to appear somewhere,
# and a result carries the heading it matched so the link lands on the right part
# of the page rather than at its top.
DOCS_SEARCH = """(function () {
  var box = document.querySelector('.dsearch');
  if (!box) return;
  var input = document.getElementById('dsq');
  var out = document.getElementById('dsr');
  var status = document.getElementById('dsstatus');
  var base = box.getAttribute('data-base') || '';
  var loading = false;

  function load() {
    if (loading || window.__MUXDOCS) return;
    loading = true;
    var s = document.createElement('script');
    s.src = base + 'search-index.js';
    s.onload = function () { run(); };
    document.head.appendChild(s);
  }

  // A window around the first hit, snapped to word boundaries at both ends.
  // Slicing on raw offsets opens a snippet mid-word ('...ding. Attention'),
  // which reads as a rendering fault rather than as a truncation.
  function snippet(body, term) {
    var at = body.toLowerCase().indexOf(term);
    if (at < 0) return body.slice(0, body.indexOf(' ', 120) + 1 || 120) + '\\u2026';
    var from = Math.max(0, at - 55);
    var to = Math.min(body.length, from + 165);
    if (from > 0) {
      var space = body.indexOf(' ', from);
      from = space >= 0 && space < at ? space + 1 : from;
    }
    if (to < body.length) {
      var end = body.lastIndexOf(' ', to);
      to = end > at ? end : to;
    }
    return (from ? '\\u2026' : '') + body.slice(from, to).trim() +
      (to < body.length ? '\\u2026' : '');
  }

  function rank(query) {
    var terms = query.toLowerCase().split(/\\s+/).filter(Boolean);
    if (!terms.length) return [];
    var hits = [];
    (window.__MUXDOCS || []).forEach(function (page) {
      var title = page.t.toLowerCase();
      var body = page.b.toLowerCase();
      var score = 0;
      var heading = null;
      for (var i = 0; i < terms.length; i++) {
        var term = terms[i];
        var here = 0;
        if (title.indexOf(term) >= 0) here += 8;
        for (var j = 0; j < page.h.length; j++) {
          if (page.h[j][0].toLowerCase().indexOf(term) >= 0) {
            here += 4;
            if (!heading) heading = page.h[j];
            break;
          }
        }
        if (body.indexOf(term) >= 0) here += 1;
        if (!here) return;
        score += here;
      }
      hits.push({ page: page, score: score, heading: heading, term: terms[0] });
    });
    hits.sort(function (a, b) { return b.score - a.score; });
    return hits.slice(0, 8);
  }

  function run() {
    var query = input.value.trim();
    if (!query) {
      out.hidden = true;
      out.textContent = '';
      input.setAttribute('aria-expanded', 'false');
      status.textContent = '';
      return;
    }
    if (!window.__MUXDOCS) { load(); return; }
    var hits = rank(query);
    out.textContent = '';
    if (!hits.length) {
      var none = document.createElement('p');
      none.className = 'dsnone';
      none.textContent = 'No page matches "' + query + '".';
      out.appendChild(none);
    }
    hits.forEach(function (hit) {
      var a = document.createElement('a');
      a.href = base + hit.page.u + (hit.heading ? '#' + hit.heading[1] : '');
      var t = document.createElement('span');
      t.className = 't';
      t.textContent = hit.page.t + (hit.heading ? '  \\u203a  ' + hit.heading[0] : '');
      var d = document.createElement('span');
      d.className = 'd';
      d.textContent = snippet(hit.page.b, hit.term);
      a.appendChild(t);
      a.appendChild(d);
      out.appendChild(a);
    });
    out.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    status.textContent = hits.length + (hits.length === 1 ? ' result' : ' results');
  }

  input.addEventListener('focus', load);
  input.addEventListener('input', run);

  function results() { return [].slice.call(out.querySelectorAll('a')); }

  input.addEventListener('keydown', function (e) {
    var found = results();
    if (e.key === 'ArrowDown' && found.length) { e.preventDefault(); found[0].focus(); }
    else if (e.key === 'Enter' && found.length) { e.preventDefault(); found[0].click(); }
    else if (e.key === 'Escape') { input.value = ''; run(); input.blur(); }
  });

  out.addEventListener('keydown', function (e) {
    var found = results();
    var at = found.indexOf(document.activeElement);
    if (at < 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      found[Math.min(at + 1, found.length - 1)].focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (at === 0) input.focus(); else found[at - 1].focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      input.focus();
      input.value = '';
      run();
    }
  });

  // `/` is the shortcut every documentation site has, so it is the one people
  // try. Never while a field is focused, because then it is a character.
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.ctrlKey || e.altKey || e.metaKey) return;
    var el = document.activeElement;
    var tag = el && el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (el && el.isContentEditable)) return;
    e.preventDefault();
    input.focus();
    input.select();
  });

  document.addEventListener('click', function (e) {
    if (!box.contains(e.target)) {
      out.hidden = true;
      input.setAttribute('aria-expanded', 'false');
    }
  });
})();"""


# ---------------------------------------------------------- acknowledgements

ECOSYSTEM_LABEL = {"python": "Python", "npm": "Frontend"}


def build_acknowledgements(up: str) -> str:
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


def build_roadmap(up: str) -> str:
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


# --------------------------------------------------------------- hand-written pages


def _hand_written(name: str, marker: str, block: str) -> str:
    """A page whose prose is a file in `content/`, with one generated block in it.

    The same device `build_roadmap` uses, and for the same reason: the argument a
    page makes is worth writing by hand and reviewing as a diff, while the part
    that is data must not be. Exactly one marker, not at least one, because the
    substitution is a plain replace and a second copy in that file's own header
    comment would draw the block twice, once of it inside a comment where nobody
    would see it was wrong.
    """
    text = (SITE / "content" / name).read_text(encoding="utf-8").strip()
    found = text.count(marker)
    if found != 1:
        raise SystemExit(
            f"content/{name} carries the {marker} marker {found} times; it needs exactly "
            "one, marking where the generated block goes"
        )
    if not block:
        # The blank line after it goes too, so a page without the block is
        # byte-identical to one that never carried it rather than carrying a
        # whitespace change nobody wrote.
        return re.sub(rf"^[ \t]*{re.escape(marker)}\n\n?", "", text, flags=re.M)
    return text.replace(marker, block)


# ------------------------------------------------------------------------ compare

#: The four verdicts a cell may carry, and the marker each is drawn with. The
#: vocabulary is closed on purpose: a fifth value would be somebody smuggling a
#: qualifier into the data to avoid saying `unclear`, which is the one honest
#: answer this page depends on being cheap to give.
VERDICTS = {
    "yes": ("[x]", "yes"),
    "partial": ("[~]", "partly"),
    "no": ("[ ]", "no"),
    "unclear": ("[?]", "unclear"),
}


def read_comparison() -> dict[str, object]:
    """`content/compare.json`, validated hard enough that a gap cannot ship.

    Every check here failed for a real reason at some point in writing the page,
    and each one is the difference between a table that can be defended and one
    that cannot:

    - a **missing cell** would render as an empty box, which a reader charitably
      reads as "no" and uncharitably reads as an omission;
    - an **undefined source id** would print a citation with no link behind it,
      which is worse than no citation at all;
    - an **uncited source** is a link nobody checked, kept because deleting it
      felt like losing evidence;
    - a **verdict outside the vocabulary** is a qualifier being smuggled in;
    - **no losing row at all** means the page has quietly become marketing, which
      is the exact failure a comparison table written by one of the products is
      most likely to reach.
    """
    data = json.loads((SITE / "content/compare.json").read_text(encoding="utf-8"))
    tools = data["tools"]
    sources = data["sources"]
    rows = data["rows"]
    if not data.get("checked"):
        raise SystemExit("content/compare.json: no 'checked' date, and the page prints one")
    keys = [t["key"] for t in tools]
    if len(keys) != len(set(keys)):
        raise SystemExit("content/compare.json: two tools share a key")
    if sum(1 for t in tools if t.get("self")) != 1:
        raise SystemExit("content/compare.json: exactly one tool must be marked 'self'")

    cited: set[str] = set()
    ids: set[str] = set()
    for row in rows:
        if row["id"] in ids:
            raise SystemExit(f"content/compare.json: two rows share the id {row['id']!r}")
        ids.add(row["id"])
        missing = [k for k in keys if k not in row["cells"]]
        if missing:
            raise SystemExit(
                f"content/compare.json: row {row['id']!r} has no cell for "
                + ", ".join(missing)
                + ". Every row answers for every tool, and 'unclear' is a real answer."
            )
        for key, cell in row["cells"].items():
            if key not in keys:
                raise SystemExit(
                    f"content/compare.json: row {row['id']!r} has a cell for {key!r}, "
                    "which is not a tool"
                )
            if cell["v"] not in VERDICTS:
                raise SystemExit(
                    f"content/compare.json: row {row['id']!r}, {key}: verdict "
                    f"{cell['v']!r} is not one of " + ", ".join(VERDICTS)
                )
            if not cell.get("src"):
                raise SystemExit(
                    f"content/compare.json: row {row['id']!r}, {key}: no source. Every "
                    "cell on this page names where it came from."
                )
            for src in cell["src"]:
                if src not in sources:
                    raise SystemExit(
                        f"content/compare.json: row {row['id']!r}, {key} cites {src!r}, "
                        "which is not a defined source"
                    )
                cited.add(src)
    unused = sorted(set(sources) - cited)
    if unused:
        raise SystemExit(
            "content/compare.json: these sources are defined and cited by nothing: "
            + ", ".join(unused)
        )
    if not any(row.get("loses") for row in rows):
        raise SystemExit(
            "content/compare.json: no row is marked 'loses'. A comparison written by one "
            "of the products, in which that product concedes nothing, is read as "
            "marketing and dismissed whole. See content/compare.html, rule 3."
        )
    return data


def _cite(cell: dict[str, object], sources: dict[str, dict[str, str]]) -> str:
    links = ", ".join(
        f'<a href="{html.escape(sources[s]["url"], quote=True)}">'
        f'{html.escape(sources[s]["label"], quote=False)}</a>'
        for s in cell["src"]  # type: ignore[union-attr]
    )
    return f'<span class="cite">source: {links}</span>'


def build_comparison_block() -> str:
    data = read_comparison()
    tools = data["tools"]
    sources = data["sources"]
    rows = data["rows"]
    checked = html.escape(str(data["checked"]), quote=False)

    out: list[str] = []
    out.append('<div class="rel" id="matrix">')
    out.append(
        '      <div class="relhead"><h2>At a glance</h2>'
        f'<span class="when">sources read {checked}</span>'
        '<span class="fill"></span></div>'
    )
    out.append(
        '      <p class="prose">Markers only. Every one of them is a sentence with a '
        "source under it, in the section of the same name below, and a row label links "
        "straight there.</p>"
    )
    out.append('      <div class="legend">')
    for key, (mark, word) in VERDICTS.items():
        out.append(
            f'        <span><b class="v-{key}">{mark}</b> '
            f"{html.escape(word, quote=False)}</span>"
        )
    out.append("      </div>")
    out.append('      <div class="tablewrap">')
    out.append('      <table class="cmp">')
    head = "".join(
        f'<th class="tool{" self" if t.get("self") else ""}">'
        f'{html.escape(str(t["name"]), quote=False)}</th>'
        for t in tools
    )
    out.append(f"        <thead><tr><th></th>{head}</tr></thead>")
    out.append("        <tbody>")
    for row in rows:
        label = html.escape(str(row["label"]), quote=False)
        cells = ""
        for t in tools:
            cell = row["cells"][t["key"]]
            mark, word = VERDICTS[cell["v"]]
            cls = f'c v-{cell["v"]}' + (" self" if t.get("self") else "")
            cells += (
                f'<td class="{cls}" title="{html.escape(str(t["name"]), quote=True)}: '
                f'{html.escape(word, quote=True)}">{mark}</td>'
            )
        out.append(f'        <tr><td><a href="#row-{row["id"]}">{label}</a></td>{cells}</tr>')
    out.append("        </tbody>")
    out.append("      </table>")
    out.append("      </div>")
    out.append(
        '      <p class="note">Read a marker as shorthand for the sentence below it, '
        "never as a score. Nothing on this page is weighted, nothing is added up, and "
        "<code>[ ]</code> often means a tool is not trying to do the thing rather than "
        "trying and failing.</p>"
    )
    out.append("    </div>")

    for row in rows:
        out.append(f'    <div class="rel" id="row-{row["id"]}">')
        flag = '<span class="loseflag">swe-mux loses</span>' if row.get("loses") else ""
        out.append(
            f'      <div class="relhead"><h2>{html.escape(str(row["label"]), quote=False)}'
            f"</h2>{flag}<span class=\"fill\"></span></div>"
        )
        out.append('      <ul class="cmprow">')
        for t in tools:
            cell = row["cells"][t["key"]]
            mark, word = VERDICTS[cell["v"]]
            self_cls = " class=\"self\"" if t.get("self") else ""
            out.append(
                f"        <li{self_cls}>"
                f'<span class="m v-{cell["v"]}" title="{html.escape(word, quote=True)}">'
                f'{mark}</span>'
                f'<span class="who">{html.escape(str(t["name"]), quote=False)}</span>'
                f'<span class="d">{html.escape(str(cell["t"]), quote=False)}'
                f"{_cite(cell, sources)}</span></li>"
            )
        out.append("      </ul>")
        out.append("    </div>")
    return "\n".join(out)


def build_compare(up: str) -> str:
    return _hand_written("compare.html", COMPARISON_TABLE, build_comparison_block())


# --------------------------------------------------------------------------- blog

POST_KEYS = ("title", "date", "summary")


def read_posts() -> list[dict[str, object]]:
    """Every published post, newest first. May be empty, and empty is not an error.

    A filename beginning with an underscore is never published, which is what lets
    `content/blog/_template.md` document the format by being the format.

    The header is strict in both directions: a missing key and an unknown key both
    raise. A post with no date would sort arbitrarily and read as undated, and an
    unknown key is almost always a typo in one of the three that matter, which
    would otherwise ship as a post missing its summary.
    """
    directory = SITE / "content/blog"
    posts: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            raise SystemExit(f"content/blog/{path.name}: no `---` header; see _template.md")
        header = dict(re.findall(r"^([a-z]+):\s*(.+)$", m.group(1), re.M))
        if set(header) != set(POST_KEYS):
            raise SystemExit(
                f"content/blog/{path.name}: header keys are {sorted(header)}, expected "
                f"exactly {list(POST_KEYS)}"
            )
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", header["date"]):
            raise SystemExit(
                f"content/blog/{path.name}: date {header['date']!r} is not YYYY-MM-DD"
            )
        posts.append({"slug": path.stem, "body": m.group(2), **header})
    posts.sort(key=lambda p: (str(p["date"]), str(p["slug"])), reverse=True)
    return posts


def _render_post(post: dict[str, object]) -> list[str]:
    out = [f'    <div class="post" id="{html.escape(str(post["slug"]), quote=True)}">']
    out.append(
        f'      <div class="relhead"><h2>{inline(str(post["title"]))}</h2>'
        '<span class="fill"></span></div>'
    )
    out.append(f'      <div class="meta">{html.escape(str(post["date"]), quote=False)}</div>')
    out.append(f'      <p class="prose"><b>{inline(str(post["summary"]))}</b></p>')
    for b in parse_markdown(str(post["body"])):
        if b.kind == "h":
            out.append(f"      <h3>{inline(b.text)}</h3>")
        elif b.kind == "p":
            out.append(f'      <p class="prose">{inline(b.text)}</p>')
        elif b.kind == "ul" and b.items:
            out.append('      <ul class="bullets">')
            for item in b.items:
                out.append(f"        <li><span>{inline(item)}</span></li>")
            out.append("      </ul>")
        elif b.kind == "table" and b.rows:
            out.extend(_table(b.rows))
    out.append("    </div>")
    return out


def build_posts_block() -> str:
    posts = read_posts()
    if not posts:
        # Written, not defaulted. A blog with no posts and no explanation reads as
        # abandoned, which is worse than the honest version and is the reason this
        # page ships at all rather than waiting for its first post.
        return """<div class="rel">
      <div class="relhead"><h2>Nothing published yet</h2><span class="fill"></span></div>
      <p class="prose">This page exists before its first post on purpose: a
      <code>blog</code> link that 404s is worse than an empty blog, and an empty blog
      that says so is better than one that looks abandoned. When there is something
      worth a post it will appear here, with its own permalink, and nothing else about
      the page will change.</p>
    </div>"""
    out: list[str] = []
    if len(posts) > 1:
        out.append('    <nav class="toc" aria-label="Posts">')
        for post in posts:
            out.append(
                f'      <a href="#{html.escape(str(post["slug"]), quote=True)}">'
                f'{html.escape(plain(str(post["title"])), quote=False)}</a>'
            )
        out.append("    </nav>")
    for post in posts:
        out.extend(_render_post(post))
    return "\n".join(out).lstrip()


def build_blog(up: str) -> str:
    return _hand_written("blog.html", POSTS, build_posts_block())


# ------------------------------------------------------------- privacy and terms
#
# Both are entirely hand-written, and neither takes a generated block. Nothing on
# either page is derived from a file this script could read: the privacy claims are
# statements about `update_check.py`, `config.py`, and the optional integrations,
# and generating them from anything would mean inventing a machine-readable
# description of network behaviour that nobody would then maintain.
#
# The rule that matters more than the mechanism: a generated corporate template
# would be inaccurate in both directions here. It would imply an operator holding
# data, and it would bury the one request that actually leaves the machine under a
# page about cookies this site does not set.


def build_privacy(up: str) -> str:
    return (SITE / "content/privacy.html").read_text(encoding="utf-8").strip()


def build_terms(up: str) -> str:
    return (SITE / "content/terms.html").read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- main

BUILDERS = {
    "docs": (
        build_docs,
        "Docs · swe-mux",
        "swe-mux documentation: install it, run a first agent session, reach it from a "
        "phone, and a page for every surface, setting, and command.",
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
    "compare": (
        build_compare,
        "Compared · swe-mux",
        "swe-mux against Herdr, tmux, Orca, Conductor, Superset, and Warp. Every cell is "
        "sourced and dated, and the rows where swe-mux loses are marked.",
    ),
    "blog": (
        build_blog,
        "Blog · swe-mux",
        "Engineering write-ups about swe-mux: what broke, what the measurement said, and "
        "what got decided because of it.",
    ),
    "privacy": (
        build_privacy,
        "Privacy · swe-mux",
        "swe-mux runs on your own machine and talks to no service this project operates. "
        "Exactly which requests it makes, and what this website sees.",
    ),
    "terms": (
        build_terms,
        "Terms · swe-mux",
        "swe-mux is Apache-2.0 software you run yourself, so the licence is the terms. What "
        "that covers, and the four things it does not answer on its own.",
    ),
}

# Every `/docs/<slug>/` page, registered from the content module rather than
# listed here. A page added to `docs_content.SECTIONS` is generated, indexed for
# search, in the sidebar, and in the prev/next chain by that one edit; there is
# no second list to forget. The key is the URL path, which is what `shell()`
# derives a page's depth from.
for _page in docs_content.pages():
    BUILDERS[f"{DOCS_ROOT}/{_page.slug}"] = (
        (lambda p, i: lambda up: build_docs_page(p, i))(
            _page, docs_content.pages().index(_page)
        ),
        f"{_page.title} · swe-mux docs",
        _page.description,
    )

# The lazily loaded search index. Generated output like the pages, committed like
# the pages, and covered by `--check` like the pages - a stale index beside a
# changed page is a search that confidently returns the wrong words.
SEARCH_INDEX_PATH = f"{DOCS_ROOT}/search-index.js"

# The scripts each page carries beyond the theme toggle and the menu. Only the
# documentation browser has any.
PAGE_SCRIPTS = {
    slug: "\n/* ----------------------------------------------------------------- search */\n"
    + DOCS_SEARCH
    for slug in BUILDERS
    if slug == DOCS_ROOT or slug.startswith(f"{DOCS_ROOT}/")
}


def _write(target: Path, text: str, args: argparse.Namespace, stale: list[str], label: str) -> None:
    """Write one generated artifact, or record it as stale under `--check`."""
    current = target.read_text(encoding="utf-8") if target.exists() else None
    if current == text:
        print(f"  {label}  up to date")
        return
    if args.check:
        stale.append(label)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the LF this builds stays LF: Python's text mode would
    # translate it to CRLF on Windows and every regenerate would read as a
    # whole-file diff.
    with target.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print(f"  {label}  written ({len(text.splitlines())} lines)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if any page on disk is stale")
    args = ap.parse_args()

    stale: list[str] = []
    _write(
        SITE / SEARCH_INDEX_PATH,
        build_search_index(),
        args,
        stale,
        SEARCH_INDEX_PATH,
    )
    for slug, (builder, title, description) in BUILDERS.items():
        page = shell(
            slug, title, description, builder(page_up(slug)), PAGE_SCRIPTS.get(slug, "")
        )
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
        _write(SITE / slug / "index.html", page, args, stale, f"{slug}/index.html")

    if stale:
        print("\nSTALE: " + ", ".join(stale))
        print("Their sources changed. Run: python site/tools/build.py")
        return 1
    print("\nall pages current" if args.check else "\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
