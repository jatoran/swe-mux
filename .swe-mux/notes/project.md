---
swe_mux_note = 1
kind = "projects"
id = "29a044bb-a06b-4216-95e4-39c5e91d48fb"
---
# swe-mux project notes

Agentic Controle Plane (ACP)

- big issue in recent sessions:
      - a session i had renamed to "git worktree conversation"... when i went to session history to resume it, it was a different covnersation than the one i had renamed.  claude-590998 is the actual
  one (now claude-590998 resumed)
      - also some sessions seem linked in their status, so if one says working, anmother one will update to say working, even if its not, like its not in charge of its own status but tied to other
  session(s) somehow
      - Evaluate and discuss these issues. Figure out why there would be any bleed between sessions and history or anything like that
    
    
- making worktrees more first class
	- more native worktree handling when spawning new sessions - can spawn a new worktree and run the worktree creation script? and then spawn sessions inside those worktrees directly?


- in sidebar, sesisons have an icon for "shares one pane region"
	- i dont need that icon


- session titling issues:
	- i have session titling enabled - but have a bunch of sessiong going that just have their default titles. what is going on with this? update to this: so a session finished, and then i saw the new title apply. so is it waiting until the agent returns before it applies the title?
	- actually another bug though..weird. another session finished, and then that didnt get atitle, AND the other one that HAD gotten a title...reverted to its native "claude-..." title
	- ... so all finished, andi  went through to cpoy allof their final responses, an the titles updated again for some of them to "Clipboard copy" or "clipboard review"  so......this system needs work

- session titling update:
	- should session title periodically re-run? or run at every boundary of user/agent communication or something? maybe not always retitling, but if theres an important shift in covo, it could retitle or something? hmm. not sure.
	
	- should session title apply right when user sends message? (and then re-calibrate periodically?)
	
	- should the recalibration be tied to the scan timeline automation? rather than doing its own re-reading of transcripts?
		- and as such.. i shouldnt make changes here until scan timeline is done?
			- hmm. but the initial session titling should still be done id think regardless? hmm. cuz what if session title is on but scan timeline isnt?

on desktop, when many tabs open in a pane, and they ahve a scrollbar, shift+mouse wheel works to scroll, but regular mouse wheel should work as well when hovered over the tabs

Test automations/control plane before proceeding through remaining roadmap
	Ensure everything is firing and working properly across the control plane/automation/observation hierarchy

codex has a "latest" button that scrolls you to bottom, and it works. claude has a "jump to bottom" button which works on desktop, but doesnt work on mobile. the ctrl+end command in the command rail still works on claude, but id like tapping that jump to bottom button to also work if we can do that?
	if there is a serious limitation here, then i'm fine deferring this, but if we can make that work, I'd like to

disable gesture shortcuts when rearranging right sidebar drawer tabs on mobile

gestures for switching between projects

task list - that an agent can check off and add comments too - maybe replace or supercede the observability inbox

- custom tasks - not just vscode tasks. probably good to just not rely on vscode conventions while still allowing importing of them though, but you can just define ur own swemux scripts for a project. agents can do that easily once the swemux mcp is up and can be queried for swe-mux development type info

command rail command: copy nth response

- command rail commands: quick-model changes. tap it and it expands into a model and effort picker that then does the switch on claude/codex?


- should be able to resume sessions even when they weren't run in swe-mux
	
	
- can i tie certain claude/codex accounts to specific browsers and/or specific chrome profiles? so when it asks to auth, it only opens those browsers/profiles?

- allow users to select their text renderer from options (continuity, etc)


- preview pane - allow seeing the dev console logs and entering dev console commands

- setting a session to "auto-approve requests" - the harness will then automatically approve requests - basically "dangerously approve permissions" but at the control plane level

- Continuity-related Updates
	- continuity embedded: easy text search feature on command rail and gesture trigger (also hotkey ctrl+f on desktop). just for the currently focused window/note
	
	- continuity mobile embedded: any way to bring back the mobile drag handles on highlight select?
	
	- markdown horizontal breaks arent rendering, "---"
	- need ctrl+f search for project notes and in files - continuity update?
	
	- markdown outlines vieable? is this for continuity or for the wrapping application to handle? hmm
	
	- there are no guidelines on the embedded continuity's indented lines. the guidelines for nested lines that work great on the desktop version
	- .
	- on line wrapping under bullets, they work fine on the first bullet, but on nested bullets, they wrap..but then thers an extra indentation on the wrapping



- ability to click file paths from agent chats and they open

- maybe congifurable gesture zones? swipe left top half of screen does one thing, swipe left bottom half of screen does another
	- what other ways to expose more UI in a clean and intuitive way?
	- and not just on mobile/gestures?
	- 2 finger drag down and up? would this be fine and 1 finger would still work for scroll?
	

- allow making command rail be multiple rows? on mobile and/or desktop? and configuring what is on each row


- [ ] control plane updates
- [ ] the remaining roadmap updates


