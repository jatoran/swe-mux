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
TODO_VALUES = ("blog URL",)

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

PAGES = [
    ("", "index.html", "swe-mux"),
    ("docs", "docs/index.html", "Docs"),
    ("changelog", "changelog/index.html", "Changelog"),
    ("roadmap", "roadmap/index.html", "Roadmap"),
    ("acknowledgements", "acknowledgements/index.html", "Acknowledgements"),
]

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

/* The bar's nav keeps `index.html`'s scroll-fade for narrow screens, but this
   nav is short and its last item would sit under the fade at every width. The
   padding gives the gradient empty space to fall on once it is scrolled out. */
.pagenav { padding-right: 15px; }
.pagenav a[aria-current="page"] { color: var(--fg); }
.pagenav a[aria-current="page"]::before { content: "\\25B8 "; color: var(--green); }

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


def shell(slug: str, title: str, description: str, body: str) -> str:
    """The chrome every generated page shares: head, top bar, footer, scripts."""
    up = "../"  # every generated page lives one directory below the deploy root
    items = [(up, "home", False)] + [
        (f"{up}{s}/", label.lower(), s == slug) for s, _, label in PAGES if s
    ]
    nav = "\n".join(
        f'      <a href="{href}"{" aria-current=\"page\"" if current else ""}>{text}</a>'
        for href, text, current in items
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
    <nav class="secnav pagenav">
{nav}
    </nav>
    <div class="util">
      <a href="{REPO}" aria-label="Source on GitHub" title="Source on GitHub">
        {GITHUB_MARK}
        <span class="lbl">github</span>
      </a>
      <button type="button" id="themebtn" aria-label="Toggle colour scheme" title="Toggle colour scheme">
        {MOON}
        {SUN}
      </button>
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
        <div><a href="{up}">home</a> &middot; <a href="{up}docs/">docs</a></div>
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
        <h4>license</h4>
        <div><a href="{BLOB}/LICENSE">Apache-2.0</a></div>
        <div><a href="{BLOB}/THIRD-PARTY-NOTICES.md">Third-party notices</a></div>
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


# ------------------------------------------------------- quick starts, hand-written
#
# Four tasks, in the order a new install hits them. These are the one part of
# `/docs/` that is written here rather than lifted, and the reason is that no
# document in `.docs/` is shaped like this: the design documents state invariants
# for whoever maintains a subsystem next, and a person who has just run
# `uv tool install` needs eight numbered lines and a way to tell whether it
# worked.
#
# Every command below is one that exists. The install commands were executed
# against the published 0.1.0 wheel (`README.md`, and `site/README.md` section 7
# records what was run); the keybindings are `keybindings.py`'s own defaults; the
# data-directory paths are `config.py`'s. A command invented here would be read by
# somebody whose install is already broken, which is the worst possible audience
# for a plausible guess.
QUICKSTARTS: list[tuple[str, str, str, list[str], str]] = [
    (
        "quickstart-install",
        "Install it, and see the workspace",
        "Ten minutes, and it needs no checkout and no Node. The published wheel is pure "
        "Python and already carries the built frontend.",
        [
            "Check you have <b>Python 3.12 or newer</b>: <code>python --version</code>.",
            "Install it. <code>uv tool install swe-mux</code> is the recommended form and "
            "leaves <code>mux</code>, <code>muxd</code>, and <code>swe-mux</code> on your "
            "PATH globally. <code>pipx install swe-mux</code> does the same without uv.",
            "<b>On Windows, take the desktop extra instead:</b> "
            "<code>uv tool install \"swe-mux[desktop]\"</code>. That is what adds the native "
            "window and the tray icon; without it the <code>swe-mux</code> command exists "
            "and fails on a missing import.",
            "Start the daemon: <code>muxd</code>. On Windows with the extra, "
            "<code>swe-mux</code> opens the same thing in a window.",
            "Open <code>http://127.0.0.1:8765</code>.",
            "Create a Project and point it at a folder you already work in. Nothing else "
            "works until there is one, and nothing is spawned until you ask for it.",
        ],
        "Run <code>mux doctor</code>. It is read-only, and it reports on the daemon, the "
        "supervisor, the frontend build, the agent CLIs it can detect, the tailnet "
        "listener, and the background loops. It is the command that tells installed from "
        "working.",
    ),
    (
        "quickstart-session",
        "Run your first agent session",
        "There is no special ritual for starting an agent. You open a terminal and type "
        "the command you already type.",
        [
            "With a Project selected, press <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd>. "
            "That opens a real terminal at the Project's root.",
            "Type <code>claude</code>, <code>codex</code>, or whichever CLI you use, "
            "normally. swe-mux puts its own launchers first on that terminal's PATH, so "
            "the ordinary command <b>promotes the terminal in place</b>: same pane, same "
            "scrollback, now carrying a transcript, a status, a queue, and a context "
            "meter.",
            "Give it something to do, then watch the session's status. Working, ready, "
            "awaiting approval, and blocked mean the same thing whichever vendor's CLI "
            "produced them.",
            "While it is mid-turn, open the <b>Queue</b> tab in the utility drawer and "
            "stage your next message. It is durable and head-of-line, and "
            "<b>automatic delivery is off by default</b>: a queued message waits for you "
            "to send it.",
            "Use the <b>Run</b> menu for anything you did not want to type: another "
            "agent, a shell, a worktree session, or a task imported from the repository.",
        ],
        "The pane's status strip stops saying <em>working</em> and the transcript tab has "
        "something in it. If a session sits on <em>awaiting</em>, that is the agent waiting "
        "on you rather than a stall.",
    ),
    (
        "quickstart-cli",
        "Add an agent CLI",
        "swe-mux does not install, manage, authenticate, or proxy your agent CLIs. It "
        "finds the ones you already have.",
        [
            "Install the CLI the vendor's own way, outside swe-mux, and log into it "
            "there. Your subscription and your agreement stay with that vendor.",
            "Check swe-mux can see it: <code>mux harnesses</code> lists every harness in "
            "the registry with its detection state, and Settings, Harnesses shows the same "
            "thing with the executable each one resolved to.",
            "If it resolved to the wrong binary, or to none, set the path for that harness "
            "in Settings, Harnesses. That is the usual fix on a machine with several "
            "installs.",
            "Open a terminal in a Project and run it. A harness the registry knows gets "
            "normalized status, transcripts, history, and account switching on top.",
            "<b>A CLI the registry has never heard of still works.</b> It runs in a real "
            "pseudoterminal exactly as it does outside swe-mux; what it does not get is "
            "the layer.",
        ],
        "<code>mux harnesses</code> shows it detected, and typing its command in a Project "
        "terminal produces an agent session rather than a plain shell.",
    ),
    (
        "quickstart-phone",
        "Reach it from your phone",
        "The phone client is the same application, not a companion, and it goes over your "
        "own tailnet with no relay and no swe-mux login.",
        [
            "Install Tailscale on the host machine and on the phone, on the same tailnet.",
            "Leave the daemon running. It listens on loopback and on the machine's "
            "detected Tailscale address; <code>muxd --local-only</code> is how you stop "
            "that.",
            "On the phone, open the machine's <code>.ts.net</code> hostname over HTTPS. "
            "Turn <b>Use Tailscale DNS</b> on, and leave Android's Private DNS off or "
            "automatic: the certificate is bound to the hostname, so the raw "
            "<code>100.x</code> address cannot serve HTTPS.",
            "Install it to the home screen when the browser offers. It is a progressive "
            "web app, so it gets its own window and can receive push.",
            "<b>HTTPS is not optional if you want the microphone or the clipboard</b>, "
            "because browsers restrict both outside a secure context. swe-mux puts "
            "Tailscale Serve on 443 in front of the daemon for exactly this.",
        ],
        "The workspace renders on the phone with your Projects in it. Understand the "
        "consequence before you leave it running: <b>Tailscale policy is the entire access "
        "boundary</b>, and any device your tailnet admits has terminal and code-execution "
        "authority on that host.",
    ),
]

# ------------------------------------------ features and settings, hand-written
#
# What each subsystem does and where its controls are, written for somebody using
# swe-mux rather than maintaining it. Derived from `.docs/design/features/` and
# deliberately not lifted from it: those documents are internal-voiced, name
# phases and incident dates, and state invariants that read as commitments on a
# public page.
#
# The third element of each entry is the surface it is reached at, and every one
# of them is a real tab. Settings tabs are `frontend/src/settingsTabs.ts`; drawer
# tabs are `frontend/src/drawerTabs.ts`. The fourth is the design document that
# owns it, named by its `/docs/#<slug>` anchor - `build_docs` fails on a slug the
# index does not carry, so a document that stops being published cannot leave a
# dead link here.
FEATURE_GUIDE: list[tuple[str, str, str, list[tuple[str, str, str, str]]]] = [
    (
        "guide-sessions",
        "Sessions, terminals, and status",
        "Everything else is built on swe-mux owning the pseudoterminal rather than "
        "wrapping the program inside it.",
        [
            (
                "Sessions that outlive the app",
                "A supervisor process separate from the daemon and the UI holds every "
                "terminal, so restarting the daemon or rebuilding the desktop app leaves "
                "the agents working. Reconnecting replays only the bytes you missed. A "
                "session whose process did end stays readable rather than disappearing.",
                "Settings, Terminals",
                "sessions",
            ),
            (
                "One status vocabulary",
                "Working, ready, awaiting approval, or blocked, meaning the same thing "
                "across vendors. It is read from provider hooks, the transcript, the "
                "terminal, and the CLI's own state files, and every transition is kept. "
                "<b>Awaiting is the one to watch</b>: it means the agent is waiting on you.",
                "the session's status strip",
                "status-detection",
            ),
            (
                "Approvals",
                "When a harness asks permission, the decision is made from its structured "
                "request rather than from what is on screen, and there is a floor no "
                "configuration can reach past. swe-mux never decides to deny; that stays "
                "yours.",
                "Settings, Prompt queue, Approvals",
                "approvals",
            ),
            (
                "Multi-device terminals",
                "One session can be attached from several devices at once. Exactly one "
                "connection may write, and the terminal size is arbitrated rather than "
                "fought over, so the phone does not reflow the desktop.",
                "Settings, Terminals",
                "terminal-input",
            ),
            (
                "Launch profiles",
                "Named shell and agent launches: which executable, which arguments, which "
                "working directory. A WSL distro shell is a profile like any other, and "
                "the WSL agent bridge reports whether the distro can actually reach the "
                "daemon rather than assuming it.",
                "Settings, Harnesses",
                "launch-profiles",
            ),
        ],
    ),
    (
        "guide-workbench",
        "Projects and the workbench",
        "A Project binds a folder to everything else: sessions, layout, notes, files, "
        "history, and its own settings.",
        [
            (
                "Projects and Groups",
                "Point a Project at a folder you already work in. Groups are optional "
                "sidebar organization above them. Most per-Project switches live here "
                "rather than globally, which is what lets the control plane be on for one "
                "repository and off for the rest.",
                "Settings, Projects",
                "projects",
            ),
            (
                "Panes, tabs, and splits",
                "A mixed workspace of panes, tabs, and splits, with drag and drop. Desktop "
                "split geometry is durable Project state; the phone renders a single-pane "
                "projection of the same tree rather than a second layout.",
                "the workspace itself",
                "workspace-layout",
            ),
            (
                "Notes, files, and watches",
                "Project-owned notes in a real Markdown editor, a file browser with "
                "editors, ignore rules, and leased non-recursive watches. Notes and files "
                "both open into a pane rather than only into the drawer.",
                "the Notes and Files drawer tabs",
                "project-resources",
            ),
            (
                "The Run menu and imported tasks",
                "Starts an agent, a shell, a worktree session, or a task discovered in the "
                "repository: VS Code tasks, root <code>package.json</code> scripts, and "
                "<code>.swe-mux/actions.toml</code>. <b>An imported task stays inert until "
                "its exact current bytes are approved</b>, and any edit revokes that.",
                "the Run menu, and Settings, Projects",
                "project-actions",
            ),
            (
                "Processes and previews",
                "Whatever your sessions started, and what each one is serving. A local dev "
                "server is proxied through the daemon's own URL, HMR included, so a phone "
                "never needs a raw port. Static documents in the checkout can be previewed "
                "the same way under a sandbox policy.",
                "the Processes drawer tab",
                "processes-and-previews",
            ),
            (
                "Prompt templates",
                "Saved reusable messages. Selecting one <b>inserts and never submits</b>, "
                "which is deliberate: a template is text, not an action.",
                "the Actions drawer tab",
                "prompt-library",
            ),
        ],
    ),
    (
        "guide-queue",
        "Queues, messaging, and landing",
        "Getting work to an agent that is busy, and getting a finished branch onto the "
        "trunk, without either of them happening behind your back.",
        [
            (
                "The prompt queue",
                "Stage ordered messages against a session that is mid-turn. It is durable, "
                "head-of-line, and bound to the conversation rather than to the pane. "
                "<b>Automatic delivery is off by default</b>, and when you do turn it on it "
                "waits for a readiness gate and a stability window rather than for a "
                "binary done signal.",
                "the Queue drawer tab, and Settings, Prompt queue",
                "prompt-queue",
            ),
            (
                "Auto-delivery, when you want it",
                "Per-conversation, with quiet hours, an emergency pause, an expiry on each "
                "item, and a cap on consecutive sends. It is the setting to understand "
                "before turning on, and the defaults are the conservative ones.",
                "Settings, Prompt queue, Auto-delivery",
                "auto-delivery",
            ),
            (
                "Agent-to-agent messages",
                "One session can put a message into another's queue. <b>A non-human "
                "sender's write ends at a human</b> unless the receiving session granted it "
                "standing permission or itself asked the question being answered.",
                "the Fleet Queue, and Settings, Prompt queue, Agent messaging",
                "agent-messaging",
            ),
            (
                "The land queue",
                "Landing a finished worktree branch, one at a time: reconcile with the "
                "trunk, run the verification command whose exact bytes you approved, then "
                "fast-forward only. Git refuses a fast-forward that would lose work, which "
                "is what makes the last step safe for a machine. A conflict or a failed "
                "gate goes back to the branch's own agent as a message.",
                "the Git drawer tab, Map",
                "land-queue",
            ),
            (
                "Scheduled runs",
                "Cron, interval, or one-off. A schedule is a deferred press of a button you "
                "could have pressed yourself, so it goes through the ordinary spawn, "
                "resume, and queue paths and grows no second authority. Definitions stay on "
                "the machine rather than in the repository, so they do not arm themselves "
                "in every clone.",
                "the Schedule drawer tab",
                "scheduled-runs",
            ),
        ],
    ),
    (
        "guide-git",
        "Git and history",
        "What changed, who changed it, and being able to go back to the conversation that "
        "produced it.",
        [
            (
                "The worktree map",
                "One row per worktree, with its files, its changes, and the live sessions "
                "standing in it. Creating and removing worktrees is here, and removal is "
                "declined rather than forced wherever Git itself would refuse.",
                "the Git drawer tab, Map",
                "git",
            ),
            (
                "Commit provenance",
                "Which session and which conversation produced a commit, split into "
                "committer and contributor, with a confidence. It is drawn from "
                "deterministic capture rather than from the agent's account of its own "
                "work, which is what makes it worth reading after a batch landing.",
                "the Git drawer tab, Provenance",
                "tier0-facts",
            ),
            (
                "Cross-vendor history",
                "One search and one resume across every supported harness. Native "
                "transcript directories are reconciled at startup and <b>never moved, "
                "rewritten, or deleted</b>. A Claude transcript is read as the branching "
                "graph it actually is, so a retry does not silently join two conversations.",
                "the History browser",
                "history",
            ),
        ],
    ),
    (
        "guide-control-plane",
        "The control plane",
        "The part that decides what you look at. <b>It is off by default, per Project</b>, "
        "and nothing in it can type, approve, or spawn.",
        [
            (
                "Deterministic facts",
                "Content hashes computed at the tool boundary rather than by reading the "
                "file back, parsed test outcomes, git tree hashes, and write-then-read "
                "lineage. This is the substrate everything else reads, and it is evidence "
                "rather than the agent's own account.",
                "Settings, Automation",
                "tier0-facts",
            ),
            (
                "Model-free detectors",
                "Loops and stalls, claims declared but not verified, documentation debt, "
                "and provenance gaps. No model runs, so they cost nothing per session and "
                "cannot hallucinate a finding.",
                "the Activity drawer tab, Findings",
                "deterministic-consumers",
            ),
            (
                "Attention ranking",
                "Ranked items with <b>a hard daily interrupt budget</b>, four a day by "
                "default. Incidents merge rather than repeat, demotion rules are mined and "
                "expire, and the count of what was suppressed is always shown rather than "
                "hidden.",
                "the Alerts drawer tab",
                "attention-ranking",
            ),
            (
                "Automation observers",
                "Model-backed observers that watch a run and report. They <b>can never "
                "type, approve, spawn, execute a script, or change a Project</b>, which is "
                "structural rather than a policy setting. Every one is gated by a "
                "per-Project opt-in and an install-wide ceiling, and each carries a budget.",
                "the Automation dashboard, Policy",
                "automation",
            ),
            (
                "The scan timeline",
                "A narration of what a session did during a run, with per-run grants and "
                "budgets, so it is readable later without re-reading the transcript. No "
                "scan can be triggered from an agent-facing tool, because a read costs "
                "nothing and a scan spends your budget.",
                "the Activity drawer tab, Timeline",
                "scan-timeline",
            ),
            (
                "The code graph",
                "A tree-sitter structural graph of the repository, behind blast-radius, "
                "navigation, context, and test-gap reads, and behind the per-session change "
                "map.",
                "the Activity drawer tab, Changes",
                "code-graph",
            ),
            (
                "What agents can read back",
                "A per-session MCP server that lets an agent pull from the control plane: "
                "sibling status, prior resolutions, dead ends, provenance, Project notes. "
                "Its writes are bounded to staging a message, drafting a spawn request for "
                "a human, arming a watch, and interrupting or ending a session behind a "
                "per-Project grant.",
                "Settings, Automation",
                "mux-mcp",
            ),
        ],
    ),
    (
        "guide-voice",
        "Voice, alerts, and the assistant",
        "Driving it without a keyboard, and being told when something needs you.",
        [
            (
                "Read aloud",
                "A summarized or verbatim slice of the last turn, spoken by the OS voice "
                "engine, a local model, or an explicitly acknowledged external provider. "
                "One policy in three layers: a master switch, per-session generation, and "
                "a per-device autoplay rule where the focused session plays and every "
                "other one holds its clip rather than talking over you.",
                "Settings, Voice, Read aloud",
                "voice",
            ),
            (
                "Hands-free conversation",
                "Browser capture, on-device voice activity detection, and local "
                "transcription, with configurable wake words and a fixed set of commands. "
                "Spoken navigation addresses Projects and sessions by number. It needs a "
                "secure context, which on a phone means the HTTPS step in the quick start "
                "above.",
                "Settings, Voice, Talk &amp; dictation",
                "voice",
            ),
            (
                "The Mux assistant",
                "Ask for something in words. The model proposes names, deterministic code "
                "resolves and executes them through the paths a button would have used, "
                "and <b>the confirmation floor for a consequential action is not "
                "configurable</b>.",
                "Settings, Voice, Mux assistant",
                "assistant",
            ),
            (
                "Alerts and push",
                "Web push to a phone, with per-device preferences, plus sounds. Device "
                "presence decides which device you are actually at, once, for the whole "
                "application, rather than each feature guessing separately.",
                "Settings, Alerts",
                "notifications",
            ),
        ],
    ),
    (
        "guide-accounts",
        "Accounts, usage, and running it",
        "Where the money and the network boundaries are.",
        [
            (
                "Provider accounts",
                "Save, relabel, reauthenticate, switch, and remove Claude and Codex logins, "
                "with subscription-window polling. Only authentication is copied and "
                "switching is always an explicit act. It is for <b>one person switching "
                "between accounts they own and pay for</b>, not pooling.",
                "Settings, Accounts, Provider accounts",
                "provider-accounts",
            ),
            (
                "Where a model call goes",
                "Every model setting is edited in one place and each feature shows a "
                "read-only row naming the model it resolved to. Changing endpoint is the "
                "operation that touches all of them at once, which is why they are together "
                "rather than beside the features they serve.",
                "Settings, Accounts, Models",
                "usage",
            ),
            (
                "Usage, and the three pots",
                "Agent spend, metered automation spend, and provider quota. <b>They are "
                "never summed</b>, because one is a subscription reconstructed from "
                "transcripts, one is a key billed by the call, and one is not money at all. "
                "Every figure carries the basis it was drawn on.",
                "Settings, Usage",
                "usage",
            ),
            (
                "Budgets",
                "A cap is tokens, dollars, or both, per feature. A dollar cap cannot bind "
                "against a provider that reports no cost, so absent cost is recorded as "
                "unmeasured rather than as zero and totals drawn over it read as a floor.",
                "Settings, Usage",
                "budgets",
            ),
            (
                "Remote access",
                "Loopback plus an optional Tailscale listener carrying the same UI and API. "
                "There is no swe-mux login, so <b>Tailscale policy is the entire access "
                "boundary</b>. Binding <code>0.0.0.0</code>, a LAN interface, port "
                "forwarding, and Funnel are unsupported rather than merely discouraged.",
                "Settings, Remote",
                "remote-access",
            ),
            (
                "The desktop shell and updates",
                "On Windows, a native window, a tray supervisor, login startup, and a "
                "frozen bundle that can rebuild and redeploy itself while live sessions "
                "keep running. The daily release check is one request for a static file and "
                "one switch turns it off; installing an update is a separate act that "
                "verifies a hash before staging anything.",
                "Settings, Diagnostics",
                "desktop-shell",
            ),
        ],
    ),
]


def _agent_block() -> list[str]:
    """The copy-paste line that hands setup to an agent, and the guide behind it.

    Placed above the section nav rather than in a section of its own, because the
    reader it is for has not decided to read anything yet. The guide is served
    from the deploy root as a plain Markdown file, which is what makes it fetchable
    by an agent without a parser.
    """
    return [
        '    <div class="agentbox">',
        '      <div class="relhead"><h2>Have an agent set it up</h2>'
        '<span class="fill"></span></div>',
        '      <p class="prose">Paste this to Claude Code, Codex, or any agent that can '
        "fetch a URL. It reads a guide written for that job: install, first run, the "
        "concepts worth explaining, and what leaves the machine.</p>",
        '      <div class="code"><pre>'
        f"{html.escape(AGENT_PROMPT, quote=False)}</pre></div>",
        f'      <p class="note">The guide is <a href="../{AGENT_GUIDE_PATH}">'
        f"<code>{html.escape(AGENT_GUIDE_URL, quote=False)}</code></a>, and it is plain "
        "Markdown rather than a page, so an agent gets the text and not a layout. "
        "Reading it costs nothing and it will tell your agent to ask before it installs "
        "anything.</p>",
        "    </div>",
    ]


def _quickstart_section() -> list[str]:
    out = ['    <div class="docsec" id="sec-quickstart">']
    out.append(
        '      <div class="head"><span class="n">#</span><h2>Quick starts</h2>'
        '<span class="fill"></span></div>'
    )
    out.append(
        '      <p class="prose">Four tasks, in the order a new install hits them. Every '
        "command here is one that exists today; where a claim is about a default, the "
        "default is the one shipping.</p>"
    )
    for anchor, title, intro, steps, proof in QUICKSTARTS:
        out.append(f'      <div class="rel" id="{anchor}">')
        out.append(
            f'        <div class="relhead"><h2>{html.escape(title, quote=False)}</h2>'
            '<span class="fill"></span></div>'
        )
        out.append(f'        <p class="prose">{intro}</p>')
        out.append('        <ol class="steps">')
        for step in steps:
            out.append(f"          <li><span>{step}</span></li>")
        out.append("        </ol>")
        out.append(f'        <p class="proof"><b>It worked when</b> {proof}</p>')
        out.append("      </div>")
    out.append("    </div>")
    return out


def _feature_sections(anchors: set[str]) -> list[str]:
    out: list[str] = []
    for anchor, title, intro, entries in FEATURE_GUIDE:
        out.append(f'    <div class="docsec" id="{anchor}">')
        out.append(
            f'      <div class="head"><span class="n">#</span>'
            f"<h2>{html.escape(title, quote=False)}</h2><span class=\"fill\"></span></div>"
        )
        out.append(f'      <p class="prose">{intro}</p>')
        out.append('      <ul class="flat">')
        for name, what, where, doc in entries:
            if doc not in anchors:
                raise SystemExit(
                    f"FEATURE_GUIDE entry '{name}' points at docs anchor '{doc}', which "
                    "this page does not carry. Either the document stopped being "
                    "published or the slug moved; see doc_anchor()."
                )
            out.append(
                f"        <li><b>{html.escape(name, quote=False)}</b><span>{what} "
                f'<span class="src">{html.escape(where, quote=False)} '
                f'&middot; <a href="#{doc}">reference</a></span></span></li>'
            )
        out.append("      </ul>")
        out.append("    </div>")
    return out


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

    # Computed before anything renders, because the hand-written feature guide
    # above the index links into it and has to fail the build rather than emit a
    # dead fragment. `doc_anchor` is a pure function of the path, so this is the
    # same set the loop below will produce.
    anchors = {
        doc_anchor((base / rel).resolve().relative_to(ROOT).as_posix())
        for _, _, paths in DOC_SECTIONS
        for rel in paths
    }

    out: list[str] = []
    out.append('<section class="page">\n  <div class="wrap">')
    out.append('    <div class="kick">install it, use it, then read why</div>')
    out.append("    <h1>Documentation</h1>")
    out.append(
        '    <div class="lede"><p>Three things live on this page. <b>Quick starts</b> for '
        "the four tasks a new install hits first. <b>Features and settings</b>, which says "
        "what each part does and which screen its controls are on. And the "
        "<b>reference index</b>, generated from the design documents in the repository "
        "rather than rewritten beside them.</p>"
        "<p>The reference documents are written for whoever maintains a subsystem next. "
        "They state invariants and the decisions already made, which is what you want when "
        "a behaviour surprises you and not what you want in your first ten minutes. For "
        "that, start above and keep <code>mux doctor</code> to hand.</p></div>"
    )

    out.extend(_agent_block())

    out.append('    <nav class="toc" aria-label="Documentation sections">')
    out.append('      <a href="#sec-quickstart">quick starts</a>')
    for anchor, title, _, _ in FEATURE_GUIDE:
        out.append(f'      <a href="#{anchor}">{html.escape(title.lower(), quote=False)}</a>')
    for slug, title, _ in DOC_SECTIONS:
        out.append(
            f'      <a href="#sec-{slug}">reference: '
            f"{html.escape(title.lower(), quote=False)}</a>"
        )
    out.append("    </nav>")

    out.extend(_quickstart_section())
    out.extend(_feature_sections(anchors))

    out.append('    <div class="sub-h">Reference</div>')
    out.append(
        '    <p class="prose">The design documents themselves, in the repository where '
        "they are already public. Each entry is the document's own title and its own "
        "opening line; nothing here is written for this page. Paths under each title are "
        "relative to <code>.docs/</code>, and a bare filename means "
        "<code>.docs/design/features/</code>. The link is the whole path.</p>"
    )

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
            # The hand-written blocks above the index carry their own ids, so the
            # collision check has to cover them too: a duplicate `id` makes the
            # second one unreachable, and `/docs/#<slug>` is a published contract.
            reserved = (
                {f"sec-{s}" for s, _, _ in DOC_SECTIONS}
                | {"sec-quickstart"}
                | {a for a, _, _, _, _ in QUICKSTARTS}
                | {a for a, _, _, _ in FEATURE_GUIDE}
            )
            if anchor in seen or anchor in reserved:
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


def build_compare() -> str:
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


def build_blog() -> str:
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


def build_privacy() -> str:
    return (SITE / "content/privacy.html").read_text(encoding="utf-8").strip()


def build_terms() -> str:
    return (SITE / "content/terms.html").read_text(encoding="utf-8").strip()


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
