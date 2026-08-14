# Observation storage compatibility

## What it is

The Observation Inbox user surface was retired in Phase 5.6.
Humans use Project notes for Project-scoped context and the global Scratchpad for cross-Project context.
Agent-authored spawn requests appear as approval rows in Fleet Queue.

The old `<project>/.swe-mux/observations.json` format remains as compatibility storage for typed spawn requests and for existing installations that still contain legacy note rows.
There is no `observations.open` command, Project-menu entry, or mounted Observation Inbox component.

## Current data model

- A typed spawn item has `kind: "spawn_request"`, a stable id, creation time, calling-session provenance, requested prompt/backend/name/cwd, and `pending | approved | dismissed` status.
- The file is bounded to 500 items and each legacy summary body is bounded to 2,000 characters.
- Missing and malformed files remain distinct.
- A malformed file reports `observations_unreadable` and is never rewritten as an empty list.

## Approval contract

- `mux.request_spawn` appends one inert typed request and emits `spawn_request_drafted`.
- The request is filed in the Project that would run the session, which is the caller's own unless the call named another; a request that crossed a Project says so in its Fleet Queue body.
- `GET /api/queue/mailbox` projects typed requests into Fleet Queue beside queued messages.
- A human approves or dismisses through `POST /api/projects/{project_id}/observations/{id}/decide`.
- Approval follows the ordinary spawn path with the reviewed prompt as `seed_text`.
- The once-only decision guard remains unchanged and a second decision returns `already_decided`.
- The decision emits `spawn_request_decided` without including the prompt body.

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
- `src/swe_mux/agent_messaging.py` creates requests, exposes caller status, and projects requests into Fleet Queue.
- `src/swe_mux/server.py` owns the human decision and ordinary `seed_text` spawn.
- `frontend/src/FleetQueue.tsx` is the only current human approval surface.
- `frontend/src/Observations.tsx` is retained as unmounted legacy source and has no route or command.

## Relates to

- `agent-messaging.md` defines the request and approval authority.
- `prompt-queue.md` defines Fleet Queue and message delivery.
- `project-resources.md` defines Project notes and the Scratchpad that replace human observation capture.
