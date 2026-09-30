# GURPS Basic Set certification gate

Issue #122 adds the final fail-closed accounting layer for the frozen
`gurps-basic-set-4e-2004` conformance target. This is a release claim gate, not a
second rules engine and not a substitute for the mechanics owned by the linked
implementation issues.

Run:

```bash
uv run --frozen python scripts/certify_gurps_basic_set.py \
  --execution-report artifacts/pytest.xml \
  --output artifacts/release/gurps-basic-set.json
```

The command exits nonzero until every required dimension is complete. The JSON
report records the exact repository commit, registered profile version and digest, frozen source
baseline, required/verified capability counts, required item-level inventory
count and every blocking item with its owning issue.

Certification requires all of the following at the same time:

- the frozen source, errata, scope and fixture audit passes without stale review
  evidence;
- every Basic-required capability in `wayfarer.engine.rules.conformance` is `verified`;
- every Basic-required owner inventory row is source-reviewed and has executable
  implementation evidence (`implemented` or `verified`), and any bound exhaustive
  source-ledger row independently has a required, reviewed, `implemented` or
  `verified` disposition;
- the latest registered Basic profile declares exactly the frozen required
  capability set, carries the exact selected-source certification declaration,
  and does not silently enable optional rules;
- the profile cannot advertise support while any source, capability or item-level
  blocker remains.

The gate intentionally reports current blockers rather than hiding them behind a
generic hook, a manual ruling or generated narration. Scenario and LLM validators
continue to use the existing exact capability registry; model-authored fallback
mechanics do not satisfy certification.

Source, scope, and fixture blockers are filtered by the exact conformance profile.
The unavailable GURPS Lite artifact therefore continues to block Lite certification
without contaminating the Basic Set report. The shared source-audit command still
reports the combined state when no profile is selected.

## Current status

The frozen Basic Set profile is **not certified**. Historical status/path evidence
still describes catalog and construction coverage, but cannot certify execution.
Every required capability, executable inventory row and required mechanic source
row now needs an explicit case binding in
`src/wayfarer/certification/basic_set_audit/executable-evidence.json`. Unbound rows
stay blocked; parent or family status never implicitly certifies children.

Each binding resolves an independently entered source case in `conformance.json`
to exact passing JUnit node IDs. The behavioral test records the case ID and
case, profile, baseline and checkout fingerprints after its consequence assertions.
Missing, failed, skipped, duplicate, stale or status-only evidence fails closed.
The initial registry binds only the existing source-derived success-roll cases;
remaining evidence belongs to the bounded audit issues in #742.

Run pytest with `--junitxml=artifacts/pytest.xml`, then pass
`--execution-report artifacts/pytest.xml` to the certification command. Package
CI publishes blockers using `--report-only` and enforces ordinary quality and
test gates. The product release workflow requires certification and fails while
any obligation remains unverified. `--basic-set-report-only` similarly publishes
accounting from the shared mechanics reporter without claiming Basic Set success.
