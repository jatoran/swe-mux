"""Reads the top-reacted open `Ideas` discussions into `content/ideas.json`.

    python site/tools/ideas.py            # write the data file
    python site/tools/ideas.py --dry-run  # print what it would write, touch nothing

Feature requests for swe-mux are GitHub Discussions in the `Ideas` category and a
thumbs-up reaction on one is a vote. `.github/workflows/ideas.yml` runs this once
a day with the built-in `GITHUB_TOKEN`, then runs `tools/build.py` to redraw the
roadmap page from what this wrote.

**This script writes data and never markup.** Everything about how the block
reads - its copy, its ordering inside the page, the honesty line above it - lives
in `tools/build.py` and in `content/roadmap.html`, which are edited through the
ordinary site gates. A scheduled job that could write HTML onto a published page
is a second author for that page, and the one that runs unattended is the one
nobody reviews.

Three behaviours are deliberate and each is the answer to a specific way this
could lie on a public site:

- **It fails loudly rather than publishing an empty list.** Any API error, any
  malformed response, and any missing `Ideas` category exits non-zero without
  touching the file. A section that quietly empties itself is indistinguishable
  from nobody wanting anything, and it is the one failure a reader would believe.
- **Discussions being switched off is not that failure.** It is a repository
  setting the operator has not made yet, so it warns, writes nothing, and exits
  zero. Failing daily on a state nobody has acted on trains people to ignore a
  red workflow, which is how the real failure gets missed.
- **The scan is bounded and the bound is fatal.** Ranking has to see every open
  request to be a ranking at all, so running past `MAX_PAGES` raises instead of
  ranking a prefix. On a repository this size it cannot fire; if it ever does,
  the fix is a decision, not a silently truncated list.

The vote floor and the row count are `tools/build.py`'s, imported rather than
repeated. Filtering here as well keeps a sub-threshold request out of the
committed data entirely, so an ordinary reaction on an unpopular idea does not
produce a daily commit to a file whose rendering never changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import MAX_IDEAS, MIN_VOTES, REPO, SITE  # noqa: E402

API = "https://api.github.com/graphql"
CATEGORY = "Ideas"
DATA = SITE / "content/ideas.json"

# 100 is the API's page maximum, and 20 pages is 2000 open requests in one
# category. See the module docstring for why overrunning it is fatal.
PAGE_SIZE = 100
MAX_PAGES = 20

# A scheduled job that reddens on a transient 502 teaches its owner to ignore it.
# Retries cover exactly that: a 5xx or a transport error, never a 4xx, which is a
# real answer that will be identical on the next attempt.
ATTEMPTS = 3
BACKOFF = 4

CATEGORIES_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    hasDiscussionsEnabled
    discussionCategories(first: 50) {
      nodes { id name slug }
    }
  }
}
"""

# `states: [OPEN]` rather than a `closed` filter here: a closed request is either
# built, refused, or a duplicate, and each of those has a better home than a
# ranked list of what to look at next. There is no reaction-count ordering in the
# discussions connection, so the ranking is done over the full open set below.
DISCUSSIONS_QUERY = """
query($owner: String!, $name: String!, $category: ID!, $size: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    discussions(
      first: $size
      after: $cursor
      categoryId: $category
      states: [OPEN]
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        url
        reactions(content: THUMBS_UP) { totalCount }
      }
    }
  }
}
"""


class Failure(SystemExit):
    """Anything that must stop the run without writing. Exits non-zero.

    Constructed with the exit code rather than the message, because `annotate`
    has already printed it: `SystemExit("...")` would write the same sentence a
    second time on the way out.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        annotate("error", message)
        super().__init__(1)


def annotate(level: str, message: str) -> None:
    """A GitHub Actions annotation when running there, a plain line otherwise.

    Written on one line with no newline in the message: Actions terminates a
    workflow command at the first newline, so a multi-line annotation silently
    loses everything after the first line.
    """
    text = " ".join(message.split())
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level} title=ideas::{text}", flush=True)
    else:
        print(f"{level}: {text}", flush=True)


def summarize(message: str) -> None:
    """One line onto the job summary, where a run's outcome is read without logs."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(message.rstrip() + "\n")


def repository() -> tuple[str, str]:
    """The `owner/name` this runs against.

    `GITHUB_REPOSITORY` first so a fork's scheduled run reads its own
    discussions rather than this account's, falling back to the constant every
    other URL on the site is built from.
    """
    slug = os.environ.get("GITHUB_REPOSITORY") or REPO.removeprefix("https://github.com/")
    owner, _, name = slug.partition("/")
    if not owner or not name:
        raise Failure(f"cannot read an owner/name out of {slug!r}")
    return owner, name


