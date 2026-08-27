# Marketing drafts

Every launch venue from `ROADMAP.md` Phase 11 has a draft here.
These are working drafts in the operator's voice: blunt, concrete, no corporate filler, no hype vocabulary.
Numbers marked `[verify]` must be re-measured against the release build before anything ships.
Every `[gif]` / `[video]` marker maps to an asset from the Phase 11 demo-environment work.

## Positioning line

Primary, used verbatim everywhere:

> **swe-mux - mission control for your coding-agent fleet.**

Alternates, if the primary tests badly:

- "Run a fleet of coding agents. Keep every session alive. Land their work safely."
- "The control plane for parallel coding agents - local, durable, phone-reachable."

The differentiation, stated the same way in every draft: sessions survive daemon restarts and app rebuilds (a separate supervisor owns the PTYs), a land queue merges agent branches behind a verification gate, full provenance of who wrote what, and the whole fleet is operable from a phone - including by voice.
Local-only: no cloud, no accounts, no telemetry.

## Inventory

| File | Venue | When |
|---|---|---|
| `blog/01-launch.md` | swemux.dev/blog + dev.to + Hashnode | Launch day |
| `blog/02-session-preserving-runtime.md` | Blog; submit to HN + lobste.rs + r/programming | Week 1-2 |
| `blog/03-land-queue.md` | Blog; submit to HN + r/programming | Week 2-3 |
| `blog/04-status-detection.md` | Blog; submit to HN | Week 3-4 |
| `blog/05-phone-fleet.md` | Blog; r/selfhosted crosslink | Week 4+ |
| `blog/06-no-server-updates.md` | Blog | Post-launch |
| `posts/show-hn.md` | Hacker News | After soft launch |
| `posts/product-hunt.md` | Product Hunt | After Show HN |
| `posts/reddit-soft-launch.md` | r/ClaudeAI, r/ChatGPTCoding, r/LocalLLaMA | First, before HN |
| `posts/reddit-tier2.md` | r/programming, r/commandline, r/selfhosted, r/opensource, r/vibecoding, r/codex | Staggered |
| `posts/discord-claude-developers.md` | Claude Developers Discord | With soft launch |
| `posts/lobsters.md` | lobste.rs (invite required) | With blog 02 |
| `posts/x-thread.md` | X launch thread + clip cadence | Show HN day |
| `posts/bluesky-linkedin.md` | Bluesky, LinkedIn | Show HN day |
| `posts/awesome-list-prs.md` | The four awesome lists | Post-launch |
| `posts/newsletter-submissions.md` | Console.dev, TLDR AI, Changelog News | Week 1 |
| `posts/youtube-outreach.md` | AI-tooling channels | Week 1+ |
| `posts/alternativeto.md` | AlternativeTo, selfh.st | Post-launch |

## Rules

- One positioning line, verbatim, everywhere.
- Engineering posts get submitted to aggregators; the announcement does not (except Show HN, which is the one sanctioned announcement).
- Never post the same text to two subreddits; each draft below is already differentiated by audience.
- Every claim in a draft must be true of the shipped artifact on the day it posts, not of the roadmap.
