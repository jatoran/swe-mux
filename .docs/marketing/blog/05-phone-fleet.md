# Running my coding-agent fleet from my phone

*Feature post. Lighter than the engineering posts; heavy on clips. Crosslink from r/selfhosted thread.*

---

The point of running agents in parallel is that they don't need you most of the time.
Which raises the question: why am I at my desk?

My fleet is operable from my phone.
Not a dashboard that shows me things - the actual control plane: read any session's terminal, send prompts, approve permission requests, land a finished branch, spin up a new session.
From the couch, from the car (parked, allegedly), from bed when a push notification says an agent is blocked.

[video: phone clip - notification arrives, open PWA, read the session, approve, agent continues]

## The plumbing: no cloud, on purpose

There is no relay server and no account.
The path is:

- The daemon binds on the tailnet; **Tailscale Serve** fronts it with real HTTPS on the machine's `.ts.net` name.
  HTTPS matters because phone browsers gate the useful APIs - mic, notifications, PWA install - behind a secure context.
- The UI installs as a **PWA**, so it's an icon, full-screen, with web push notifications.
- Push is filtered on the daemon rather than in a tab, because the tab is dead exactly when an alert matters most.
  Alerts come from a small set of normalized events - a turn completing, a session going ready, an approval or question, a failure, a confirmed quota reset - and three rules hold back the ones not worth waking you for: a turn that ended while background work is still running, a session merely settling after startup, and a "ready" that has not stabilized yet.
  A fourth rule routes around the *other* device, so the phone does not buzz for an approval you are watching happen at your desk.
  That is what keeps phone notifications enabled past week one. It is not a promise that every buzz is one you needed.

Your terminal bytes never touch anyone's server: the phone talks to your machine over your own tailnet, there is no relay, and there is no swe-mux login.
Push is the one exception worth naming, because it is a browser-vendor service by construction and there is no way to have lock-screen notifications without one.

## Voice, because thumbs are slow

Phone keyboards are a bad way to write prompts, so the fleet is voice-operable:

- **Speech-to-text decodes on the host in both shipped configurations**: faster-whisper by default (the `voice-local` extra, whose models download once from Hugging Face and then run offline), or Windows Speech Recognition. There is no cloud speech path and no browser speech-recognition fallback - without an engine, transcription returns a typed error rather than sending audio somewhere. Voice activity detection runs in the browser.
- A wake word plus a command grammar covers navigation and fleet queries: which sessions need me, read me the last reply, session three, send it.
- Dictated prompts land in the same queue as typed ones, with the same delivery rules.
- All of it is off until you turn it on: read aloud, hands-free conversation, and push are each their own switch.

[video: voice clip - wake word, fleet status spoken back, dictate a prompt, send]

Voice on a desktop is a party trick.
Voice on a phone while the actual work happens on a machine at home is a workflow.

## What this changes

Agents already decoupled my output from my typing speed.
This decouples it from my location.
An eight-hour agent workload needs maybe twenty minutes of human judgment scattered through the day, and there's no reason those twenty minutes have to happen in a chair.

swe-mux is open source (Apache 2.0): github.com/jatoran/swe-mux.
Setup for the phone path is in the docs - it's Tailscale plus one toggle, not a networking project.
