# Direct outreach for testers

The Stage 2 quiet trial from [`GTM_ROADMAP.md`](GTM_ROADMAP.md): five to fifteen people, contacted individually, before anything is posted publicly.

No real people are named in this file, and none should be added to it.
The repository is public.
Keep the actual names and addresses in the operator's own notes, and keep only the categories, the ask, and the anonymized tracker here.

## What the ask is, and why the wording decides the response rate

The goal is **bug reports and honest reactions, not adoption.**

"Would you try my tool" reads as a request for a favour with no defined end, and gets a polite yes followed by no install.
"I need someone to install this on a machine that isn't mine and tell me where it falls over - I expect it to fall over" reads as a bounded job with a clear finish line and an obvious way to succeed at it.
It also gives the recipient permission to report something bad, which is the whole point: the failure mode of a friendly trial is people who install it, hit something, quietly give up, and say it looks great.

Three things the ask must contain:

1. **A stated expectation of failure.** Say the install has never been run on a machine that is not the development host. That is true, and it converts "I didn't want to bother you with this" into "this is the thing they asked for".
2. **A bounded scope.** Twenty minutes, install and reach one running session, stop there. Not "use it for a week".
3. **An explicit non-ask.** Say you are not asking them to adopt it, keep using it, or tell anyone about it. Removing the implied ongoing obligation is most of what makes people say yes.

## Categories to approach

Described by role, in rough order of expected value.

| Category | Why them | The specific ask |
|---|---|---|
| **People who already run two or more agent CLIs daily** | They have the problem, they will recognize the value in thirty seconds, and their bug reports will be about the interesting layer rather than the install | Install, run two sessions, tell me whether the status column ever lied to you |
| **Windows developers** | Windows is the proving platform and the one with the most install-path surface. This is where the clean-machine defects actually are | Install from PyPI on Windows, tell me every prompt, warning, or error between `uv tool install` and a running session |
| **Linux and macOS developers** | The two platforms CI install-smokes but never starts a daemon on. Genuinely unproven, and the plan says so publicly, so finding out before a stranger does is worth a lot | Install, run `muxd`, open the UI, spawn a shell, tell me what broke. Expect breakage; that is the point |
| **Someone who has never seen the project** | The only people who can find the onboarding cliff. Everyone else already knows the thing that is not written down | Read the README, install it, and tell me the first moment you did not know what to do next |
| **Maintainers of adjacent tools** | They will see the design trade-offs immediately and will say the uncomfortable thing. A rival maintainer's critique before launch is worth more than a compliment after | Not a trial request. Ask what they would attack in a comment thread, and offer the same in return |
| **Anyone with a phone and Tailscale already set up** | The mobile path has the most setup steps and the most ways to fail silently | Get the PWA working on your phone and tell me how many steps it took and where you got stuck |
| **Someone who will read a licence file** | The `THIRD-PARTY-NOTICES.md` and licence-audit posture is unusual and is a launch talking point. It should survive one adversarial read first | Look at the notices and the audit and tell me what a diligence scan would flag |

Do not approach anyone in all seven categories with the same message.
The specific ask column is the message.

## The email or DM template

Short.
The version that fits on a phone screen without scrolling is the one that gets read.

> Subject: Twenty minutes breaking something for me?
>
> I've been building swe-mux for a while - it runs several coding-agent CLIs as one fleet, with the sessions owned by a separate process so they survive restarts. It's on PyPI now and I haven't announced it anywhere.
>
> Before I do, I need it installed by someone whose machine isn't mine. It has literally never been done. I'm expecting it to fall over and I want to know where.
>
> The ask is about twenty minutes: `uv tool install swe-mux`, run `muxd`, open <http://127.0.0.1:8765>, get one terminal running. Then tell me every prompt, warning, error, or moment where you didn't know what to do next - including the ones you think are your fault, because those are the ones I most need.
>
> I'm not asking you to keep using it, adopt it, or mention it to anyone. Bug reports and an honest reaction is the whole thing.
>
> Repo: <https://github.com/jatoran/swe-mux>
>
> If it's a no, a no is completely fine and needs no reply.

Adapt the third paragraph per category using the specific ask above.
Leave everything else alone; the last two lines are doing most of the work.

### The follow-up

One, after a week, two sentences, and then stop.

> Following up once and then I'll leave it: did you get a chance to try the install? A "I tried and gave up at X" is genuinely more useful to me than nothing, so please send that if that's what happened.

Naming the give-up case explicitly is the point.
It is the most common outcome and the least likely to be reported unprompted.

## Tracker

Keep one row per person.
**Use a role label, not a name** - the real mapping lives in the operator's own notes, outside this repository.

| # | Category | Platform | Contacted | Replied | Installed | Findings | Fixed |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |
| 4 | | | | | | | |
| 5 | | | | | | | |
| 6 | | | | | | | |
| 7 | | | | | | | |
| 8 | | | | | | | |
| 9 | | | | | | | |
| 10 | | | | | | | |

Columns:

- **Contacted / Replied / Installed** - dates, so the reply rate and the install rate are separable. They fail for different reasons and want different fixes.
- **Findings** - issue numbers, not prose. Every finding becomes an issue even if the fix is immediate, because the trial's whole output is a defect list and prose in a table is not one.
- **Fixed** - the commit or "documented as a known limit". Both are acceptable outcomes; silence is not.

### What the numbers mean

- **Fewer than three installs out of ten contacted:** the ask is wrong, or the install is worse than believed. Fix the ask first, since it is free, and re-send to five more.
- **Installs but no findings:** the trial was too friendly. Widen it to the "never seen the project" category, which is the one that produces findings.
- **The same finding from three people:** stop the trial and fix it before contacting anyone else. Spending more testers on a known defect wastes the scarcest thing in this plan.

## YouTube outreach is not this

Channel outreach is a Stage 6 activity, it needs the demo video to exist, and its template lives in [`posts/youtube-outreach.md`](posts/youtube-outreach.md).
It is a different ask - coverage, not bug reports - and mixing the two produces a message that does neither.
