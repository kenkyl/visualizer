# Architecture changelog

Changes to the architecture model, newest first. Written by `viz.py update`. The version each entry replaced is kept under [archive/](archive/).

## 2026-10-12 · `1111111` → `bbbbbbb`

Jobs moved to a worker.

**System**

- Changed `highlights`: added Jobs run off the request path.

**Components**

- Added **Worker** (`worker`): Runs jobs.
- Changed **CLI** (`cli`): `responsibility`: Runs commands. → Runs commands for a user.
- Changed **Database** (`db`): `evidence`: added api/main.py

**Edges**

- Added `api` → `worker` (async): enqueues jobs
- Removed `api` → `db` (sync)

Previous version: [archive/2026-10-01-1111111/](archive/2026-10-01-1111111/)
