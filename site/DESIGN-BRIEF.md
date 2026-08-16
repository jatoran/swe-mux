# swe-mux site: design brief

How the landing page should read, look, and argue.
Read this before editing `index.html`.

## What swe-mux is, in one line

An **ADE** (agentic development environment) *and* an **ACP** (agent control plane).
Both halves are load-bearing and the page must say so above the fold.

- **ADE / the workbench.** Sessions, panes, tabs, notes, files, git, previews, a Run menu. Everything you look at. Table stakes, and every competitor has some of it.
- **ACP / the control plane.** Deterministic evidence, model-free detectors, incident ranking, an interrupt budget, a return path agents can read. Decides *what* you look at.

The connective argument, which is the actual moat: **the control plane is only buildable on top of the workbench.**
You cannot measure a fleet whose terminals, shell profiles, and input telemetry you do not own.
Fan-out estimation, breakpoint delivery, resumption lag, and race-free content hashes all require owning the PTY.

## Differentiators, ranked by how hard they are to copy

1. **Ground truth over self-report.** Everyone else's "what happened" is the transcript, which is the agent's story. We have a deterministic fact substrate: content hashes taken at the adapter boundary, parsed test outcomes, git tree hashes, write-then-read lineage.
2. **A theory of interruption.** Four channels split by cost-to-resolve, incident merging, a hard daily budget, demotion rules mined from behavior and expiring at 14 days, suppressed counts always shown. Competitors give you a grid of tiles and let you decide.
3. **The inverse arrow.** Agents pull from the control plane through MCP: sibling status, prior resolutions, dead ends, provenance. Not an orchestrator pushing commands down to workers.
4. **A refusal to actuate.** Observers can never type, approve, or spawn. Structurally, not by policy.
5. **Mobile as a first-class surface.** Not a companion app, not a status board. The full workspace.
6. **Long-lived sessions.** Processes you live with for weeks, not tickets that open and close.

Do **not** lead with worktree parallelism, diff review, or agent count. That is the commodity axis, and it is their axis.

## Mobile is top-of-page

Mobile gets its own section immediately after the hero, before the workbench and before the control plane.
It is not a responsive-CSS footnote; it is a product position.
The argument is structural: one session attached from several devices, exactly one writer, one arbitrated size, and the phone renders a projection of the same workspace tree rather than a second layout.

## Voice and language

- **Direct, factual, load-bearing.** Every sentence should carry a fact, a mechanism, or a boundary. If a sentence could appear on any competitor's page, cut it.
- **Short.** A feature is a title plus at most two sentences. If it needs a third, it is two features or it belongs in the flat list.
- **No marketing register.** Banned: seamless, powerful, effortless, unleash, supercharge, revolutionize, game-changing, blazing fast, "the future of".
- **No LLM tells.** No "it's not just X, it's Y" as a reflex, no rule-of-three padding, no rhetorical questions, no exclamation marks, no "in today's world".
- **Never use the em dash.** Use a plain dash with spaces, or restructure the sentence.
- **Name the mechanism, not the benefit.** "Content hashes computed at the adapter boundary, never by reading the file back" beats "reliable change tracking".
- **State limits plainly and early.** Windows only, single user, not an editor, not a task board, useless below three concurrent agents. Honest limits buy credibility for the claims.
- **Cite when a number is doing work.** AgentLens on trajectory quality, MAST on failure taxonomy, Olsen and Goodrich on fan-out. One clause, no footnote apparatus.
- **Second person, present tense.** "You approve the file's exact bytes", not "users are prompted to approve".

## Visual language

TUI, because the product is a terminal multiplexer and the app already looks like this.
Match the app rather than inventing a brand.

- **Monospace everywhere.** Body copy included. That is the look.
- **Dark only.** Palette sampled from the running UI: background `#101119`, panel `#171821`, hairlines `#262939`, body text `#c6cee6`, green `#9ece6a`, cyan `#73daca`, orange `#e0af68`, red `#f7768e`.
- **Green is the app accent, cyan marks the ADE half, orange marks the ACP half.** Keep that mapping consistent.
- **Square corners, 1px hairlines, no shadows, no gradients, no rounded cards.**
- **Grid seams instead of gaps.** Card grids use a 1px background line showing through, which reads as a TUI panel grid.
- **Structure over decoration.** Numbered section rules, `[01]` style indices, `▸` and `[x]` markers, a blinking block cursor after the headline. No icons, no illustrations, no logos.
- **Self-contained.** One file, inline CSS, no external fonts, no CDN, no analytics.
- **Mobile must not overflow.** Verify `document.documentElement.scrollWidth === clientWidth` at 390px after every change. Grid tracks need `minmax(min(Npx, 100%), 1fr)`.

## Screenshot rules

- **Every image surface is a desktop and mobile pair.** A desktop crop with no mobile counterpart is incomplete.
- **Show the feature, not the application.** Crop to the panel, the list, the menu, the badge row. A full-window screenshot of the whole UI teaches nothing at page width and is the single most common mistake here.
- **Desktop crops are roughly 3:2. Mobile crops are roughly 9:16.** Not full phone screens.
- **The crop must contain the claim.** The attention inbox shot has to show the budget line and a suppressed item with its reason, because that is the argument. A shot of a pretty empty panel proves nothing.
- **Real application, never mockups**, and say so on the page.
- **Scrub before publishing.** Live screenshots carry real project names, file paths, branch names, and transcript text.

## Structure

1. Hero: headline, three sentences, spec strip, the ADE/ACP split, the connective argument.
2. Mobile.
3. The workbench (ADE cards).
4. The control plane (ACP cards).
5. Also in the box (flat list, one line each).
6. What it will not do (enforced boundaries).
7. Not for you if.
8. Install.

Sections 6 and 7 are not filler.
The boundaries are the credibility half of the control-plane claim, and the disqualifiers are what make the rest believable.
