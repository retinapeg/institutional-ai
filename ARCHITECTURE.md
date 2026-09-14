# Institutional AI architecture

## Purpose

Institutional AI models a persistent organisation, not a prompt chain. Workers retain identity,
jurisdiction, projects, workspace, scoped memory, communication, review duties, and budget across
state transitions. Organisational topology is governed data that can change over time; executable
code is not self-modified.

```text
MISSION
  -> TEAM_FORMING
  -> INDEPENDENT_WORK
  -> REPORT_COMMIT barrier
  -> PEER_REVIEW
  -> REVISION
  -> DIRECTOR_SYNTHESIS
  -> COMPLETE
```

`BLOCKED` and `FAILED` are explicit interruption states with a recorded recovery checkpoint. Every
meaningful transition and mutation emits an audit event.

## Core aggregate

One `ProjectState` is the durable aggregate for a mission. It contains:

- persistent `Worker` records and per-worker workspace paths;
- a mutable `OrganisationGraph` of role relationships;
- a separate task dependency graph through each `Task.depends_on` field;
- typed messages, artifacts, reports, reviews, decisions, and role requests;
- worker, project, and organisation memory records retrieved by scope and tags;
- a report barrier and explicit lifecycle state;
- project and role budget ledgers;
- a final `DirectorReport` that preserves disagreement.

The store writes an atomic typed JSON snapshot and an append-only JSONL event stream. Specialist
artifacts are separately attributable files under their worker project workspace.

## Invariants

1. A worker can own or approve only subjects permitted by the Constitution.
2. A worker cannot approve a subject listed in `cannot_approve`.
3. Mandatory reviewers come from governance data, not model prose.
4. Initial reports are write-once commits for a barrier round.
5. Before every required initial report is committed, workers can read only their own report.
6. Peer conclusions are exposed only after the barrier unlocks.
7. Reviews target immutable report versions and never erase the reviewed version.
8. Revision creates a new report version; disagreement may remain explicit.
9. Only authorised roles can decide a `RoleRequest`; approval creates a worker and graph edges through
   controlled APIs.
10. `OrganisationGraph` records reporting/review/consultation relationships; task dependencies remain
    separate.
11. Provider requests contain only scoped worker/project memory and permitted artifacts, never a
    giant shared transcript.
12. Budget accounting occurs before and after every provider call; over-budget work fails closed.
13. The Director report cites underlying report/review/artifact IDs and separates consensus, dissent,
    assumptions, risks, unresolved questions, judgement, and action.

## Components

| Module | Responsibility |
|---|---|
| `models` | Pydantic schemas and enums for workers, work, messages, reports, provenance, graph, budgets, and project state |
| `governance` | Constitution checks for jurisdiction, approval, mandatory review, role creation, and safety objections |
| `store` | Atomic snapshots, worker workspaces, append-only audit events, scoped memory retrieval, and manifest |
| `providers` | Provider-independent request/result protocol plus deterministic fleet-demo provider |
| `engine` | Explicit state machine, report barrier, communication routing, reviews, revisions, role creation, and synthesis |
| `api` | Minimal FastAPI surface returning one inspectable project snapshot |
| `static/index.html` | Framework-free dashboard over the typed API |

## Independence barrier

At the start of independent work, the barrier freezes the required worker IDs. Each provider receives
only the mission, its worker record, its task, its own scoped memory, and permitted source artifacts.
Reports are committed separately. `visible_peer_reports(worker_id)` raises until the required set is
complete. Once unlocked, only reports relevant to jurisdiction and assigned review obligations are
routed for peer review.

## Controlled topology changes

A worker submits a typed `RoleRequest`. The Director evaluates it against role-creation authority,
project budget, duplicate capability, reason, capabilities, and related tasks. Approval instantiates
a generic specialist with explicit jurisdiction/tools/budget, adds timestamped graph edges, assigns
an initial task, and logs a `Decision` plus an organisational change event. Rejection and deferral are
also retained. No code, prompt, or policy file rewrites itself.

## Provider boundary

Providers receive a typed `WorkRequest` and return a normalized `ProviderResult` containing typed
domain output and usage. Worker records name a provider/model but do not depend on its implementation.
The MVP uses a deterministic provider, making the full demo and tests free of model/API calls. Real
Claude, Codex, or API adapters can be added later without changing governance or lifecycle rules.

## Dashboard boundary

The web layer does not orchestrate work. It creates/runs a mission through the engine and renders a
single `ProjectSnapshot`: mission, state, workers, graph, communications, reports, reviews, dissent,
role changes, director judgement, artifacts, and audit events. The deterministic MVP may run
synchronously; queues and streaming wait until real provider latency requires them.
