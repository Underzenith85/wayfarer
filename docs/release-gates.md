# Wave 15 release gates

The `Python package` check runs all engine tests on Python 3.14, including a real
PostgreSQL service. It publishes `engine-release-evidence`: JUnit results, branch
coverage XML, and JSON/Markdown mechanics reports tied to the commit SHA. Reports
are uploaded even on failure. The existing 65% branch-aware coverage floor,
strict typing, lint, formatting, contracts and installed-wheel checks still apply.

`release_gates.py` rejects failed, errored, skipped, empty or missing required
engine evidence. The exact reference-adventure and Wave 9 parameter cases are
pinned in `tests/fixtures/release_cases.json`: deleting a rescue route or a
PostgreSQL variant cannot turn the gate green. The only skip exception is the
explicitly optional, credential-dependent `test_authenticated_codex_smoke`;
provider contracts, timeout, cancellation and adversarial tests remain mandatory.
A failed optional smoke test also blocks release.

```sh
uv run --frozen pytest --junitxml=artifacts/pytest.xml --cov-report=xml:artifacts/coverage.xml
uv run --frozen python -m scripts.release_gates artifacts/pytest.xml
```

Start the test PostgreSQL service from `compose.test.yml` and set
`WAYFARER_TEST_DATABASE_URL` before running the full gate. A local suite that skips
PostgreSQL is useful feedback but cannot certify release. Reports must be generated
from the same checkout; do not reuse another revision's artifacts.

## Declared mechanics and approved sources

`tests/fixtures/approved_rules.json` freezes the complete original Wayfarer
prototype package, source rights, definition statuses and digest. A package change
requires review of the fixture and its tests; never silently upgrade campaigns.
The reference adventure's pinned original rules and serialized scenario fixtures
are additionally checked by the mandatory Wave 14 round-trip/validation tests.
No licensed GURPS rule text is bundled or claimed to be verified.

| Coverage | Evidence | Limit |
| --- | --- | --- |
| Attributes, skills, costs, legality, effects and power | Compiler, rules, power and generated legality tests | Original prototype definitions |
| Inventory, clocks, resource pools | Generated transfer/consume/retry sequences, durable transactions | Authored equipment and effects only |
| Checks, combat and bounded rulings | Server dice, defense/replay, approval bounds | Implemented authored combat coverage; profile certification remains separate |
| GURPS profile checks, contests and resistance | Independent source-referenced fixtures, replay without rerolls, property tests | Profile-gated services; campaign pipelines use the prototype package until a GURPS profile is fully verified |
| Multiplayer and knowledge | Concurrent writers, shared-time barriers, private projections/streams | Explicit sharing only |
| Persistence | SQLite process death during writes, PostgreSQL rollback/concurrency, replay | Recorded authoritative projections; not re-rolling historical actions |
| Adventure lifecycle | Every reference route, all endings, generated graph, capture/rescue, rewards and continuation | Authored and structurally validated scenarios |
| AI boundary | Prompt forgery, hidden-context isolation, stale/timeout/cancel/degradation | Narration is untrusted; semantic prose accuracy is not guaranteed |
| Manual definitions | Keen senses, Fit, Curious, Code of honor | Catalog labels do not grant executable effects |
| Rules profiles and migration | Exact profile selection, fail-closed GURPS profiles, dispatch, atomic and idempotent host migration | Registered pins include bounded verified mechanics, but no full GURPS profile is supported |
| Unsupported | Uncertified GURPS remainder and capabilities still marked `partial`, `manual`, or `absent` | Require independently evidenced implementation or an explicit reviewed boundary |

The divergent-narration test deliberately supplies false victory/HP/equipment
claims and verifies that persisted engine outcomes do not change. This establishes
an authority boundary, not an assertion that arbitrary model prose is truthful.

## Full product release

Run **Product release gates** manually on the candidate revision, or push a `v*`
tag. This does not publish a release. It reuses both existing workflows, runs the
full desktop/phone/tablet browser suites, live setup/voice/capture coverage,
reference-adventure and production-startup tests, and requires every job to pass.
The browser evidence checker names Issue #24's disclosure, reviewed-command parity,
interruption, denied/unsupported/network fallback and scope-revocation journeys;
it also names the scenario-catalog and production PWA journeys. An unrelated green
browser report cannot certify those behaviors.
Routine PR path filters remain in place to avoid running frontend tests on every
backend change; complete browser verification is mandatory for a release candidate.

`docs/product-release.json` records the outstanding manual release verification
in #250. Issues #59 and #60 are closed for implementation, and their automated
browser suites run in the reusable frontend workflow. The product readiness job
intentionally fails until the untested-browser, real-device, assistive-technology,
and manual performance evidence in #250 is recorded. Update this reviewed ledger
when that evidence is complete. An engine gate pass alone does not certify the
product release.

Configure the candidate/release process to require `Product release gates / release`.
Branch protection and publishing policy are repository settings; this change does
not modify them. Unresolved correctness/security failures must be fixed, not
reclassified as optional, skipped, or hidden by a coverage threshold.
