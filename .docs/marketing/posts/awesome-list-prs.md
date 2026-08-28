# Awesome-list submissions

Four lists, and **only two of them take a pull request.**
Rechecked against each list's own repository on 2026-08-28; the earlier version of this file described a PR for all four, which was wrong for two.

Re-read each list's rules the week of submission and match its entry format exactly - alphabetical position, the punctuation the list uses, and no adjectives it does not use for other entries.
Where a PR is the route, the body is two sentences and not a pitch.

Ranking and rationale: [`../GTM_ROADMAP.md`](../GTM_ROADMAP.md) § Awesome lists and directories.

---

## 1. andyrewlee/awesome-agent-orchestrators

The best fit of the four: the exact category, curated rather than exhaustive, actively maintained, 191 entries and no swe-mux among them (checked 2026-08-28).
Nearly every neighbour swe-mux is compared against is already on it - herdr, claude-squad, cmux, Orca, vibe-kanban.

- **Mechanism:** pull request against `README.md`. The repository contains only `README.md`, with no `CONTRIBUTING.md` and no issue forms.
- **Section:** `Parallel Coding Agents - Desktop & Web`, which the list defines as "the same parallel-sessions workflow as a desktop app or browser/mobile dashboard, with diff review and merge". Alphabetical, so between `Solo` and `Tide`-adjacent neighbours depending on the list at the time - place by the current ordering rather than by this note.
- **Format:** `- [name](url) - Sentence describing what it does. Optional second sentence naming the distinguishing property. Trailing list of harnesses.` No adjectives, no sales language, no emoji.
- **Timing:** any time after Stage 3. There is no eligibility floor.

Entry:

