# Technical documentation

Implementation-facing rules supplement the product/design contracts in `../design/`. Read the
design feature first, then the relevant technical page before changing code.

## Backend

- `backend/packages.md`: the composition boundary, dependency direction, and the index of the
  per-domain package maps under `backend/packages/`.
- `backend/sqlite.md`: one-database concurrency, transaction, and worker rules.

## Frontend

- `frontend/packages.md`: the composition boundary, the extraction rule, UI state boundaries, and
  the index of the per-domain package maps under `frontend/packages/`.
- `frontend/workspace-state.md`: layout-v6 state, persistence, pointer drag, and mobile projection.

## Extensions

- `plugin-authoring.md`: standalone repository model, manifest and callback contracts, agent rules, development workflow, testing, and publishing.

## Package map shape

The package maps are the documents most branches touch at once, so they are written as per-feature
sections with one sentence per line and no prose in table cells.
That shape is what lets Git merge two branches' disjoint edits instead of conflicting over one
enormous line, and `tests/test_package_map_shape.py` keeps it from regressing.
Add a module to the domain file that owns it, and link any new domain file from its index.

## Validation rule

Technical pages describe current implementation constraints, not planned abstractions. Update
them in the same change whenever package ownership, persistence sequencing, or state authority
moves. Product behavior and security boundaries remain authoritative in `../design/`.
