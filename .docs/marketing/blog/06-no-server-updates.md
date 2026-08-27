# Shipping a desktop app in 2026 without running a server

*Post-launch engineering post. The angle: infrastructure minimalism as a feature, not a limitation.*

---

swe-mux has users, releases, an update check, and auto-update for a frozen desktop bundle.
Total server count: zero.
Not "serverless" as in someone else's servers with extra billing - zero, as in there is no backend anywhere, and no path by which the project learns anything about you.

This was a design decision, not a budget one, and it turned out to cost nothing.

## The update loop

The entire distribution stack is static files:

- **Release CI writes `version.json`** - latest version, artifact URLs, SHA-256 hashes, changelog pointer - into the static site (GitHub Pages, same repo as the code).
- **The app polls that file** about once a day, compares versions, and shows a banner.
  Nothing downloads without an explicit click.
  Fallback endpoint: the GitHub Releases API, whose unauthenticated rate limit is laughably sufficient for a daily check.
- **Artifacts live on GitHub Releases.** Free hosting, unlimited bandwidth, download counts available if I'm curious.
- **The updater reuses the staged-swap machinery** the app already had for development redeploys: download, verify the hash, stage next to the running install, swap, health-check, roll back on failure.
  Live agent sessions survive the update, because the session-owning supervisor rides through it - same mechanism as every other restart.
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
An unsigned frozen executable means SmartScreen warnings and the occasional antivirus false positive, and that is the real distribution tax on indie desktop software - not hosting.
[verify: state what was actually chosen - Trusted Signing / OV cert / documented-unsigned - and what it cost]

## The principle

Every server is a liability with a monthly bill: something to secure, something to break at 3am, something whose disappearance bricks the product.
A tool built to outlive its maintainer's attention should depend on the smallest possible set of things that need maintaining.
Static files and a git forge is about as small as that set gets.

swe-mux: github.com/[org]/swe-mux - local-only by design, all the way down to how it updates itself.