> - [swe-mux](https://github.com/jatoran/swe-mux) - Browser and phone control plane whose PTY supervisor is a separate process, so sessions survive daemon restarts and full app redeploys. Adds one status vocabulary across harnesses, a prompt queue, commit-level provenance, and a land queue that reconciles each finished worktree branch, runs a human-approved verification command, and fast-forwards trunk one branch at a time. Windows-first; Claude Code, Codex, opencode, OMP, Pi.

---

## 2. hesreallyhim/awesome-claude-code

By far the largest audience of people who run the primary harness.

- **Mechanism: an issue form in the GitHub web UI, not a pull request.** The `CONTRIBUTING.md` is explicit: "ALL RECOMMENDATIONS MUST BE MADE USING THE WEB UI ISSUE FORM TEMPLATE, OR YOU RISK BEING RESTRICTED FROM INTERACTING WITH THIS REPOSITORY TEMPORARILY", and separately, "It is **not** possible to submit a resource recommendation using the `gh` CLI." The form is [`recommend-resource.yml`](https://github.com/hesreallyhim/awesome-claude-code/issues/new?template=recommend-resource.yml).
- **Eligibility floor:** at least 14 days since the first commit on the default branch **and** continuing commits after day one, **or** at least 100 stars. The public repository's first commit is 2026-08-16, so the 14-day floor clears on **2026-08-30**. Submitting earlier is closed automatically.
- **One resource per submission.** Do not bundle.
- **Section:** `Agent Orchestration` is the primary fit. `Alternative Clients` and `Remote Control, Notifications & Voice I/O` are both defensible; the maintainer decides, so suggest one and do not argue it.
- **Style, quoted from the contributing guide:** "Resource descriptions should be written as _descriptions_ - not a sales pitch. Don't address the reader... state what the software does. Keep it formatted to one line. Don't use any emojis." Existing entries run two to three sentences with the second naming what makes the entry notable, and the badges are generated rather than written.
- **Timing:** after the soft launch, not before. The maintainer states the position directly: getting on the list is a poor promotional strategy and a good consequence of already having users, and recommendations are reviewed best-effort with no guarantee of a response.
- **Licence:** the bot discovers it from the repository. `LICENSE` is a standard Apache-2.0 file, so this should resolve without intervention.

Entry text for the form:

> Local daemon and web UI that runs many Claude Code sessions as one fleet, with a separate supervisor process owning the pseudoterminals so sessions survive daemon restarts and full app redeploys with scrollback intact. Status per session is read from Claude Code's hooks, the transcript, the PTY, and the CLI's own state into one vocabulary, and every transition is kept in a durable ledger, so notifications fire on "needs a human" rather than on terminal activity. Agents work in parallel git worktrees and a land queue reconciles each finished branch, runs a verification command whose exact bytes a human approved, and fast-forwards trunk one branch at a time; an agent cannot approve the gate its own land runs. Fully local, with an installable phone client over Tailscale and on-device speech-to-text.

Trim to the list's prevailing length at submission time if entries have got shorter.

---

## 3. awesome-selfhosted/awesome-selfhosted

Enormous, legitimate fit, and the slowest of the four.

- **Mechanism:** a pull request against **[awesome-selfhosted/awesome-selfhosted-data](https://github.com/awesome-selfhosted/awesome-selfhosted-data)**, never against `awesome-selfhosted` itself. That repository's pull request template says only: "Please do not submit pull requests in this repository. Use https://github.com/awesome-selfhosted/awesome-selfhosted-data instead."
- **Form of the change:** create `software/swe-mux.yml` in the data repository, based on the template in `.github/ISSUE_TEMPLATE/addition.md`. Kebab-case filename. Remove comments and unused optional fields. Commit message `add swe-mux`.
- **Curation rules that apply later, not at submission:** software with no development activity for 6-12 months may be removed, as may non-working or unmaintained software. This is a list that expects a release history.
- **Description rules, quoted:** avoid redundant terms such as _open-source_, _free_, _self-hosted_, "as their presence on awesome-selfhosted already implies this". Prefer shorter forms - `Minimalist text adventure game` over `A minimalist text adventure game`. If presented as an alternative to another product, add `(alternative to $PRODUCT1, $PRODUCT2)` at the end.
- **A rule to read twice:** "Machine/LLM-generated contributions, that do not respect project guidelines are not allowed and will result in a ban." The submission must be read and edited by a human before it is opened, and it must obey the format exactly.
- **Category:** in single-page mode software appears only under the **first** category in its `tags` list, so the first tag is the one that decides where anyone sees it. `Software Development - Tools` or the nearest current equivalent; read `tags/` at submission time.
- **Timing:** post-launch, after at least one further release.

Description field, obeying the no-redundant-terms rule:

> Control plane for AI coding-agent CLIs: persistent terminal sessions owned by a separate supervisor process, per-session status detection, prompt queues, git worktree parallelism with a verification-gated merge queue, and a mobile PWA client (alternative to Conductor, Orca).

---

## 4. e2b-dev/awesome-ai-agents

Broadest reach and the weakest fit.
It is a list of AI autonomous agents; swe-mux is not one, it is the layer above them.
Submit, expect nothing, and do not build any part of the plan on it.

- **Mechanism: a Google Form**, linked from the README as "Submit new product here". Not a pull request, despite the repository being a README of entries.
- **Format:** entries are not one-liners. Each is an H2 section carrying `Category`, `Description`, sometimes `Features` and `Stack`, then a `Links` list. Match the shape of neighbouring open-source entries.
- **Timing:** post-launch, whenever there is a spare half hour.

Description field:

> swe-mux is a self-hosted control plane for coding-agent CLIs rather than an agent itself. A supervisor process owns the agents' pseudoterminals so sessions survive daemon restarts and app updates; the daemon adds one cross-vendor status vocabulary, prompt queues with gated delivery, commit-level provenance, and a land queue that merges parallel worktree branches behind a human-approved verification command. Runs Claude Code, Codex, opencode and other CLIs unchanged, entirely locally, with a phone client and on-device speech-to-text.

---

## Deliberately not submitted

**The awesome-MCP-server lists** (`punkpeye/awesome-mcp-servers` and siblings).
swe-mux exposes an MCP surface, but it is a per-session, token-gated surface the daemon offers to agents it is already running - not a server anyone installs standalone.
Listing it would be a category error, would likely be rejected or miscategorized, and would spend credibility on a list that cannot send a single relevant user.
Recorded so this is not re-derived.

## Pull request body, for the two lists that take one

> Adds swe-mux, an Apache-2.0 [category phrase matching the list].
> Disclosure: I'm the author.
