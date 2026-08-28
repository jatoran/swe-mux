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
- Push notifications are gated on the status detection being trustworthy: they fire when an agent genuinely needs a human, which is the only way phone notifications stay enabled instead of getting muted in week one.

Your terminal bytes never touch anyone's server.
The phone talks to your machine over your tailnet, and that's the whole topology.

## Voice, because thumbs are slow

Phone keyboards are a bad way to write prompts, so the fleet is voice-operable:

- Speech-to-text runs **locally** on the host - faster-whisper, with VAD in the browser - so nothing you say leaves your machines.
- A wake word plus a command grammar covers navigation and fleet queries: which sessions need me, read me the last reply, session three, send it.
- Dictated prompts land in the same queue as typed ones, with the same delivery rules.

[video: voice clip - wake word, fleet status spoken back, dictate a prompt, send]

Voice on a desktop is a party trick.
Voice on a phone while the actual work happens on a machine at home is a workflow.

## What this changes

Agents already decoupled my output from my typing speed.
This decouples it from my location.
An eight-hour agent workload needs maybe twenty minutes of human judgment scattered through the day, and there's no reason those twenty minutes have to happen in a chair.

swe-mux is open source (Apache 2.0): github.com/jatoran/swe-mux.
Setup for the phone path is in the docs - it's Tailscale plus one toggle, not a networking project.
