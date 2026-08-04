---
swe_mux_note = 1
kind = "projects"
id = "29a044bb-a06b-4216-95e4-39c5e91d48fb"
---
# swe-mux Agentic Controle Plane (ACP)


when drag left sidebar smaller, or right sidebar smaller, if you drag them all the way to the edge of UI or something (not all the way, but past their minimum width a bit and near edge), it should collapse it. so you can grab the divider and swipe mouse over to the side and it will just collapse it (and if you dont release mouse you can drag it back out too of course and it will expand, but it just stays at whaever state it is in when u release)

stop limiting the right sidebar's width on desktop. you should be able to drag it wider up until the limit of the main workspace's minimum width which should bea bout 150px

a small indicator on right sidebar drawer tab icons that indicatres when they are session-scoped, some little green dot in the corner. minimal. shouldnt be confused with a notification or something


	

and I also wonder..if we should pull out any of the other functionailty in this application into utility tabs - such as usage analytics (and maybe a condensed view of it and still can expand it to see full view in the modal?)


transcript tab:
	add a search input bar in its top bar
	make it so that the copy button floats along with you if you are scrolling a long message, and isnt just at the top. so it should start at the top, and then if you scroll down and top is off view, the copy button hovers in the top right corner still, until you are past that message

when project files are opened as tabs - add right click context option on the tabs for "open in default explorer", "copy full path", and  "copy path from project root"

maybe a global notes scratchpad. project agnostic

- in the right sidebar drawer: each separate section should show the section title at the top of its drawer content (File Explorer, Clipboard History, Commands), etc. Right now it doesnt display those titles in any of them and so if you dont know what any are, you wont know what you're looking at

- in the right sidebar drawer notes section:
	- ability to delete session notes - an "x" icon to the left of the "open in workspace tab" button. 2 click inline confirm
		- also make session notes have a right click context that has those 2 options as well
		- on mobile, this will be in the tap and hold to bring up the right click context
	- make the project note card a bit more standout so it is clearly a button or something and shows its size as well, right now its small compared to the session note cards

in the right sidebar drawer transcript tab, when a user expands a message "show more", I want it to persist that state for that message (even when more message are appended). taht way they can close drawer, change projects ,change sessions, etc. but when they come abck, and view transcript in that session again, it is still expanded
	this way if you're having to go back and forth and doing multiple things, you dont have to keep expanding it







codex:
	Treat the 703 MiB log database as suspect. Do not modify it while Codex processes are using it. A controlled cleanup would require stopping all Codex processes, backing up the SQLite files, and letting Codex recreate the diagnostic database. That is an upstream workaround, not a supported swe-mux operation.





on mobile, all current agents were idle, but I kept getting notifications that agent is ready. or that it was waiting for my input. annoying.
	make sure this is still happening after rebuilding supervisor




- setting a session to "auto-approve requests" - the harness will then automatically approve requests - basically "dangerously approve permissions" but at the control plane level
	- lots of requests are very benign (create vscode task, access skills/claude config to read).
	- These should be able to be approved by swe-mux itself




any way to make my init-project script (C:\Users\Jatora\Desktop\Development\init-project.ps1) also add a new project in swe-mux for it?
	will this require swe-mux daemon to have an API of some sort that other applications can tap into?





Test automations/control plane before proceeding through remaining roadmap
	Ensure everything is firing and working properly across the control plane/automation/observation hierarchy


task list - that an agent can check off and add comments too - maybe replace or supercede the observability inbox

- custom tasks - not just vscode tasks. probably good to just not rely on vscode conventions while still allowing importing of them though, but you can just define ur own swe-mux scripts for a project. agents can do that easily once the swemux mcp is up and can be queried for swe-mux development type info

- command rail commands: 
	- copy nth response - perhaps leveraging the right sidebar's session message viewer to make this easier? or whatever the source of its data is
		- when u hit the copy nth button it then brings up a quick number selector for quick tap/click of the number

	- quick-model changes. tap it and it expands into a model and effort picker that then does the switch on claude/codex?

- can i tie certain claude/codex accounts to specific browsers and/or specific chrome profiles? so when it asks to auth, it only opens those browsers/profiles?

- allow users to select their text renderer from options (continuity, etc)

- preview page tabs - allow seeing the dev console logs and entering dev console commands?


- maybe configurable gesture zones? swipe left top half of screen does one thing, swipe left bottom half of screen does another
	- what other ways to expose more UI in a clean and intuitive way?
	- and not just on mobile/gestures?
	- 2 finger drag down and up? would this be fine and 1 finger would still work for scroll?
	

- allow making command rail be multiple rows? on mobile and/or desktop? and configuring what is on each row
	- So you could configure the rails, and the sidebar drawer all in that config UI that already exists
	- and it needs to take into account if a rail row is shown on mobile, or desktop, or both, and you can have duplicates of course, just like you can with rail and sidebar


- [ ] control plane updates
- [ ] the remaining roadmap updates




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
