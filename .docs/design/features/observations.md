# Observation storage compatibility

## What it is

The Observation Inbox user surface was retired in Phase 5.6.
Humans use Project notes for Project-scoped context and the global Scratchpad for cross-Project context.
Agent-authored spawn requests and drafted session-control requests appear as approval rows in Fleet Queue.

The old `<project>/.swe-mux/observations.json` format remains as compatibility storage for typed spawn requests, drafted control requests, and existing installations that still contain legacy note rows.
There is no `observations.open` command, Project-menu entry, or mounted Observation Inbox component.

## Current data model

- A typed spawn item has `kind: "spawn_request"`, a stable id, creation time, calling-session provenance, requested prompt/backend/name/cwd, and `pending | approved | dismissed` status.
- A drafted control item has `kind: "control_request"` (Phase 7.6), a stable id, creation time, the `action` (`interrupt` | `end_session`), the target session id and name, an optional reason, calling-session provenance, and the same `pending | approved | dismissed` status. It is inert - it started nothing.
- The file is bounded to 500 items and each legacy summary body is bounded to 2,000 characters.
- Missing and malformed files remain distinct.
- A malformed file reports `observations_unreadable` and is never rewritten as an empty list.

## Approval contract

- `mux.request_spawn` appends one inert typed request and emits `spawn_request_drafted`.
- A drafted `mux.interrupt`/`mux.end_session` appends one inert `control_request` and emits `agent_control_drafted`; the draft path is what runs when the Project's `session_control_grant` is `draft` (`features/mux-mcp.md`).
- The request is filed in the Project that would run the session, which is the caller's own unless the call named another; a request that crossed a Project says so in its Fleet Queue body.
- `GET /api/queue/mailbox` projects both typed request kinds into Fleet Queue beside queued messages, as `spawn_requests` and `control_requests`.
- A human approves or dismisses through `POST /api/projects/{project_id}/observations/{id}/decide`.
- Approving a spawn request follows the ordinary spawn path with the reviewed prompt as `seed_text`.
- Approving a control request runs the same shared interrupt/graceful-end daemon operation the granted path uses, still subject to the daemon-owner, non-agent, and readiness guards, so the human approval is what carries the authority.
- The once-only decision guard remains unchanged and a second decision returns `already_decided`.
- A spawn decision emits `spawn_request_decided` without the prompt body; a control-request approval emits `agent_session_control` (the audit event the granted path also emits) and a dismissal emits `control_request_decided`.

## Compatibility endpoints

```text
GET  /api/projects/{project_id}/observations
POST /api/projects/{project_id}/observations
PUT  /api/projects/{project_id}/observations
POST /api/projects/{project_id}/observations/{observation_id}/decide
```

The first three endpoints remain for compatibility with existing clients and stored files.
The current frontend does not call them for human note capture.

## Key files

- `src/swe_mux/project_files.py` owns validation and compatibility storage.
- `src/swe_mux/agent_messaging.py` creates spawn requests, exposes caller status, and projects both request kinds into Fleet Queue.
- `src/swe_mux/session_control.py` drafts the inert `control_request` under a `draft` grant (`features/mux-mcp.md`).
- `src/swe_mux/server.py` owns the human decision, the ordinary `seed_text` spawn, and `_approve_control_request` (the approved interrupt/end).
- `frontend/src/FleetQueue.tsx` is the only current human approval surface.
- `frontend/src/Observations.tsx` is retained as unmounted legacy source and has no route or command.

## Relates to

- `agent-messaging.md` defines the request and approval authority.
- `prompt-queue.md` defines Fleet Queue and message delivery.
- `project-resources.md` defines Project notes and the Scratchpad that replace human observation capture.
