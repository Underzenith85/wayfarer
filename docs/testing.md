# Test strategy

Run `uv run --frozen pytest`. Pytest owns collection; pytest-asyncio runs async
storage, HTTP and provider tests. Hypothesis exercises domain invariants and
prints a reproducible falsifying example and seed information on failure.

Markers are reserved for `integration` (storage/HTTP boundary) and `e2e`
(installed application). The fast default suite currently includes integration
tests because SQLite and loopback HTTP are deterministic and inexpensive. Tests
use temporary databases, injected random sources and local provider fakes. They
never require credentials or public network access.

Coverage is branch-aware and fails below 65% for the package. This initial floor
covers the inherited prototype while emphasizing rules, transactions, runtime
validation and failure boundaries. Raise it as production modules replace demo
paths; do not add mirror tests solely to inflate the number. Every engine issue
must test its domain invariants and negative paths.

The CI matrix runs Python 3.12–3.14 with frozen dependencies, Ruff, mypy strict,
quality-gate probes, pytest, wheel build and an installed application smoke test.

PostgreSQL transaction/replay work begins in #10. Its reproducible local service is
reserved now with `docker compose -f compose.test.yml up -d --wait`; future tests
marked `integration` will read their test-only connection setting. The current
SQLite integration suite does not require Docker.
