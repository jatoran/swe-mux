# Shipping a desktop app in 2026 without running a server

*Post-launch engineering post. The angle: infrastructure minimalism as a feature, not a limitation.*

---

swe-mux has users, releases, an update check, and auto-update for a frozen desktop bundle.
Servers I operate: zero.

Be precise about what that claims, because the loose version of this sentence is wrong and gets corrected in public.
swe-mux *is* a local HTTP daemon - that is the whole architecture, and it is running on your machine right now if you use it.
What does not exist is a backend or relay **this project operates**: nothing I run sits between you and anything, and there is no path by which the project learns anything about you.
"Zero servers" is a fun line and a false one. "No vendor-operated backend" is the true one and is just as good.

This was a design decision, not a budget one, and it turned out to cost nothing.

## The update loop

The entire distribution stack is static files:

- **Release CI writes `version.json`** - latest version, artifact URLs, SHA-256 hashes, changelog pointer - into the static site (GitHub Pages, same repo as the code).
- **The app polls that file** about once a day, compares versions, and shows a banner.
  Nothing downloads without an explicit click.
  Fallback endpoint: the GitHub Releases API, whose unauthenticated rate limit is laughably sufficient for a daily check.
- **Artifacts live on GitHub Releases.** Free hosting, unlimited bandwidth, download counts available if I'm curious.
- **The updater reuses the staged-swap machinery** the app already had for development redeploys: download, verify the hash, stage next to the running install, swap, health-check, roll back on failure.
  Live agent sessions ride through the update on the PTY supervisor, the same mechanism as every other restart.
  The interesting consequence is the refusal: an update that would need a *new* supervisor ends every live session by construction, so the updater declines to install it rather than doing that quietly. A distribution stack made of static files still has to know which of its own updates are destructive.
- **The second channel is PyPI.** Developers `uv tool install swe-mux` and upgrades are `uv tool upgrade`. Zero infrastructure by definition.

## What a server would have bought

I made the list before deciding:

- Crash telemetry - don't want it; users can attach a diagnostic bundle to an issue, which is consent-shaped.
- Forced updates and kill switches - actively don't want them; those are features against the user.
- Download analytics - GitHub's release counts and the static host's aggregate stats are plenty.
- License keys - it's Apache 2.0.

Every item was either unwanted or already covered by static files.
The remaining argument for a server is habit.

## The part that does cost money

Windows code signing.
The installer exists and is unsigned, which means a SmartScreen warning on first run and the occasional antivirus false positive.
That is the real distribution tax on indie desktop software, and it is the one line item in this whole stack that hosting was never going to be.
[verify: state what was actually chosen - Trusted Signing / OV cert / documented-unsigned - and what it cost]

## The principle

Every server is a liability with a monthly bill: something to secure, something to break at 3am, something whose disappearance bricks the product.
A tool built to outlive its maintainer's attention should depend on the smallest possible set of things that need maintaining.
Static files and a git forge is about as small as that set gets.

swe-mux: github.com/jatoran/swe-mux - it runs on your own machine, and there is no backend I operate anywhere in it, including in how it updates itself.
