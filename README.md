# Institutional AI

Institutional AI is a local, inspectable organisation of persistent specialist workers. It models
jurisdiction, independent analysis, typed communication, peer challenge, dissent, controlled team
formation, provenance, memory, budgets, and Director judgement as durable state—not as one shared
chat or a fixed agent DAG.

The first release demonstrates a toy urban fleet optimisation mission using a deterministic provider.
No paid model call is required.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

## Run it

Requires Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
uv run institutional-ai --data-dir .institutional-data demo
uv run institutional-ai --data-dir .institutional-data serve --port 8000
```

Open <http://127.0.0.1:8000>. The dashboard can create and run another mission itself.

Run all verification gates:

```bash
uv run ruff check .
uv run mypy src/institutional_ai
uv run pytest
```

Inspect a persisted run without starting the server:

```bash
uv run institutional-ai --data-dir .institutional-data inspect PROJECT_ID
```

Each run is stored under `.institutional-data/PROJECT_ID/` with:

- `state.json`: machine-readable typed project snapshot;
- `events.jsonl`: append-only, hash-linked audit history;
- `manifest.json`: run counts and integrity hashes;
- `workspaces/WORKER_ID/`: separately attributable specialist artifacts.

## What the demo proves

One mission creates a Director and five initial specialists. The Mathematician submits a typed role
request; the Director approves an allowlisted Operations Researcher and changes the organisation
graph before freezing the report round. Six specialists then produce initial reports with no peer
reports in their provider context. The barrier unlocks only after all six commit.

Typed questions, findings, review requests, objections, and decisions then cross worker inboxes. The
mandatory review policy produces accepted, changes-requested, and disputed outcomes. Revisions are
new immutable versions. The final Director report cites the current reports and reviews while keeping
the empirical and safety disagreements visible.

## Real versus mocked

Real in this release:

- lifecycle, governance checks, authority boundaries, dynamic role creation, graph history;
- independent-report barrier, typed routing, review/revision state, dissent preservation;
- worker/project/organisation memory scopes and cross-project organisation-memory loading;
- budget accounting, workspace isolation, artifact provenance, persistence, audit log, API, and UI.

Mocked in this release:

- all specialist reasoning, peer-review prose, usage figures, and Director synthesis;
- the fleet data, optimiser, physical calibration, and operational performance claims;
- external model providers and executable tools.

The deterministic provider is intentional: tests and the demo require no credentials, network, or
paid calls. A real provider adapter is the next integration boundary, not a change to the institution.

## Dashboard evidence

![Institutional AI dashboard showing the dynamic team, reports, dissent, Director report, and audit log](evidence/dashboard.png)

## Deliberate limits

This is a one-process local MVP. It uses atomic JSON snapshots rather than a database and simple
scope-and-tag memory retrieval rather than embeddings. It does not execute arbitrary tools, mutate
its own code, silently add role types, force consensus, or merge agent output. Add a database, queue,
streaming updates, vector retrieval, or real provider adapters only when measured use requires them.
