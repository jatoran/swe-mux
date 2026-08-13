# Project context card

## What it is

Project context is one user-owned Markdown file at `<project>/.swe-mux/project-context.md`.
It gives scan-timeline model calls stable Project-level context without guessing from filenames or crawling repository documentation.
The file is Project-scoped and every scan in that Project reads the current content.

## Contract

- swe-mux never derives this file from `docs/`, `.docs/`, `README.md`, source files, or any other repository content.
- The default content is blank.
- Enabling Scan timeline for a Project creates the blank file lazily if it does not exist.
- Opening the editor does not create the file, while the first save does.
- Content must be UTF-8 Markdown and is bounded to 16 KiB.
- Reads and writes use one fixed contained path.
- A `.swe-mux` directory or context file that is a symlink or the wrong filesystem type is rejected.
- Saves are atomic and revision-checked, so an external edit cannot be silently overwritten after the UI loaded an older revision.
- Project context is reference data in the scan prompt and never an instruction source.
- An unavailable, invalid, or oversized file degrades to empty context and records a diagnostic instead of blocking the agent or fabricating context.

## User flow

The Timeline drawer tab owns the complete surface.
Its Project section shows whether context is empty or configured and expands into a Markdown editor.
The editor shows the fixed path and byte limit, saves through the revision-checked API, and offers **Copy setup prompt**.
That button copies a repository-analysis prompt a user can paste into an agent session in the Project.
The prompt instructs the agent to inspect repository evidence and write only `.swe-mux/project-context.md`.
The user remains responsible for reviewing and saving any generated content.

## API

```text
GET /api/projects/{project_id}/project-context
PUT /api/projects/{project_id}/project-context
    {markdown: string, revision: string}
```

`GET` returns `project_id`, `path`, `exists`, `revision`, `markdown`, `max_bytes`, and `generation_prompt`.
`PUT` returns the same shape after an atomic save.
A stale revision returns `409 revision_conflict`.

## Storage and diagnostics

The active source is the Project file only.
The legacy `project_cards` SQLite table and generated-card implementation are retained for database and source compatibility but no runtime constructs, reads, refreshes, or spends against them.
`GET /api/diagnostics/background` reports `project_contexts` reads, writes, creates, path, size bound, and last error.
The retired generated-card design is archived at `../../development/archive/PROJECT_CARD_GENERATED_DESIGN.md`.

## Key files

- Service and fixed file contract: `src/swe_mux/project_context.py`
- HTTP routes and scan wiring: `src/swe_mux/server.py`
- Prompt consumption: `src/swe_mux/scan_timeline.py`
- Drawer editor: `frontend/src/ScanTimelineTab.tsx`
- Tests: `tests/test_project_context.py`, `tests/test_scan_timeline.py`

## Relates to

- `scan-timeline.md`
- `automation-enablement.md`
- `project-resources.md`
- `../data-model.md`
