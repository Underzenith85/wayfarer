# Test strategy

Run `uv run --frozen pytest`. Pytest owns collection; pytest-asyncio runs async
storage, HTTP and provider tests. Hypothesis exercises domain invariants and
prints a reproducible falsifying example and seed information on failure.

Markers are reserved for `integration` (storage/HTTP boundary) and `e2e`
(installed application). The fast default suite currently includes integration
tests because SQLite and loopback HTTP are deterministic and inexpensive. Tests
use temporary databases, injected random sources and local provider fakes. They
never require credentials or public network access.

Composition goes through `tests/support/runtime.py`: `open_store`, `build_play`,
`build_runtime`, `build_orchestrator` and `job_worker` build the production objects
with fakes passed as constructor arguments — a temporary store, a scripted command
clock, a counting seed source, an injected engine factory, a named job partition.
New production collaborators should use constructor arguments rather than module
patches. Ruff bans `unittest.mock`. Existing tests still use pytest's
`monkeypatch` for controlled failure probes, environment/process boundaries, and
some legacy module seams; that is not the primary runtime-composition path.

Coverage is branch-aware and fails below 65% for the package. This initial floor
covers the inherited prototype while emphasizing rules, transactions, runtime
validation and failure boundaries. Raise it as production modules replace demo
paths; do not add mirror tests solely to inflate the number. Every engine issue
must test its domain invariants and negative paths.

The Python CI job runs Python 3.14 with frozen dependencies, Ruff, mypy strict,
the explicit-`Any` gate, quality-gate probes, source/equipment audits, contract
validation, pytest and release-evidence generation, then builds and smoke-tests
the installed wheel. The frontend job runs supported Node 22 and 26 matrices;
Node 22 additionally runs the desktop or full browser journeys, live backend
journeys, reference adventure, production startup, PWA, and browser-evidence gate.
Path filters keep unrelated PRs from running both jobs. The manual/tagged product
release workflow always calls both reusable jobs and the readiness ledger.

PostgreSQL transaction, replay, snapshot, catalog and engine integration tests run
against PostgreSQL 17 in CI. To reproduce them locally, start the service with
`docker compose -f compose.test.yml up -d --wait`, then export the test-only
connection setting:

```bash
export WAYFARER_TEST_DATABASE_URL='postgresql://wayfarer:wayfarer-test-only@127.0.0.1:55432/wayfarer_test'
```

Tests that require PostgreSQL skip when that variable is absent. The SQLite
integration paths do not require Docker.

The integrated [Wave 15 release gates](release-gates.md) publish mechanics coverage,
require reference-adventure and multiplayer evidence, and distinguish engine checks
from full product readiness.
