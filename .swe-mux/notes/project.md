---
swe_mux_note = 1
kind = "projects"
id = "1c1ea28e4e6b3a443392efa0"
---

swe-mux
	Potential Primitives
		The prompt queue. Per-session, persistent, ordered list of pending prompts. Delivery rule: when the session goes idle (your composite trigger), inject the next item. That's it. A table plus one built-in rule.

  	The mailbox is the queue generalized by one field: sender. Anything can address a session: you from your phone, a rule ("on stall, mail it a nudge"), another session, an overnight miner. Messages carry provenance and wait for idle. This is Gas Town's Mail, which practitioners consistently identify as the piece that actually works there, and it's your spec's reserved relay reduced to its atomic form: agent-to-agent communication becomes "session A mails session B," policy stays in rules, and no orchestration framework ever appears. Session A doesn't even need to know mail exists; a rule can lift its output into a message.

		Work items (Yegge's beads) is where the line ends: the queue holds not strings but durable task objects with state that outlive sessions and attach to your session-lineage graph. A task started in Claude on Tuesday, resumed twice, verified by a Codex session, is one work item. This is the least atomic of the three (schema gravity, scope creep risk toward rebuilding Jira), so I'd say: design the queue so an item can carry an id and state, and let work items emerge rather than front-loading them.

	play sound notificsfion on agent sessions finishing/stopping/waiting for input
	
	managing multiple accounts more seamlessly
		what is that claudeswitcher repo? see if we watn to replicate anything from that

		but for claude code AND codex would be nice to have multiple accounts set up. although they frequently require auth so i dono how really feasible this is

	universal skills and prompt templates - just sends the skill into the CLI chat
		a universal library - and a per-project library of these
		browseable + command pallete callable

	tracking skill and tool usage metrics - parsing session histories, all historical sessions across all projects. different metrics and parsing logic for claude and codex
	
	testing framework for regressions and changes in the parsing/tracking of PTY and session indicators