def graphql(token: str, query: str, **variables: object) -> dict:
    """One GraphQL request. Raises `Failure` on anything that is not a clean answer."""
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "swe-mux-site-ideas",
        },
    )
    payload: dict = {}
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code < 500 or attempt == ATTEMPTS:
                raise Failure(f"GitHub API returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == ATTEMPTS:
                raise Failure(f"GitHub API request failed: {exc}") from exc
        time.sleep(BACKOFF * attempt)

    # A GraphQL error is an HTTP 200 with an `errors` array, which is exactly the
    # shape that produces an empty section if it is not checked for.
    if errors := payload.get("errors"):
        joined = "; ".join(str(e.get("message", e)) for e in errors)
        raise Failure(f"GitHub API returned errors: {joined}")
    data = payload.get("data") or {}
    if not data.get("repository"):
        raise Failure("GitHub API returned no repository; check the token's access")
    return data["repository"]


def category_id(token: str, owner: str, name: str) -> str | None:
    """The `Ideas` category id, or `None` when Discussions is not enabled yet."""
    repo = graphql(token, CATEGORIES_QUERY, owner=owner, name=name)
    if not repo.get("hasDiscussionsEnabled"):
        annotate(
            "warning",
            f"Discussions is not enabled on {owner}/{name}, so there is nothing to rank. "
            "Enable it in Settings and add an Ideas category; site/README.md section 12 "
            "has the steps. Nothing was written and this is not a failure.",
        )
        summarize(f"`ideas`: Discussions is not enabled on {owner}/{name}; nothing written.")
        return None
    nodes = repo.get("discussionCategories", {}).get("nodes") or []
    for node in nodes:
        if node.get("name") == CATEGORY or node.get("slug") == CATEGORY.lower():
            return str(node["id"])
    known = ", ".join(sorted(str(n.get("name")) for n in nodes)) or "none"
    raise Failure(
        f"{owner}/{name} has Discussions enabled but no {CATEGORY!r} category. "
        f"Categories found: {known}. The roadmap page links to it, so this is a "
        "misconfiguration rather than an empty list."
    )


def fetch(token: str, owner: str, name: str, category: str) -> list[dict]:
    """Every open request in the category, with its vote count."""
    out: list[dict] = []
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        repo = graphql(
            token,
            DISCUSSIONS_QUERY,
            owner=owner,
            name=name,
            category=category,
            size=PAGE_SIZE,
            cursor=cursor,
        )
        page = repo.get("discussions") or {}
        for node in page.get("nodes") or []:
            reactions = node.get("reactions") or {}
            if node.get("title") is None or node.get("url") is None:
                raise Failure(f"a discussion came back without a title or url: {node!r}")
            out.append(
                {
                    "number": int(node["number"]),
                    "title": str(node["title"]),
                    "url": str(node["url"]),
                    "votes": int(reactions.get("totalCount", 0)),
                }
            )
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            return out
        cursor = info.get("endCursor")
    raise Failure(
        f"more than {MAX_PAGES * PAGE_SIZE} open {CATEGORY} discussions. Ranking a prefix "
        "of them would publish a wrong 'most requested' list that looks right, so this "
        "stops instead; raise MAX_PAGES deliberately."
    )


def rank(items: list[dict]) -> list[dict]:
    """The rows the page may draw: over the floor, best first, capped.

    Ties break on the title so the file is a function of the data rather than of
    the order the API happened to return, which is what keeps an unchanged list
    from producing a commit.
    """
    over = [i for i in items if i["votes"] >= MIN_VOTES]
    over.sort(key=lambda i: (-i["votes"], i["title"].lower()))
    return over[:MAX_IDEAS]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="print, do not write")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise Failure("GITHUB_TOKEN is not set; this reads the API and cannot run without it")

    owner, name = repository()
    category = category_id(token, owner, name)
    if category is None:
        return 0

    items = fetch(token, owner, name, category)
    ranked = rank(items)
    document = {
        "comment": (
            "Generated by site/tools/ideas.py from GitHub Discussions. Do not hand-edit; "
            "site/tools/build.py renders it onto the roadmap page and re-applies the vote "
            "floor."
        ),
        "category": CATEGORY,
        "url": f"{REPO}/discussions/categories/{CATEGORY.lower()}",
        "min_votes": MIN_VOTES,
        "items": ranked,
    }
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"

    print(
        f"{len(items)} open {CATEGORY} discussions, "
        f"{len(ranked)} at or above {MIN_VOTES} votes"
    )
    for item in ranked:
        print(f"  {item['votes']:>4}  #{item['number']}  {item['title']}")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    current = DATA.read_text(encoding="utf-8") if DATA.exists() else None
    if current == text:
        print("\ncontent/ideas.json up to date")
        summarize(f"`ideas`: unchanged, {len(ranked)} request(s) at or above {MIN_VOTES} votes.")
        return 0
    # newline="" so the LF this builds stays LF on every host, the same reason
    # `build.py` writes its pages that way.
    with DATA.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print("\ncontent/ideas.json written")
    summarize(f"`ideas`: rewritten, {len(ranked)} request(s) at or above {MIN_VOTES} votes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
