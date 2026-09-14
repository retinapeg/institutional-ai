# Migration plan

The source repository remains unchanged. Institutional AI is a new Git repository with its own
package, lifecycle, state, tests, and user interface.

## Reuse decisions

| Source capability | Decision | Institutional adaptation |
|---|---|---|
| `providers/base.py` | Adapt | Keep a small provider protocol and normalized usage/error result; replace coding phases with typed institutional work |
| `providers/scripted.py` | Adapt | Deterministic provider drives the scientific demo and tests without paid calls |
| `audit.py` | Adapt | Retain atomic JSON, append-only JSONL, safe paths, hashes, and manifest; make events organisational |
| `process.py` | Defer | Preserve the bounded-runner design, but add executable tools only when a real provider/tool vertical slice needs them |
| `config.py` | Rewrite small | Pydantic models hold Constitution and budgets; no arena scoring configuration |
| `gitops.py` | Skip | Persistent worker workspaces are project directories, not competing Git candidates |
| `adaptive.py` / `orchestrator.py` | Skip | Replace fixed loops with an explicit institutional state machine and governed APIs |
| scoring/evaluator/integration | Skip | No winner selection or automatic code integration |
| FastAPI/frontend | New | Source has none; use one FastAPI app and one vanilla dashboard page |

The MIT license and [`NOTICE.md`](NOTICE.md) preserve attribution for adapted generic work.

## Vertical delivery order

1. Define typed workers, tasks, artifacts, messages, reports, reviews, role requests, governance,
   budgets, graph, memory, and audit events.
2. Persist one project aggregate and specialist workspace artifacts.
3. Implement state transitions, report barrier, communication routing, review/revision, and dissent.
4. Implement Director-controlled role creation and timestamped graph mutation.
5. Run one deterministic urban-fleet mission end to end.
6. Expose one project snapshot through FastAPI and render the minimal dashboard.
7. Verify domain tests, API tests, clean install, live server, snapshot evidence, and screenshot.

## Deliberate MVP limits

- No real model call or unrestricted tool execution.
- No vector database; memory retrieval is scope-and-tag filtered over persisted records.
- No distributed queue, event broker, WebSocket, SSE, React, D3, or database.
- One local process writes a project at a time.
- Dynamic role creation changes validated state only; it never changes Python code.

Add those capabilities only when the deterministic institutional mechanics remain correct and a real
usage requirement justifies the extra operational surface.

