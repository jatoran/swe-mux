# The beta: two cohorts, their asks, and their trackers

Step 3 of [`GTM_ROADMAP.md`](GTM_ROADMAP.md), contacted individually, before anything is posted publicly.

No real people are named in this file, and none should be added to it.
The repository is public.
Keep the actual names and addresses in the operator's own notes, and keep only the categories, the asks, and the anonymized trackers here.

## Why there are two

The previous version of this file ran one cohort with one question: install it, spend twenty minutes, tell me where it falls over.

That is **usability testing of the install path**, and it is worth doing.
What it cannot answer is whether anybody wants to keep using this - and that is the question step 6 is gated on, because Show HN cannot be retried and a front page spent on a tool people install once and abandon proves exactly that, in public, durably.

So the two cohorts ask two different questions and are recruited from two different places.
Sending one ask to both is how the old plan ended up measuring only the install.

---

# Cohort A: clean-install testers

**5 to 10 people. One session each, twenty minutes, scripted.**

Recruit for platform coverage and for **not** having seen the project.
Enthusiasm is actively unhelpful here: someone who wants it to work will push through a rough step and not report it.

## The ask, and why the wording decides the response rate

The goal is **defects and abandonment points, not adoption**, and the ask must say so.

"Would you try my tool" reads as a request for a favour with no defined end, and gets a polite yes followed by no install.
"I need someone to install this on a machine that isn't mine and tell me where it falls over - I expect it to fall over" reads as a bounded job with a clear finish line and an obvious way to succeed at it.
It also gives the recipient permission to report something bad, which is the whole point: the failure mode of a friendly trial is people who install it, hit something, quietly give up, and say it looks great.

Three things the ask must contain:

1. **A stated expectation of failure.** Say the install has never been run on a machine that is not the development host. That is true, and it converts "I didn't want to bother you with this" into "this is the thing they asked for".
2. **A bounded scope.** Twenty minutes, install and reach one running session, stop there. Not "use it for a week" - that is cohort B and it is a different conversation.
3. **An explicit non-ask.** Say you are not asking them to adopt it, keep using it, or tell anyone about it. Removing the implied ongoing obligation is most of what makes people say yes.

## The script, fixed so the results are comparable

Every participant runs the same steps, so "where people abandon" is a distribution rather than an anecdote.