ability to tap, on mobile in agent sessions, to a specific part of your chat input (this doesn't work on native codex on desktop, but it works on claude code.. hmm). i wonder if we can just make it work foe both? to move the carat to wherever you tap or click in your currently being typed message in these agent cli sessions?


on mobile agent sessions:
	want to fix tap and hold and drag highlighting not allowing you to drag if content is off of the current screen (somehow triggeting scroll while also highlighting)
		pretty hard problem probably. evaluate and discuss if this is reasonably feasible

agents with swemux mcp send tasks to other projects (swe-mux sending a task to continuity)

scheduled agent runs, and repeated ones on a schedule

jump to previous user messages in agent chats



knowledge graph building?

memory building?

- a combined ledger that agents can be made aware of
	- how made aware?
	
	- that they can refer to. will help them with parallel work, provenance, decisions, etc.
	
can capture your entire update prompt and traversal timeline when parsing all your session transcripts and tagging them all


this could heavily encourage you giving positive feedback to the models
	"k x works, y works, z works"  just little notes you know will get logged and filed away


test swe-mux on CMR laptop. install tailscale there. see what needs to be hardened for another user/system to use it





- is it possible to detatch a session from swe-mux into an external terminal?
	- i could always resume of course but just wondering if there's a seamless way to do thsi that wouldnt break the claude code caching or whatever - or that could keep the process continuous?
		- probably not?



- openrouter call - can i set this to go through my generative gateway instead of openrouter? while still leaving openrouter routing open for other people that use this and done have generative gateway?


- optional resumption of any chat sessions that were open when you closed swe-mux
	- it starts UI and says x,y,z were open, do you want to reopen them? and you can say yes to all, not to all, or check the ones you watn to re-open




- git/source control pane
	- showing branches and worktrees in it as well

git commit message generation - based on changes and session ssince that change and the annotations since that change
	and eventually when we build in source control, you can hti a button next to the commit message and it will generaet that based upon the knowledge we have accumulated already (if hte project ahs those automations enabled)
	THIS UPDATE SHOULD LEVERAGE THE CONTROL PLANE UPDATES


- getting STT global and actually doing other things in swe-mux UI with it
	- To get your desired "talk is one global thing that follows the focused session, without bleeding":
	
	- Lift talk to a single app-level controller. Move the mic/capture ownership out of the per-pane ConversationControl into one App-level instance that targets activeId dynamically. This is the core change and it's what makes talk survive mobile tab switches (the per-pane component can't, because its pane unmounts). The mutex/claim machinery then goes away — there's structurally one mic.
	- On focus switch while talk is live: finalize-then-retarget. When activeId changes, commit the current buffer against the origin session (either auto-submit or clear — I'd clear and show a brief "buffer dropped on session switch" note rather than silently submitting a half-formed thought), then rebind the target to the new focused session with an empty buffer. This is what guarantees no bleed: a buffer is never carried across the switch.
	- Decouple talk from persisted TTS. Stop force-writing voice_mode='auto' (line 154). Instead, if the focused session's TTS is off, drive playback with a transient in-memory "talk is active" flag for that session, and restore on talk-off. Otherwise turning talk on permanently mutates the per-session TTS you just told me you want independent.
	- Persist the talk on/off intent globally (a single client-side or per-user flag), so "talk is on" is a property of the workspace, not of whichever pane happened to own it — matching your mental model.
	
	- Scope: (1) and (2) are the real work (one refactor of ConversationControl into an App-level singleton plus a focus-change effect). (3) and (4) are small. Want me to write this up as a concrete implementation plan, or start with just the mobile "talk follows the focused session" behavior since that's the most visible gap?



- Transcript-first agent view
	- Claude/Codex sessions should eventually have a clean message transcript and native multiline composer by default, with “Live terminal” as a toggle. Raw terminal streaming remains invaluable, but it should not be the primary phone interface.

	- something like this in it:
		- a heatmap/sidebar for agent chats for jumping around to user/agent replies quickly
			- maintained by the system, overlaid on window or something
			- also able to selectively copy them to clipboard without even jumping to them or having to do `/copy`
			- probably a local processing of raw transcripts involved to make this possible and actually performant?

- AMBIENT AGENT IDEAS
	- Monitor the active transcript at intervals - giving a running status of what it is actually doing
		- The agent would need to hold a bit of context, but it just needs to give a small annotation on what is happening. Specifically what the agent is chasing down in that moment, tracing its path as it does different things and WHY it does them


- schedule new sessions and messages for a space/project. cron schedule or one-offs, etc

- opencode integration

- redo desktop tutorial + create a separate mobile tutorial


- speak system updates:
	- user configurable trigger word + variants		- replace "mux" with "swe"?

	- expanding the voice system for use navigating swe-mux, going to open/active sessions, starting new sessions, etc.

  - Mux, send / submit — submits buffered speech.
  - Mux, cancel / clear — clears the entire draft.
  - Mux, undo / delete last phrase — removes the latest dictated chunk.
  - Mux, mute / stop speaking — stops playback but keeps listening.
  - Mux, read reply — reads the agent’s latest reply.
  - Mux, summary mode — switches spoken replies to summaries.
  - Mux, verbatim mode — reads replies verbatim.
  - Mux, interrupt — stops playback and sends Ctrl-C to the agent.
  - Mux, help / list commands — displays the command list.
  - Mux, stop listening / sleep — turns Conversation mode off.

