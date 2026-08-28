---
title: The sentence a reader decides on
date: 2026-08-28
summary: One sentence, shown under the title on the index and in nothing else. No trailing "read more".
---

This file is the shape a post takes, and it is never published: `build_blog()` in
`site/tools/build.py` skips every filename beginning with an underscore.
Copy it to `content/blog/<slug>.md` and the slug becomes the post's permalink,
`https://swemux.dev/blog/#<slug>`.

## The header

Four keys, all required, between the two `---` lines, and `build.py` fails on a post
missing any of them rather than rendering a post with no date.

- `title` - the post's own H1. Do not repeat it as a heading in the body.
- `date` - `YYYY-MM-DD`. It is what the index sorts on, newest first.
- `summary` - one sentence, shown beside the title.
- Nothing else is read. A key this list does not name is an error, so a typo in `date`
  cannot silently become an untitled, undated post.

## What the body may contain

The Markdown reader this site uses is deliberately small: headings, bullet lists,
tables, paragraphs, and inline code, links, bold, and emphasis.
It drops nothing it does not recognize, so an unsupported construct arrives on the page
as prose rather than disappearing from it, which is the failure mode worth having.

There are no images.
Every capture this project has taken of a live machine carried project names and paths,
so the site ships deliberate placeholders instead, and a post is not the place to make
an exception.

## What a post is for

The same rule the rest of this site holds: every sentence carries a fact, a mechanism, or
a boundary.
A post that could have been written by any of the neighbouring projects is not worth
publishing.
What is worth publishing is a measurement that contradicted the obvious hypothesis, an
incident with its root cause, or a decision and the thing it gave up.

Never use an em dash.
Use a plain dash.