1. Install, by the route assigned to them (see the tracker's Path column).
2. Run `swemux doctor`.
3. Start the daemon and open the UI.
4. Register a Project pointing at any existing folder.
5. Open a terminal in it.
6. Type the name of an agent CLI they already have installed, and reach its first prompt.
7. Stop. Note the wall-clock time and the step they were on if they stopped early.

**Both install paths are covered, because they fail in different places.**
`uv tool install swe-mux` fails at PATH, Python versions, and extras.
The unsigned Windows installer from the v0.1.2 release page fails at SmartScreen, at the Start Menu entry, and at the bundled supervisor.
Assign roughly half to each.

## What is measured

- **Completion rate.** How many reached step 6 at all.
- **Time to first session.** Wall clock from starting the install to the agent's first prompt.
- **Where people abandon.** The step number, named. This is the output that matters; a completion rate with no abandonment point is a number with nothing to do about it.

## Check-in

One, at the end of the session, and it is a message they write rather than anything that is collected.

> 1. Which step number did you stop on, and did you finish?
> 2. How long did it take, start to first agent prompt?
> 3. What is the first moment you did not know what to do next?
> 4. Every prompt, warning, or error you saw, including the ones you think were your fault.
> 5. Anything you had to already know to get through it that was not written down.

Question 5 is the one that finds the onboarding cliff, and it is the one only this cohort can answer.

## Categories to approach

| Category | Why them | Path to assign |
|---|---|---|
| **Someone who has never seen the project** | The only people who can find the onboarding cliff. Everyone else already knows the thing that is not written down | Either |
| **Windows developers** | The proving platform and the one with the most install-path surface | Installer, mostly |
| **Linux and macOS developers** | CI install-smokes the wheel there and starts a daemon only from a source checkout. Genuinely unproven, and the plan says so publicly, so finding out before a stranger does is worth a lot | PyPI |
| **Someone with a phone and Tailscale already set up** | The mobile path has the most setup steps and the most ways to fail silently | Either, plus the phone step |

## Template

Short.
The version that fits on a phone screen without scrolling is the one that gets read.

> Subject: Twenty minutes breaking something for me?
>
> I've been building swe-mux for a while - it runs several coding-agent CLIs as one fleet and keeps a record of what each of them actually did. It's on PyPI now and I haven't announced it anywhere.
>
> Before I do, I need it installed by someone whose machine isn't mine. It has literally never been done. I'm expecting it to fall over and I want to know where.
>
> The ask is about twenty minutes, and there's a five-step script so I can compare what happens across people: install, run `muxd`, open <http://127.0.0.1:8765>, register a folder, get one agent session running. Then tell me which step you stopped on, how long it took, and every prompt, warning, error, or moment where you didn't know what to do next - including the ones you think are your fault, because those are the ones I most need.
>
> I'm not asking you to keep using it, adopt it, or mention it to anyone. This is the whole thing.
>
> Repo: <https://github.com/jatoran/swe-mux>
>
> If it's a no, a no is completely fine and needs no reply.

Add one line naming their assigned install path.
Leave everything else alone; the last two lines are doing most of the work.

### The follow-up

One, after a week, two sentences, and then stop.

> Following up once and then I'll leave it: did you get a chance to try the install? A "I tried and gave up at step 3" is genuinely more useful to me than nothing, so please send that if that's what happened.

Naming the give-up case explicitly is the point.
It is the most common outcome and the least likely to be reported unprompted.

## Tracker A

**Use a role label, not a name** - the real mapping lives in the operator's own notes, outside this repository.

| # | Category | Platform | Path | Contacted | Ran it | Stopped at step | Minutes to first session | Findings |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| 3 | | | | | | | | |
| 4 | | | | | | | | |
| 5 | | | | | | | | |
| 6 | | | | | | | | |
| 7 | | | | | | | | |
| 8 | | | | | | | | |
| 9 | | | | | | | | |
| 10 | | | | | | | | |

- **Stopped at step** is blank when they completed. A column of blanks is the good outcome and a cluster on one number is the finding.
- **Findings** are issue numbers, not prose. Every finding becomes an issue even if the fix is immediate, because the cohort's whole output is a defect list and prose in a table is not one.

---

# Cohort B: design partners

**5 to 10 people who already run multiple coding agents. Two weeks.**

Not scripted, and explicitly **not** a bug hunt.
If this cohort spends two weeks filing install bugs, it has been recruited wrong or briefed wrong.

## The ask

The wording problem is the opposite of cohort A's.
Here the risk is that "help me test it" produces dutiful testing rather than use, and dutiful testing tells you nothing about whether anyone wants this.

Three things this ask must contain:

1. **Permission to stop.** Say plainly that abandoning it after three days is a result you want, and that you will ask why rather than trying to talk them back. Without this, the people who drift away are exactly the people who stop replying.
2. **Three named things to do at least once**, because they are the three the positioning rests on and a partner who never does them has not tested the claim: run several sessions at the same time, use the status column to decide where to look next, and land at least one worktree branch through the queue.
3. **A stated end date.** Two weeks, then one check-in a week after that. A trial with no end is a trial nobody finishes.

Tell them what ships off, in the invitation rather than after.
A design partner who spends day one confused about why nothing is running is a day of the two weeks spent on something the README should have told them.

## What is measured

- **How many used it on three separate days.** The single best proxy available for "this survived contact with an actual workflow", and the number that gates step 6.
- **Which capability became habitual.** Asked directly, in their own words, per partner. If the answers do not cluster, the positioning line is wrong and needs to move again - which is a finding, not a failure.
- **Whether they came back after the novelty.** The one-week-after check-in, asking only whether they still have it running and whether they have opened it since.

## Check-ins: three, fixed, comparable

**Day 3**

> 1. Have you opened it since the day you installed it? On how many separate days?
> 2. What is the most sessions you have had running at once?
> 3. What is the first thing that annoyed you enough to notice?

**Day 10**

> 1. How many separate days have you used it now?
> 2. Have you landed a branch through the queue? If not, what stopped you - it not coming up, or something in the way?
> 3. Is there anything you now do here that you used to do somewhere else?
> 4. If you stopped using it tomorrow, what is the one thing you would miss?

**One week after the end**

> 1. Is it still running?
> 2. Have you opened it since the trial ended?
> 3. If not: what did you go back to, and why?

Question 3 of the last set is the most valuable question in this whole document, and it only works if the invitation already gave them permission to answer it honestly.

## Measuring this without telemetry

**Telemetry is deliberately absent and stays absent.**
The check-ins above are the primary instrument and are sufficient on their own; everything below is an improvement to their accuracy, not a substitute for them.

Everything cohort B's numbers need is already on the participant's own disk: the durable status ledger, the land-queue event trail, and the usage history are all local SQLite, and `swemux doctor --export` already exists as a bundle a person generates, reads, and chooses to attach to an issue.
A short adoption summary drawn from those - days used, sessions run, branches landed, capabilities enabled - would replace "how many separate days have you used it now?" with a number the participant can read before deciding to send it.

**No such command exists.**
It is recorded as an open decision in [`GTM_ROADMAP.md`](GTM_ROADMAP.md) § Open decisions, not as a plan, and the beta runs on the check-ins either way.
Do not describe it to a participant as though it exists.

## Categories to approach

| Category | Why them | The specific ask |
|---|---|---|
| **People already running two or more agent CLIs daily** | They have the problem, and this is the cohort. Everyone below is a variant of it | The three named things, then tell me on day 10 what you would miss |
| **Someone running agents across several repositories** | The land queue and provenance only matter at that scale, and they are the differentiators | Land at least one branch through the queue, and tell me whether you trusted it the second time |
| **Someone who has been burned by an agent's own summary** | They will use the evidence layer immediately and can say whether it answers the question they actually had | Compare what an agent said it did against the record, at least once, and tell me whether the record was worth having |
| **Maintainers of adjacent tools** | They will see the design trade-offs immediately and will say the uncomfortable thing. A rival maintainer's critique before launch is worth more than a compliment after | **Not a design-partner ask.** One conversation, no trial: what would you attack in a comment thread? Offer the same in return |
| **Someone who will read a licence file** | The `THIRD-PARTY-NOTICES.md` and licence-audit posture is unusual and is a launch talking point. It should survive one adversarial read first | **Also not a design-partner ask.** Look at the notices and the audit and tell me what a diligence scan would flag |

The last two rows are one-off conversations rather than cohort members, and they are kept here because they are outreach that happens in the same window.
Do not count them in the cohort's numbers.

## Template

> Subject: Two weeks of running your agents in something I built?
>
> You run several coding agents at once, which is why I'm asking you specifically.
>
> I've been building swe-mux for months and it's open source now, unannounced. Short version: it records what each agent actually did - file writes hashed on the bytes written, commands with their exit codes, commits attributed to the session that made them - and it lands finished worktree branches behind a verification command you approved, one at a time.
>
> The ask is two weeks of using it the way you already work, plus three short check-ins I'll send you. Not a bug hunt: I have separate people doing the install testing. What I don't know is whether anyone wants to keep using this, and you're one of about eight people who can tell me.
>
> If you do three things at least once I'll get most of what I need: run several sessions at the same time, use the status column to decide where to look next, and land one branch through the queue.
>
> One thing worth knowing before you start: almost everything is off by default and per-Project - automations, the land queue, the model-backed parts, and session survival. That's deliberate, and it means the first ten minutes are configuration. I'll send you what I turn on for my own projects.
>
> And please stop if you want to. Someone abandoning it on day three is a result I need, and I'll ask you why rather than trying to talk you back into it.
>
> Repo: <https://github.com/jatoran/swe-mux>
>
> If it's a no, a no is completely fine and needs no reply.

## Tracker B

| # | Category | Platform | Invited | Started | Days used | Landed a branch | Habitual capability | Still running (+1wk) |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | |
| 2 | | | | | | | | |
| 3 | | | | | | | | |
| 4 | | | | | | | | |
| 5 | | | | | | | | |
| 6 | | | | | | | | |
| 7 | | | | | | | | |
| 8 | | | | | | | | |
| 9 | | | | | | | | |
| 10 | | | | | | | | |

- **Days used** is separate days, not sessions. Three or more is the bar.
- **Habitual capability** is their words, not a category from a list. Paraphrasing into a bucket is how this column stops being evidence.
- **Still running** comes from the one-week-after check-in and is the column that decides whether step 6 fires.

---

## What the numbers mean

### Cohort A

- **Fewer than half complete the script:** the install is worse than believed, and nothing downstream is worth doing until it is fixed. Stop and fix it.
- **Completion but no findings:** the cohort was too friendly or too familiar. Widen it toward "never seen the project", which is the category that produces findings.
- **The same abandonment step from three people:** stop the cohort and fix it before contacting anyone else. Spending more testers on a known defect wastes the scarcest thing in this plan.
- **Fewer than three of ten reply at all:** the ask is wrong rather than the product. Fix the ask, which is free, and re-send to five more.

### Cohort B

- **Half or more used it on three separate days, and at least two landed a branch:** this is the success condition, and it is what step 6 is waiting for.
- **Everyone installs and nobody returns in week two:** the problem is the product's value, not its onboarding. **Do not launch.** The thing to revisit is the positioning line, and the honest read is that the centre chosen for it is not the one people want.
- **They return, but the habitual capability is something the positioning does not mention:** the product is working and the line is wrong. Move the line, which is cheap, and re-run the drafts through it.
- **They land branches but never look at the evidence layer:** the two lead claims are not equally load-bearing. Reorder the proof points rather than assuming both landed.

## YouTube outreach is not either of these

Channel outreach is a step 7 activity, it needs the demo video to exist, and its template lives in [`posts/youtube-outreach.md`](posts/youtube-outreach.md).
It is a different ask - coverage, not evidence - and mixing it into either cohort produces a message that does neither.
