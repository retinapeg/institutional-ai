# Frozen research checkpoint

The user stopped live-workers-v1 development to pursue a separate personal CLI,
institutional-workbench. This branch preserves unfinished work, not a release.
The published main branch at 5839bc65e2f041615d3256e9ce38bcd3c091d58a remains the
previous verified deterministic MVP.

Preserved: draft CLI adapters, bounded subprocess execution, provider dispatch,
attempt accounting, recovery changes and stricter report/dissent validation.
Missing: background API execution, cancellation/progress UI, dedicated live-worker
tests, lint/type/build verification and approved live smoke test. No live model
invocation has been performed. CLI restrictions have not been verified end to end.

Checkpoint verification: git diff --check passed; pytest: 22 passed, 1 failed.
The failure is test_health_and_dashboard: health returns provider 'dispatch'
where the existing test expects 'deterministic'. No fix was made after the freeze.

Do not merge this branch into main as a completed milestone.
