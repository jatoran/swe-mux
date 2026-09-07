# swe-mux website

The public site lives in `site/` and is deployed as static files.
The landing page is hand-authored; sibling pages and the demo are committed build output.
This repository does not use a hosted application backend for the public demo.

## Message

**Run your coding agents together.**
**Know which ones need you.**

Keep Claude Code, Codex and other coding agents side by side, with live status, flexible panes and messages between sessions.
Work from your desktop or phone.
Dictate prompts, hear replies, and ask the assistant what needs attention.

Lead with the daily workflow, then explain the mechanism that supports a claim.
The first feature sections are status and alerts, mobile access, and voice.
Familiar input, pane arrangement, agent communication, approved landing and history establish the rest of the product.
Detailed configuration belongs in `/docs/`.

## Source map

| Surface | Source | Output |
|---|---|---|
| Homepage | `index.html` | Same file |
| Shared style and chrome | `index.html`, `tools/build.py` | All sibling pages |
| User documentation | `tools/docs_content.py` | `docs/`, including search index |
| Comparison | `content/compare.html`, `content/compare.json` | `compare/` |
| Roadmap | `content/roadmap.html`, `content/ideas.json` | `roadmap/` |
| Privacy and terms | `content/privacy.html`, `content/terms.html` | `privacy/`, `terms/` |
| Plugins | `content/plugins.html`, `content/plugins.js` | `plugins/` |
| Demo | `../frontend/src/demo/` | `demo/` |
| Frontpage feature images | Focused crops from the demo | `img/frontpage/` |
| Documentation examples | Existing demo stills | `img/showcase-*.webp` |

`tools/build.py` reads the landing page's style block rather than maintaining another theme.
Regenerate sibling pages after changing that style block or their content sources.
Do not hand-edit generated pages or hashed demo assets.

## Demo

The demo runs the production frontend against an in-page simulated daemon.
Sessions, process activity, model answers and verification results are invented.
No real agent process or microphone recognition runs in it.
The visible disclosure must remain near the embed.

The desktop toolbar provides keyboard presets and shows useful shortcuts from the resolved map.
The standalone desktop demo has the same control.
Ordinary shortcuts work in the focused embedded frame; browser and OS shortcuts still apply.
“Open demo” opens a standalone page and does not request Fullscreen or Keyboard Lock.
On phones, the embedded terminal suppresses the soft keyboard; the standalone view demonstrates normal terminal keyboard behavior.

Walkthroughs play themselves and offer Pause, Play, Next, Replay and Stop.
Paused playback finishes an action already in progress and waits before the next one.
Next advances one beat while staying paused.
Taking over the interface offers a resumable walkthrough without repeating its previous action.
Full architecture and capture contracts: [DEMO.md](DEMO.md).

## Language

- State what the visitor can do before describing internals.
- Use short sentences and concrete verbs.
- Keep limits beside the claims they qualify.
- Distinguish workspace keymaps from normalized agent text editing.
- Distinguish image attachments from shared in-app text clipboard history.
- Distinguish dictation, read-aloud and the model-backed assistant.
- Avoid “any harness” where support depends on a recognized composer or image-capable CLI.
- Avoid “no tradeoffs,” “sessions never die,” and claims of perfect status or verification.
- Use “agent CLI” in introductory prose; technical documents may use “harness.”
- No em dashes, hype vocabulary, jokes or stock motivational copy.

The claim matrix and channel drafts are operator-private, in `../.private/marketing/` (gitignored; primary checkout only).
The operational copy checklist is [tools/COPY_CHECKLIST.md](tools/COPY_CHECKLIST.md).

## Visuals and media

Use the app's terminal visual language: monospace headings, system-sans body text, theme tokens and simple borders.
Both light and dark themes must remain readable.
Do not introduce remote fonts, analytics or third-party embeds.

Below the interactive demo, the frontpage uses only static WebP images cropped to the relevant feature.
Do not use full-workspace screenshots for a feature that fits in a panel, message or composer.
Captures exclude tutorial cards, controls, callouts and transient notifications; the interactive demo keeps its guide.
The landing examples separately show the verification gate and the failure returned to the agent.
Feature examples remain visible without opening an expander or starting playback.
`frontpage.css` holds homepage-only layout rules so its changes do not regenerate documentation pages.
Audio features are described in prose; there are no audio samples or sound-preview buttons.

The phone still focuses on its question, composer and touch controls.
Capture the phone layout directly; never crop a desktop recording to imitate it.

Recapture with `node site/tools/capture-frontpage.mjs`, optionally followed by image names from `img/frontpage/manifest.json`.
The script requires ffmpeg and the frontend's Playwright dependencies, archives previous output in `.trash/`, and updates homepage image revisions and dimensions.
The manifest records each scenario, crop and file digest; `node site/tools/check-media.mjs` checks the delivered images and responsive page.

## Build and verify

Run from the repository root unless noted:

```text
npm --prefix frontend run build:demo
python site/tools/build.py
python site/tools/build.py --check
python site/tools/contrast.py
python site/tools/check_changelog.py
node site/tools/check.mjs
```

Before replacing demo build output or captures, move the previous output into the project's `.trash/` directory.
The full worktree gate also checks generated page staleness and scenario menu agreement.
Use headless Playwright for keyboard, playback, viewport and media checks.
Capture and preview servers use an ephemeral loopback port, never the live daemon's port or data directory.
Worktrees are for editing and verification; do not start the product daemon or redeploy from one.

## Publication

Deployments copy `site/` verbatim.
A source edit without its regenerated page or demo bundle publishes stale content.
Compare deployed content with the intended bundle after an authorized deployment.
Committing a worktree does not authorize landing, pushing or deployment.

## Video delivery

`worker/media.mjs` implements single byte ranges for MP4 and WebM assets because the static-assets endpoint returned full 200 responses to Range requests during the 2026-09-06 check.
Only the video paths and the existing update-check counter run through the Worker.
Video delivery records no visitor data and never increments the counter.
Ranges stream without buffering the whole file; matching validators return 206 and unsatisfiable ranges return 416.
Cloudflare's `FixedLengthStream` preserves an accurate Content-Length for partial responses.
The local capture server uses the same response implementation so headless playback checks exercise it.
`worker/test/media.test.mjs` and `tests/test_site_video_ranges.py` cover the protocol without binding a port.
The showcase exporter adds content revisions to media URLs, including the README image, and the docs generator does the same for its examples.
