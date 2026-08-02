---
swe_mux_note = 1
kind = "projects"
id = "29a044bb-a06b-4216-95e4-39c5e91d48fb"
---
# swe-mux Agentic Controle Plane (ACP)


right sidebar drawer - new addition:
	the currently focused session's transcript, with copy commands for agent responses and user messages
	easier to view/browse through and select/copy from
	excludes all the various tool calls? or maybe collapses them

in preview panes tabs, on mobile, the right edge of content is generally slightly cut off - so it doesnt fit fully

in preview panes - if an update is made to a file served on a server that im viewing in preview, will that preview update? earlier i had to have it reserve it on a different port for it to update the content. so im not sure if it was an issue with the preview tab functionality or what


I think project/session processes should be a right sidebar drawer item
	and we dont need the 'proc' button in agent session topbars taking up space
	so when opened it should be filtered to the processes of the current project by default? maybe not the session, or should it be the session? kaybe project but highlighting the current session if a session is currently focused?

im considering more and more if the project and session notes should be part of the tight sidebar drawer

when i resume a session from session history, it should focus to that resumed session. currently it just returns me to whatever tab was focused before, without switching, after i hit the 'resume as new' button. please fix this



abiliity to attach files / drag files into chats? if compatible? excel/csv etc?





Test automations/control plane before proceeding through remaining roadmap
	Ensure everything is firing and working properly across the control plane/automation/observation hierarchy


gestures for switching between projects

task list - that an agent can check off and add comments too - maybe replace or supercede the observability inbox

- custom tasks - not just vscode tasks. probably good to just not rely on vscode conventions while still allowing importing of them though, but you can just define ur own swe-mux scripts for a project. agents can do that easily once the swemux mcp is up and can be queried for swe-mux development type info

- command rail commands: 
	- copy nth response

	- quick-model changes. tap it and it expands into a model and effort picker that then does the switch on claude/codex?

- can i tie certain claude/codex accounts to specific browsers and/or specific chrome profiles? so when it asks to auth, it only opens those browsers/profiles?

- allow users to select their text renderer from options (continuity, etc)

- preview page tabs - allow seeing the dev console logs and entering dev console commands?

- setting a session to "auto-approve requests" - the harness will then automatically approve requests - basically "dangerously approve permissions" but at the control plane level


- maybe configurable gesture zones? swipe left top half of screen does one thing, swipe left bottom half of screen does another
	- what other ways to expose more UI in a clean and intuitive way?
	- and not just on mobile/gestures?
	- 2 finger drag down and up? would this be fine and 1 finger would still work for scroll?
	

- allow making command rail be multiple rows? on mobile and/or desktop? and configuring what is on each row
	- So you could configure the rails, and the sidebar drawer all in that config UI that already exists
	- and it needs to take into account if a rail row is shown on mobile, or desktop, or both, and you can have duplicates of course, just like you can with rail and sidebar


- [ ] control plane updates
- [ ] the remaining roadmap updates


ability to tap, on mobile in agent sessions, to a specific part of your chat input (this doesn't work on native codex on desktop, but it works on claude code.. hmm). i wonder if we can just make it work foe both? to move the carat to wherever you tap or click in your currently being typed message in these agent cli sessions?


on mobile agent sessions:
	want to fix tap and hold and drag highlighting not allowing you to drag if content is off of the current screen (somehow triggeting scroll while also highlighting)
		pretty hard problem probably. evaluate and discuss if this is reasonably feasible

agents with swemux mcp send tasks to other projects (swe-mux sending a task to continuity)

scheduled agent runs, and repeated ones on a schedule

jump to previous user messages in agent chats. a quick-seek



knowledge graph building?

memory building?

- a combined ledger that agents can be made aware of
	- how made aware?
	
	- that they can refer to. will help them with parallel work, provenance, decisions, etc.
	
- can capture your entire update prompt and traversal timeline when parsing all your session transcripts and tagging them all


- this could heavily encourage you giving positive feedback to the models
	- "k x works, y works, z works"  just little notes you know will get logged and filed away


- test swe-mux on CMR laptop. install tailscale there. see what needs to be hardened for another user/system to use it





- is it possible to detatch a session from swe-mux into an external terminal?
	- i could always resume of course but just wondering if there's a seamless way to do thsi that wouldnt break the claude code caching or whatever - or that could keep the process continuous?
		- probably not?



- openrouter call - can i set this to go through my generative gateway instead of openrouter? while still leaving openrouter routing open for other people that use this and done have generative gateway?


- optional resumption of any chat sessions that were open when you last closed swe-mux (on purpose or from a crash)
	- it starts UI and says x,y,z were open, do you want to reopen them? and you can say yes to all, not to all, or check the ones you watn to re-open



- git commit message generation - based on changes and sessions since that change and the annotations since that change
	- and eventually when we build in source control, you can hit a button next to the commit message and it will generate that based upon the knowledge we have accumulated already (if the project has those automations enabled)
	- THIS UPDATE SHOULD LEVERAGE THE CONTROL PLANE UPDATES
	- .
	- This update maybe isnt...needed anymore? If leveraging worktrees at least. Will be nice to have here and there though to have something that analyzes project, diffs, efficiently, and creates a git msg. But this requires a little bit of engineering


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
	- ability to move around projects in the whole UI.
	- some sort of top level sweet mux agent keeps track of all the statuses of all current sessions has hooks into all commands. and potential endpoints. so you can use this to load it up and say hey. what's the status of everything and it will respond and say hey Ethen the session is finished since you last checked this pending session 's ending approval request from project y if you'd like me to approve it okay? etc etc
		- so this would be capable of managing swingbox from the speak mode using all of the audio commands. Aunt, we can discuss what type of model that we use or what data it will leverage to to not have to be real-time calls but it should have all that data cached already easily accessible so it can just pull it up in a moment's notice. it will just have a cash or something that makes it easy for it to pull up data when you ask at a moment's notice and also verify things are accurate itself by checking the statuses of different agent sessions. and confirming there are no regressions or anything like that
	- The primary purpose of this: being able to and accurately and efficiently navigate the UI hands-free. it's less of a feat for the navigation of the UI. that's fairly straightforward, but it being able to actually manage things and be up to date on statuses and being able to give you a rundown that is accurate. accurate natural language and succinct that is actually useful. one hands-free is the goal of this. hands-free is an absolute must this feature
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
