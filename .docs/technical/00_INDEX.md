# Technical documentation

Implementation-facing rules supplement the product/design contracts in `../design/`. Read the
design feature first, then the relevant technical page before changing code.

## Backend

- `backend/packages.md`: package responsibilities and dependency direction.
- `backend/sqlite.md`: one-database concurrency, transaction, and worker rules.

## Frontend

- `frontend/packages.md`: component/helper ownership and App orchestration boundaries.
- `frontend/workspace-state.md`: layout-v6 state, persistence, pointer drag, and mobile projection.

## Validation rule

Technical pages describe current implementation constraints, not planned abstractions. Update
them in the same change whenever package ownership, persistence sequencing, or state authority
moves. Product behavior and security boundaries remain authoritative in `../design/`.

